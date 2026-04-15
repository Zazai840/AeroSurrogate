"""Surrogate model loader and prediction wrapper.

The model is a scikit-learn Pipeline (StandardScaler + MLPRegressor).
It is loaded once at app startup (in the FastAPI lifespan) and stashed
on `app.state.model`. Per-request loading would be catastrophic for
latency and is the single most common beginner mistake in ML serving.

The `predict` call is synchronous CPU work. For a toy MLP with ~10k
parameters, inference is sub-millisecond, so we call it directly on
the event loop. For anything heavier (real NN, GP with O(n^2) kernel,
etc.) you would offload to a thread pool with
`await anyio.to_thread.run_sync(model.predict, x)` so the event loop
stays responsive. Good thing to mention in an interview.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.schemas import GeometryInput

# Feature order MUST match ml/train.py. If you change one, change both.
FEATURE_ORDER = (
    "max_camber",
    "camber_position",
    "thickness",
    "angle_of_attack",
    "reynolds",
    "mach",
)


def load_model(path: str | Path) -> Any:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Model file not found at {path}. Run `python ml/train.py` first."
        )
    return joblib.load(path)


def predict(model: Any, inputs: GeometryInput) -> tuple[float, float]:
    """Run the surrogate. Returns (cl, cd)."""
    x = np.array([[getattr(inputs, name) for name in FEATURE_ORDER]], dtype=np.float64)
    y = model.predict(x)
    cl, cd = float(y[0, 0]), float(y[0, 1])
    return cl, cd
