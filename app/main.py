"""FastAPI application entry point.

Lifespan responsibilities:
  * Load the ML model into app.state once, at startup, not per request.
  * Create the Redis client and verify connectivity.
  * Dispose of the SQLAlchemy engine and close Redis on shutdown.

`app.state` is FastAPI/Starlette's blessed place to stash per-process
singletons. Don't use module-level globals for this — lifespan ordering
becomes ambiguous and tests get hard to isolate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.cache import create_redis_client
from app.config import get_settings
from app.db import engine
from app.logging_config import configure_logging
from app.middleware import RequestIDMiddleware
from app.ml_model import load_model
from app.routers import health, history, predict

configure_logging()
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log.info("startup.begin", env=settings.app_env, model_path=settings.model_path)

    app.state.model = load_model(settings.model_path)
    log.info("startup.model_loaded", version=settings.model_version)

    app.state.redis = await create_redis_client()
    log.info("startup.redis_connected")

    log.info("startup.complete")
    try:
        yield
    finally:
        log.info("shutdown.begin")
        await app.state.redis.aclose()
        await engine.dispose()
        log.info("shutdown.complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Aero Surrogate API",
        version="0.1.0",
        description=(
            "Production-style backend wrapping an aerodynamic surrogate model. "
            "Predicts lift and drag coefficients from airfoil geometry and flight "
            "conditions, with Redis caching and a Postgres audit log."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)

    app.include_router(health.router)
    app.include_router(predict.router)
    app.include_router(history.router)

    # Prometheus metrics at /metrics — request count, latency histogram, etc.
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/health/ready",
            "metrics": "/metrics",
        }

    return app


app = create_app()
