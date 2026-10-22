# Integrated unpaid pilot: combined release approval

Prepared 22 October 2026. This is a review package, not deployment approval.
No hosted migration, credential change, deployment, storage resource, or payment
operation has been performed by this preparation. No database credential entry
was repeated. The release uses the two existing Courier Lifts services and the
existing internal PostgreSQL database. NavGlass is outside this work.

## Release identity and reconciliation

The backend candidate is PR [#8](https://github.com/CourierLift/CL-backend/pull/8),
prepared on `work/integrated-unpaid-pilot-20261021` from
`63b14e408e518e9252773f0665c6bc235cefe917`. Its base is `launch-readiness`
at `a320a65f9d8be8c9f0d2a3e790f98a7ed3ff668e`.

The frontend candidate is PR
[#20](https://github.com/CourierLift/courierlifts-web/pull/20), prepared from
`f1f0f851b8abe56df91c58eff734ad291df6d714`, with reviewed candidate commit
`4541cf1bec1bdf29f8f5885a8f57b1f2863ca3ff`. It descends from the published
orange/charcoal theme at `39bdc934e005359c2e114b67afb94e0545b9b34f`.
The candidate was 14 commits ahead and zero behind that theme before this
preparation. Frontend `main` has diverged and is not the release source.

Both PRs remain draft and unmerged. Pin the final full commit IDs reported with
this approval package; do not use a moving branch tip or silently substitute
`main`. The proposed code adds preservation/recovery tooling and test guards,
corrects local release templates, allows the two actual frontend origins, and
labels completed courier history amounts as sample/unpaid. CSS, SVG/logo assets,
HTML theme, payments, and NavGlass are unchanged. The published stylesheet is
`index-7hZk4HBD.css`, SHA256
`bdc6b1c80cac7ad2c3f06f5f24d66a1257ebd598b26c5ba347df3fe62c1e63e5`.

## Current hosted state and exact rollback anchors

| Resource | Existing identity | Current state / rollback anchor |
| --- | --- | --- |
| Backend | `srv-d2tbhgmuk2gs73cneer0`, Oregon | Live deploy `dep-db8ductckfvc73btjntg`, commit `7580a9ca95f0d0795b6362e0b638181359f1b3c5`; main/on-commit; SQLite; no disk |
| Frontend | `srv-d25d0k49c44c73d6msb0` | Live deploy `dep-dbcdihbrjlhs73bl4ne0`, commit `39bdc934e005359c2e114b67afb94e0545b9b34f`; launch-readiness; automatic deploy off |
| PostgreSQL | `dpg-dauihb893c1s73ecnihg-a`, `courierlifts_db`, Oregon | Version 16, 1 GB, internal connection verified; public schema has no app tables or migration history; external access blocked |

[Read-only observation record](evidence/hosted-preparation-20261022.json) records
the fresh live SQLite integrity check and zero rows in all five existing tables,
zero local photo files, and the live resource identities. PostgreSQL connectivity
and empty public schema use the user's accepted prior inspection. Its Recovery
page currently offers three-day PITR and logical exports, with no export visible.
No hosted recovery has been executed.

Both `https://courierlifts.com` and `https://courierlifts-web.onrender.com` returned
200. The existing `www` hostname returned 503; no DNS change is included. Current
backend `/health` says development; `/ready` and `/pilot-info` return 404. The new
frontend deliberately refuses to run the unpaid delivery flow against that old
backend.

## Tests that actually passed

All writable tests used disposable local databases, synthetic accounts and
photos. No test suite was pointed at the hosted database.

| Verification | Recorded result | Boundary |
| --- | --- | --- |
| Backend full suite on PostgreSQL 16.15 | 298 passed, zero skipped | Precedes five final recovery-guard cases; not a claim of a full 303-case run |
| Backend full suite on SQLite | 288 passed, 10 PostgreSQL-only skips | Same source boundary as the full PostgreSQL run |
| Final cutover/recovery suite | PostgreSQL: 55 passed; SQLite: 50 passed, 5 PostgreSQL-only skips | Includes newer-schema rejection, independent old-source file checks and ancestor `.env` rejection |
| Migration mechanics | Empty → head → base → head passed on both engines | Disposable databases only; production downgrade is prohibited |
| Native PostgreSQL recovery | Custom-format dump restored to a fresh database; all six application-table digests matched | Fresh process and fresh logins retained the delivered order, original $20.27 sample price, quote, retry key and proof hash |
| Exact old-code SQLite recovery | Actual old commit `7580a9c`, restored before process startup; customer/courier histories and database/photo hashes matched | Local maintenance-only checkpoint; hosted artifact retrieval/start route remains a gate |
| Private photo recovery tooling | 27 focused storage/unpaid/proof cases passed | Includes 18 new mocked versioned-S3 cases; no real S3 account was contacted |
| Frontend installation/check/build | Locked install, 23 checks and final production builds passed | Node 22.23.1; actual API origin included |
| Frontend release tests | Exact-commit React CI: 85/85 passed, 23/23 checks and production build passed | Run `39862399033` checked commit `4541cf1bec1bdf29f8f5885a8f57b1f2863ca3ff` on Node 22.23.3 |
| Real browser with real local APIs | 10/10 acceptance checks on SQLite; 10/10 on PostgreSQL | Chromium, not physical iPhone Safari |
| PostgreSQL application restart | Fresh customer and courier logins retained delivered order 2, original sample price and the identical 68-byte proof | Local process restart, not a Render restart |
| Final browser layout | 320, 390 and 1280 px passed; no overflow or provider requests | Published CSS/SVG/HTML remain unchanged |

The exact backend files, versions, commands and recovery hashes are recorded in
[backend validation evidence](evidence/backend-validation-20261022.json).
Frontend evidence and its source-materialization limit are recorded in
[FRONTEND_RELEASE_PREPARATION.md](https://github.com/CourierLift/courierlifts-web/blob/4541cf1bec1bdf29f8f5885a8f57b1f2863ca3ff/FRONTEND_RELEASE_PREPARATION.md).
The [final frontend CI run](https://github.com/CourierLift/courierlifts-web/actions/runs/39862399033)
passed after the history-label correction, and all original theme assets,
including the historical PNG, are retained in the Git tree.
Exact new-commit CI is reported separately with the final PR heads; an older
green workflow is not evidence for the new commits.

## What the one approval authorizes

Approval must cover all of the following as one coordinated release, after the
exact storage account and backup destinations below are resolved:

1. Temporarily close Courier Lifts API intake using maintenance mode, disable
   backend automatic deploys, capture and export a protected SQLite/photo/config
   checkpoint, and verify recovery before replacing its ephemeral instance.
2. From a separate reviewed candidate process, migrate the existing empty
   PostgreSQL public schema through **0001 → 0002 → 0003 → 0004 → 0005** and import
   the frozen source state with exact row/field/hash comparisons. Do not stamp
   an unknown schema or run destructive resets.
3. Reuse or create the specifically approved private S3 bucket and dedicated
   least-privilege identities; configure the compatible backend's database,
   signing, S3, unpaid, manual-invite, and exact-origin settings securely.
   Keep credentials out of frontend settings, source, chat, and logs.
4. Deploy the exact reviewed frontend/backend commits to their existing
   services, with the published theme, single backend instance, `/ready`,
   payments/payouts disabled, and no live routing-provider calls.
5. Run synthetic hosted customer/courier acceptance, restart persistence,
   coordinated logical-backup restore, private-photo authorization and recovery
   checks, then the real iPhone Safari checks. Open pilot invitations only after
   the gates pass. Preserve synthetic acceptance records as labelled evidence;
   do not silently delete production history.

This approval does not authorize a new Render web service/database/disk/worker,
a workspace or compute upgrade, a public PostgreSQL endpoint, a payment provider,
NavGlass changes, or destructive database/object cleanup. A Render PITR restore
creates a new database and is therefore **not** included unless separately
named and priced in the same approval. The proposed hosted logical export can
be restored in an isolated local PostgreSQL16 instance without a new paid DB.

## Required non-secret storage decisions

No AWS account has been inspected or selected. Render/GitHub sign-in does not
identify an AWS payer. The approval record must name the authorized AWS account,
approved existing bucket or new bucket
`courierlifts-pilot-proofs-<account-id>-us-west-2`, runtime/recovery identities,
and independent encrypted backup destination. Do not paste access keys or a
database URL into chat. Use the existing authenticated Render session and a
secure account connection/configuration route. No repeated database credential
entry is needed.

The [photo-storage plan](docs/PILOT_PHOTO_STORAGE.md) contains the exact desired
bucket controls, IAM policy templates, version-pinned checkpoint tooling,
retention, operational ownership proposal, and account-side validation gates.
Those templates intentionally contain unresolved account/resource placeholders.
They cannot be applied to an inferred account.

## Cost approval

| Item | Existing / proposed charge |
| --- | --- |
| Existing backend, 0.5 CPU / 512 MB | Published rate $7/month; no new service or upgrade proposed |
| Existing PostgreSQL, 0.1 CPU / 256 MB, 1 GB | Published rate $6/month with 1 GB included; no new DB or upgrade proposed |
| Existing static frontend | No fixed compute charge; normal workspace bandwidth/build usage still applies |
| Proposed private S3 storage | Usage based; about $0.87/month for the documented 500-delivery / 3 MiB example; about $5.08 for the 10 MiB backend/API upper-bound example |
| Proposed S3 operating allowance | **$10/month incremental**, unapproved; daily forecast review, not an AWS hard billing cap |
| Independent encrypted backup destination | Must use an owner-approved existing destination or separately price/approve it before use |

The existing Courier Lifts backend/database baseline is approximately $13/month,
before workspace charges, excess transfer/build use, taxes, and other existing
services. Storage estimates include requests, transfer with no assumed free
allowance, accumulated history, and one recovery copy; extra versions/copies,
reads, retries, and retention increase the bill. A budget alert is not a hard
cap and must never delete saved history. No new paid resource has been created.

Sources checked 22 October 2026: [Render pricing](https://render.com/pricing),
[Render compute plans](https://render.com/docs/compute-plans),
[AWS S3 pricing](https://aws.amazon.com/s3/pricing/), and the selected official
tariffs/checksums in
[pricing evidence](ops/pilot-storage/pricing-2026-10-22.json).

## Execution order and gates

**Storage and recoverability first.** Resolve account/bucket/backup destination,
validate permissions and public-access denial, enable versioning/encryption,
and allow AWS's initial versioning propagation period before object writes.
Do not close the public API while waiting for an unresolved account decision.

**Freeze before any restart or configuration-driven deployment.** Disable
backend automatic deploys without deploying, enable existing paid-service
maintenance mode, verify the public API returns 503, and establish that no private
client, provisioning job, or in-flight request can write. Maintenance mode keeps
the instance and shell running; a frontend gate alone cannot freeze direct API
calls. Do not suspend the service or attach a disk to accomplish the freeze.

**Preserve the source.** In the current instance, run the reviewed snapshot tool
against `/opt/render/project/src/courier_lifts.db` and its object-storage tree.
SQLite's backup API includes committed WAL state. Copy the complete protected
bundle off the ephemeral instance, hash-verify it there, restore it to new local
paths, and record the exact pre-change settings and deploy IDs without exposing
secret values. If this transfer or verification fails, stop before any deploy.
Even today's zero-row inventory must be rechecked under the freeze.

**Migrate independently of the old app.** Use the reviewed candidate checkout
and a separate `CL_CUTOVER_DATABASE_URL` process environment. Check the exact
internal host/database and empty public schema. Execute the pinned migration,
transactional typed import, sequence correction, and complete digest comparison.
Upload any referenced legacy photos using conditional writes and verify pinned
versions. No old process's database URL is changed during these operations.

**Stage settings without running old code on PostgreSQL.** Confirm that Render
offers Save only. With automatic deploy off, save new settings without deploying,
then deploy the exact compatible candidate SHA. Never use Save and deploy with
the existing old build, and never allow a stale main auto-deploy. If the platform
cannot keep configuration and compatible code paired, stop. Candidate pre-deploy
is pinned to `0005_quotes_idempotency`; runtime startup does not create schema in
production. Only the candidate uses the new `CL_DATABASE_URL`.

**Verify before intake.** Keep maintenance while checking candidate `/ready`
internally, migration revision, source/import digests, production/unpaid/manual
settings, photo access, and frontend artifact. The new frontend may be deployed
first while its PilotGate blocks the old API. Enable public access only for the
controlled synthetic two-role test, with self-registration closed and no invited
customers yet. Verify both actual origins and no wildcard CORS. If any gate
fails, re-enable maintenance and preserve all evidence/data.

Detailed exact commands and tested guard conditions are in
[POSTGRES_CUTOVER.md](docs/POSTGRES_CUTOVER.md).

## Exact rollback and its limits

Render's documented rollback reuses the selected artifact and its service-specific
environment/start/health settings for that rollback. It does not restore database
data or disks and does not overwrite current settings for the next standard
deploy. Environment groups and static redirects/headers have separate behavior.
Keep automatic deployment off and record the actual original configuration.

**Before replacing the old instance:** an aborted cutover can leave the original
SQLite process and files intact. Preserve the exported source bundle and restore
the original configuration before any later restart. Never stage PostgreSQL
settings and then restart that old artifact.

**After replacement but before any post-cutover application write:** keep intake
closed, preserve PostgreSQL/S3 state, and verify that it still matches the frozen
source. The prepared `scripts/start_sqlite_rollback.py` verifies a clean checkout
of exact commit `7580a9ca95f0d0795b6362e0b638181359f1b3c5`, checks the protected
original environment and legacy-only source bundle, independently compares old
source bytes to the pinned Git tree, rejects ancestor `.env` injection, restores the SQLite database
and photo tree to new private paths, clears candidate PostgreSQL/S3 settings,
and only then starts the old application with absolute SQLite/local-photo paths.
Fresh customer/courier history and every table/photo hash passed in the local
rehearsal. Restore the exact published frontend artifact with the old backend.

**The hosted bootstrap remains a release gate.** The exact old checkout,
encrypted backup and protected original environment must be securely retrieved
into the same instance/start invocation before the wrapper runs. That route
requires the approved storage account and configuration; it has not been tested
on Render. The old artifact does not contain the new wrapper. A normal artifact
rollback followed by a shell copy is insufficient: an already-running SQLite
connection can retain its old file, and a restart can discard copied ephemeral
files. If secure prestart recovery is unavailable, remain in maintenance and fix
the compatible backend forward.

**This is a closed recovery checkpoint.** Keep maintenance enabled while running
the restored old code. Each wrapper invocation restores the original frozen
bundle; rerunning it after new SQLite writes would rewind those writes. Reopening
an ongoing SQLite pilot therefore requires a separately approved durable
persistence/reconciliation strategy. No old process ever receives PostgreSQL.

**After any new PostgreSQL application write, including hosted acceptance:** an
old SQLite snapshot cannot represent the new history. Preserve a fresh PG dump
and S3 version checkpoint, retain the compatible backend or maintenance gate,
and fix forward. A return to old code would require a separately reviewed
reverse-data reconciliation; no such reverse migration is claimed. Never run
production Alembic downgrade, overwrite newer data with a pre-release backup,
or purge proof versions to make rollback appear successful.

Provider references:
[maintenance mode](https://render.com/docs/maintenance-mode),
[deploy lifecycle](https://render.com/docs/deploys),
[environment Save only](https://render.com/docs/configure-environment-variables),
[rollback semantics](https://render.com/docs/rollbacks), and
[database recovery](https://render.com/docs/postgresql-backups).

## Hosted acceptance to run after approval

Use distinct customer, assigned courier, and unrelated account sessions, with
synthetic addresses/photos and explicitly measured test vehicle capacity. Verify
manual-invite provisioning and no public registration. Confirm the interface
says unpaid and all amounts are sample quotes with $0 due/no payouts.

The customer obtains a server-bound quote and creates exactly one order even
after retry/lost-response recovery. The courier sees an eligible job, claims it,
advances through pickup and in-transit, uploads a proof, and completes delivery.
Both roles must see the same saved delivered order and original quote after
logout/fresh login, refresh, and an approved backend restart. Verify the photo's
original size/hash, authorized access, denied anonymous/unrelated access, and
denied anonymous direct S3 access. Check both canonical and onrender origins.

Create a coordinated database/object checkpoint and logical export, recover into
an isolated disposable PostgreSQL16 environment with exact pinned photos, and
compare IDs, every mapped field, rewards, history, quote and proof hashes. A
provider recovery control is not a successful recovery test. Retain the evidence
and protected source/new-data backups.

Real iPhone Safari remains a distinct gate: camera/file selection, supported
image type/size, orientation/display, proof retrieval, refresh/relogin history,
small-screen navigation, optional external directions, and no unexpected GPS or
payment activity. Chromium mobile-width tests cannot establish that result.
