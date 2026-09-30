# CourierLifts Render release

## Current deployment - September 30, 2026

The frontend candidate is now deployed and renders the actual React sign-in screen at https://courierlifts-web.onrender.com/. Render deployment `dep-dauic2gjo6nc738hrkv0` published application commit `416e64c0fd0f22d61b9d170656f3d8db3f0d8228` at 15:09:20 UTC. The user applied branch `launch-readiness`, build `npm ci && npm run check && npm run build`, publish directory `dist`, and auto-deploy off. All 13 release checks, TypeScript, and the Vite build passed on Render's Node 22.23.3. Browser verification confirmed the sign-in UI and hashed JavaScript asset `/assets/index-BoVdMf26.js`.

The backend remains on its existing main commit and free instance. It has not been promoted to the production launch candidate. Following a cold start, Render logs record successful startup and `GET /health` returning 200 at 15:10:57 UTC. A browser URL-policy block prevented a fresh response-body/readiness check in this pass. No authenticated customer/courier transaction was performed.

Still required: frontend routing/security/cache settings; production PostgreSQL with backup/restore verification; permanent proof storage; Google Routes credentials and a real-distance quote; production secrets/migrations; monitoring; and the full sender/courier delivery test. The below September 29 section is historical and its unbuilt-frontend finding has been resolved. Deployment stays manual during setup; final main promotion and checks-passing auto-deploy remain future release steps.

## Approved database provisioned - September 30, 2026

The user approved the approximately $13.30/month Render baseline. Created `courierlifts-db` at 15:20:13 UTC; Render subsequently reported `available`.

| Setting | Actual value |
| --- | --- |
| Render resource | `dpg-dauihb893c1s73ecnihg-a` |
| Dashboard | https://dashboard.render.com/d/dpg-dauihb893c1s73ecnihg-a |
| Plan | `0.1c-256mb` ($6/month compute) |
| PostgreSQL | 16 |
| Region | Oregon, matching CL-backend |
| Database / user | `courierlifts_db` / `courierlifts_db_user` |
| Storage | 1 GB ($0.30/month), autoscaling off |
| External access | Disabled: `ipAllowList: []` |

Database baseline is approximately $6.30/month. The approved $7/month, 512 MB backend upgrade has not been applied: CL-backend still uses the Free plan. The connector cannot update an existing service's compute plan; use the existing service's Dashboard Settings / Instance Type (or Compute Plan), selecting `0.5c-512mb` / the $7 option.

The database is provisioned but **not connected to the application**. No connection URL was retrieved or installed, no application migration ran, and no old data was moved or deleted. A read-only connector query was blocked because the database correctly denies all external connections; retain that restriction and verify connectivity from the same-region backend. Resource status `available` is not an application connectivity test.

The user subsequently confirmed the existing SQLite records are tests and may be left behind; see the test-data cutover decision below. Complete Google Routes, private proof storage, and production secrets before enabling production mode. The backend's launch candidate must be deployed with migrations and the internal database URL. Backup retention and a restore drill remain unverified.

`render.yaml` now matches the provisioned database's immutable database/user names and current compute-plan IDs. Import/link the existing database and CL-backend when applying it; do not create duplicates. Syntax and resource mapping were checked locally; authenticated Render CLI validation was unavailable.

## Test-data cutover decision and login secret - September 30, 2026

At 10:43 AM America/Chicago, the user confirmed the existing SQLite accounts/orders are tests. Proceed with a fresh application database; no old test-record transfer is required. The reported legacy variable begins `DATABASE` and its value begins `sqlite`; the current application reads `CL_DATABASE_URL`, so do not mistake the legacy variable for an active production database connection.

A cryptographically random 64-character `CL_SECRET_KEY` was saved directly to the canonical backend's Render environment, preserving all other variables. The value is not included in this repository. Existing test login tokens are invalidated when the new configuration runs. Render automatically triggered deploy `dep-dauisjm7bikc73aq8m90` of the unchanged main application commit `7580a9ca95f0d0795b6362e0b638181359f1b3c5`.

The database remains available and private. The backend remains on Free; upgrade it through **Upgrade your instance** on the existing CL-backend dashboard, choosing the approved $7/month 512 MB plan. The connector does not expose existing compute-plan updates or retrieval of database connection credentials.

Next, stage the launch candidate with explicit migrations before adding the database's internal URL as `CL_DATABASE_URL`. Do not attach the empty PostgreSQL database to the old main configuration: it lacks the candidate's automatic psycopg 3 URL normalization, and development startup can create tables before Alembic, conflicting with a fresh migration. Use the Dashboard's Save only option when staging dependent environment changes. Keep production mode pending until Google Routes and persistent photo storage credentials are ready.

## Historical hosting audit — September 29, 2026

The connected workspace contains these existing services:

| Service | Repository | Deployed main commit | Observation |
| --- | --- | --- | --- |
| CL-backend | CourierLift/CL-backend | 7580a9ca95f0d0795b6362e0b638181359f1b3c5 | `/health` returns 200 with `env=development`; `/ready` returns 404 |
| courierlifts-web | CourierLift/courierlifts-web | 9dd21e61cefb539c750be6f350d25b3e3104abf1 | Build command is empty, publish directory is `.`, and the live HTML references `/src/main.tsx` |
| courierlifts-backend | CourierLift/courierlifts-backend | Older service | Suspended; the canonical app uses CL-backend |

The current frontend is `https://courierlifts-web.onrender.com`; the canonical backend is `https://cl-backend-ppv1.onrender.com`. Render reports the first two deployments as live, but the frontend serves unbuilt source. A login preflight from the Render frontend returned 400 with `Disallowed CORS origin`. No managed Render PostgreSQL instance was found. Existing backend database/storage credentials were not inspected.

The connector saved these non-secret settings, preserving other variables:

| Service | Key | Value |
| --- | --- | --- |
| CL-backend | CL_FRONTEND_ORIGIN | https://courierlifts-web.onrender.com |
| CL-backend | WEB_CONCURRENCY | 1 |
| courierlifts-web | NODE_VERSION | 22 |
| courierlifts-web | VITE_COURIER_LIFTS_API_URL | https://cl-backend-ppv1.onrender.com |

These environment saves automatically triggered Render API redeployments of the existing main commits. Backend deployment `dep-dau0fs5g1s2s73b0d3tg` and frontend deployment `dep-dau0ftnavr4c73f7cfvg` reached live. The new login preflight returns 200 and allows exactly `https://courierlifts-web.onrender.com`, resolving the observed CORS failure. The backend still reports development and lacks `/ready`; the frontend still serves unbuilt source. The launch candidates have not been deployed.

## Desired service settings

The frontend's `render.yaml` and this repository's `render.yaml` record the desired configuration. Import/link the existing services when applying the Blueprints and inspect the proposed changes before provisioning. The files alone do not change Render.

| Setting | Frontend | Backend |
| --- | --- | --- |
| Repository | CourierLift/courierlifts-web | CourierLift/CL-backend |
| Final release branch | main | main |
| Build command | `npm ci && npm run check && npm run build` | `pip install -r requirements.txt` |
| Publish directory | dist | — |
| Pre-deploy command | — | `alembic upgrade head` |
| Start command | — | `uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | — | /ready |
| Instances | Static CDN | 1 |
| Auto-deploy | checksPass | checksPass |

Add frontend `SKIP_INSTALL_DEPS=true` when its explicit `npm ci` build command is applied. Configure the `/*` rewrite to `/index.html` and the headers from the frontend Blueprint. Render static sites do not run Netlify Functions, so this deployment uses the direct backend origin and exact backend CORS. Never put Google, database, AWS, or signing credentials in `VITE_*` variables.

## Approved pilot infrastructure and cost

The approved setup uses a $7 always-on 512 MB web service (`0.5c-512mb`) and a $6 256 MB PostgreSQL 16 instance (`0.1c-256mb`) in Oregon with 1 GB storage and external database access disabled. Published monthly pricing rechecked September 30: $7 web compute + $6 database compute + $0.30 for 1 GB storage = approximately **$13.30/month**, before usage charges, taxes, Google Routes, and object storage. Static hosting uses Render's included quotas. The database is provisioned; the backend compute upgrade remains pending.

Paid Render PostgreSQL provides point-in-time recovery. The documented window is three days on Hobby and seven days on Pro or higher; confirm the actual workspace plan and retention, create an export, and complete a restore drill before accepting real delivery data. Do not substitute an expiring free database for the production backup requirement.

## Production secrets and configuration

Set real values in the backend's Render Environment page or through an authorized secret handoff. `sync: false` entries are not reapplied during Blueprint updates to existing resources, so verify their presence on CL-backend explicitly.

| Key | Required configuration |
| --- | --- |
| CL_APP_ENV | production |
| CL_DATABASE_URL | The new same-region database's internal connection URL; provider URLs are normalized to psycopg 3 |
| CL_SECRET_KEY | A new securely generated secret of at least 32 characters; rotation invalidates old login tokens |
| CL_GOOGLE_MAPS_API_KEY | Restricted backend key with Routes API enabled and billing active |
| CL_OBJECT_STORAGE_BACKEND | s3 |
| CL_S3_BUCKET | Persistent proof bucket |
| CL_S3_REGION | Bucket/provider region |
| AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY | Deployment credentials authorized for proof storage |
| CL_S3_ENDPOINT_URL | Set only if using a non-AWS S3-compatible provider |
| CL_FRONTEND_ORIGIN | The final HTTPS frontend origin |
| PYTHON_VERSION | 3.11.15 |
| WEB_CONCURRENCY | 1 |

Use a private proof bucket and a deployment identity scoped to the application's proof objects. Do not paste secret values into repository files or launch reports.

## Apply and verify

1. Confirm green release CI on both launch PRs. Reuse the provisioned PostgreSQL instance, provision permanent proof storage, and supply Google/S3 credentials.
2. Confirm database backups and restore capability. Preserve any existing application data before switching databases.
3. Apply the reviewed service settings and stage the candidate commits using production-style infrastructure. Record the exact commits used. Keep the launch gate open until the live checks pass.
4. Confirm Alembic reaches head before application startup, one worker runs, `/health` reports production, and `/ready` is 200.
5. Build and publish the React frontend from `dist`. Confirm the live HTML references hashed JavaScript assets and the browser receives the correct CORS headers.
6. Run a real-address quote and require `distance_source=google_routes`.
7. Complete sender/courier sessions on separate devices: create, claim, pick up, transit, persistent proof, completion, and fresh-session recovery.
8. Verify monitoring, alerts, backup restore, and application rollback. Complete the launch gate and promote the verified candidates to main.
9. Complete payments, courier payouts, refunds, support, and onboarding before a paid public launch.

The installed Render connector can read services/logs, save environment variables, and trigger deployments. Environment saves also trigger a redeployment, as observed in this audit. It does not expose updates to existing build/publish/start/health settings or Blueprint import. Applying those settings needs the Render Dashboard or separately authorized API/CLI access.

## Official references

- [Render pricing](https://render.com/pricing)
- [Static site deployment and dependency installation](https://render.com/docs/static-sites)
- [Blueprint specification](https://render.com/docs/blueprint-spec)
- [PostgreSQL connections and storage](https://render.com/docs/postgresql-creating-connecting)
- [PostgreSQL recovery and backups](https://render.com/docs/postgresql-backups)
- [SQLAlchemy psycopg dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#psycopg)
