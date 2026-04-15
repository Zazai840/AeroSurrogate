"""Async SQLAlchemy 2.0 database layer.

Key design points:

* One `Database` instance per process, created at module import and
  disposed at app shutdown.
* `async_sessionmaker(expire_on_commit=False)` — critical in async code.
  If expire_on_commit is True (the default), SQLAlchemy expires all ORM
  attributes after commit, which triggers an implicit lazy-load the next
  time you touch any attribute.  Lazy loads in async sessions raise
  `sqlalchemy.exc.MissingGreenlet`.  Setting it to False here means
  committed objects stay usable without hitting the database again.
* `get_session` is the FastAPI dependency.  It yields a session inside an
  `async with` block so the session is always closed, even on exceptions.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator

from sqlalchemy import DateTime
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(AsyncAttrs, DeclarativeBase):
    """Declarative base for all ORM models.

    `AsyncAttrs` lets us use `await obj.awaitable_attrs.x` for relationship
    access without errors in async contexts.
    """

    type_annotation_map = {
        # Map Python's datetime to a timezone-aware column across all models.
        dt.datetime: DateTime(timezone=True),
    }


class Database:
    """Owns the async SQLAlchemy engine and session factory.

    Encapsulates engine creation, pool configuration, and session management.
    Instantiated once at module level — one engine per process.
    """

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self.engine = create_async_engine(database_url, **self._build_engine_kwargs())
        self.session_maker = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    def _build_engine_kwargs(self) -> dict:
        """Return engine kwargs appropriate for the target database dialect.

        SQLite (used in tests) doesn't support pool_size / max_overflow, so
        those keys are only added for non-SQLite URLs.
        """
        kwargs: dict = {"echo": False, "pool_pre_ping": True}
        if "sqlite" not in self._url:
            kwargs["pool_size"] = 10
            kwargs["max_overflow"] = 20
        return kwargs

    async def dispose(self) -> None:
        """Dispose the engine's connection pool.  Call this at app shutdown."""
        await self.engine.dispose()


# One Database instance per process.
_database = Database(get_settings().database_url)

# Expose engine and session maker at module level so that Alembic env.py
# and any other code that imports them directly continues to work.
engine = _database.engine
async_session_maker = _database.session_maker


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency.  Yields one session per request, always closed."""
    async with _database.session_maker() as session:
        yield session
