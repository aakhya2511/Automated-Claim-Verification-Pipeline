"""Exception handlers producing a uniform, client-safe error envelope.

Every failure leaves the service in the same shape::

    {"error": {"code": "...", "message": "...", "details": {...}},
     "request_id": "..."}

Stack traces and provider messages are logged, never serialized. The stable
``code`` field is what clients branch on; ``message`` is for humans and may
change.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.core.exceptions import ClaimVerificationError, RaterError
from app.core.logging import get_logger, get_request_id

logger = get_logger(__name__)

#: Below this, a failure is the caller's fault and logs at warning; at or above
#: it, ours, and logs at error.
_SERVER_ERROR_STATUS = 500

#: Cap on the number of field errors echoed back, so a pathological payload
#: cannot generate an unbounded response.
_MAX_REPORTED_FIELDS = 10


def _envelope(*, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    request_id = get_request_id()
    if request_id:
        body["request_id"] = request_id
    return body


async def handle_domain_error(_request: Request, exc: ClaimVerificationError) -> JSONResponse:
    log = logger.warning if exc.status_code < _SERVER_ERROR_STATUS else logger.error
    is_rater_error = isinstance(exc, RaterError)
    log(
        "domain_error",
        code=exc.code,
        status_code=exc.status_code,
        retryable=exc.retryable,
        detail=None if is_rater_error else exc.message,
    )
    public_message = (
        "The semantic rating provider could not complete this verification."
        if is_rater_error
        else exc.message
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(
            code=exc.code,
            message=public_message,
            details=None if is_rater_error else exc.details,
        ),
    )


async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Flatten pydantic errors into a compact, non-leaky field list."""
    fields = [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())[1:]) or "body",
            "issue": error.get("msg", "invalid value"),
        }
        for error in exc.errors()[:_MAX_REPORTED_FIELDS]
    ]
    logger.warning("request_validation_failed", field_count=len(fields))
    return JSONResponse(
        status_code=422,
        content=_envelope(
            code="invalid_request",
            message="Request payload failed validation.",
            details={"fields": fields},
        ),
    )


async def handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(code=f"http_{exc.status_code}", message=str(exc.detail)),
    )


async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
    """Last resort. The traceback goes to logs; the client gets nothing specific."""
    logger.exception("unhandled_exception", error_type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content=_envelope(code="internal_error", message="An internal error occurred."),
    )


def register_exception_handlers(app: FastAPI) -> None:
    # Starlette types handlers as accepting bare `Exception`; each handler here
    # is narrower by construction because it is registered against exactly one
    # exception class.
    app.add_exception_handler(ClaimVerificationError, handle_domain_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, handle_unexpected_error)
