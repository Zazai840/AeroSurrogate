from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.cache import RedisCache
from app.config import get_settings
from app.db import engine
from app.logging_config import configure_logging
from app.middleware import RequestIDMiddleware
from app.ml_model import load_model
from app.routers import health, history, predict

configure_logging()
log = structlog.get_logger()


class AppLifespan:
    """Manages startup and shutdown of process-lifetime resources.

    Loads the ML model and connects to Redis on startup, then
    cleanly disposes of both on shutdown. Resources are stored on
    app.state so routers can access them without globals.
    """

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.settings = get_settings()

    async def startup(self) -> None:
        log.info("startup.begin", env=self.settings.app_env, model_path=self.settings.model_path)

        # Load the surrogate model once — never per request
        self.app.state.model = load_model(self.settings.model_path)
        log.info("startup.model_loaded", version=self.settings.model_version)

        # Connect to Redis and verify the connection is live
        self.app.state.redis = await RedisCache.create(self.settings.redis_url)
        log.info("startup.redis_connected")

        log.info("startup.complete")

    async def shutdown(self) -> None:
        log.info("shutdown.begin")
        await self.app.state.redis.close()
        await engine.dispose()
        log.info("shutdown.complete")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    manager = AppLifespan(app)
    await manager.startup()
    try:
        yield
    finally:
        await manager.shutdown()


class AeroSurrogateApp:
    """Factory for the FastAPI application.

    Wires up middleware, routers, and Prometheus instrumentation.
    Using a class keeps the setup logic grouped and easy to extend.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.app = self._create_app()

    def _create_app(self) -> FastAPI:
        app = FastAPI(
            title="Aero Surrogate API",
            version="0.1.0",
            description=(
                "Backend wrapping an aerodynamic surrogate model. "
                "Predicts lift and drag coefficients from airfoil geometry and flight "
                "conditions, with Redis caching and a Postgres audit log."
            ),
            lifespan=lifespan,
        )

        self._register_middleware(app)
        self._register_routers(app)
        self._register_metrics(app)
        self._register_root(app)

        return app

    def _register_middleware(self, app: FastAPI) -> None:
        # Attaches a unique request ID to every request for log correlation
        app.add_middleware(RequestIDMiddleware)

    def _register_routers(self, app: FastAPI) -> None:
        app.include_router(health.router)
        app.include_router(predict.router)
        app.include_router(history.router)

    def _register_metrics(self, app: FastAPI) -> None:
        # Exposes request count, latency histograms etc. at /metrics
        Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    def _register_root(self, app: FastAPI) -> None:
        settings = self.settings

        @app.get("/", tags=["root"])
        async def root() -> dict[str, str]:
            return {
                "name": settings.app_name,
                "version": "0.1.0",
                "docs": "/docs",
                "health": "/health/ready",
                "metrics": "/metrics",
            }


# Backwards-compatible factory used by the test fixtures
def create_app() -> FastAPI:
    return AeroSurrogateApp().app


# Instantiate the app — uvicorn picks this up as the ASGI entry point
app = create_app()
