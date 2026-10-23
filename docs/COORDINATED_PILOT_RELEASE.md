# Courier Lifts: coordinated unpaid-pilot release and recovery approval

Prepared 22 October 2026. **This is preparation, not authorization to change production.**
The database inspection already succeeded and remains accepted. Do not repeat the
phone credential-entry workflow or rerun the inspection merely to continue this
work. The final frozen release snapshot and mutation-time safety guards described
below serve a different purpose: they preserve the state that actually exists
when an approved release begins.

Keep the existing Render services and PostgreSQL instance. Preserve the published
orange-and-charcoal theme and the latest work in backend PR #8 and frontend PR #20.
Payments remain disabled, registration remains `manual-invite`, invitations remain
closed, and NavGlass is outside this release. No paid resource, real credential,
production configuration, migration, merge or deployment has been authorized by
this document. Local synthetic keys, databases and object-storage test doubles
are test fixtures, not configured operational services.

This document coordinates [PostgreSQL cutover](POSTGRES_CUTOVER.md),
[private proof storage](PILOT_PHOTO_STORAGE.md), [automated backups](AUTOMATED_BACKUPS.md),
[iPhone photo handling](IPHONE_PHOTO_HANDLING.md), and the
[account and full cost model](PILOT_COSTS_AND_ACCOUNTS.md). Its startup and automated
recovery procedure supersedes older notes that describe a shell copy after
rollback or daily manual backups as the operating arrangement.

## 1. Accounts, exact resources and revision record

### Existing resources: retain, do not recreate

| Item | Verified identity / intended use |
| --- | --- |
| Render account connection | Existing authenticated **Primary** connection; reuse the selected account. |
| Render workspace | **My Workspace**, `tea-d20l5i7gi27c73coe8a0`, Hobby. Preserve personal account/recipient details in the secure approval record; the Render account does not establish an AWS account or prove alert receipt. |
| Backend | `srv-d2tbhgmuk2gs73cneer0`, **CL-backend**, Oregon, one 0.5 CPU / 512 MB instance, no persistent disk; `https://cl-backend-ppv1.onrender.com`. Keep the native Python web-service runtime. |
| Current backend deployment | `dep-db8ductckfvc73btjntg`, `main` at **`7580a9ca95f0d0795b6362e0b638181359f1b3c5`**. It uses SQLite/local photos. Its Python runtime is **3.13.4**. It must never receive the candidate PostgreSQL configuration. |
| Existing PostgreSQL | `dpg-dauihb893c1s73ecnihg-a`, **courierlifts-db**, logical database **`courierlifts_db`**, Oregon, PostgreSQL 16, 0.1 CPU / 256 MB, 1 GB storage, storage autoscaling off, external access blocked. Use the saved internal connection, port 5432, with its exact host recorded securely in the release configuration. |
| Frontend | `srv-d25d0k49c44c73d6msb0`, **courierlifts-web**, existing static site; `https://courierlifts.com` and `https://courierlifts-web.onrender.com`. |
| Current frontend deployment | `dep-dbcdihbrjlhs73bl4ne0`, `launch-readiness` at **`39bdc934e005359c2e114b67afb94e0545b9b34f`**. This is the published theme to preserve. |
| Existing recovery controls | The PostgreSQL recovery page shows a three-day point-in-time recovery window. An available control is not a successful restore. A [provider PITR restore](https://render.com/docs/postgresql-backups) creates another database resource and needs its own applicable cost approval. |

The backend remains on SQLite and the existing PostgreSQL public schema was empty
when successfully inspected. Those accepted findings are not a release-time
backup and do not justify discarding even an apparently empty source.

### Candidate source to preserve and approve

| Repository / PR | Candidate branch and known preserved preparation head |
| --- | --- |
| [CourierLift/CL-backend — PR #8](https://github.com/CourierLift/CL-backend/pull/8) | `work/integrated-unpaid-pilot-20261021`; preserved starting head **`5081cdd05e50d1079cdb3e37f2c3ef4f783dd6f2`**. The release must use the final reviewed descendant containing this continuation. |
| [CourierLift/courierlifts-web — PR #20](https://github.com/CourierLift/courierlifts-web/pull/20) | `work/integrated-unpaid-pilot-20261021`; preserved starting head **`4541cf1bec1bdf29f8f5885a8f57b1f2863ca3ff`**. The release must use the final reviewed descendant containing the photo/retry work. |

Frontend continuation published at **`4cbb574b5cb1ebad93abf95c74f6d1788a84c70a`**,
with [React CI run 39901527672](https://github.com/CourierLift/courierlifts-web/actions/runs/39901527672)
passed on that exact head.

Record the final full 40-character **BACKEND_RELEASE_SHA** and
**FRONTEND_RELEASE_SHA**, their exact CI runs, and the reviewed deployment settings
in the coordinated approval record/PR descriptions after preparation is published.
The two starting heads above are provenance, not automatic approval of later
changes. Reconcile any intervening branch changes before proceeding. Never deploy
a moving branch tip or assume green CI for an earlier SHA covers a later one.
`CL_RELEASE_SHA` must equal the approved backend artifact's `RENDER_GIT_COMMIT`.
Both existing PRs remain draft/unmerged until the applicable release approval.

Frontend publication must retain the existing base tree, including
`assets/logo_transparent_light.png` (blob
`02ca0689ac213d5bb6b56786d00e48f269e2a4fd`), even though that historical binary is
absent from the local materialized snapshot. Theme CSS, HTML and brand SVGs are
unchanged; use the existing integrity evidence to verify the deployed artifact.

### Proposed resources: account choice still required

No suitable connected general-purpose AWS account/storage administration surface
has established an AWS payer, owner ID, bucket or IAM identity. **The actual
12-digit AWS account ID is unknown.** Do not infer it from Render, GitHub or an
email address. Ask only which existing AWS account to use and, if necessary,
for its secure sign-in/account selection. Do not request database credentials
again. Account inspection is read-only until the coordinated resource approval.

The simplest prepared option is AWS S3 Standard in **`us-west-2`**, using the
existing S3 adapter. These are proposed names, not existing or reserved buckets:

| Proposed resource | Scope / approval binding |
| --- | --- |
| `courierlifts-pilot-proofs-<account-id>-us-west-2` | Private, versioned runtime proofs; `proofs/*` only for the application identity. Reuse an existing suitable bucket only after checking owner, actual region, policies, lifecycle and other workloads. |
| `courierlifts-pilot-backups-<account-id>-us-west-2` | Separate private, versioned backup destination. Automated encrypted objects live under **`courierlifts-backups/v1/`**. Reserve separate operator-only prefixes for the frozen original legacy bundle and recovery receipts. |
| `courierlifts-pilot-recovery-<account-id>-us-west-2` | Initially empty private, versioned bucket for the approved hosted restore drill. An incident requires a fresh empty bucket; do not empty/reuse an occupied bucket to bypass the guard. A later unique suffix must be explicitly recorded and approved. |
| `courierlifts-pilot-backup` | New Render cron service, same workspace/Oregon, 0.5 CPU / 512 MB, reviewed Dockerfile and exact backend SHA; daily **07:00 UTC**, `0 7 * * *`. No service ID exists yet. |
| `courierlifts-pilot-backup-watch` | Separate new cron service, same workspace/Oregon and exact backend SHA; hourly at minute **15**, `15 * * * *`. After a successful freshness check, retention runs at **08:15 UTC**. No service ID exists yet. |
| `courierlifts_recovery_<approved_suffix>` | New empty logical database on the existing PostgreSQL instance for the hosted restore drill, only if the existing role permissions and available storage support it. This does not authorize a new managed PostgreSQL instance, storage increase or autoscaling. |

All buckets require bucket-owner-enforced ownership, all four public-access
blocks, versioning, TLS-only access and SSE-S3 default encryption. Verify their
**actual** region is `us-west-2`; a configured SDK endpoint alone is not evidence
of bucket location. Record concrete bucket names, owner ID, IAM ARNs, policies,
new Render service IDs and logical recovery database identity before activation.
No public photo URLs, browser AWS keys, CDN or additional photo-serving service
are needed. Same-account buckets provide separation of application/backup powers;
they do not constitute protection from loss of the entire AWS account.

## 2. Named owner, retention, alerts and cost acceptance

**Proposed recovery owner: Will, the Courier Lifts owner.** Acceptance is part of
the single coordinated approval. Will holds the private recovery identity and an
independent offline copy, receives and acts on failed/stale-backup alerts,
authorizes incident recovery, and records restore evidence. No named deputy has
been supplied; one can be added without inventing an unapproved recipient.

Automatic checkpoint retention keeps the newest success in each of the latest
**7 distinct UTC days, 4 ISO weeks and 3 calendar months**, retaining their union
(at most 14 checkpoints). It verifies retained ciphertext references before
pruning expired checkpoint dump/manifest/complete-marker versions. A missing
backup, age over **26 hours**, or timestamp over five minutes in the future
fails the monitor and prevents retention. Encrypted photo archives and current
or noncurrent linked proof versions have **no automatic age expiry**. History
must not disappear to satisfy a spending target. Review growth and costs after
30 days; a later photo-retention change needs reference-aware review.

The existing Render notification configuration is **Email / only failure
notifications**. Confirm the actual recipient and each cron service's effective
settings during approved activation. A successful email-setting inspection is
not a delivered alert. Test a controlled backup failure and stale/missed-checkpoint
failure, receive both alerts, then return to healthy scheduling. Do not alter or
corrupt a real checkpoint to make the tests fail.

Render's [notification rules](https://render.com/docs/notifications) do not give
one-off-job failures the same email behavior as cron failures. The recovery operator must observe each one-off job's terminal status
and its explicit verified receipt; do not wait for an assumed failure email.
No extra Slack integration or paid alerting account is required by this plan.

The complete expected monthly cost, measured existing hosting, AWS request/storage
and transfer assumptions, restore usage and other-project separation are in
[PILOT_COSTS_AND_ACCOUNTS.md](PILOT_COSTS_AND_ACCOUNTS.md). Approve that reviewed
cost scenario together with the resources. **The proposed extra $10/month is a
planning target, not spending authorization and not a hard billing cap.** No new
resource is approved just because its estimate appears below that target. Costs
can grow with saved photos/versions, views, database size, backup runtime and
recovery work. Existing NavGlass charges are reported separately and its resources
remain untouched.

## 3. Credentials and configuration: exact placement

Generate/configure real credentials only after the applicable approval. The
existing successful database connection is reused through its secure saved
configuration; this plan does not require another iPhone credential-entry session.
No secret belongs in Git, chat, shell history, command-line URLs, frontend bundles,
ordinary logs or a container image. Local approval flags do not themselves grant
permission to perform a hosted action.

| Consumer | Exact variables / values | Credential boundary |
| --- | --- | --- |
| Compatible web service | `PYTHON_VERSION=3.11.17`, `WEB_CONCURRENCY=1`, `CL_RELEASE_SHA=BACKEND_RELEASE_SHA`, `CL_APP_ENV=production`, `CL_UNPAID_PILOT=true`, `CL_REGISTRATION_MODE=manual-invite` | Set only with the compatible artifact. Invitations stay operationally closed until all gates pass. |
| Compatible web database/auth | `CL_DATABASE_URL` = saved internal PostgreSQL URL; `CL_SECRET_KEY` = approved new strong production signing key, at least 32 characters | Preserve the original signing key only inside the encrypted original-environment recovery artifact before rotation. Rotation requires fresh logins; stored password hashes and history are preserved. Do not change the old process to PostgreSQL. |
| Compatible web photos | `CL_OBJECT_STORAGE_BACKEND=s3`, exact `CL_S3_BUCKET`, `CL_S3_REGION=us-west-2`, `CL_PROOF_MAX_BYTES=10485760`; `CL_S3_ENDPOINT_URL` absent | Dedicated `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, with `AWS_SESSION_TOKEN` only if an approved renewable mechanism exists. Runtime can access its proof prefix, not backup objects or permanent version deletion. |
| Web origins | `CL_FRONTEND_ORIGIN=https://courierlifts.com`; `CL_ADDITIONAL_FRONTEND_ORIGINS=https://courierlifts-web.onrender.com` | Exact HTTPS origins; no wildcard. No Google/payment key is needed for the unpaid sample flow. |
| Frontend build | `NODE_VERSION=22`, `SKIP_INSTALL_DEPS=true`, `VITE_COURIER_LIFTS_API_URL=https://cl-backend-ppv1.onrender.com` | No database, S3, signing or recovery secrets. Build `npm ci && npm run check && npm run build`; publish `dist`. |
| Isolated cutover tooling | `CL_CUTOVER_DATABASE_URL` plus explicit expected host/database | Separate candidate process; never fall back to the old web application's database settings. No inherited `PG*` overrides. |
| Both cron services | `CL_RELEASE_SHA=BACKEND_RELEASE_SHA`, `CL_BACKUP_CONFIG_JSON` | JSON follows `ops/pilot-backup/config.template.json`: exact source host/port/database, proof/backup buckets and owner IDs, region/prefix, public age recipient and accepted named owner. No private key in normal scheduled configuration. |
| Daily backup cron | `CL_BACKUP_DATABASE_URL`; `CL_BACKUP_WRITER_AWS_ACCESS_KEY_ID`, `CL_BACKUP_WRITER_AWS_SECRET_ACCESS_KEY` | Separate **`courierlifts_backup`** PostgreSQL login, read-only tables/sequences, and backup writer S3 identity. No app/admin/retention credentials. |
| Hourly monitor/retention cron | `CL_BACKUP_MONITOR_AWS_ACCESS_KEY_ID`, `CL_BACKUP_MONITOR_AWS_SECRET_ACCESS_KEY`; `CL_BACKUP_RETENTION_AWS_ACCESS_KEY_ID`, `CL_BACKUP_RETENTION_AWS_SECRET_ACCESS_KEY` | Monitor has marker/list reads. Retention separately has exact checkpoint-version deletion; neither receives a database password, decryption identity or photo-version deletion power. |
| Approved recovery one-off job only | `CL_BACKUP_RESTORE_DATABASE_URL`, `CL_RECOVERY_SOURCE_DATABASE_URL`, `CL_BACKUP_RECOVERY_AWS_ACCESS_KEY_ID`, `CL_BACKUP_RECOVERY_AWS_SECRET_ACCESS_KEY`, `CL_RECOVERY_AWS_ACCESS_KEY_ID`, `CL_RECOVERY_AWS_SECRET_ACCESS_KEY`; temporary `CL_RECOVERY_JOB_JSON`, `CL_RECOVERY_AGE_IDENTITY` and `CL_BACKUP_CONFIG_JSON` | Backup recovery identity reads encrypted checkpoints. Separate recovery operator writes only the approved empty recovery photo namespace and operator-only receipt prefix. The source URL is read-only; the target URL names the isolated restore database. Remove temporary secrets from the base service immediately after the job snapshots them. |
| Approved recovery web startup only | `CL_RECOVERY_REFERENCE_JSON`, `CL_RECOVERY_AWS_ACCESS_KEY_ID`, `CL_RECOVERY_AWS_SECRET_ACCESS_KEY`; legacy mode additionally uses `CL_RECOVERY_AGE_IDENTITY` and `CL_RECOVERY_VERIFY_DATABASE_URL` | Immutable reference pins bucket, expected owner, region, key, VersionId, SHA-256 and size. PG recovery startup needs receipt/photo reads, not the age private key. Remove temporary recovery variables before normal serving. |

Role-specific AWS identities may use the matching `*_AWS_SESSION_TOKEN` where
supported; ordinary runtime AWS uses `AWS_SESSION_TOKEN`. A nonrenewing temporary
session is not an operational credential strategy. Prefer supported renewable
credentials; no Render-to-AWS federation has been verified. If dedicated access
keys are needed, use non-root identities, record the owner/ARN and a rotation
procedure, test replacement credentials before retiring the old ones, and never
reuse the runtime identity for backups or retention.

Do not rotate the existing PostgreSQL owner password merely to repeat setup. If
exposure or account policy requires rotation, explicitly include it and every
affected consumer in the approved window. Create the separate backup database
login with `ops/pilot-backup/database-reader.template.sql` only after schema
migration, assigning its password through the secure channel. Do not broaden
roles to bypass a missing grant. Final IAM substitution/validation and effective
permission checks remain account-specific activation work.

Generate the real age identity offline only after approval. Verify trial
encryption/decryption and preserve its independent offline copy before using its
public recipient. Scheduled jobs receive the public recipient only. The private
key appears in a temporary recovery process only when the operator initiates
approved recovery; preserve old private identities for as long as any retained
archives depend on them.

## 4. Build and startup resolve the ephemeral-instance gap

The web build uses the candidate `requirements.txt` and
`scripts/prepare_release_runtime.py`. The latter packages a clean complete
checkout of exact old commit `7580a9c…`, a separately installed checksum-pinned
**Python 3.13.4** interpreter, the **54 observed package versions** in
`ops/pilot-recovery/legacy-requirements.lock`, and pinned **age v1.3.2**. It creates
a runtime receipt and verifies interpreter/package inventory and executable
hashes. These dependencies are isolated from the candidate's Python 3.11.17
runtime. The inventory was obtained from a read-only runtime-version listing,
not from another database or credential inspection. Matching versions is not a
claim that the downloaded interpreter binary is byte-identical to Render's
original Python build.

Normal start is **`python scripts/start_pilot.py`**, with health check **`/ready`**
and an empty pre-deploy command. It verifies the approved artifact identity,
requires the compatible PostgreSQL configuration, clears recovery-only variables
and then imports the candidate application. It never restores or migrates on an
ordinary restart. Migration/import is an explicitly approved separate operation.

Recovery startup runs the restore/verification boundary **inside the very process
invocation that will start the application**. `scripts/recovery_inputs.py`
materializes temporary environment inputs into a private `0700` directory and
`0600` files, removes their environment values, and cleans those files before
web `exec` or when the recovery job exits. This does not depend on `/etc/secrets`
permissions, a Render shell's temporary files, a mounted disk or a later manual
copy after deployment. Recovery credentials are removed from the application
process environment.

The separate reviewed backup/recovery Docker image supplies PostgreSQL 16
`pg_dump`/`pg_restore`, age and the candidate tools. Its entrypoint removes only
the base image's three known `PG_MAJOR`, `PG_VERSION`, `PGDATA` metadata variables;
unreviewed `PG*` overrides fail closed. Docker/native CI and actual native web
runtime-build evidence must pass for the final exact source SHA. Source code and
a local test are not by themselves evidence that Render has built or run it.

## 5. Approved migration and release sequence

Perform these steps only after account binding, the final commit/CI record and
one coordinated approval. Keep a sanitized operation ledger with timestamps,
commit/deploy IDs, counts/hashes, version-pinned references, job IDs and results;
keep secret values and customer rows out of it.

1. **Bind the approval.** Record exact resource names/account IDs, final backend
   and frontend SHAs, existing deploy IDs, maintenance window, named recovery
   owner and accepted cost scenario. Validate the two cron review template and
   storage/IAM substitutions. Confirm capacity and permissions for the isolated
   logical restore database; stop for a specific extra approval if that would
   require another paid database or larger plan.
2. **Prepare approved storage and backup ownership.** Create/reuse only the
   approved buckets and identities; verify actual Oregon location, privacy,
   versioning, encryption and least privilege. Enforce conditional creation on
   the backup bucket; do not apply that policy to runtime proof writes, whose
   existing adapter uses unique keys and bucket default encryption. Observe
   versioning propagation before writes. Prepare the offline recovery identity
   and separate secure configuration inventory. Initially keep new schedules
   suspended until the database source and first checkpoint are ready.
3. **Freeze the current source without losing it.** Record the current
   environment and build/start/predeploy/health/branch/auto-deploy settings in a
   protected recovery snapshot. Disable automatic deployment, enable service
   maintenance while the current instance remains available, verify public API
   requests are blocked, drain in-flight requests and stop every other writer.
   A frontend banner is insufficient. **Do not suspend, restart, redeploy or make
   restart-triggering changes to the old web service before exporting its source.**
4. **Export and prove the frozen original.** Run the reviewed candidate snapshot
   tool against `/opt/render/project/src/courier_lifts.db` and
   `/opt/render/project/src/object_storage`, with writers stopped, without
   changing the old application's settings. Make the reviewed tools and
   checksum-verified age binary available in a private operator working
   directory first; a command-local `PATH` may select that binary, without
   changing the running web process or installing candidate packages over its
   old environment. The SQLite backup API includes
   committed WAL contents. Preserve every local photo, including unreferenced
   files. Verify all rows/FKs/checksums and rehearse restoration to new paths.
   Capture the original `CL_*` environment and original signing secret privately.
   Seal bundle plus environment with `scripts/seal_legacy_recovery.py`; store the
   encrypted artifact in the operator-only backup prefix and independently record
   bucket/owner/region/key/VersionId/SHA-256/size. Retrieve/decrypt/verify it
   off-host. An empty inventory still requires this complete step.
5. **Copy legacy proofs unchanged.** Use the approved temporary migration
   identity and `ops/pilot-storage/proof_objects.py upload` with the frozen bundle,
   exact bucket/owner and `--allow-object-writes`. Preserve keys, MIME, sizes and
   hashes; record VersionIds and verify every uploaded/already-present object.
   Do not normalize historical photos or publish unreferenced private files.
6. **Migrate/import in an isolated candidate process.** Supply the existing
   internal URL securely as `CL_CUTOVER_DATABASE_URL`. Run the mutation-time
   empty-schema guard, full **`0001` → `0005_quotes_idempotency`** migration,
   transactional import and read-only `verify-target` against the frozen bundle.
   The exact command forms are in `POSTGRES_CUTOVER.md`. Preserve IDs, password
   hashes, original prices, status/timestamps, quote/idempotency data, proof
   relationships and rewards; verify sequences. A mismatch stops the release.
   Never stamp an assumed revision, use `create_all`, append to populated tables,
   drop a surprising schema or point old `main` at PostgreSQL.
7. **Create the backup login and initial encrypted checkpoint.** Apply the
   approved read-only database grants, bind exact backup config, build the two
   cron services from the approved SHA and run the first backup against the
   imported PostgreSQL/photo pair. Preserve its complete-marker reference.
   Carry out the hosted fresh-target recovery procedure below and verify all
   restored row/photo digests before accepting the recovery mechanism. The
   original SQLite/environment checkpoint remains separately preserved.
8. **Configure and deploy the compatible backend together.** Preserve the old
   secret before the approved production signing-key rotation. Use verified
   **Save-only/manual deployment** controls to stage the reviewed candidate
   branch/build/start settings, environment and `CL_RELEASE_SHA`, then deploy the
   exact compatible SHA. An environment update must not deploy old `main` with
   PostgreSQL. Keep one web worker, unpaid/manual-invite mode, private S3,
   explicit origins, no payment enablement and pre-deploy command empty.
9. **Verify backend and publish the matching frontend.** While traffic remains
   controlled, confirm actual artifact SHA, `/health`, migration-aware `/ready`,
   `/pilot-info`, saved-row preservation, private photo retrieval and no secret
   leakage. Deploy the exact frontend SHA to the existing static site with its
   direct HTTPS API origin. Verify orange/charcoal assets and origin/CORS behavior.
   Do not change NavGlass, DNS or unrelated services.
10. **Run restricted acceptance; then recheckpoint.** Public registration stays
    closed. Allow only approved synthetic customer/courier acceptance under
    controlled access, then verify the full journey, fresh-login history, proof
    bytes and restart persistence. **The first new PostgreSQL write closes the
    old SQLite recovery path.** Take another fresh coordinated checkpoint after
    acceptance writes, prove the hosted recovery/alert/scheduling and physical
    iPhone gates, and only then consider opening invitations. Any failed gate
    returns the service to maintenance while retaining the new history.

A release can span waiting for the real scheduled run and a physical iPhone test.
That waiting period does not justify calling backups operational or inviting
participants prematurely.

## 6. Exact recovery boundaries and Render execution

### Before new PostgreSQL application writes: maintenance-only legacy recovery

This path is available only while the frozen legacy bundle still equals the
imported PostgreSQL application's full table contents. The independently pinned
reference must say `kind=cl-legacy-recovery-reference`, `format_version=1`,
`phase=before-new-postgres-writes`, exact old commit and exact PostgreSQL
host/port/database, and bind the encrypted original bundle's immutable object
reference. Securely configure the temporary recovery reference, recovery read
identity, offline age identity and comparison database URL.

Use the **candidate artifact containing the recovery tools and verified old
runtime**, with the following start command:

```sh
python scripts/start_pilot.py --mode legacy-checkpoint \
  --maintenance-confirmed --no-new-postgres-writes-confirmed
```

Set the health check to **`/health`** in this same reviewed recovery configuration:
the exact old backend does not expose `/ready`. The wrapper verifies its source
checkout/runtime, fetches and decrypts the pinned bundle, compares the migrated
PostgreSQL rows with the frozen source, restores SQLite/photos to new private
paths in the current instance, clears candidate PostgreSQL/AWS/CL settings and
then starts the exact old application with its original environment/signing key.
A new PostgreSQL row/change causes the comparison to fail closed.

**Keep maintenance enabled.** This is a preservation/diagnostic escape before
new writes, not an ongoing public SQLite service on ephemeral storage. Do not
open it for new deliveries, assume its temporary files survive the next deploy,
or use Render's artifact rollback button followed by a shell copy. Keep the
migrated PostgreSQL database and its checkpoints for diagnosis. Returning to the
compatible service requires its approved configuration, normal start command
and **`/ready`** health check.

### After any new PostgreSQL writes: compatible PostgreSQL recovery only

1. Enable maintenance, drain requests and stop all writers. Preserve the current
   database and photo bucket; do not delete or overwrite the impaired state.
   Take a **fresh** encrypted PostgreSQL/photo checkpoint and pin its complete
   marker. Compare the checkpoint with the frozen current source. An older
   SQLite snapshot or stale PostgreSQL checkpoint is not a zero-loss rollback.
2. Prefer a compatible application forward fix on the existing preserved
   PostgreSQL state where safe. If restoration is needed, approve an empty
   distinct logical database and empty distinct private versioned recovery photo
   bucket. Capacity/permission failure is a blocker, not permission to create a
   paid replacement automatically.
3. Run the approved one-off recovery job using the exact backup service build
   artifact. It decrypts/verifies every artifact, restores to the empty database
   in one transaction, restores exact photo bytes to the new durable bucket,
   verifies all rows/proofs/sequences and current object versions, compares the
   frozen source again, and publishes a version-pinned durable startup receipt.
   A source write after the checkpoint blocks successful publication. Failed or
   partly populated destinations stay quarantined; never erase them for a blind
   retry.
4. Stage the approved compatible web artifact with the recovered database URL,
   recovered photo bucket and temporary receipt/read credentials. Set startup
   to the command below and health check to `/ready`. The wrapper retrieves the
   exact receipt, verifies its release SHA, target host/port/database, all table
   digests and every current proof/version/hash **before importing the app**.
5. While maintenance remains enabled, restore normal startup
   `python scripts/start_pilot.py`, remove temporary recovery variables and
   redeploy the same approved compatible SHA against the verified durable
   PostgreSQL/S3 pair. Recheck readiness and preserved state before reopening
   controlled traffic. This prevents an ordinary future restart from replaying
   an old checkpoint or requiring a receipt whose data has legitimately changed.
6. Test saved customer/courier history, original prices, proof authorization,
   bytes and another ordinary restart. Retain the source/failed targets and
   receipts according to the incident decision. Do not perform an Alembic
   downgrade or substitute old SQLite after acceptance writes.

```sh
python scripts/start_pilot.py --mode recovered-postgres --maintenance-confirmed
```

If the source is unavailable or corrupt and a fresh matching checkpoint cannot
be obtained, stop and name the newest recoverable checkpoint/time and the known
or unknown write gap. The CLI's
`--acknowledge-disaster-checkpoint-rpo` is reserved for a separately accepted
incident recovery point; it is not part of a no-loss planned rollback. Daily
logical backups have a nominal up-to-24-hour recovery point, and the freshness
alarm allows 26 hours. Provider PITR and photo recovery must be coordinated;
neither alone guarantees recovery of every latest delivery.

### One-off job: temporary environment snapshot, no secrets in commands

A [Render one-off job](https://render.com/docs/one-off-jobs) uses the base service's
latest successful build and snapshots its configured environment when created. The job creation surface does not
supply an arbitrary per-job secret-environment override. Therefore the approved
execution sequence is explicit:

1. Confirm the backup service's latest successful artifact is the approved exact
   backend SHA. Suspend its schedule and wait for any active run to finish.
   Keep the independent monitor healthy or record an approved maintenance
   suppression without treating it as backup success.
2. In the base backup service, **Save-only** the temporary recovery inputs and
   role-specific credentials from the matrix. `CL_RECOVERY_JOB_JSON` contains the
   approved checkpoint ID/pinned complete-marker reference, exact target
   host/database/photo bucket/owner, release SHA and explicit operation
   acknowledgements as required by `scripts/run_recovery_job.py`. It contains no
   secret database URL or private identity. Supply the private age identity only
   through the temporary secret variable.
3. Create the one-off job from that artifact with command
   **`python scripts/run_recovery_job.py`**. Do not interpolate secrets into its
   command string, logs or shell. Record job ID, base artifact SHA and target
   identifiers. The wrapper materializes bounded private files and invokes the
   reviewed durable restore command; no resource is created by the wrapper.
4. Immediately remove the temporary recovery inputs/credentials from the base
   service with Save-only. The already-created job retains its own environment
   snapshot. Do not leave the offline private key on the ordinary scheduled
   service, and do not resume its schedule while recovery powers remain there.
5. Observe the job through its terminal status. Require explicit
   `durable_recovery_verified=true`, `application_started=false` and the full
   pinned startup reference; independently retrieve/verify that receipt before
   configuring the web service. A job ID, a startup log or absence of email is
   not success. On failure retain targets closed and investigate.
6. Verify the base service contains only its ordinary public-recipient backup
   configuration/credentials, resume its schedule, and record successful next
   execution/freshness. The one-off job's temporary private files are removed on
   exit. Account access to historical job metadata remains part of the secure
   recovery account boundary.

The job creation/configuration and recovery writes require the applicable release
or incident approval. No such job has been created during preparation.

The nonsecret job-choice JSON has these exact keys. Replace each unresolved
identifier from the independently recorded approval/checkpoint; the example is
deliberately not executable configuration. The complete reference belongs to the
same selected checkpoint. Planned recovery keeps the disaster acknowledgement
**false**:

```json
{
  "checkpoint_id": "APPROVED_CHECKPOINT_ID",
  "complete_reference": {
    "key": "courierlifts-backups/v1/checkpoints/APPROVED_CHECKPOINT_ID/complete.json",
    "version_id": "INDEPENDENTLY_RECORDED_VERSION_ID",
    "sha256": "INDEPENDENTLY_RECORDED_64_HEX_SHA256",
    "size_bytes": 0
  },
  "target_host": "APPROVED_INTERNAL_RECOVERY_HOST",
  "target_database": "courierlifts_recovery_APPROVED_SUFFIX",
  "photo_bucket": "APPROVED_EMPTY_RECOVERY_BUCKET",
  "photo_owner": "APPROVED_12_DIGIT_AWS_ACCOUNT",
  "release_sha": "APPROVED_40_HEX_BACKEND_SHA",
  "writers_stopped": true,
  "allow_writes": true,
  "acknowledge_disaster_checkpoint_rpo": false
}
```

The zero size is an invalid placeholder: substitute the exact recorded positive
byte count. The wrapper rejects missing/extra keys, non-true operational
acknowledgements and a release SHA differing from the approved artifact. These
booleans record an approval already obtained; they do not grant it.

## 7. Acceptance evidence and invitation gate

Maintain two separate evidence columns: **isolated preparation passed** and
**hosted/physical activation passed**. Do not transfer a result from one column to
the other. Current local/frontend evidence includes 100 React tests, 26 checks,
production build, and the synthetic two-role Chromium/PostgreSQL journey with
upright photo, original-byte retry, saved history and proof persistence after an
API restart. Backend photo evidence includes genuine formats/orientations,
bounds/errors, metadata stripping and preserved earlier proof bytes. Native
recovery uses real PostgreSQL 16 and real age with injected S3; it is not an AWS
credential or hosted scheduler test. Final exact-SHA CI and runtime-build results
belong in the release evidence record, with skips/failures stated explicitly.

All of these hosted/physical gates are required before invitations open:

| Gate | Evidence required |
| --- | --- |
| Final source/build | Exact deployed backend/frontend SHAs, corresponding green CI and the actual Render build/start identity. Old interpreter/package inventory and before-start recovery build verified. |
| Private photo storage | Real approved bucket/owner/region/policy, authorized upload/read, unrelated-user denial, pinned size/hash, persisted photo after a hosted ordinary restart. |
| Scheduled backups | At least one **actual scheduled** daily run, encrypted complete checkpoint, independently recorded key/VersionId/hash/size and automatic retention success. A manual trigger alone does not prove the schedule. |
| Alert delivery | Controlled failed-run and missed/stale-checkpoint failures produce delivered notifications to the accepted owner, followed by healthy restored scheduling. Record recipient and receipt time; never claim configured email equals delivered email. |
| Hosted recovery | Decrypt a scheduled checkpoint using the offline-held identity, restore to the approved fresh PostgreSQL/photo targets, compare all row/proof digests and sequences, verify the durable startup gate and an ordinary restart; record measured time. |
| Full delivery | Fresh synthetic customer/courier logins; quote/order, courier claim, pickup, transit, proof save, delivery and both saved histories. Preserve the original quoted/order price. Verify payment actions remain disabled and proof is mandatory. |
| Physical iPhone | Record model/iOS/Safari version. Test Photos and Files JPEG selection, a new Most Compatible capture, actual HEIC selection/error guidance, portrait/landscape orientation, valid 12MP files within 4 MB, over-4-MB feedback, 24/48MP rejection, interruption/retry, upright saved preview after fresh login in both roles. |
| Hosted photo resources | Actual deployed JPEG/PNG/WebP codecs, representative real iPhone memory/latency within the one-worker 512 MB service, concurrent-upload busy response and successful retry without OOM/timeouts. Local synthetic measurements are not a hosted capacity guarantee. |
| Presentation/access posture | Published orange/charcoal theme preserved; approved API origin/CORS; no public self-registration or invitations before all gates; no payment enablement; NavGlass unchanged. |

The browser accepts static JPEG, PNG or WebP up to **4,000,000 bytes**; the direct
API raw-file ceiling is **10 MiB**, with **16 million pixels / 8192 per axis**.
Saved new proofs are upright RGB JPEG, at most 2048 pixels on the long side,
metadata stripped. HEIC/HEIF requires a supported export; **Most Compatible**
affects new iPhone captures, not existing HEIC files. The real picker may change
encoding; inspect its actual output rather than assuming Safari always converts.
The complete handling/error contract remains in `IPHONE_PHOTO_HANDLING.md`.

## 8. One coordinated approval, with only genuinely missing choices

Before asking to execute, finish available code/docs/isolated tests and bind the
final commit/CI record. The remaining account choice is which AWS account/payer
to use; secure sign-in is requested only if required to inspect/bind that choice.
No repeat PostgreSQL inspection or phone credential entry is required. Confirm
Will's recovery-owner acceptance and actual alert recipient as part of the same
release decision.

The concrete approval record must authorize, together:

- The final backend/frontend SHAs on the **existing** Render services, the named
  maintenance window, stopped-writer export, full PostgreSQL migration/import and
  verified backend cutover; no old backend pointed at PostgreSQL.
- The exact selected AWS account and concrete private proof/backup/recovery
  buckets, role-scoped identities and database backup login, plus the two named
  Render cron services and the accepted cost scenario from the cost document.
- Offline recovery-identity generation/custody, protected original-environment
  capture, approved production signing-key rotation and secure configuration;
  any other credential rotation must be named explicitly with its consumers.
- The approved isolated logical database/photo restore destinations and temporary
  one-off-job execution, with any new paid database/plan change separately named
  if existing capacity/permissions prove insufficient.
- The 7-day/4-week/3-month checkpoint selection, indefinite referenced-photo
  preservation during the pilot, delivered-alert tests, hosted scheduled run and
  recovery verification, physical iPhone checks, and restricted synthetic
  customer-to-courier acceptance while invitations stay closed.

Approval of preparation alone is not deployment or spending approval. Until the
applicable approval is recorded, all production/resource/credential operations
above remain blocked. After an approved release, report exact passed evidence
and any failed or unperformed gate; open invitations only when every required
gate has actually passed.
