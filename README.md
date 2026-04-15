# Aero Surrogate API

A REST API that wraps an aerodynamic surrogate model, predicting lift and drag coefficients (`Cl`, `Cd`) from airfoil geometry and flight conditions.

Built with **FastAPI**, **Redis**, **PostgreSQL**, and a multi-stage Docker deployment.

---

## What it does

Given a NACA 4-digit airfoil parameterisation and flight conditions, the API returns predicted lift (`Cl`) and drag (`Cd`) coefficients. Results are cached in Redis and every request — hit or miss — is written to a Postgres audit log.

The surrogate itself is a scikit-learn MLP trained on synthetic thin-airfoil-theory data. The engineering focus is the production-shaped backend around it.

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
                │  │   PredictionService (cache-aside)  │  │
                │  │  1. hash inputs → cache key        │  │
                │  │  2. GET Redis                      │  │
                │  │     ├─ hit  → return cached Cl,Cd  │  │
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

---

## Stack

| Layer         | Choice                                     |
|---------------|--------------------------------------------|
| Web framework | FastAPI 0.115+                             |
| ORM           | SQLAlchemy 2.0 async + asyncpg             |
| Migrations    | Alembic                                    |
| Cache         | Redis 7 via `redis.asyncio`                |
| Validation    | Pydantic v2 + pydantic-settings            |
| ML            | scikit-learn MLPRegressor + StandardScaler |
| Logging       | structlog (JSON in prod, console in dev)   |
| Metrics       | prometheus-fastapi-instrumentator          |
| Container     | Python 3.12-slim multi-stage, non-root     |
| Orchestration | Docker Compose with healthchecks           |

---

## Quick Start

**Prerequisites:** Docker and Docker Compose.

```bash
cp .env.example .env
docker compose up --build
```

On first boot the `migrate` service runs `alembic upgrade head` and exits before the `api` container starts. The API is then available at `http://localhost:8000` — interactive docs at `/docs`.

### Make a prediction

```python
import requests

response = requests.post("http://localhost:8000/predict", json={
    "max_camber": 0.02,
    "camber_position": 0.4,
    "thickness": 0.12,
    "angle_of_attack": 5.0,
    "reynolds": 1_000_000,
    "mach": 0.3
}).json()

# {'cl': 0.83, 'cd': 0.031, 'cache_hit': False, 'latency_ms': 24.1, ...}
```

Run the same request again and `cache_hit` flips to `true`, with latency dropping from ~25ms to under 1ms.

---

## API Reference

### `POST /predict`

| Field             | Type  | Range        | Description               |
|-------------------|-------|--------------|---------------------------|
| `max_camber`      | float | 0.0 – 0.09   | NACA max camber           |
| `camber_position` | float | 0.0 – 0.9    | NACA camber position      |
| `thickness`       | float | 0.05 – 0.30  | NACA thickness ratio      |
| `angle_of_attack` | float | -10.0 – 20.0 | Angle of attack (degrees) |
| `reynolds`        | float | 1e5 – 1e7    | Reynolds number           |
| `mach`            | float | 0.0 – 0.8    | Mach number               |

Out-of-range or unknown fields return `422`.

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

Accepts a JSON array of prediction inputs, returns an array of responses.

### `GET /history`

Returns the prediction audit log, newest first.

| Param       | Default | Description              |
|-------------|---------|--------------------------|
| `limit`     | 50      | Max rows (1–500)         |
| `offset`    | 0       | Pagination offset        |
| `cache_hit` | —       | Filter by hit/miss       |

### `GET /health/ready`

Checks Postgres, Redis, and model availability. Returns `503` if any dependency is unreachable.

### `GET /metrics`

Prometheus exposition — request counts, latency histograms, and standard instrumentation.

---

## Design Notes

**Cache key hashing** — The cache key is `SHA256(sorted_json(rounded_inputs))`. Inputs are rounded to 6 decimal places before hashing; without this, float parsing differences across clients would collapse the hit rate to near zero.

**Audit log on every request** — Logging only cache misses would make it impossible to compute hit rate or request volume from the database alone. Every request gets a row so the audit trail can answer operational questions independently of Redis.

**Migrations as a separate service** — The `migrate` container runs `alembic upgrade head` once and exits. The `api` service depends on it via `condition: service_completed_successfully`, preventing migration races when scaling replicas.

**Model loaded at startup** — The sklearn pipeline is deserialised once into `app.state` at lifespan startup, avoiding ~50ms of joblib overhead on every request.

**`expire_on_commit=False`** — Required for async SQLAlchemy. The default (`True`) expires ORM attributes after commit and triggers implicit lazy-loads, which raise `MissingGreenlet` in an async session.

---

## Tests

```bash
pytest -q
```

Tests run against SQLite in-memory and `fakeredis` — no real services required. Coverage includes prediction shape and validation, cache key determinism, cache-hit semantics, audit logging, history pagination, and health checks.

---

## Local Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Optionally retrain the surrogate
python ml/train.py

# Set DATABASE_URL and REDIS_URL in .env, then:
alembic upgrade head
uvicorn app.main:app --reload
```
