# Aero Surrogate API

A production-style REST API that wraps an aerodynamic surrogate model, predicting lift and drag coefficients (`Cl`, `Cd`) from airfoil geometry and flight conditions. Built with FastAPI, Redis caching, PostgreSQL audit logging, and a multi-stage Docker deployment.

---

## Overview

The surrogate model is a scikit-learn MLP trained on synthetic thin-airfoil-theory data (NACA 4-digit parameterisation). The focus of the project is the production-shaped backend around it: async request handling, cache-aside caching with deterministic key hashing, per-request audit logging, versioned schema migrations, structured logs with request ID propagation, Prometheus metrics, dependency-ordered healthchecks, and a non-root multi-stage Docker image.

---

## Architecture

```
                ┌──────────────────────────────────────────┐
                │                Client                    │
                └─────────────────┬────────────────────────┘
                                  │ HTTP
                                  ▼
                ┌──────────────────────────────────────────┐
                │            FastAPI (uvicorn)             │
                │  ┌────────────────────────────────────┐  │
                │  │  RequestIDMiddleware               │  │
                │  │  /predict  /history  /health       │  │
                │  └──────────┬─────────────────────────┘  │
                │             │                            │
                │             ▼                            │
                │  ┌────────────────────────────────────┐  │
                │  │ PredictionService (cache-aside)    │  │
                │  │  1. hash inputs → key              │  │
                │  │  2. GET Redis                      │  │
                │  │     ├─ hit  → use cached cl,cd     │  │
                │  │     └─ miss → model.predict()      │  │
                │  │             → SET Redis            │  │
                │  │  3. always: INSERT PredictionLog   │  │
                │  └─────┬──────────────┬───────────────┘  │
                │        │              │                  │
                │        ▼              ▼                  │
                │   Surrogate       AsyncSession           │
                │   (app.state)   (SQLAlchemy 2.0)         │
                └────────┼──────────────┼──────────────────┘
                         │              │
                         ▼              ▼
                  ┌─────────────┐  ┌─────────────┐
                  │    Redis    │  │  Postgres   │
                  │ (cache:TTL) │  │ (audit log) │
                  └─────────────┘  └─────────────┘
```

The model is loaded once into `app.state` at lifespan startup — never per request. The Redis client and SQLAlchemy engine each maintain internal connection pools.

---

## Stack

| Layer            | Choice                                     |
|------------------|--------------------------------------------|
| Web framework    | FastAPI 0.115+                             |
| ORM              | SQLAlchemy 2.0 async + asyncpg             |
| Migrations       | Alembic (async template)                   |
| Cache            | Redis 7 via `redis.asyncio` (redis-py 5+)  |
| Validation       | Pydantic v2 + pydantic-settings            |
| ML               | scikit-learn MLPRegressor + StandardScaler |
| Logging          | structlog (JSON in prod, console in dev)   |
| Metrics          | prometheus-fastapi-instrumentator          |
| Container        | python:3.12-slim multi-stage, non-root     |
| Orchestration    | Docker Compose with healthchecks           |

---

## Quick Start

### Prerequisites

- Docker and Docker Compose

### Run the stack

```bash
cp .env.example .env
docker compose up --build
```

On first boot:

1. `postgres` and `redis` start and pass their healthchecks.
2. `migrate` runs `alembic upgrade head` against Postgres, then exits.
3. `api` starts only after `migrate` exits successfully.

The API is available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### Make a prediction

```python
import requests

result = requests.post("http://localhost:8000/predict", json={
    "max_camber": 0.02,
    "camber_position": 0.4,
    "thickness": 0.12,
    "angle_of_attack": 5.0,
    "reynolds": 1000000,
    "mach": 0.3
}).json()

print(result)
# {'cl': 0.83, 'cd': 0.031, 'cache_hit': False, 'latency_ms': 24.1, ...}
```

Run the same request again and `cache_hit` flips to `true`, with latency dropping from ~25ms to under 1ms.

---

## API Reference

### `POST /predict`

**Request body**

| Field             | Type  | Range         | Description               |
|-------------------|-------|---------------|---------------------------|
| `max_camber`      | float | 0.0 – 0.09    | NACA max camber           |
| `camber_position` | float | 0.0 – 0.9     | NACA camber position      |
| `thickness`       | float | 0.05 – 0.30   | NACA thickness ratio      |
| `angle_of_attack` | float | -10.0 – 20.0  | Angle of attack (degrees) |
| `reynolds`        | float | 1e5 – 1e7     | Reynolds number           |
| `mach`            | float | 0.0 – 0.8     | Mach number               |

Out-of-range or extra fields return `422`.

**Response**

```json
{
  "cl": 0.8296,
  "cd": 0.0310,
  "model_version": "v0.1.0",
  "cache_hit": false,
  "latency_ms": 24.1,
  "request_id": "f823cf3a-1243-403c-96d3-c5b6c43e29a0"
}
```

### `POST /predict/batch`

Accepts a JSON array of `GeometryInput` objects, returns an array of `PredictionResponse`.

### `GET /history`

Returns the prediction audit log from Postgres, newest first.

| Param       | Default | Description                   |
|-------------|---------|-------------------------------|
| `limit`     | 50      | Max rows returned (1–500)     |
| `offset`    | 0       | Pagination offset             |
| `cache_hit` | —       | Filter by hit/miss (optional) |

### `GET /health/ready`

Checks Postgres, Redis, and model availability. Returns `503` if any dependency is unreachable.

### `GET /metrics`

Prometheus exposition format — request counts, latency histograms, and standard instrumentation.

---

## Design Decisions

### Async SQLAlchemy 2.0 + asyncpg

FastAPI runs on an async event loop. A synchronous database driver would block the worker thread on every query, eliminating the concurrency benefit. With `asyncpg`, the event loop yields during I/O and can handle other requests while the database works.

### Cache-aside with rounded input hashing

The cache key is `SHA256(canonical_json({rounded inputs}))`. Inputs are rounded to 6 decimal places before hashing — without rounding, floating-point parsing differences across clients would give an effective cache hit rate of zero. Six decimal places is enough precision to distinguish meaningfully different queries while still collapsing identical ones.

Cache-aside keeps Redis as a pure accelerator: if Redis goes down, the app degrades gracefully to direct model inference.

### Audit logging on cache hits and misses

Every request — hit or miss — is written to `prediction_log`. Logging only misses would make it impossible to compute hit rate, request volume, or per-input frequency from the database. The audit trail answers operational questions that the cache alone cannot.

### Migrations in a separate container

The `migrate` service runs `alembic upgrade head` once and exits. The `api` service depends on it via `condition: service_completed_successfully`. This prevents migration races when scaling to multiple replicas and makes migration failures clearly distinguishable from application failures.

### Model loaded at lifespan startup

Loading the model once into `app.state` at startup avoids ~50ms of joblib deserialisation on every request. The FastAPI `lifespan` context manager is the correct place for process-lifetime resources, and it makes test isolation straightforward.

---

## Bug Fixes

Two bugs were identified and fixed during development:

### 1. Request ID mismatch (`app/services/prediction.py`)

`run_prediction` was generating a fresh `uuid.uuid4()` instead of reading the request ID already set by `RequestIDMiddleware`. This meant the `request_id` in the response body and the `PredictionLog` row never matched the `X-Request-ID` response header, breaking log correlation.

**Fix:** Read the ID from `structlog.contextvars` so the header, response body, and audit log all carry the same value.

### 2. `camber_position` silently ignored in training (`ml/train.py`)

`sample_inputs` generated `camber_position` as column 1 and included it in the feature matrix, but `ground_truth` skipped index 1 entirely when computing labels. The model was trained on 6 features but only 5 influenced the outputs, so it learned a spurious relationship for `camber_position`.

**Fix:** `ground_truth` now reads `camber_position` and uses it to modulate the zero-lift angle — camber near the leading edge (low `p`) produces a stronger lift effect than camber near the trailing edge. The model was retrained after the fix (R² = 0.9946).

---

## Local Development (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Retrain the surrogate (optional)
python ml/train.py

# Start Postgres and Redis, then set DATABASE_URL and REDIS_URL in .env
alembic upgrade head
uvicorn app.main:app --reload
```

## Tests

```bash
pytest -q
```

15 tests covering prediction shape and validation, cache key determinism, cache-hit semantics, audit logging, history pagination and filtering, and health checks. Tests run against SQLite-in-memory and `fakeredis`.

---

## Project Layout

```
aero-surrogate/
├── app/
│   ├── main.py               # FastAPI app, lifespan, router wiring
│   ├── config.py             # pydantic-settings
│   ├── db.py                 # async engine, session factory
│   ├── cache.py              # Redis client, cache-key hashing
│   ├── ml_model.py           # surrogate loader and predict wrapper
│   ├── models_db.py          # SQLAlchemy ORM models
│   ├── schemas.py            # Pydantic request/response models
│   ├── middleware.py         # RequestIDMiddleware
│   ├── logging_config.py     # structlog setup
│   ├── services/
│   │   └── prediction.py     # cache → model → log orchestration
│   └── routers/
│       ├── predict.py
│       ├── history.py
│       └── health.py
├── alembic/
│   └── versions/
│       └── 0001_initial.py
├── ml/
│   ├── train.py              # synthetic data generator + MLP trainer
│   └── model.pkl             # trained model artifact
├── tests/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```
