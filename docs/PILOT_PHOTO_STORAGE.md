# Durable delivery-proof storage: reviewable release plan

Prepared 22 October 2026. **Preparation only. No AWS account was inspected, no
bucket or identity was created, no credentials were configured, and no hosted
proof was written or deleted.** Apply this plan only as part of the user's
combined approval for migration, credential configuration, and deployment.

## Decision and release blockers

Keep PR #8's existing S3 adapter and authenticated API. Use one private AWS S3
general-purpose bucket, S3 Standard, in `us-west-2` (Oregon), with versioning,
bucket-owner-enforced ownership, all four Block Public Access settings, TLS, and
SSE-S3 default encryption. Reuse an existing approved bucket only after its
ownership, controls, retention rules, and other workloads are checked. AWS
documents the relevant [ownership controls][ownership], [public access
blocks][public-access], and [encryption defaults][encryption].

The following are still unknown and must be resolved in the approval record:

| Item | Concrete proposal / required evidence |
| --- | --- |
| AWS account and payer | Actual 12-digit account ID and authorized owner; do not infer it from Render or GitHub sign-in. |
| Bucket | Actual approved existing name; otherwise propose `courierlifts-pilot-proofs-<account-id>-us-west-2`, subject to availability and explicit new-resource approval. No bucket has been reserved. |
| Runtime identity | Dedicated `courierlifts-pilot-runtime` identity, with only the runtime policy below. Exact IAM ARN and credential mechanism must be recorded. |
| Recovery operator | Will is the proposed backup/recovery and retention owner, subject to his approval, using a separate recovery identity outside the web service. |
| Durable backup destination | Approved private `backups/<backup-set-id>/` prefix, plus the owner's approved independent encrypted copy location. Same-bucket copies alone do not survive account/bucket loss. |
| Credentials | Approved secure Render secret configuration route; no secrets in source, unprotected files, chat, logs, frontend bundles, or browser storage. Keep the deliberately protected original-environment recovery snapshot encrypted in the approved backup destination. Reuse existing authenticated sessions; no repeat database credential entry. |
| Retention owner and budget | Approve the retention and spending proposal below; none is currently configured. |

An empty database or zero-proof snapshot does not prove that photo storage works.
The hosted upload/read/restart/recovery gates below remain required.
The candidate's database readiness response also does not certify S3 access.

## Existing application contract

`backend/routes/proofs.py` already implements these behaviors:

- Only the assigned courier can attach a proof while an order is `in_transit`.
  A delivery cannot complete without its proof metadata.
- Keys are `proofs/<order-id>/<random-uuid>.<extension>`. PostgreSQL must preserve
  `storage_key`, `content_type`, `size_bytes`, `sha256`, proof/order/user IDs, and
  timestamps exactly during the SQLite import.
- The browser reads `GET /orders/{id}/proof` with its ordinary bearer token.
  The order's customer, its assigned courier, or an admin may read it; unrelated
  accounts and anonymous requests cannot. The backend fetches bytes privately,
  verifies size and SHA256 against the database, and responds with
  `Cache-Control: private, no-store` and `X-Content-Type-Options: nosniff`.
- No public bucket, public object ACL, browser AWS key, direct signed image URL,
  website hosting, or S3 browser CORS rule is needed.
- Accepted declared types are JPEG, PNG, and WebP. The published candidate browser
  limits selection to **4 MB (4,000,000 bytes)**; the backend/API default limit is
  **10 MiB (10,485,760 bytes)**. The larger API limit is not an ordinary browser
  upload allowance.
  The current backend does not decode/re-encode image pixels or remove EXIF.
  HEIC is not accepted. Local mocked tests do not prove physical iPhone camera
  conversion, orientation, or privacy behavior; verify real iPhone uploads with
  non-sensitive test images before inviting device users.

The production setting guard continues to require PostgreSQL and S3 storage.
Keep `CL_UNPAID_PILOT=true`, manual invites, and payments disabled. None of the
storage work changes the published theme or NavGlass.

## Bucket and credential configuration for approval

`ops/pilot-storage/bucket-settings.json` is a desired-state document, not a
provisioning command. `bucket-policy.template.json` adds a TLS-only deny. It must
be reviewed together with any existing bucket policy; never blindly replace an
existing bucket's unrelated configuration.

Use S3 default encryption with `AES256`. The current runtime `put_object` call
does not send an encryption header, so a policy that requires that header would
break uploads despite correct bucket default encryption. The preparation upload
utility does send the header. SSE-S3 has no additional encryption fee; this plan
does not require KMS, a CDN, a worker, a disk, replication, or a new web service.
See [AWS's SSE-S3 behavior and pricing][encryption].

Configure the following **only on the compatible candidate backend** after
approval. Never attach PostgreSQL configuration to the old deployed backend.

| Configuration | Approved value |
| --- | --- |
| `CL_OBJECT_STORAGE_BACKEND` | `s3` |
| `CL_S3_BUCKET` | Exact approved private bucket name |
| `CL_S3_REGION` | `us-west-2` (override the current `us-east-1` default) |
| `CL_S3_ENDPOINT_URL` | Absent for AWS S3 |
| `CL_PROOF_MAX_BYTES` | `10485760` |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Dedicated server-side runtime credentials through the approved secret UI, if no supported automatic temporary-credential mechanism exists |
| `AWS_SESSION_TOKEN` | Only if the approved temporary credential mechanism provides it and renews it before expiry |

Prefer renewable temporary credentials where the existing platform supports
them. No Render-to-AWS federation has been verified here; a short-lived session
token with no refresh would cause a delayed production outage. If a dedicated
access key is necessary, record its owner and rotation procedure, test a new key
before retiring the previous one, and never use the AWS root user's keys.

### Least privilege and separate recovery powers

The JSON templates contain deliberately unresolved `__PLACEHOLDERS__`; they are
not ready to apply until exact resources are approved. AWS's [API permission
mapping][permissions] defines the action/resource combinations.

If versioning is enabled for the first time, [AWS recommends allowing 15 minutes
for propagation before object writes][enable-versioning]. Record the activation
time in the release checklist; do not mistake an immediate settings response
for a completed propagation or photo-durability test.

| Template | Identity / scope |
| --- | --- |
| `runtime-policy.template.json` | `GetObject`, `PutObject`, `DeleteObject` on this bucket's `proofs/*` only; explicit deny on `DeleteObjectVersion`. `GetObject` is essential for private history-photo retrieval. |
| `migration-writer-policy.template.json` | Temporary migration operator: versioning check, put, current read, exact-version read on `proofs/*`. Used for copying frozen legacy files; remove afterward. |
| `recovery-read-policy.template.json` | Recovery operator: inspect controls; prefix-scoped list/version-list; read current/exact versions under `proofs/` and `backups/`. No object write or delete. |
| `backup-writer-policy.template.json` | Backup operator: put only inside one named `backups/<backup-set-id>/*` set. Use unique backup-set IDs and conditional writes. |
| `restore-write-policy.template.json` | Separately authorized incident action: put one exact approved proof key. Substitute a literal key; do not widen to `*`. Combine temporarily with recovery read permissions. |

These must be dedicated identities without inherited broad S3 permissions.
Runtime credentials must not receive list, exact-version read, bucket/policy
management, lifecycle, backups, or permanent-version-delete privileges. Bucket
setup/retention administration stays outside the application. AWS [versioning
semantics][versioning] preserve prior versions after an ordinary delete marker;
versioning by itself does not stop a bucket administrator deleting everything.

Before configuration, validate the substituted policies with the account's
approved IAM validation mechanism, confirm the effective permissions, and record
the resulting ARN. No IAM validation against an account has been performed yet.

## Retention, backup, and a database/object checkpoint

Proposed launch retention: keep all linked proofs and their current/noncurrent
versions for the unpaid pilot; configure **no automatic current/noncurrent
expiration**. Review retention and storage cost after 30 days. The three-month
capacity model below is a cost illustration, not permission to delete day-90
history. A later retention change must reconcile database references, retained
database backups, and all object versions before permanent deletion.

Keep the pre-cutover SQLite database, complete local object tree (including
unreferenced files), candidate revision, configuration names, and checksums as
one protected source bundle. A failed database commit can leave an unreferenced
object/version; do not automatically delete possible recovery evidence. The
runtime's ordinary cleanup uses delete markers in a versioned bucket.

For each release and subsequent scheduled backup, bind one database snapshot to
one object manifest. A database backup alone preserves links but not pixels.
An object backup alone cannot reconstruct customer permissions or order history.

1. Freeze all application writers and confirm no in-flight upload, claim,
   delivery, or provisioning request remains. The operator must keep writers
   stopped throughout the coordinated checkpoint. Do not infer a freeze from an
   empty count or from hiding a frontend button.
2. Produce the SQLite backup bundle using `scripts/postgres_cutover.py` before
   any redeploy. It includes `source.sqlite3`, `manifest.json`, and
   `proof-files/<storage_key>`. Preserve every local file; a missing or mismatched
   referenced proof blocks cutover. For later PostgreSQL checkpoints, produce a
   dump and JSON proof metadata from the same frozen database state; the JSON
   envelope is `format_version: 1`, `database_sha256: <dump digest>`, and `proofs`
   containing the exact `delivery_proofs` rows.
3. After approval, copy only referenced legacy proof bytes to S3 without
   overwriting current keys. Verify every uploaded or already-present object
   against the frozen metadata. Preserve unrelated files in the source bundle;
   do not publish them into the application prefix.
4. Save a checkpoint with bucket, owner account, region, original proof metadata,
   immutable S3 `VersionId`, database-backup digest, and canonical source-manifest
   digest. Keep the checkpoint and its checksum with the encrypted database
   backup in an access-separated backup location. A checksum detects accidental
   corruption; it is not a signature against an attacker who can replace both.
5. Restore the database into an isolated disposable database and the exact
   pinned object versions into a new local directory. Run the compatible
   candidate against that restored pair, check counts/IDs/hashes and authorized
   history retrieval, and record the result. Keep the source and newer live data.

The proposed operating procedure requires no new worker or scheduled service:
**Will, subject to his approval, owns backup, recovery, retention review, and the
daily cost check.** He performs one manual coordinated checkpoint at the end of
each pilot day and before every release, using the existing service shell, the
approved protected S3 `backups/<backup-set-id>/` prefix, and an owner-selected
independent encrypted location. The named backup-set identifier is unique; the
database dump, exact-version proof manifest, and verification record travel
together. Record checkpoint time, verified counts/hashes, destination and restore
result without recording credentials or customer data in ordinary logs.

Automated daily backup is **unconfigured**. This proposal creates no cron job,
worker, paid monitoring service, or new Render resource. The independent backup
location and any incremental cost remain approval blockers. The provider's
database recovery window must still be verified separately. Until Will accepts
ownership and the manual procedure, destinations, and hosted restore drill pass,
no production recovery point or recovery time guarantee is claimed. Retain all
launch backup sets until the owner approves a pruning policy; cost grows with
retained versions/copies.

### Prepared tooling and exact commands

`ops/pilot-storage/proof_objects.py` never calls AWS on import. Its default
operations read only. `upload` additionally requires `--allow-object-writes`;
the flag is an operator acknowledgement of already-obtained approval. It does
not grant authorization. All remote calls include the expected bucket owner.

Commands below are templates. Replace only approved non-secret identifiers and
paths; credentials come from the secure operator environment. **None has been
run against a hosted bucket.**

```bash
# AFTER approval, temporary migration credentials, frozen source bundle:
python ops/pilot-storage/proof_objects.py upload \
  --bundle /secure/cutover/source-bundle \
  --bucket APPROVED_BUCKET --expected-account-id APPROVED_12_DIGIT_ACCOUNT \
  --output /secure/cutover/s3-checkpoint.json --allow-object-writes

# Read current objects and bind their versions to a frozen DB backup:
python ops/pilot-storage/proof_objects.py capture \
  --source-manifest /secure/backup/db-proof-manifest.json \
  --bucket APPROVED_BUCKET --expected-account-id APPROVED_12_DIGIT_ACCOUNT \
  --output /secure/backup/s3-checkpoint.json

# Read only: prove all pinned original versions still match:
python ops/pilot-storage/proof_objects.py verify \
  --manifest /secure/backup/s3-checkpoint.json \
  --expected-account-id APPROVED_12_DIGIT_ACCOUNT

# Read only in S3: download exact versions into a NEW local directory:
python ops/pilot-storage/proof_objects.py restore-local \
  --manifest /secure/backup/s3-checkpoint.json \
  --expected-account-id APPROVED_12_DIGIT_ACCOUNT \
  --destination /secure/recovery/restored-proof-files
```

Upload uses a conditional `IfNoneMatch="*"` put. On an existing key, it verifies
the current bytes rather than replacing them. A collision with different bytes
stops the operation. A failed multi-object upload can leave already-verified
new versions; preserve them and retry from the same frozen bundle. No cloud
delete is performed. An existing unversioned `null` object fails verification
and needs a separately reviewed conversion to a versioned object.

Checkpoint verification uses `GetObject` with the recorded `VersionId`, checks
SSE-S3, type, size, and SHA256, bounds reads, and closes response bodies. Local
restore rejects unsafe paths/symlinks, refuses an existing destination, and
publishes the new directory only after every file verifies. Restore this object
root together with the database backup whose digest the checkpoint records.

### Exact incident recovery and rollback

The application reads the current object version and does not store S3
`VersionId` in `delivery_proofs`. If a proof disappears or changes, its endpoint
fails closed; it does not silently show a mismatched image. The recovery manifest
provides the missing historical version mapping without a new application
schema migration.

To recover a specific live proof, freeze writes, preserve the present database
and current object/version metadata, and verify the original version offline.
After explicit incident authorization, temporarily grant `PutObject` for that
exact key, then write the verified original bytes/type as a new current version.
Use a conditional put against the observed current ETag, or `IfNoneMatch="*"`
when no current object exists; a concurrent change must abort. Check through the
authenticated API, record both old/new VersionIds, and revoke the temporary
write permission. Do not permanently delete versions or overwrite later valid
proofs during a broad restore.

For a release rollback **before any new PostgreSQL writes**, follow the backend
cutover runbook's verified frozen-state gate. The prepared
`scripts/start_sqlite_rollback.py` restores and verifies the original
SQLite/photo bundle at new private paths **before importing or starting the old
application**, using the exact approved old checkout and protected original
environment snapshot. Secure retrieval of those artifacts and a supported
hosted pre-start execution mechanism remain an explicit release gate. An ordinary
Render rollback followed by copying SQLite underneath a running process is
prohibited. Keep the matching frontend and maintenance enabled; this is a
**maintenance-only recovery checkpoint**, not permission to reopen ongoing
SQLite writes. Repeating this frozen-bundle bootstrap after new SQLite writes
would rewind them. Do not point the old backend at PostgreSQL.

After any new PostgreSQL application write, including hosted acceptance, an old
SQLite snapshot would lose history: keep the compatible PostgreSQL/S3 backend or
maintenance gate, preserve both new and old data, and reconcile explicitly. A
code rollback must not run Alembic downgrade, delete S3 objects, or silently
restore an older database over newer records. See
[POSTGRES_CUTOVER.md](POSTGRES_CUTOVER.md) for the complete rollback gates.

## Costs for the combined approval

Current public AWS Oregon tariffs were retrieved anonymously on 22 October
2026. Evidence, source publication dates, selected rate entries, and source
digests are in `ops/pilot-storage/pricing-2026-10-22.json`.

| Component | USD unit price used |
| --- | ---: |
| S3 Standard, first 50 TiB | $0.023 per GiB-month |
| PUT/COPY/POST/LIST | $0.005 per 1,000 requests |
| GET / ordinary reads | $0.0004 per 1,000 requests |
| Internet transfer out, first paid 10 TiB | $0.09 per GiB |
| SSE-S3 | No extra encryption fee |

Sources: [S3 pricing][pricing], [Oregon S3 public rate file][s3-rates], and
[Oregon outbound-transfer public rate file][transfer-rates]. AWS storage billing
uses binary GiB. The first 100 GB/month of eligible outbound data is shared
across the AWS account's services/regions; the conservative examples below do
not assume any of it remains available. Same-region exceptions must be verified
for the actual billing path; do not assume Render traffic qualifies.

Calculated examples use 500 new deliveries/month, one photo per delivery, three
months of accumulated proof history charged as if the full end-of-month stock
were present all month, **one additional full proof recovery copy**, and 0.25 GiB
of database/manifest backups. They assume one PUT per new proof and reads only
at the stated monthly volume; retries, additional old versions/copies, recovery
downloads, and older-history reads add usage.

| Scenario | Average photo / monthly reads | Stored capacity incl. recovery copy | S3 cost with no free outbound allowance |
| --- | --- | ---: | ---: |
| Illustrative pilot | 3 MiB / 5 reads per new proof | 9.039 GiB | About **$0.87/month** |
| Backend/API upper bound | 10 MiB / 10 reads per new proof | 29.547 GiB | About **$5.08/month** |

The 3 MiB example fits the browser's 4 MB limit. The 10 MiB example deliberately
models the larger backend/API limit; the candidate browser cannot upload a file
of that size. It is not the expected ordinary browser usage.

The larger-file calculation is `29.546875 × $0.023 + 500/1000 × $0.005 +
5000/1000 × $0.0004 + 48.828125 × $0.09 = $5.078609375`. This is an estimate,
not a provider cap. Render service/database and Render outbound bandwidth,
taxes, existing account charges, additional retention, and independent backup
storage are separate. No new paid disk/service is needed by this proposal.

Proposed approval allowance: authorize only the named storage resources and
necessary request/transfer usage, with an initial **$10/month incremental S3
operating allowance** and review before expanding intake/retention beyond this
model. An owner must check spend and forecast daily during launch and stop new
intake if the forecast would exceed the approved allowance. Alerts and manual
thresholds are not a hard billing cap and must never trigger destruction of
stored delivery history. The user has not approved this allowance or any new
resource yet; exact account/bucket and any independent-backup cost remain open.

## Verification ledger and hosted release gates

Locally available evidence is the existing authorization/integrity tests and
`tests/test_s3_proof_recovery.py`. The latter exercises a versioned in-memory
S3 contract: gated upload, whole-bundle preflight, conditional collision/retry,
metadata checks, pinned recovery after current-version changes, corruption,
unsafe paths/symlinks, existing-target preservation, disabled versioning, and
account mismatch. It makes no hosted storage call. Record the actual run result
in the integrated release report; prior-run counts are not fresh evidence.

Fresh 22 October result: **27 passed** for
`python -m pytest -q tests/test_s3_proof_recovery.py tests/test_unpaid_pilot.py
tests/test_delivery_proof.py` (18 new recovery cases and nine existing cases;
local SQLite, synthetic data, in-memory S3). The default sandbox blocked the
TestClient loopback path; that run was stopped and the isolated suite passed
with local-loopback permission. One upstream Starlette/AnyIO deprecation warning
remains. Installed boto3/botocore `1.43.118` recognizes `IfMatch`, `IfNoneMatch`,
and `ExpectedBucketOwner` for `PutObject`; this is SDK compatibility evidence,
not a live provider authorization test.

After the combined approval, keep customer intake gated until all of these pass:

1. Inspect the actual bucket/account/region/ownership, effective IAM, encryption,
   public access block, versioning, absence of destructive lifecycle rules, and
   retention owner. Validate runtime cannot list/read backups or delete versions.
2. Using the deployed compatible backend and synthetic invited users, create a
   quote/order, claim it as the courier, progress it, upload a non-sensitive
   image, complete delivery, and find the saved delivered record as both users.
3. Confirm anonymous/unrelated-user access and a direct anonymous S3 object URL
   are denied; customer/assigned-courier reads have the original SHA256.
4. Restart/redeploy the backend without changing credentials or objects, then
   verify the same proof and both users' saved histories survive.
5. Produce a coordinated checkpoint; restore the database and the exact pinned
   synthetic proof into an isolated environment. Verify the same relationships,
   hash, authorized reads, and delivery history. Exercise a synthetic object's
   delete/restore scenario only with the approved recovery operator and scope.
6. Test actual iPhone Safari selection/camera behavior and the supported types,
   size feedback, image orientation, and saved-photo display. Do not use real
   addresses or identifiable recipients for this check.

Missing any hosted gate is reported as blocked or failed, never inferred from
the local mock. Payment processing and payouts remain disabled throughout.

[ownership]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/about-object-ownership.html
[public-access]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html
[encryption]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingServerSideEncryption.html
[permissions]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html
[versioning]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html
[enable-versioning]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/manage-versioning-examples.html
[pricing]: https://aws.amazon.com/s3/pricing/
[s3-rates]: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/us-west-2/index.json
[transfer-rates]: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSDataTransfer/current/us-west-2/index.json
