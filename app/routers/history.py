"""History router: paginated read of the prediction log.

Exposes two read-only endpoints:
  GET /history           — newest-first list with limit/offset pagination
  GET /history/{request_id} — single entry lookup by UUID
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models_db import PredictionLog
from app.schemas import PredictionLogEntry

router = APIRouter(prefix="/history", tags=["history"])


@router.get("", response_model=list[PredictionLogEntry])
async def get_history(
    limit: int = Query(50, ge=1, le=500, description="Maximum rows to return"),
    offset: int = Query(0, ge=0, description="Number of rows to skip"),
    cache_hit: bool | None = Query(None, description="Filter by cache hit status"),
    session: AsyncSession = Depends(get_session),
) -> list[PredictionLogEntry]:
    """Return the most recent prediction log rows, newest first.

    Offset pagination is fine for a demo. At scale you would switch to
    cursor pagination (keyset on created_at + id) to avoid the O(offset)
    cost that grows on large tables.
    """
    # Secondary sort on id ensures stable ordering when timestamps collide
    # (common in tests; also possible on high-throughput inserts in prod).
    stmt = select(PredictionLog).order_by(
        PredictionLog.created_at.desc(),
        PredictionLog.id.desc(),
    )

    # Optionally narrow results to only cache hits or only misses.
    if cache_hit is not None:
        stmt = stmt.where(PredictionLog.cache_hit == cache_hit)

    stmt = stmt.limit(limit).offset(offset)

    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [PredictionLogEntry.model_validate(row) for row in rows]


@router.get("/{request_id}", response_model=PredictionLogEntry)
async def get_history_entry(
    request_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> PredictionLogEntry:
    """Return a single prediction log entry by its request UUID.

    Returns 404 if no entry exists for the given request_id.
    """
    stmt = select(PredictionLog).where(PredictionLog.request_id == request_id)
    result = await session.execute(stmt)
    row = result.scalars().first()

    if row is None:
        raise HTTPException(status_code=404, detail="Prediction log entry not found")

    return PredictionLogEntry.model_validate(row)
