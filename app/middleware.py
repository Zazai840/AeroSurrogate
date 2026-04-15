"""Request ID middleware.

Attaches a UUID to every request, binds it into structlog's context vars
so every log line emitted during the request carries it, and echoes it
back in the X-Request-ID response header. This is how you correlate a
user-reported failure to logs without grep-ing on timestamps.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Respect incoming header if a load balancer already set one.
        incoming = request.headers.get("x-request-id")
        request_id = incoming or str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
