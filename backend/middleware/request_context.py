"""Request-scoped middleware: correlation IDs, access logging, security headers."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from backend.config.config import settings
from backend.core.logging_config import access_logger, app_logger

#: Paths that would otherwise fill the access log with noise.
_QUIET_PATHS = frozenset({"/health", "/favicon.ico", "/metrics"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request ID, time the request, and write an access log line.

    The request ID is echoed in the `X-Request-ID` response header and stamped
    into every audit event raised during the request, so a user reporting "it
    said something odd at 3pm" can be traced through the logs from one value.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id

        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            app_logger.exception(
                f"Unhandled error {request.method} {request.url.path} "
                f"({elapsed_ms:.0f} ms) request_id={request_id}"
            )
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"

        if request.url.path not in _QUIET_PATHS:
            access_logger.info(
                f"{_client_ip(request)} | {request.method} {request.url.path} | "
                f"{response.status_code} | {elapsed_ms:.1f}ms | "
                f"user={getattr(request.state, 'username', '-')} | "
                f"dept={getattr(request.state, 'department', '-')} | "
                f"rid={request_id}"
            )

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline hardening headers.

    The CSP is deliberately strict but allows inline styles, which the frontend
    build needs. `connect-src 'self'` keeps a compromised script from
    exfiltrating an answer to a third party.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )

        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "font-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'",
            )

        return response


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "-"
