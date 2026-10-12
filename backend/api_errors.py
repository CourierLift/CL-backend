"""Structured error contract for the quote-bound order creation routes.

Every 4xx from routes using ``StructuredErrorRoute`` is returned as
``{"detail": {"code": str, "message": str, "fields": list, ...}}``. The
conversion is scoped to those routes so other endpoints keep their existing
error bodies.
"""

from typing import Any, Callable

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


DEFAULT_CODES = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "invalid_request",
}


def api_error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message, "fields": []}
    detail.update(extra)
    return HTTPException(status_code=status_code, detail=detail)


def _validation_fields(exc: RequestValidationError) -> list[dict[str, Any]]:
    fields = []
    for error in exc.errors():
        fields.append(
            {
                "loc": [str(part) for part in error.get("loc", ())],
                "message": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return fields


class StructuredErrorRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": {
                            "code": "invalid_request",
                            "message": "The request body is invalid.",
                            "fields": _validation_fields(exc),
                        }
                    },
                )
            except HTTPException as exc:
                if 400 <= exc.status_code < 500 and not (
                    isinstance(exc.detail, dict) and "code" in exc.detail
                ):
                    message = exc.detail if isinstance(exc.detail, str) else "Request rejected"
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={
                            "detail": {
                                "code": DEFAULT_CODES.get(exc.status_code, "request_rejected"),
                                "message": message,
                                "fields": [],
                            }
                        },
                        headers=exc.headers,
                    )
                raise

        return handler
