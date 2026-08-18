# Courier Lifts backend

This repository is the canonical FastAPI backend for the Courier Lifts MVP. It
supports the first marketplace loop:

1. A customer or merchant creates a delivery.
2. Eligible couriers see the unclaimed delivery.
3. One courier atomically claims it.
4. Only the assigned courier can advance the delivery.
5. The delivery completes after the legal status sequence.

The MVP uses Python 3.11, FastAPI, SQLAlchemy, and SQLite. It does not charge
cards, call a maps provider, use Redis, or claim production-scale telemetry.

## First-time setup on macOS or Linux

Run these commands from the repository root, one line at a time:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and replace `CL_SECRET_KEY` with a long random value. Keep `.env`
local; Git ignores it.

Start the API:

```bash
uvicorn backend.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API documentation.

## First-time setup on Windows PowerShell

Run these commands from the repository root, one line at a time:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Open `.env` and replace `CL_SECRET_KEY` with a long random value. Then start the
API:

```powershell
uvicorn backend.main:app --reload
```

## Verify the installation

With the virtual environment active:

```bash
python -c "from backend.main import app; print(app.title)"
pytest -q
```

The health endpoint should return HTTP 200:

```bash
curl http://127.0.0.1:8000/health
```

## Environment variables

| Variable | Purpose |
| --- | --- |
| `CL_APP_ENV` | Environment label returned by `/health`. |
| `CL_SECRET_KEY` | Secret used to sign authentication tokens. |
| `CL_JWT_ALGORITHM` | JWT signing algorithm; the MVP default is `HS256`. |
| `CL_ACCESS_TOKEN_EXPIRE_MINUTES` | Authentication-token lifetime. |
| `CL_DATABASE_URL` | SQLAlchemy database URL. The MVP default is local SQLite. |
| `CL_FRONTEND_ORIGIN` | Browser origin allowed by CORS. |
| `CL_DEVELOPMENT_FALLBACK_MILES` | Fixed distance for address-only local estimates. |
| `CL_AUTH_REGISTER_RATE_LIMIT` | Maximum registration attempts per client IP and rate window. |
| `CL_AUTH_LOGIN_RATE_LIMIT` | Maximum login attempts per client IP and rate window. |
| `CL_AUTH_RATE_WINDOW_SECONDS` | Sliding window used by the single-process auth rate limiter. |

All application configuration uses the `CL_` prefix.

When `CL_APP_ENV` is `production`, startup fails unless `CL_SECRET_KEY` is a
new value of at least 32 characters. The development placeholders are rejected.

## Frontend-compatible API

The existing frontend contract remains available:

- `POST /auth/register`
- `POST /auth/login`
- `POST /quote/estimate`
- `POST /orders/create_compat`
- `GET /orders/mine`
- `GET /rewards/balance`
- `POST /rewards/event` (trusted administrators only)
- `GET /health`

`/quote/estimate` still returns `price_total` and `eta_min`.
`/orders/create_compat` still accepts `origin`, `destination`, `vehicle`,
`item_type`, `weight_kg`, `quantity`, and optional dimensions.

## Marketplace API

- `GET /orders/available` lists only deliveries that match the authenticated
  courier's transportation mode, weight/dimension limits, volume limit, and
  required capabilities. Exact pickup/drop-off addresses and coordinates are
  redacted until the courier successfully claims the order.
- `POST /orders/{order_id}/claim` atomically changes one unclaimed `pending`
  order to `assigned` and records the courier.
- `PATCH /orders/{order_id}/status` enforces the sequence
  `assigned -> picked_up -> delivered`. Customers and merchants may only cancel
  their own order from a legal state. Couriers cannot update another courier's
  delivery.

Supported transportation inputs include foot, bike, cargo bike, e-bike,
scooter, motorcycle, car, EV, SUV, van, light truck, and box truck. Common
hyphen, space, case, and legacy aliases are normalized by the quote engine.

## Pricing and maps limitation

`backend/quote_engine.py` is the only pricing implementation. It accounts for
distance, weight, dimensions/volume, quantity, item type, weather, traffic,
surge, transportation mode, and a mode-specific environmental adjustment.

Coordinate-based `/quote` requests use Haversine distance. The MVP has no maps
or geocoding provider, so address-only requests use the fixed
`CL_DEVELOPMENT_FALLBACK_MILES` value. Those responses are explicitly marked:

```json
{
  "estimated": true,
  "distance_source": "development_fallback"
}
```

Address text is never converted into fabricated coordinates and is never used
as a pricing signal.

## Tracking limitation

The WebSocket is an authenticated, server-to-client event channel. Browser
clients offer two WebSocket subprotocol values: `bearer` followed by the JWT:

```javascript
const socket = new WebSocket(
  "ws://127.0.0.1:8000/ws/track?order_id=123",
  ["bearer", accessToken],
);
```

The server derives the role from the authenticated user. Only the order creator,
assigned courier, or an administrator may join an order room. Client-sent
tracking messages are rejected.

Order mutations publish typed `order.created`, `order.claimed`,
`order.status_changed`, and `order.completed` events. The current connection
manager is intentionally process-local. It loses rooms on restart and does not
work across multiple server workers. Redis or another shared event layer must
replace the in-memory service before horizontal scaling.

Authentication rate limits are also process-local and match the MVP's required
single-worker deployment. Replace them with a shared limiter before scaling to
multiple instances.

## Database migration warning

`Base.metadata.create_all()` creates a new database but does not alter an
existing SQLite schema. The tracked database was development data and has been
removed from the repository. Before deploying this branch over any persistent
database:

1. Back up the database.
2. Use a fresh database for the MVP, or add and run a reviewed migration that
   adds the courier profile, assigned-courier, address, distance, requirements,
   and lifecycle fields.
3. Set the new `CL_` environment variables in the deployment platform.
4. Expect existing browser sessions to log in again if the signing-secret name
   or value changes.

Do not point this branch at a persistent database that must retain data until a
migration has been reviewed.
