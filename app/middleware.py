"""Request ID middleware.

Attaches a UUID to every request, binds it into structlog's context vars
so every log line emitted during the request carries it, and echoes it
back in the X-Request-ID response header. This is how you correlate a
user-reported failure to logs without grep-ing on timestamps.
"""

from __future__ import annotations

import time
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

        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000.0

        # Log the completed request with end-to-end wall-clock latency.
        structlog.get_logger().info(
            "request.complete",
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )

        response.headers["X-Request-ID"] = request_id
        # Echo end-to-end latency back to the caller for client-side measurement.
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
        return response
