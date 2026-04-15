"""Pydantic schemas for API request/response validation.

These are the API contract. Anything wrong with a request gets rejected here
with a 422 before it ever touches the model or the database.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GeometryInput(BaseModel):
    """Airfoil geometry + flight conditions. Ranges match the training data."""

    model_config = ConfigDict(extra="forbid")

    max_camber: Annotated[float, Field(ge=0.0, le=0.09, description="NACA max camber")]
    camber_position: Annotated[float, Field(ge=0.0, le=0.9, description="NACA camber position")]
    thickness: Annotated[float, Field(ge=0.05, le=0.30, description="NACA thickness ratio")]
    angle_of_attack: Annotated[float, Field(ge=-10.0, le=20.0, description="Angle of attack (deg)")]
    reynolds: Annotated[float, Field(ge=1e5, le=1e7, description="Reynolds number")]
    mach: Annotated[float, Field(ge=0.0, le=0.8, description="Mach number")]


class PredictionResponse(BaseModel):
    """Surrogate output plus metadata for observability."""

    cl: float = Field(description="Lift coefficient")
    cd: float = Field(description="Drag coefficient")
    model_version: str
    cache_hit: bool
    latency_ms: float
    request_id: UUID


class PredictionLogEntry(BaseModel):
    """One row from the prediction_log table, serialised for /history."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: UUID
    inputs: dict
    cl: float
    cd: float
    model_version: str
    cache_hit: bool
    latency_ms: float
    created_at: dt.datetime


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, str]
