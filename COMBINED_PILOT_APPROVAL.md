# Courier Lifts — coordinated unpaid-pilot approval package

Updated during the October 22–23 UTC continuation. **Prepared for review; no
production migration, credentials, paid resource or deployment is authorized.**
The already-successful database inspection and phone credential entry were not
repeated. Preserve the published orange/charcoal theme, disabled payments and
closed invitations. NavGlass remains untouched.

## Current review record

The complete authoritative execution plan is
[COORDINATED_PILOT_RELEASE.md](docs/COORDINATED_PILOT_RELEASE.md).
It supersedes the earlier manual-backup proposal and incomplete Render startup
route. [PILOT_COSTS_AND_ACCOUNTS.md](docs/PILOT_COSTS_AND_ACCOUNTS.md) contains the
observed hosting charges and reproducible storage/automation cost calculations.
The final exact backend SHA and exact-SHA CI run are recorded in PR #8 and the
accompanying status report; a moving branch tip is never deployment approval.

| Candidate | Preserved lineage / release identity |
| --- | --- |
| [Backend PR #8](https://github.com/CourierLift/CL-backend/pull/8) | This continuation descends from `5081cdd05e50d1079cdb3e37f2c3ef4f783dd6f2` on `work/integrated-unpaid-pilot-20261021`. Pin the reviewed continuation SHA from the final PR record. |
| [Frontend PR #20](https://github.com/CourierLift/courierlifts-web/pull/20) | Prepared photo/history update `4cbb574b5cb1ebad93abf95c74f6d1788a84c70a`, descending from `4541cf1bec1bdf29f8f5885a8f57b1f2863ca3ff`. Published theme ancestry and historical PNG are preserved. |

Both PRs remain draft and unmerged. The production backend is still
`7580a9ca95f0d0795b6362e0b638181359f1b3c5` using SQLite, deployment
`dep-db8ductckfvc73btjntg`, on existing service `srv-d2tbhgmuk2gs73cneer0`.
The production frontend remains the theme commit
`39bdc934e005359c2e114b67afb94e0545b9b34f`, deployment
`dep-dbcdihbrjlhs73bl4ne0`, service `srv-d25d0k49c44c73d6msb0`.
The existing PostgreSQL instance is `dpg-dauihb893c1s73ecnihg-a`,
`courierlifts_db`, Oregon, in workspace `tea-d20l5i7gi27c73coe8a0`.

## What is prepared and verified locally

- Full compatible `0001` through `0005_quotes_idempotency` migration and guarded
  all-field SQLite import, preserving identities, password hashes, original
  prices, status/timestamps, quote/retry data, rewards, proof relationships,
  sequences and all original local photo files.
- Private authenticated proof storage, immutable version/checksum recovery,
  encrypted incremental photo archives and PostgreSQL backups from one exported
  snapshot. The backup suite passed **60 tests**, including **5 native PG16/age**
  cases; actual bucket/owner/port guards and latest-only freshness are covered.
- **33 startup/recovery boundary tests** passed, including stale-checkpoint
  refusal after a new PG write, private-input cleanup, secret exclusion and
  refusal to overwrite an existing recovery bucket.
- **2 actual legacy-runtime tests** passed with old source `7580a9c`, separately
  built Python **3.13.4**, all **54 observed package versions**, real encryption,
  restored SQLite/local photos, both users' fresh logins/history in two processes,
  and refusal after a later PG write. Old code never connected to PG. Matching
  interpreter/package versions does not claim an identical Render OS image.
- Frontend **100 React tests, 26 checks and production build** passed. Real local
  PG/Chromium customer-to-courier flow and fresh-login/API-restart checks passed,
  with the saved upright JPEG, original **20.27** sample price and both histories.
- Photo-format/orientation/error tests cover JPEG/PNG/WebP, all eight EXIF
  orientations, corrupt/truncated/animated files, HEIC/HEIF guidance, size/pixel
  limits, metadata stripping, retry integrity and unchanged historical proofs.
- The full continuation SQLite run passed **445 tests, 19 skips**; skipped PG and
  native-runtime cases have separate opt-in runs. The full
  PostgreSQL16 continuation run passed **464 tests, zero skips**, including the
  native encryption, fresh-target restore and actual legacy-runtime cases. Exact
  GitHub CI results remain separately recorded for the published commit.
- Both proposed Render YAML files passed local validation against the captured
  official Render schema. No Blueprint was applied.

Evidence: [automated backups](evidence/automated-backup-validation-20261022.json),
[actual legacy runtime](evidence/legacy-runtime-validation-20261022.json),
[SQLite full run](evidence/continuation-full-sqlite-20261023.txt),
[PostgreSQL full run](evidence/continuation-full-postgres-20261023.txt),
[iPhone handling](docs/IPHONE_PHOTO_HANDLING.md), and frontend
[photo preparation](https://github.com/CourierLift/courierlifts-web/blob/4cbb574b5cb1ebad93abf95c74f6d1788a84c70a/IPHONE_PHOTO_PREPARATION.md).

## Storage, automation and cost proposal

Keep existing Render hosting. Use private, versioned AWS S3 Standard in Oregon:
separate proof and encrypted-backup buckets, plus a fresh empty recovery bucket
for the approved restore drill. The AWS account/payer is not yet identified;
final bucket names and IAM identities must be bound to the actual account first.

Proposed new Render services are `courierlifts-pilot-backup` (daily 07:00 UTC)
and `courierlifts-pilot-backup-watch` (hourly at :15, retention at 08:15 UTC).
Both use the reviewed Docker artifact in the existing workspace/Oregon. Keep
7 daily, 4 weekly and 3 monthly checkpoint representatives, at most 14 in their
union; do not automatically delete encrypted photos or linked proof versions.
A missing/older-than-26-hour checkpoint fails the monitor. Scheduled services get
only the public encryption recipient; the private recovery key stays offline.

**Proposed recovery owner: Will (Courier Lifts owner), pending acceptance.**
Existing Render email/failure settings were observed read-only. Actual cron
inheritance, recipient and delivered failure/stale alerts remain activation gates.
One-off recovery jobs must be observed through completion; they do not have the
same email-failure notification behavior as cron jobs.

| Monthly example | Courier Lifts total | Extra over current $13.30 |
| --- | ---: | ---: |
| First month, 500 deliveries, 1 MB saved photos, 1,500 views | **$15.86** | **$2.56** |
| Month 12 at 500 deliveries/month, 1 MB photos, 1,500 views | **$16.66** | **$3.36** |
| Month 12, 4 MB photos, 2,000 views | **$18.80** | **$5.50** |

These include two cron minima ($2 combined), storage, encrypted copies, requests,
verification/readback, retained 100 MiB database dumps and conservative paid S3
transfer. They are scenarios, not billing caps. Other unchanged NavGlass hosting
adds about **$7.25/month** to the workspace total. Taxes, paid build overages,
extra versions/retries and any new paid recovery database are excluded. The
**$10 extra is a planning target, not spending approval**. Full assumptions,
optional restore-drill charges and primary sources are in the cost document.

## One approval after the remaining account choice

The approval must bind the exact reviewed frontend/backend SHAs, existing Render
resource IDs, actual AWS account and final bucket/identity names, accepted costs,
maintenance window and Will's recovery-owner/alert-destination acceptance.
It then authorizes together:

1. Approved private buckets, least-privilege identities, read-only backup DB
   login, two cron services and an empty logical recovery DB on the existing
   instance if capacity/permissions allow. No unpriced new managed DB/upgrade.
2. Freeze/drain writers, preserve the original SQLite/photos/environment and
   signing secret in a verified off-host encrypted artifact **before any restart**.
3. Migrate/import from a separate compatible process, verify every row/photo,
   and configure/deploy the exact compatible backend together with Save-only
   controls. Never configure the old backend with PostgreSQL.
4. Explicit production signing-key rotation after preserving the old secret;
   securely configure existing internal PG and new scoped storage/backup values.
   No repeated phone entry of the already-working database connection.
5. Deploy the matching frontend and run controlled hosted delivery, photo,
   saved-history, restart, scheduled-backup, delivered-alert and restore checks.
   Keep invitations closed until these and physical iPhone checks pass.

Before PG receives new writes, the packaged startup wrapper may restore the
exact frozen legacy checkpoint and only then start old SQLite code in maintenance.
After **any new PG write**, preserve a fresh PG/photo checkpoint and use a
compatible forward fix or fresh-target PG recovery. Never rewind to old SQLite.
A source write after the checkpoint blocks publishing its recovery receipt.
Source-unavailable disaster recovery needs a separately accepted recovery point;
daily backups are not a promise of zero lost writes in every disaster.

## Remaining blockers — no operational claims

- Bind the actual AWS account/payer and available bucket names through necessary
  secure account selection/read-only access. No AWS credentials were created.
- Finish publication and CI for the exact backend continuation, and final
  cross-document verification. An old green PR run is not the new run.
- Obtain the coordinated resource/configuration/migration/deployment approval.
- After approval, prove real hosted storage, a successful scheduled encrypted
  backup, delivered failure/stale alerts, fresh-target restore and startup.
- Complete physical iPhone Safari picker/camera, HEIC guidance, orientation,
  size/pixel-limit errors, interruption/retry and saved history/photo checks.

The automation is **prepared, not operational**. No production migration,
configuration, deployment, paid-resource creation or invitations were performed.
