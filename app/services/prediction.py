"""Prediction service: orchestrates cache -> model -> DB log.

This module defines `PredictionService`, which encodes the core request flow.
Keeping it out of the router makes it unit-testable and keeps the HTTP layer dumb.

Flow:
    1. Hash inputs into a deterministic cache key.
    2. Look up in Redis. On hit, use cached cl/cd and mark cache_hit=True.
    3. On miss, run the model, write result to Redis.
    4. Always persist a PredictionLog row (hit or miss).
    5. Return the response.

We log on hits too. If we skipped logging on hits, the DB would only show
misses and we'd have no way to measure hit rate from the audit trail. The
whole point of the log is to answer questions like 'what fraction of requests
are served from cache at 2am' — that answer lives in the DB, not Redis.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import RedisCache, make_cache_key
from app.ml_model import predict
from app.models_db import PredictionLog
from app.schemas import GeometryInput, PredictionResponse


class PredictionService:
    """Orchestrates a single aerodynamic surrogate prediction.

    Constructed per-request with the model, cache, and DB session injected.
    This keeps each dependency explicit and makes the class trivially testable
    without needing the full FastAPI app running.
    """

    def __init__(
        self,
        model: Any,
        cache: RedisCache,
        session: AsyncSession,
        model_version: str,
    ) -> None:
        self._model = model
        self._cache = cache
        self._session = session
        self._model_version = model_version

    async def run(self, inputs: GeometryInput) -> PredictionResponse:
        """Execute the predict → cache → log pipeline and return a response."""
        started = time.perf_counter()
        request_id = self._resolve_request_id()
        inputs_dict = inputs.model_dump()

        cl, cd, cache_hit = await self._get_or_compute(inputs, inputs_dict)

        latency_ms = (time.perf_counter() - started) * 1000.0

        await self._log_to_db(request_id, inputs_dict, cl, cd, cache_hit, latency_ms)

        return PredictionResponse(
            cl=cl,
            cd=cd,
            model_version=self._model_version,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
            request_id=request_id,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_request_id(self) -> uuid.UUID:
        """Pull the request ID from structlog context, or mint a fresh one.

        The middleware binds a UUID string to the structlog context under
        'request_id'. When we're outside a real HTTP request (e.g. in unit
        tests that call the service directly), there is no bound context, so
        we fall back to generating a new UUID.
        """
        ctx = structlog.contextvars.get_contextvars()
        if "request_id" in ctx:
            return uuid.UUID(ctx["request_id"])
        return uuid.uuid4()

    async def _get_or_compute(
        self,
        inputs: GeometryInput,
        inputs_dict: dict,
    ) -> tuple[float, float, bool]:
        """Return (cl, cd, cache_hit). Populates the cache on a miss."""
        cache_key = make_cache_key(inputs_dict)
        cached = await self._cache.get(cache_key)

        if cached is not None:
            return cached["cl"], cached["cd"], True

        cl, cd = predict(self._model, inputs)
        await self._cache.set(cache_key, {"cl": cl, "cd": cd})
        return cl, cd, False

    async def _log_to_db(
        self,
        request_id: uuid.UUID,
        inputs_dict: dict,
        cl: float,
        cd: float,
        cache_hit: bool,
        latency_ms: float,
    ) -> None:
        """Persist a PredictionLog row regardless of cache outcome."""
        log_row = PredictionLog(
            request_id=request_id,
            inputs=inputs_dict,
            cl=cl,
            cd=cd,
            model_version=self._model_version,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
        )
        self._session.add(log_row)
        await self._session.commit()
