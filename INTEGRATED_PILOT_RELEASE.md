# Integrated invite-only unpaid Midland pilot — review candidate

Prepared October 21, 2026 America/Chicago (verification timestamps in evidence are UTC).
Production replacement is NOT authorized. No hosted resources, credentials, access settings,
production database, DNS or NavGlass resources were changed. No torn-down staging service
was recreated. This candidate is not a commercial-launch release.

## Source reconciliation

- Backend base: `8bfed555d62ad63cfe78eb1ee1219076772517f9` (PR 7). It already includes
  launch hardening (PR 5), quote binding/idempotency (PR 6), and manual-invite registration.
- Frontend production base: `39bdc934e005359c2e114b67afb94e0545b9b34f` (October 21 theme).
  Retain its public entry navigation/focus correction, SVGs, CSS, HTML and orange/charcoal
  presentation. Integrate the tested recovery/permission logic from `204abbc` / PR 19,
  rather than reverting the current theme or merging the old branches blindly.
- The public backend still runs `7580a9ca95f0d0795b6362e0b638181359f1b3c5`, deployment
  `dep-db8ductckfvc73btjntg`; `/health` reports development and `/ready` is 404.
- Frontend at courierlifts.com is deployed as `dep-dbcdihbrjlhs73bl4ne0` at `39bdc934`.
  Its current JS is `index-CRHZm-ZX.js`; CSS is `index-7hZk4HBD.css`.
  The integrated candidate produces the SAME CSS asset; the application JS necessarily changes.
- Existing candidate PRs stay open/unmerged; frontend launch-readiness/main divergence
  remains an explicit promotion decision. These new changes are a coordinated review branch.

## Behavior and API contract

Set `CL_UNPAID_PILOT=true`, `CL_REGISTRATION_MODE=manual-invite` for this release.
The backend rejects public registration configuration in unpaid mode. The frontend checks
`GET /pilot-info` before exposing booking or courier actions. Missing/older/incompatible
backend capability blocks the workspace with a retryable connection message.

No Stripe, card entry, payment route, charge, payout, or payment-complete state is added.
UI always labels the pilot unpaid, amount due $0, and money figures as SAMPLE estimates.
The stored quote total is still bound to creation and copied unchanged, so the price contract
is tested without collecting money. Address quotes in unpaid mode use fixed server-side
sample mileage (`distance_source=unpaid_pilot_sample`, `estimated=true`), never measured
route distance or promised ETA; they make ZERO Google/provider calls even in production.
Coordinates and addresses are displayed only under the existing order permissions.
Directions are an explicit external link (addresses are shared only when the user opens it).
There is no automatic GPS collection or live courier location sharing.

Customer/merchant: quote -> create with quote_id + stable Idempotency-Key -> history/detail.
Courier: authorized available jobs -> claim -> picked_up -> in_transit -> upload -> delivered.
A 201/200 with a numeric order ID confirms creation; timeouts/body failures are uncertain.
A signed-in pending request reuses the original key/body. Deliberate logout clears addresses
and uses authenticated READ-ONLY history recovery; legacy attempts never auto-resubmit.
Unresolved recovery information is retained after quote expiry. Role navigation never grants roles.

`GET /orders/{id}/proof` authenticates before consulting storage and allows only the creator,
assigned courier or existing administrator policy. It serves bytes, not a public/signed URL,
checks stored size/SHA256, and sends private/no-store + nosniff headers. Missing/corrupt
objects return 503. Browser object URLs are revoked on unmount/logout. Existing upload and
proof-gated completion are reused. No schema change is needed for this endpoint.

The pilot UI offers car/Volkswagen, SUV or truck, without claiming real capacities.
Unpaid quote requests reject other vehicle modes. Operator CLI requires all five measured
courier capacity limits and a supported mode. Existing profiles still require manual physical
verification before inviting real participants; do not infer capacities from generic defaults.
No store partnership is claimed. First operation is Midland after Will's move, not Austin.

## Verified locally

- Python 3.11.17, PostgreSQL 16.15: **220 passed, 0 skipped**; concurrent retries, roles,
  quote binding, proof integrity and invite gate. See evidence/postgres16-release.txt.
- Local PostgreSQL upgrade 0004 -> 0005, downgrade/upgrade on a SEPARATE disposable database.
- Local pg_dump/pg_restore into another database: exactly one delivered order, quote and
  proof row; fresh customer/courier login; same-key replay returns the original order;
  original sample price and photo SHA256 unchanged. See evidence/migration-restore.txt.
- Persistent LOCAL photo directory survives new storage instances/database restore;
  S3 adapter read contract tested with a fake private client. This is NOT a hosted S3 test.
- SQLite: **215 passed, 5 PostgreSQL/persistence skips**. Frontend Node 22.23.1: locked installation, **23 checks**, production build and **84 release tests** pass.
- Chromium against real local frontend/backend with fictional accounts, synthetic locations
  and no provider calls: sign-in/quote/booking, double clicks, pre-forward dropped POST (zero
  orders), committed/lost response (one order), read-only post-logout recovery, real expired
  JWT handling, role separation, pickup/transit/photo/completion, same-photo retry, customer
  proof image rendering and unauthorized proof denial, fresh-login history for BOTH roles.
- Rendered 320/390/1280px, keyboard navigation, focus, logo, no horizontal overflow and
  orange control contrast. These checks do not substitute for physical iPhone/Android.

The local executor requires PostgreSQL on a temporary in-memory filesystem because its
workspace filesystem produced relation EOF errors under repeated DDL. A disposable UID
compatibility shim was used only to run PostgreSQL in the executor's single-UID namespace.
Neither shim nor local server packaging belongs to the application/deployment. Final successful
results are kept separately from unsuccessful harness/environment attempts.

## Still blocked / unverified on hosting

Only public HTML/assets and backend health/readiness were read. No fictional delivery was
written to production. Application connection to existing `courierlifts-db`, installed migration
revision, actual backup retention/restore and private proof storage credentials/durability are
NOT established. Render inventory shows the existing PostgreSQL 16 database available;
that is not an integration/restore test. No new S3 bucket, key, paid provider, disk or server
may be created without approval. New credential/access configuration also requires approval.
If no existing durable private object storage is available, stop before hosted pilot release.
Physical iPhone Safari/Android Chrome camera, photo picking, file formats, logout and
fresh-session recovery remain required. Monitoring/rollback drills on hosting are not executed.

## Coordinated release — stop for Will's approval

1. Confirm exact reviewed frontend/backend commit IDs and green CI. Retain current hosted
   deployment IDs and asset hashes (frontend evidence/hosted-rollback-manifest.json).
2. Read-only inspection from the authorized existing backend host must identify the active
   database and Alembic revision. Preserve `courierlifts-db` `dpg-dauihb893c1s73ecnihg-a`
   and all data. Verify backup retention/export and a restored-copy drill. If creating the
   restore copy incurs cost, obtain approval first. Never run pytest on this database.
3. Verify private durable proof storage already exists and the deployment can PUT and GET
   only its objects. Verify photo retrieval/checksum across restart/redeploy and restore.
   Ask before any new storage, credentials, spending or permission changes.
4. Approve a maintenance/release window and explicit existing-service settings: Python 3.11,
   one worker, production environment, manual-invite, unpaid mode, exact allowed HTTPS
   frontend origin, correct internal database URL and existing S3 configuration. No Google
   key is required or used in unpaid mode. Do not apply stale staging Blueprints.
5. Frontend first: manually publish the reviewed candidate to the EXISTING frontend service
   (auto-deploy stays off). PilotGate blocks transactional UI against old backend. Verify
   branded landing, unpaid labels and blocked workspace before touching backend.
6. Back up first, then inspect and run ONLY the reviewed additive migration to
   `0005_quotes_idempotency` on the existing intended database, if required. It adds quotes
   and nullable order quote/key/hash columns; legacy rows remain unchanged. No DROP,
   reset, downgrade, table recreation or create_all in production. Unexpected revision or
   data stops the release for review. Preserve rows/counts and proof metadata before/after.
7. Manually deploy the exact reviewed backend candidate on EXISTING CL-backend only.
   `/ready` must be 200 with required migration/columns. Verify pilot-info unpaid/invite-only,
   provider calls absent, correct origin, and the full APPROVED fictional two-account hosted
   workflow. Provision real accounts only after measured capacities and secure invitation
   handoff are approved; no hardcoded fixture credentials go to hosting.
8. Retain sanitized evidence, then do physical-device checks and Will's unpaid pilot acceptance.
   Do not enable paid service, payments, background tracking or commercial-launch messaging.

## Exact rollback checkpoints and limits

Frontend current theme: `39bdc934e005359c2e114b67afb94e0545b9b34f`, deployment
`dep-dbcdihbrjlhs73bl4ne0`. Immutable live asset hashes recorded in frontend evidence.
Backend current revision: `7580a9ca95f0d0795b6362e0b638181359f1b3c5`, deployment
`dep-db8ductckfvc73btjntg`. Record availability of these deployment artifacts in Dashboard
before approving replacement; no production rollback was executed in this work.

If backend candidate fails, retain the integrated frontend gate while rolling backend code
back: it blocks booking when old backend lacks pilot-info. Do NOT downgrade 0005 or delete
quote/retry/proof records. Old backend compatibility with the actual production database and
runtime must be verified before its use; if unsafe, hold the pilot closed instead of forcing it.
Restoring frontend 39bdc934 also restores its older transaction behavior, so this is only a
visual/site rollback while participation is halted, not an approved active-pilot configuration.
Rollback never means restoring a database backup over new delivery data without a separate
reconciliation decision. Preserve new records and object versions.

## Separate location-sharing proposal — NOT enabled

Smallest future design: courier explicitly opts in for ONE assigned active order; foreground
browser sends timestamped fixes to an authenticated order-scoped endpoint, throttled ~15s.
Server rechecks courier assignment/status on every update. Only that order's creator and
assigned courier can read latest fix. Show last-updated age; mark stale at 60s and offline
when disconnected. Stop watcher and reject updates on completion, cancellation, opt-out
or reassignment. Short retention for the last fix only; no location history. Browser sleep
and background behavior are not guaranteed. Tests use synthetic coordinates.

No maps SDK or routing provider is required for this design; provider API cost can be $0.
Hosting/database traffic is not proven free: at 15s a single active foreground courier sends
240 updates/hour, so N simultaneous couriers send 240*N updates/hour plus reads. Benchmark
on existing infrastructure and confirm hosting/bandwidth limits before giving a dollar quote
or enabling. New schema/endpoints, privacy approval and load tests are a separate proposal.
