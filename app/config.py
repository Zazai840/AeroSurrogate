"""Typed application configuration loaded from environment variables.

Settings are parsed once at startup via `get_settings()` and cached for
the lifetime of the process.  Override any field by setting the matching
environment variable (e.g. APP_ENV=production).  In production, skip the
.env file entirely and inject secrets through the environment.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All application settings in one place.

    Pydantic-settings reads values from environment variables first, then
    falls back to the .env file, then to the defaults declared here.
    `extra="ignore"` means unknown env vars are silently discarded — no
    noisy warnings about unrelated variables already in the shell.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_name: str = "aero-surrogate"
    app_env: str = "development"  # development | test | production
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    database_url: str = (
        "postgresql+asyncpg://aero:aero_dev_password@postgres:5432/aero_surrogate"
    )

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    cache_ttl_seconds: int = 3600

    # ------------------------------------------------------------------
    # ML model
    # ------------------------------------------------------------------
    model_path: str = "/app/ml/model.pkl"
    model_version: str = "v0.1.0"

    # ------------------------------------------------------------------
    # Environment helpers
    # ------------------------------------------------------------------

    @property
    def is_production(self) -> bool:
        """True when the app is running in a production environment."""
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        """True when running locally in development mode."""
        return self.app_env.lower() == "development"

    @property
    def is_test(self) -> bool:
        """True when running under the test suite."""
        return self.app_env.lower() == "test"

    @property
    def log_level_int(self) -> int:
        """Numeric logging level derived from `log_level`.

        Falls back to INFO if the configured string isn't a recognised
        level name, so a typo in the env var never silences all logs.
        """
        level = logging.getLevelName(self.log_level.upper())
        # getLevelName returns a string when the name is unknown.
        return level if isinstance(level, int) else logging.INFO


@lru_cache
def get_settings() -> Settings:
    """Singleton settings accessor.  lru_cache ensures env is parsed once."""
    return Settings()
