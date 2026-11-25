# Automated encrypted pilot backups — prepared, not operational

This is a reviewable implementation and operating plan. No cron service, bucket,
identity, database role, credential or production setting has been created or
changed by this preparation. It is **not operational** until an approved real
scheduled run, delivered failure/staleness notifications and a real isolated
restore have all been verified. The additional $10/month figure is a planning
target, not spending authorization or a guaranteed bill.

The application stays in the integrated unpaid pilot. The published theme,
payments-disabled behavior and NavGlass remain outside this change. PostgreSQL
recovery always uses compatible candidate code. Old SQLite code must never be
pointed at PostgreSQL; the separately preserved original SQLite checkpoint is
only applicable within the pre-cutover recovery boundary.

## What is prepared

`ops/pilot-backup/pilot_backup.py` implements four commands:

| Command | Purpose | Required role / secret |
| --- | --- | --- |
| `run --allow-writes` | Produce a consistent encrypted database/photo checkpoint | Read-only PostgreSQL backup login, S3 writer identity, public age recipient |
| `freshness` | Exit nonzero if no completed backup exists, the latest is over 26 hours old, or its timestamp is over 5 minutes in the future | S3 monitor identity; no database credential or decryption key |
| `retention [--apply]` | Select retained checkpoints; optional automatic pruning of expired checkpoint versions | Separate S3 retention identity; no database credential or decryption key |
| `restore ... --apply` | Recover into an isolated empty database and new local photo directory, then write a verified receipt | Read-only S3 recovery identity, explicit recovery database login, offline private age identity |

The command-line approval flags record an already-authorized operation. They do
not grant authorization. The current task authorizes local synthetic testing
only. No command starts the application, changes the running app database URL,
creates a cloud resource or sends messages.

## One database snapshot, matching photos

The runner opens a PostgreSQL 16 repeatable-read, read-only transaction. Within
that transaction it validates the exact `0005_quotes_idempotency` application
schema, reads every application row for canonical per-table digests and reads
the proof metadata. It exports the transaction snapshot and runs PostgreSQL 16
`pg_dump --snapshot=... --format=custom --no-owner --no-acl` while the exporting
transaction is still alive. The manifest and dump therefore represent the same
visible rows even while application writes continue. Unreviewed extra schemas,
tables, columns, views or public routines stop the run. The implementation uses
a full database dump after those schema checks; it does not drop/recreate an
existing `public` schema during restore. PostgreSQL documents the snapshot
lifetime and synchronized-dump option in [snapshot synchronization](https://www.postgresql.org/docs/16/functions-admin.html#FUNCTIONS-SNAPSHOT-SYNCHRONIZATION)
and [pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html).

For each proof in that frozen snapshot, the first successful archive reads the
S3 object, requires a non-null immutable `VersionId`, and checks content type,
byte size and SHA-256 against the database. The exact source version is recorded.
The photo is age-encrypted and uploaded once under a key derived from the
recipient fingerprint, original storage-key hash and plaintext hash. Subsequent
backups reuse that verified archive using its immutable version and full-object
S3 SHA-256 checksum. They do **not** download all historical source photos every
day. A new key or changed content produces a new archive. A source object that
disappears before its first successful archive makes the run fail; it is never
silently omitted. Already archived versions remain restorable even if the
runtime bucket later changes or loses its current object.

Every upload uses `If-None-Match: *`, a submitted SHA-256 checksum, SSE-S3 and an
expected bucket owner. Fresh ciphertext is downloaded once to verify the exact
uploaded bytes; reused archives use version-pinned checksum HEAD requests.
The bucket policy must enforce conditional creation; IAM must separate the app
and backup identities. See AWS's [conditional-write enforcement](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html)
and [HeadObject version/checksum behavior](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html).

The database dump and its manifest are encrypted before upload. The final
`complete.json` marker is written only after both ciphertexts and every
referenced photo archive have been verified. Partial uploads have no valid
completed checkpoint and cause a nonzero exit. The marker contains only
checkpoint time/IDs, opaque archive keys and version/checksum/size references;
customer rows and original proof paths remain inside encrypted artifacts.

This is a daily logical backup, not continuous PostgreSQL WAL/PITR. A daily
schedule provides a nominal recovery point of up to 24 hours before the incident;
the 26-hour freshness threshold allows job runtime/scheduling variance. The
unavoidable PostgreSQL sequence high-water mark can advance independently of
row snapshots. Restore preserves dumped sequence state and verifies that the
next generated IDs cannot collide with preserved rows. It does not invent
orders, reprice deliveries, rehash passwords or regenerate customer history.

## Encryption, authentication and ownership

The reviewed tool is official **age v1.3.2**, using a native X25519 public
recipient, not a custom encryption scheme. `install_age.py` accepts only the
official Linux amd64 release archive with SHA-256
`cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10`.
See the [official release](https://github.com/FiloSottile/age/releases/tag/v1.3.2)
and [age usage](https://github.com/FiloSottile/age#usage).

Generate the real recovery identity offline with `age-keygen`. Verify a trial
encrypt/decrypt and record the public recipient independently before approved
credential configuration. Put only that public recipient in scheduled jobs.
Store the private identity in the owner's secure recovery vault, with an
independent offline recovery copy. Never put it in Git, an image layer, the
ordinary backup cron environment, app settings, build logs or the backup
bucket. Recovery reads a private regular identity file; group/world-readable
files and symlinks are rejected. Key rotation requires preserving old private
identities until all archives encrypted for them are legitimately retired;
changing the recipient creates a separate archive namespace and extra storage.

Encryption protects confidentiality and detects damaged ciphertext. Anyone
knowing a public recipient can create new encrypted files, so it does not by
itself establish who authored a checkpoint. Successful backup output records
the complete marker's **key, VersionId, SHA-256 and size**. Restore requires that
independently recorded reference; it never trusts a newer current version just
because it shares the checkpoint key. Record the reference in protected job
evidence at the successful run. The scoped credentials, immutable creation
policy, approved exact checkpoint reference and trusted application revision
are part of the recovery trust boundary.

**Proposed recovery owner: Will, the Courier Lifts owner, subject to acceptance
in the coordinated release approval.** The template's
`REQUIRED_NAMED_RECOVERY_OWNER` is intentionally rejected by validation until
that decision is made concrete. The owner accepts responsibility for holding
the recovery key, receiving failure and staleness alerts, initiating recovery
and recording restore results. A backup job is not an accountable human owner.
Confirm the actual alert recipient and receipt during activation; an email
login alone does not prove notification delivery. A named deputy can be added
later, but is not a separate prerequisite for this pilot.

## Scheduling and alerts

Proposed deployment is a daily backup cron plus an independent hourly monitor
cron, both in the existing Render workspace/Oregon region. The monitor runs
`freshness` before retention. It detects a missed daily job even if the daily
process never starts. Hourly freshness lists the completion keys and downloads
only the newest fixed-format UTC checkpoint ID's marker, pinned to its exact
object version. It validates that marker's contents, identity and timestamp;
it fails on an invalid newest marker rather than trying an older successful
one. Daily retention still reads all completion markers and verifies retained
payload references. This avoids downloading every retained photo-reference
list every hour. Render's failure notifications handle a nonzero backup
or monitor exit. The scripts emit a short JSON failure category without SQL,
driver credentials, private key material or customer row values. Exceptions
are never converted into success.

The root release plan owns the reviewed cron schedules/container, current
pricing and actual notification-recipient setup. It proposes two cron services
with a combined $2/month minimum before storage, requests or excess runtime;
this is not a resource-creation instruction. See [Render cron jobs](https://render.com/docs/cronjobs)
and [notifications](https://render.com/docs/notifications). Actual scheduled
execution, alert delivery and missed-run detection remain acceptance gates.
Daily manual execution is not part of the proposed operating dependency.

## Retention without broken photo references

The retention command keeps the newest successful checkpoint in each of the
latest **7 distinct UTC days, 4 ISO weeks and 3 calendar months** for which a
successful checkpoint exists. It retains their union (at most 14 checkpoints),
not three duplicate copies. Multiple runs on one day do not consume multiple
daily slots. Gaps do not cause the last older valid copies to be discarded.
Retention refuses to prune if the latest completion is absent, stale or dated
in the future, and verifies all retained ciphertext references first. Shared
photo references are deduplicated within each verification pass to avoid
repeating the same HEAD request for every retained checkpoint.

Only the expired checkpoint's exact marker, encrypted dump and encrypted
manifest versions may be deleted. The marker is removed first, preventing a
partially pruned checkpoint from looking complete. Retention uses a separate
identity limited to `DeleteObjectVersion` under the checkpoint prefix.
Neither that identity nor the backup writer can delete photo archive versions.

**Encrypted photo archives have no automatic age expiration.** This preserves
all references in retained checkpoints and the current database, including an
old photo still shown in a customer's saved delivery history. S3 lifecycle
rules must not expire photo versions or delete them merely because a database
checkpoint is older than seven days. A future garbage collector would need a
consistent current-DB reference set, all retained manifests, race protection
and its own reviewed tests. It is intentionally not implemented here.

Consequently photo storage grows with unique saved photos and recipient/key
changes. Monitor archive bytes, request volume and costs against the proposed
budget; the $10 target is not a hard cap. Failed-run partial dump/manifest
objects can also remain until reviewed orphan cleanup; they are never selected
for restore. Resolve failure alerts rather than allowing repeated failed runs
to accumulate. The same-region deployment avoids repeatedly transferring all
historical photo bytes; retained-reference checks still incur HEAD requests.

## Credential and configuration contract

Copy `ops/pilot-backup/config.template.json` to the approved configuration
location only after the bucket names, AWS owner account, exact PostgreSQL host,
public recipient and named recovery owner are reviewed. `source_port` is an
explicit integer (5432 for the existing hosted PostgreSQL service); a URL with
a different port is rejected before cloud writes. No secret belongs in
that JSON. The scheduled database URL is `CL_BACKUP_DATABASE_URL`; recovery uses
`CL_BACKUP_RESTORE_DATABASE_URL`. Neither falls back to `CL_DATABASE_URL` or
`DATABASE_URL`. Host, port and database must match explicit expectations, inherited
`PG*` overrides are rejected, and subprocess credentials are passed through a
short-lived private password file, never a command-line URL.

Role-specific AWS variables are `CL_BACKUP_<ROLE>_AWS_ACCESS_KEY_ID`,
`CL_BACKUP_<ROLE>_AWS_SECRET_ACCESS_KEY` and optional
`CL_BACKUP_<ROLE>_AWS_SESSION_TOKEN`, where `<ROLE>` is `WRITER`, `MONITOR`,
`RETENTION` or `RECOVERY`. The app's AWS variables and default credential chain
are not reused. The S3 endpoint and region are explicit; all bucket operations
include the approved expected owner. The runner also checks the actual source
and backup bucket locations and refuses anything outside `us-west-2`; an SDK
redirect to another region cannot silently change the approved resource or
transfer-cost assumptions. Monitor, retention and recovery read identities have
the narrowly scoped `GetBucketLocation` permission needed for that check.

The `.template.json` IAM policies and `database-reader.template.sql` in
`ops/pilot-backup/` are preparation only. The database backup login has SELECT
on the six application tables, Alembic history and sequences, with read-only
transactions; it has no insert/update/delete/schema/role permission. The
runtime proof identity, writer, monitor, retention and recovery identities
remain separate. Recovery upload/startup receipt permissions are specified by
the separate recovery bootstrap preparation. The daily writer's archive
permissions are restricted to `checkpoints/*` and `photos/*`; it cannot read or
publish legacy recovery bundles or startup recovery receipts. No real IAM or SQL changes have
been executed.

## Exact restore and before-start handoff

Keep the service in maintenance, stop other writers, and choose an approved
checkpoint. After new PostgreSQL writes, take a fresh consistent checkpoint
where the incident permits it; reverting to the original SQLite snapshot would
lose those writes. Preserve the impaired database and photo bucket for diagnosis.
Prepare a distinct empty logical recovery database and a new photo namespace.

```sh
python ops/pilot-backup/pilot_backup.py restore \
  --config /secure/approved-backup-config.json \
  --checkpoint-id APPROVED_CHECKPOINT_ID \
  --complete-reference /secure/approved-complete-reference.json \
  --identity /secure/offline-recovery-identity.txt \
  --target-host APPROVED_RECOVERY_POSTGRES_HOST \
  --target-database courierlifts_recovery_APPROVED_ID \
  --photos-new /private/new-recovery-photos \
  --receipt-new /private/verified-recovery-receipt.json \
  --apply
```

The command refuses the live source database name, populated/unreviewed database
objects, another connected recovery process, existing output directories or
files, and symlink output ancestors. It verifies the independently pinned
complete marker, decrypts the dump/manifest and every referenced photo, checks
every plaintext hash and relationship, then runs `pg_restore --single-transaction
--exit-on-error` into the empty target. It compares all six canonical table
count/hash pairs, the exact proof rows, saved photo files and usable ID sequences.
It never uses `--clean`, drops an existing database or starts the application.
Failures leave the target closed, with no success receipt; occupied partial
targets cannot be silently retried/overwritten.

The receipt is format version 1, kind `cl-pilot-verified-restore`, with the
checkpoint ID, pinned complete-marker reference, canonical decrypted-manifest
hash, exact target host/port/database, `tables`, exact `proofs`, `photo_files`,
photo-root/count/list hash and verification time. `verify_database(engine,
receipt)` is a public helper for the separate before-start wrapper. A local
receipt alone is not proof of durable recovery: the reviewed root recovery
path must publish the verified photos into an approved empty private recovery
bucket, pin a durable receipt, then recheck the database and stored photo bytes
before importing/starting compatible application code. Maintenance remains
enabled until the application acceptance gates pass.

If an incident adopts the recovered database/photo pair, backup coverage must
move with it before reopening. Follow the incident-adoption handoff in
[COORDINATED_PILOT_RELEASE.md](COORDINATED_PILOT_RELEASE.md): rebind normal
application proof permissions, the database reader grants/URL, both cron source
configurations and role-specific IAM. Use a separately approved **fresh backup
bucket** with the existing fixed `courierlifts-backups/v1` prefix, retaining the
old bucket/configuration/private keys for earlier checkpoints. A different prefix
is unsupported, and reusing the old backup bucket after changing the source photo
bucket collides with immutable archive source metadata. Verify a fresh checkpoint,
restore, actual scheduled run and delivered alerts for the adopted pair. A restore
drill leaves the live source's ordinary configuration in place. Neither a green
old-source monitor nor a manual backup establishes new-source automation.

Database dumps do not contain deployment configuration, signing secrets,
offline recovery keys, IAM/bucket policy definitions, Git source or the original
SQLite backup's unreferenced local files. Preserve those separately through the
reviewed release/recovery bundle and secure configuration inventory. Do not
claim that PostgreSQL restoration alone recovers them.

## Verification and activation gates

Offline tests use injected S3 clients and synthetic fixtures. The native drill
uses real PostgreSQL 16 `pg_dump`/`pg_restore` and real age encryption, with
ephemeral synthetic identities and isolated loopback databases. It proves
concurrent-write snapshot consistency, incremental photo reuse, exact table
and proof recovery, a later-write checkpoint restored into another fresh DB,
wrong/corrupt reference rejection, and refusal to overwrite occupied targets.
It does not exercise AWS/Render credentials, actual scheduled execution,
managed restore permissions, notification delivery or a hosted app restart.

Enable the native drill with `CL_BACKUP_TEST_ADMIN_URL` (loopback `/postgres`
only), `CL_BACKUP_TEST_PG_BIN` (PostgreSQL 16 client directory) and
`CL_BACKUP_TEST_AGE_BIN` (age binary with sibling `age-keygen`). Run
`python -m pytest -q tests/test_pilot_backup.py tests/test_pilot_backup_native.py`.
Tests create/drop only their unique `pilot_backup_*` databases; they refuse a
hosted admin URL. Test identities and plaintext artifacts are not release secrets.

The 2026-10-22 isolated rerun passed **60 tests: 55 offline boundary tests and 5
native PostgreSQL/age tests**, with one upstream AnyIO deprecation warning.
See `evidence/automated-backup-validation-20261022.json` for the exact scope.
These fixtures are small. The proposed 512 MB cron plan still needs a measured
hosted run using the actual database and photo inventory; schema digests
currently load table rows into memory. Growing data can require a streaming
digest implementation or an explicitly approved compute increase. No test here
establishes a maximum production dataset size or a hosted recovery time.

Before calling backups operational, record all of the following after explicit
approval: the named owner's acceptance and offline key recovery, real scheduled
completion reference, successful automatic retention, a delivered intentional
failure alert, a delivered stale/missed-checkpoint alert, and a real isolated
restore from the scheduled encrypted checkpoint. Verify both roles' saved
history and durable proof retrieval after a real app restart. The final evidence
must distinguish these hosted results from the local tests above.
