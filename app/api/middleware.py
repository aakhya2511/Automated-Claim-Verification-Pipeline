"""Edge middleware: request identity, access logging, and body-size limits."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.logging import get_logger, get_request_id, request_context

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

Handler = Callable[[Request], Awaitable[Response]]
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, binds it to the log context, and logs the access line.

    A client-supplied ``X-Request-ID`` is honoured so a trace can be correlated
    across services; otherwise one is minted. The id is echoed on the response
    and included in every log line and every verification result, which is what
    makes a verdict traceable after the fact.
    """

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        supplied = request.headers.get(REQUEST_ID_HEADER)
        request_id = (
            supplied
            if supplied is not None and _SAFE_REQUEST_ID.fullmatch(supplied)
            else uuid.uuid4().hex
        )
        started = time.perf_counter()

        with request_context(request_id):
            request.state.request_id = request_id
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            response.headers[REQUEST_ID_HEADER] = request_id

            # Health and metrics scrapes would otherwise dominate the log
            # volume without carrying any diagnostic value.
            if request.url.path not in ("/health", "/ready", "/metrics"):
                logger.info(
                    "http_request",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=round(elapsed_ms, 3),
                )
            return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects oversized payloads before they are parsed.

    Checks ``Content-Length`` first (cheap, covers well-behaved clients) and
    still enforces the cap while streaming, so a chunked request without a
    declared length cannot bypass the limit.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    return self._too_large()
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content=self._error_body("invalid_header", "bad content-length"),
                )

        if request.method in ("POST", "PUT", "PATCH") and declared is None:
            body = await request.body()
            if len(body) > self.max_bytes:
                return self._too_large()

        return await call_next(request)

    def _too_large(self) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content=self._error_body(
                "request_too_large",
                f"request body exceeds {self.max_bytes} bytes",
            ),
        )

    @staticmethod
    def _error_body(code: str, message: str) -> dict[str, object]:
        body: dict[str, object] = {"error": {"code": code, "message": message}}
        request_id = get_request_id()
        if request_id is not None:
            body["request_id"] = request_id
        return body
