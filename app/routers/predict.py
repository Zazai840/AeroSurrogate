"""Prediction endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.schemas import GeometryInput, PredictionResponse
from app.services.prediction import PredictionService

router = APIRouter(prefix="/predict", tags=["predict"])


@router.post("", response_model=PredictionResponse)
async def predict_endpoint(
    inputs: GeometryInput,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PredictionResponse:
    """Predict Cl and Cd for a given airfoil geometry and flight condition."""
    service = PredictionService(
        model=request.app.state.model,
        cache=request.app.state.redis,
        session=session,
        model_version=get_settings().model_version,
    )
    return await service.run(inputs)


@router.post("/batch", response_model=list[PredictionResponse])
async def predict_batch_endpoint(
    inputs: list[GeometryInput],
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[PredictionResponse]:
    """Batch prediction. Each item is handled independently (simple loop).

    Note: this is intentionally naive. A production batch endpoint would
    stack inputs into one model call for vectorised inference, and would
    batch-insert log rows. Left as a deliberate 'what would you improve'
    talking point for interviews.
    """
    service = PredictionService(
        model=request.app.state.model,
        cache=request.app.state.redis,
        session=session,
        model_version=get_settings().model_version,
    )
    return [await service.run(x) for x in inputs]
