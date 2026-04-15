"""SQLAlchemy ORM models.

`PredictionLog` is the audit trail. Every prediction request gets a row,
cache hits included. This is the table an interviewer will ask about when
they want to see how you think about observability and auditability.
"""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy import JSON, BigInteger, Boolean, Float, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PredictionLog(Base):
    __tablename__ = "prediction_log"

    # BigInteger in Postgres (BIGSERIAL), Integer in SQLite so the
    # INTEGER PRIMARY KEY autoincrement semantics kick in for tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(sa.Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        nullable=False,
        unique=True,
        index=True,
    )
    # JSONB on Postgres, falls back to JSON elsewhere (e.g. SQLite in tests).
    inputs: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=False)
    cl: Mapped[float] = mapped_column(Float, nullable=False)
    cd: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_prediction_log_created_at_desc", created_at.desc()),
        Index("ix_prediction_log_cache_hit", cache_hit),
    )
