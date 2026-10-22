# Unpaid pilot: PostgreSQL cutover and exact recovery

Status: prepared locally; no hosted migration, credential change, resource creation,
merge, or deployment is authorized by this document. The combined release approval
must name the reviewed backend/frontend commits, existing services/database,
storage choice and costs, and maintenance window. Keep payments disabled and
NavGlass untouched. Preserve the published frontend theme.

## Reconciled source and destination

Backend PR #8 is based on `launch-readiness` (`a320a65f9d8be8c9f0d2a3e790f98a7ed3ff668e`),
with prior reviewed head `63b14e408e518e9252773f0665c6bc235cefe917` on
`work/integrated-unpaid-pilot-20261021`. The current preparation extends that
candidate; the final reviewed SHA must include these preparation changes.
The current deployed `main` is `7580a9ca95f0d0795b6362e0b638181359f1b3c5`.
Those are different applications and database contracts.

Current-session read-only evidence identifies the old backend's active database
as `/opt/render/project/src/courier_lifts.db` (81,920 bytes), with five application
tables, no Alembic history, no WAL bytes, zero rows in every application table,
and a successful quick check. Local `object_storage` contains zero files. These
counts are evidence at inspection time, **not permission to discard data**.
Take and verify a final frozen backup even if the release-time counts are zero.

The existing internal PostgreSQL database is reachable and its `public` schema
has no application tables or Alembic history. It therefore needs the **full
`0001` through `0005_quotes_idempotency` chain**, not an assumed `0004` baseline.
Do not stamp a version, use `create_all` to bypass migrations, or configure the
old backend to use PostgreSQL. Do not repeat credential entry for this preparation.

`render.yaml` is an existing-service settings review template, not a provisioning
instruction. It uses manual deployment, the compatible pilot branch, a pinned
`0005` migration head, unpaid/manual-invite mode, explicit existing credentials,
and no `databases` or implicit secret-generation block. Deployment must use an
exact reviewed commit, not a moving branch tip. The separate storage plan is
in [PILOT_PHOTO_STORAGE.md](PILOT_PHOTO_STORAGE.md).

## What the cutover tool guarantees

`scripts/postgres_cutover.py` is independent of application imports. It never
changes a backend environment setting and never uses `CL_DATABASE_URL` as its
target. Hosted mutation commands must run only after the combined approval.

| Operation | Guard and preservation behavior |
| --- | --- |
| `snapshot` | Requires the CLI acknowledgement that writers have stopped; creates a new private bundle, uses SQLite's backup API so committed WAL data is included, runs integrity/foreign-key checks, and rejects unknown tables/columns/views/triggers. |
| Source schema | Accepts the reviewed old five-table `0004` shape without migration history (or correct `0004` history), and the six-table `0005` shape with no history or correct history. No schema guessing, source migration, or source stamping. |
| Photo bundle | Copies the complete local photo tree, including unreferenced files. Rejects symlinks and unsafe keys. Every referenced proof must match `storage_key`, `size_bytes`, and `sha256`; every copied file is checked again. |
| Source consistency | Rechecks source row digests and the photo inventory after copying. External maintenance must remain enabled afterward; the tool cannot prevent subsequent writes. |
| Target identity | Requires the separate `CL_CUTOVER_DATABASE_URL`, explicit expected host/database, psycopg, and only reviewed URL options. Rejects libpq query overrides such as `host` or `service` and inherited `PG*` environment overrides such as `PGHOSTADDR`. Does not print credentials or failed SQL parameters. |
| `migrate` | Refuses existing public tables, views, materialized views, enums or sequences. Runs the candidate's full Alembic chain to exactly `0005`; it does not reset an existing schema. |
| `import` | Requires the reviewed target table/column set and `0005` history, locks all application tables, rejects any existing application rows, and imports in one transaction. Preserves IDs, relationships, password hashes, statuses, timestamps, original prices, JSON snapshots, quote/idempotency records, proof metadata and rewards. |
| Import verification | Canonical per-table hashes and counts are checked before commit. ID sequences restart after the preserved maximum using transactional `ALTER SEQUENCE`, so a failed import rolls back sequence changes as well as rows. A repeat import into populated tables stops. |
| `verify-target` | Uses a repeatable-read, read-only transaction and checks every canonical row against the frozen bundle. A mismatch stops rather than overwriting new data. |
| `restore-sqlite` | Restores only to two new, nonexisting paths and verifies the exact backup file hash, table digests and all photo bytes. Never overwrites a live database or photo tree. |

Canonical comparison treats SQLite's application timestamps as UTC (the reviewed
application writes UTC), represents booleans consistently, and compares JSON as
data rather than whitespace. The original SQLite backup is also retained as a
byte-exact recovery artifact. No passwords are rehashed. Legacy orders receive
NULL for the three new quote/idempotency columns; their existing fields do not
change.

The bundle contains password hashes and potentially private delivery records and
photos. Its root is mode `0700`; database/manifest/files are mode `0600`. Keep it
out of source control, ordinary logs and public artifacts. Export it through the
approved secure channel, store it encrypted outside the ephemeral backend, and
record the manifest/archive hash separately. Internal hashes detect accidental
corruption; they do not authenticate a manifest an attacker can also replace.

## Approved execution order

1. Record the exact backend/frontend release SHAs and current deploy IDs, plus
   a protected snapshot of the current environment, build/start/predeploy/health
   settings, branch, auto-deploy mode, and source paths. Preserve the existing
   signing secret securely; never print it or infer its value from presence.
2. Disable automatic deployment, enable the existing service's maintenance mode
   while its current instance remains available for SSH, and verify public API
   requests are blocked. Drain in-flight requests and ensure no private-network
   workers or manual sessions can write. A frontend banner alone is insufficient.
   **Do not suspend, restart, redeploy or change restart-triggering settings before
   the source bundle has been exported and verified off-host.**
3. Run the snapshot tool from the reviewed candidate tooling without changing the
   old application's environment. Confirm the observed source paths at release
   time; the examples below use the paths found during this inspection.

   ```sh
   python scripts/postgres_cutover.py snapshot \
     --sqlite /opt/render/project/src/courier_lifts.db \
     --photos /opt/render/project/src/object_storage \
     --bundle /tmp/courier-cutover-final \
     --writers-stopped
   python scripts/postgres_cutover.py verify-bundle \
     --bundle /tmp/courier-cutover-final
   ```

   Export the complete bundle, not just the manifest. Verify it independently
   off-host and rehearse `restore-sqlite` at new paths. Keep maintenance enabled.
4. Copy and verify every referenced proof to the approved private, durable object
   store using the storage plan. Preserve object keys, content type, sizes and
   hashes. Preserve unreferenced source files in the backup even if they are not
   uploaded as application objects. The uploaded object/version manifest must be
   paired with this exact database snapshot. No bucket/resource creation is
   permitted without the combined approval.
5. In a **separate reviewed candidate process**, supply the existing internal URL
   securely as `CL_CUTOVER_DATABASE_URL`; reuse the approved secure credential
   workflow. Do not put it in shell history or print it. The live old backend's
   `CL_DATABASE_URL` remains unchanged. Check the exact host from the approved
   existing database inventory, then execute:

   ```sh
   python scripts/postgres_cutover.py preflight \
     --expected-host "$CUTOVER_EXPECTED_HOST" \
     --expected-database courierlifts_db --empty-schema
   python scripts/postgres_cutover.py migrate \
     --expected-host "$CUTOVER_EXPECTED_HOST" \
     --expected-database courierlifts_db --apply --writers-stopped
   python scripts/postgres_cutover.py import \
     --expected-host "$CUTOVER_EXPECTED_HOST" \
     --expected-database courierlifts_db \
     --bundle /tmp/courier-cutover-final --apply --writers-stopped
   python scripts/postgres_cutover.py verify-target \
     --expected-host "$CUTOVER_EXPECTED_HOST" \
     --expected-database courierlifts_db --bundle /tmp/courier-cutover-final
   ```

   These are release-time guards, not a request to repeat the already-completed
   database inspection during preparation. If the public schema changed, stop;
   do not stamp, drop, append or overwrite to force the expected result.
6. Take a PostgreSQL logical dump after verified import and verify a restore in
   an approved isolated destination, paired with the proof/version manifest.
   A temporary hosted restore database may incur cost and is covered only if
   explicitly named in the approval. Local rehearsal is not a managed-backup
   or hosted-restore test. Keep the original SQLite/photo bundle as well.
7. Apply the approved saved settings and deploy the **compatible exact backend
   SHA together**. Use Save-only/manual deployment controls or an equivalent
   verified atomic workflow; an environment change must never trigger old `main`
   against PostgreSQL. Runtime: production, unpaid true, manual-invite, one
   worker, private S3 storage, strong approved signing secret, exact canonical
   and onrender frontend origins. Google billing is not needed for unpaid sample
   quoting. No payment credentials or payment enablement.
8. While maintenance remains active, verify the deployment SHA, `/health`,
   migration-aware `/ready`, `/pilot-info`, database row/hash preservation, and
   S3 recovery/version checks. Verify actual published-theme assets and frontend
   runtime origin settings against the approved PR #20 candidate.
9. Record the verified pre-write rollback checkpoint. Remove maintenance only
   for controlled synthetic two-role acceptance, with self-registration closed
   and no pilot customers invited yet. The first new PostgreSQL application write
   closes the snapshot-only old-code rollback path. Invite pilot participants
   only after all acceptance gates below pass. If a gate fails, re-enable
   maintenance and preserve the new data, monitoring evidence and backup artifacts.

## Recovery and rollback boundaries

| Failure point | Exact recovery action |
| --- | --- |
| Before migration/import, old container still intact | Keep or restore the old runtime/configuration and its still-current SQLite/photo files. Re-enable public access only after verifying the original state. |
| Migration fails, before import | Keep maintenance enabled. Inspect the target securely; transactional DDL normally rolls back, but verify actual schema before retry. Keep the verified source bundle. Never automatically drop/stamp a partial target. |
| Import fails | The import transaction rolls back all application rows and sequence restarts. Verify target counts/history before retrying. Retain source data and artifacts unchanged. |
| Candidate fails before any new PostgreSQL application writes | Keep maintenance enabled. Use the reviewed recovery bootstrap to restore the bundle **before starting** exact old commit `7580a9ca95f0d0795b6362e0b638181359f1b3c5`, with the protected original environment/signing secret and explicit restored SQLite/photo paths. Verify both histories and proof bytes while keeping maintenance enabled. Preserve the migrated PG target for diagnosis; do not downgrade it. |
| Any new PostgreSQL writes or uploaded proofs have occurred | The old SQLite snapshot is no longer a complete current database. Keep maintenance enabled, capture a new PG dump and proof/version manifest, and forward-fix or restore a compatible new-backend release. An old-backend rollback requires a separately reviewed reverse migration/reconciliation that preserves those writes; no such automatic reverse migrator is supplied. |
| Database or photo corruption after opening | Restore the database and matching object versions into approved isolated recovery destinations, compare all identities/relationships/counts/hash manifests, and switch only after verification. Retain the failing state for investigation. A database-only restore cannot recover missing photo bytes. |

Restore to new local paths for an offline recovery check:

```sh
python scripts/postgres_cutover.py restore-sqlite \
  --bundle /secure/courier-cutover-final \
  --sqlite-new /secure/restored-courier_lifts.db \
  --photos-new /secure/restored-object_storage
```

The command deliberately refuses an already-existing database or directory.
**Do not replace a SQLite file under an already-running old process.** Its pooled
connections can still reference the old inode. A normal Render restart can also
replace the ephemeral instance, discarding files copied into its shell. An
artifact rollback followed by a shell copy is therefore not an exact rollback.

The prepared `scripts/start_sqlite_rollback.py` solves the process-ordering part:
it verifies a clean checkout of the exact old SHA, reads a mode-0600 JSON snapshot
of the original `CL_*` settings (and `DATABASE_URL` if originally present), checks
the saved configuration selects SQLite/local storage, restores to new private
paths, clears candidate database/S3 settings, and only then execs old uvicorn.
It refuses a new quote-schema bundle: this rollback accepts only the frozen
original legacy SQLite shape without migration history. It pins one worker and
absolute restored database/photo paths, preserves the
original signing secret and origin, and refuses changed/extra/ignored source
files or an inherited Git checkout override. It independently hashes every
tracked working file against the pinned Git tree, so assume-unchanged or
skip-worktree flags cannot conceal modified code. It also rejects ancestor
`.env` files that old python-dotenv could otherwise discover outside the checkout.
It never imports old app code before
the restoration is complete. Example reviewed start invocation:

```sh
python scripts/start_sqlite_rollback.py \
  --old-checkout /recovery/old-backend-7580a9c \
  --bundle /recovery/courier-cutover-final \
  --original-environment /recovery/original-environment.json \
  --state-parent /tmp --port "$PORT" --maintenance-confirmed
```

The **hosted bootstrap route remains a release gate**: a reviewed recovery
artifact/start command must retrieve the exact clean old checkout, encrypted
backup and protected original environment into the same instance/start invocation
before this command runs. This utility does not fetch those artifacts or ask for
credentials. The old deployed artifact does not contain this new wrapper, so an
ordinary Render Rollback button is not an equivalent execution route. Establish
and approve the secure retrieval/start mechanism before opening the pilot; if it
is unavailable, remain in maintenance and forward-fix the compatible backend.
Do not claim a hosted rollback was tested from the local rehearsal alone.

The wrapper is a **maintenance-only recovery checkpoint**, not a durable ongoing
SQLite fallback. Every invocation restores the frozen bundle to a new directory;
after any subsequent SQLite writes, invoking it again would select stale data.
Keep maintenance enabled through this checkpoint and forward-fix, or establish
an approved durable persistence/reconciliation strategy before reopening old
code. Neither new PostgreSQL writes nor new post-rollback SQLite writes may be
silently rewound to the original snapshot.

A Render code rollback does not itself roll back a database, restore ephemeral
files, or restore a proof bucket. Restoring the frontend theme alone also does
not restore backend data. No old process may ever receive a PostgreSQL URL.

Do not run `alembic downgrade` as production rollback: `0005` downgrade drops
quotes and idempotency columns; later-history and delivery data would be lost or
made incompatible. The `0002` enum downgrade intentionally cannot erase an
already-used status. Local downgrade/upgrade tests only check schema mechanics.

## Post-release acceptance, only after approval

Use approved pilot accounts and clearly labeled unpaid test deliveries. Verify
manual-invite registration, both canonical/onrender browser origins, login,
owner-bound unexpired quote, a single order after same-key retry, exact original
price, eligible courier marketplace, one winning claim, pickup, in-transit,
private image upload, delivered completion, customer history and courier assigned
history. Freshly log in both roles and verify the same delivered record and photo.
After a controlled compatible backend restart, verify history and the photo again.
Check unauthorized users cannot retrieve the proof, payments remain unavailable,
and the published theme/NavGlass are unchanged. Record actual pass/fail evidence;
local browser tests are not this hosted release acceptance.

## Local validation and limits

The preparation uses Python 3.11.17 and an isolated PostgreSQL 16.15 cluster on
loopback, with synthetic data only. `tests/conftest.py` now rejects hosted URLs,
file-backed SQLite, and libpq query overrides **before** its destructive table
fixtures import the app. `scripts/verify_pilot_persistence.py` also rejects
non-loopback/overridden targets and requires empty data for its fictional seed.

Relevant commands (the final release report records the actual counts):

```sh
python -m compileall -q backend tests scripts migrations ops/pilot-storage
CL_DATABASE_URL=sqlite:///:memory: python -m pytest -q
CL_DATABASE_URL=postgresql://nobody@127.0.0.1:54329/pilot_backend_tests python -m pytest -q
```

The synthetic migration tests exercise nonempty legacy and current SQLite data,
committed WAL preservation, all-field comparison, missing/corrupt/symlink proof
refusal, unknown schemas, target identity checks, duplicate import refusal,
injected failure after inserts, full row/sequence rollback, and exact SQLite
restore to new paths. The native PostgreSQL drill uses `pg_dump`/`pg_restore`, a
new object directory, new application process and fresh logins to verify the
delivered order, original price, quote, idempotency replay and photo checksum.
Local proof bytes and mocked object-storage tests cannot establish real S3
credentials, bucket policy, network reachability, managed backups or hosted
recovery. Those remain explicit release gates.

Current-session results and exact tested file hashes are recorded in
[backend-validation-20261022.json](../evidence/backend-validation-20261022.json).
The latest full runs passed 298 PostgreSQL cases and 288 SQLite cases (10
PostgreSQL-only skips). Final legacy-only bundle, independent checkout-byte and
ancestor-dotenv safeguards added five cases after those runs; the complete final
cutover suite passed 55/55 on PostgreSQL and 50 with 5 PostgreSQL-only skips on
SQLite. The current suite collects 303 cases; these results must not be described
as a single 303-case full run.
Native PostgreSQL dump/restore and the exact-old-code pre-start SQLite recovery
drills also passed locally. No hosted restore or deployment has been performed.
