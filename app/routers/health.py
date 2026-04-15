"""Health endpoints for Docker healthcheck, load balancers, and debugging.

Three levels:
  /health        — liveness. Cheap. "Process is up."
  /health/ready  — readiness. Checks DB and Redis. "Can serve traffic."
  /health/db     — DB only, for targeted debugging.
  /health/redis  — Redis only, for targeted debugging.

Docker healthcheck hits /health/ready so the API container is only
marked healthy once it can actually reach its dependencies.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok", checks={"process": "ok"})


@router.get("/ready")
async def readiness(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    checks: dict[str, str] = {}
    overall_ok = True

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"fail: {exc.__class__.__name__}"
        overall_ok = False

    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"fail: {exc.__class__.__name__}"
        overall_ok = False

    checks["model"] = "ok" if getattr(request.app.state, "model", None) is not None else "fail"
    if checks["model"] != "ok":
        overall_ok = False

    code = status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=code,
        content={"status": "ok" if overall_ok else "degraded", "checks": checks},
    )


@router.get("/db")
async def db_health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
        return JSONResponse(status_code=status.HTTP_200_OK, content={"database": "ok"})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"database": f"fail: {exc.__class__.__name__}"},
        )


@router.get("/redis")
async def redis_health(request: Request) -> JSONResponse:
    try:
        await request.app.state.redis.ping()
        return JSONResponse(status_code=status.HTTP_200_OK, content={"redis": "ok"})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"redis": f"fail: {exc.__class__.__name__}"},
        )
