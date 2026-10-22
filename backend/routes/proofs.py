"""Durable proof-of-delivery upload route."""

from hashlib import sha256
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps_jwt import get_current_user
from ..models import DeliveryProof, Order, OrderStatus, User, UserRole
from ..schemas import DeliveryProofOut
from ..services.object_storage import ObjectStorage, get_object_storage
from ..settings import settings


router = APIRouter(tags=["proofs"])
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

    content_type = (file.content_type or "").lower()
    extension = ALLOWED_PROOF_CONTENT_TYPES.get(content_type)
    if extension is None:
        raise HTTPException(status_code=415, detail="Proof must be a JPEG, PNG, or WebP image")

    data = await file.read(settings.CL_PROOF_MAX_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="Proof file is empty")
    if len(data) > settings.CL_PROOF_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Proof file exceeds the configured size limit")

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
