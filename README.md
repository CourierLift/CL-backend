# Courier Lifts backend

This repository is the canonical FastAPI + SQLAlchemy backend for the Courier Lifts MVP.

The verified transaction contract is:

`pending -> assigned -> picked_up -> in_transit -> delivered`

A customer or merchant creates a Lift, one eligible courier atomically claims it, only the assigned courier may progress it, durable proof of delivery is required before completion, and both sides recover the same authoritative transaction from fresh sessions.

## Runtime posture

- Python 3.11
- FastAPI monolith
- SQLAlchemy relational persistence
- Alembic schema migrations
- SQLite for local development/tests
- PostgreSQL for production
- S3-compatible object storage for production delivery proof files
- Existing backend quote engine is the only pricing authority

Do not introduce microservices for launch readiness.

## Local setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn backend.main:app --reload
```

Windows PowerShell activation:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
alembic upgrade head
uvicorn backend.main:app --reload
```

Interactive docs are available locally at `http://127.0.0.1:8000/docs`.

## Verification

```bash
python -c "from backend.main import app; print(app.title)"
python -m pytest -q
```

Liveness:

```bash
curl http://127.0.0.1:8000/health
```

Readiness, including database connectivity:

```bash
curl http://127.0.0.1:8000/ready
```

CI runs the transaction suite on both SQLite and PostgreSQL 16. PostgreSQL CI also verifies the Alembic upgrade -> downgrade -> upgrade cycle and the simultaneous courier-claim race.

## Production configuration

All application configuration uses the `CL_` prefix. See `.env.example` for the complete development template.

Key production requirements:

| Variable | Production requirement |
| --- | --- |
| `CL_APP_ENV` | `production` or `prod` |
| `CL_SECRET_KEY` | New secret, at least 32 characters; development placeholders are rejected |
| `CL_DATABASE_URL` | PostgreSQL URL |
| `CL_FRONTEND_ORIGIN` | Final HTTPS frontend origin; localhost is rejected |
| `CL_OBJECT_STORAGE_BACKEND` | `s3` |
| `CL_S3_BUCKET` | Proof-storage bucket |
| `CL_S3_REGION` | S3 region |
| `CL_S3_ENDPOINT_URL` | Optional S3-compatible endpoint |
| `CL_PROOF_MAX_BYTES` | Maximum proof file size |
| `CL_ACCESS_TOKEN_EXPIRE_MINUTES` | JWT lifetime |
| `CL_AUTH_REGISTER_RATE_LIMIT` | Registration limit per process/window |
| `CL_AUTH_LOGIN_RATE_LIMIT` | Login limit per process/window |
| `CL_AUTH_RATE_WINDOW_SECONDS` | Auth limiter window |

Application startup does **not** mutate the production schema. Run `alembic upgrade head` as an explicit release step before starting a new production version.

## Canonical transaction API

Authentication:

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`

Pricing and creation:

- `POST /quote` — coordinate-based quote using Haversine distance
- `POST /quote/estimate` — address-compatible development path
- `POST /orders` — coordinate-based canonical creation
- `POST /orders/create_compat` — address-compatible creation used by the current React sender UI

Marketplace and transaction state:

- `GET /orders/mine`
- `GET /orders/available`
- `GET /orders/assigned`
- `GET /orders/{order_id}` — canonical authorized detail
- `POST /orders/{order_id}/claim`
- `PATCH /orders/{order_id}/status`
- `POST /orders/{order_id}/proof`

Operational:

- `GET /health` — process liveness
- `GET /ready` — database readiness

## Lifecycle and authorization

The server enforces all state transitions. The MVP lifecycle is:

`pending -> assigned -> picked_up -> in_transit -> delivered`

Cancellation is allowed only from explicitly legal states. The backend rejects illegal skips and regressions.

Only the assigned courier can progress courier-owned states or submit proof. A delivery cannot become `delivered` without valid persisted proof. Sender owner, assigned courier, and admin may retrieve the full canonical transaction; unrelated users may not.

## Pricing snapshots

`backend/quote_engine.py` remains the single pricing implementation. At Lift creation the backend persists an immutable pricing snapshot containing the pricing-engine version, final customer total, detailed breakdown, mileage, ETA, tier, vehicle type, and material pricing inputs. Historical orders are read from the persisted snapshot rather than silently repriced under later rules.

## Production distance-source blocker

The current address-compatible quote path intentionally uses `CL_DEVELOPMENT_FALLBACK_MILES` only outside production. It is a development/testing convenience, not a real routing source.

In production, `POST /quote/estimate` and `POST /orders/create_compat` fail closed with HTTP 503 until a real distance/geocoding source is configured. This prevents launch with fabricated fixed-mileage pricing.

Coordinate-based `POST /quote` and `POST /orders` do not use the development fallback.

A production distance source is therefore a **launch blocker** for the current address-based React sender flow. Do not remove the guard merely to make staging appear green.

## Proof storage

Proof metadata is stored relationally on the order; proof file bytes are not stored in the database. Production requires S3-compatible object storage. The local filesystem backend remains available only for development/tests.

## Scaling constraint for MVP launch

Authentication rate limiting and WebSocket tracking are process-local. Until those are replaced by shared infrastructure, deploy the MVP as one application process/worker and do not horizontally scale it.

The database-backed transaction assignment/lifecycle remains authoritative, but multiple application workers would make rate-limit counters and live tracking rooms inconsistent.

## Release and rollback

See `LAUNCH_READINESS.md` for the launch gate and `RUNBOOK.md` for release, smoke-test, incident, and rollback procedure.
