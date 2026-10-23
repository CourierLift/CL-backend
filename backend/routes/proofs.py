"""Durable proof-of-delivery upload route."""

from hashlib import sha256
from functools import partial
from threading import BoundedSemaphore
from uuid import uuid4

from anyio import to_thread
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.formparsers import MultiPartException

from ..database import get_db
from ..deps_jwt import get_current_user
from ..models import DeliveryProof, Order, OrderStatus, User, UserRole
from ..schemas import DeliveryProofOut
from ..services.object_storage import ObjectStorage, get_object_storage
from ..services.proof_images import MAX_RAW_BYTES, ProofImageError, declared_content_type, normalize_proof
from ..settings import settings


class BoundedProofRoute(APIRoute):
    """Bound multipart bytes before FastAPI parses/spools an uploaded file."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            if request.method != "POST":
                return await handler(request)
            limit = min(settings.CL_PROOF_MAX_BYTES, MAX_RAW_BYTES) + 64 * 1024
            too_large = False
            received = 0
            declared_length = request.headers.get("content-length")
            if declared_length is not None:
                try:
                    length = int(declared_length)
                    if length < 0:
                        raise ValueError
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid upload content length") from None
                if length > limit:
                    raise HTTPException(status_code=413, detail="Proof upload exceeds the configured size limit")

            async def limited_receive():
                nonlocal received, too_large
                message = await request.receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > limit:
                        too_large = True
                        # Starlette closes partially spooled files on this error.
                        raise MultiPartException("Proof upload exceeds the configured size limit")
                return message

            try:
                return await handler(Request(request.scope, receive=limited_receive))
            except Exception:
                if too_large:
                    raise HTTPException(status_code=413, detail="Proof upload exceeds the configured size limit") from None
                raise

        return bounded


router = APIRouter(tags=["proofs"], route_class=BoundedProofRoute)
_IMAGE_PROCESSING_SLOT = BoundedSemaphore(1)
ALLOWED_PROOF_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@router.get("/orders/{order_id}/proof")
def view_delivery_proof(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    storage: ObjectStorage = Depends(get_object_storage),
) -> Response:
    # Authenticate and authorize before consulting storage. Never expose a public
    # bucket URL or put the bearer token in a photo URL.
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if not (order.user_id == current_user.id
            or order.assigned_courier_id == current_user.id
            or current_user.role == UserRole.admin):
        raise HTTPException(status_code=403, detail="Not authorized to view this order")
    proof = order.proof
    if proof is None:
        raise HTTPException(status_code=404, detail="Delivery proof is not recorded")
    if proof.content_type not in ALLOWED_PROOF_CONTENT_TYPES:
        raise HTTPException(status_code=503, detail="Delivery proof is unavailable")
    try:
        data = storage.read(proof.storage_key, settings.CL_PROOF_MAX_BYTES)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Delivery proof is unavailable") from exc
    if len(data) != proof.size_bytes or sha256(data).hexdigest() != proof.sha256:
        raise HTTPException(status_code=503, detail="Delivery proof integrity check failed")
    return Response(content=data, media_type=proof.content_type, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'inline; filename="delivery-proof' + ALLOWED_PROOF_CONTENT_TYPES[proof.content_type] + '"',
    })


@router.post(
    "/orders/{order_id}/proof",
    response_model=DeliveryProofOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_delivery_proof(
    order_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DeliveryProof:
    if current_user.role != UserRole.courier:
        raise HTTPException(status_code=403, detail="Courier role required")

    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.assigned_courier_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the assigned courier may submit proof")
    if order.status != OrderStatus.in_transit:
        raise HTTPException(status_code=409, detail="Proof may only be submitted while the order is in transit")
    if order.proof is not None:
        raise HTTPException(status_code=409, detail="Proof has already been submitted")

    try:
        content_type = declared_content_type(file.content_type)
    except ProofImageError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    if not _IMAGE_PROCESSING_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="Photo processing is busy. Try again in a moment.",
                            headers={"Retry-After": "2"})
    try:
        raw = await file.read(min(settings.CL_PROOF_MAX_BYTES, MAX_RAW_BYTES) + 1)
        normalized = await to_thread.run_sync(partial(
            normalize_proof, raw, content_type, max_input_bytes=settings.CL_PROOF_MAX_BYTES,
        ))
    except ProofImageError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    finally:
        _IMAGE_PROCESSING_SLOT.release()
    data = normalized.data
    content_type = normalized.content_type
    extension = normalized.extension

    storage_key = f"proofs/{order.id}/{uuid4().hex}{extension}"
    try:
        stored = storage.put(storage_key, data, content_type)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to store delivery proof") from exc

    proof = DeliveryProof(
        order_id=order.id,
        uploaded_by_user_id=current_user.id,
        storage_key=stored.key,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        sha256=sha256(data).hexdigest(),
    )
    db.add(proof)
    try:
        db.commit()
        db.refresh(proof)
    except IntegrityError as exc:
        db.rollback()
        storage.delete(storage_key)
        raise HTTPException(status_code=409, detail="Proof has already been submitted") from exc
    except Exception:
        db.rollback()
        storage.delete(storage_key)
        raise

    return proof
