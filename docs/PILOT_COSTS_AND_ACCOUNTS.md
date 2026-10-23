# Courier Lifts accounts and monthly cost — review before approval

Reviewed 22 October 2026. This is preparation, not spending approval. No paid
resources, credentials or production settings were created or changed. The
additional **$10/month** discussed for the pilot is a planning target, not a
hard limit or authorization to spend.

## Recommendation

Keep the existing Render web service, static frontend and PostgreSQL database.
Use **private, versioned S3 Standard buckets in AWS Oregon (`us-west-2`)** for
proof photos and separately encrypted backups, plus two small Render cron
services in the existing Oregon workspace. This fits the already prepared S3
storage adapter and its immutable version/checksum recovery checks. It needs no
new always-on application server, queue or managed database.

The proposed AWS account is **not yet selected or verified**. Read-only connector
discovery found no usable installed general-purpose S3/IAM account connection;
it did not establish that the user has no AWS account. Public AWS rates were
retrieved anonymously. No AWS session, bucket inventory or free-credit
eligibility has been assumed. The remaining account choice is: use an existing
user-controlled AWS account, or approve establishing one. If an existing account
is selected, its ID, payer, Oregon bucket availability and access settings need
a necessary secure sign-in/read-only check before resources can be bound to it.
Do not paste passwords or access keys into chat.

A Render disk is not currently attached. Adding one would change the service's
storage model and would still need a separate encrypted off-service backup.
Another S3-compatible provider would need account inspection and compatibility
work for the prepared strict VersionId, SHA-256 and conditional-write behavior.
S3 is the simplest suitable setup for the implementation being reviewed; no
unsupported provider is being presented as already verified.

## Observed existing accounts and resources

The sanitized observation is in
[`render-account-readonly-20261022.json`](../evidence/render-account-readonly-20261022.json).
It records the parent session's read-only dashboard inspection. It does not
repeat the completed database connectivity/schema check.

| Resource | Account / exact identifier | Existing approximate monthly cost |
| --- | --- | ---: |
| Render workspace | `tea-d20l5i7gi27c73coe8a0`, My Workspace, Primary connection, Hobby | $0 workspace fee |
| Backend | `srv-d2tbhgmuk2gs73cneer0`, CL-backend, Oregon, 0.5 CPU / 512 MB, one instance | $7.00 |
| PostgreSQL compute | `dpg-dauihb893c1s73ecnihg-a`, courierlifts-db, Oregon, 0.1 CPU / 256 MB | $6.00 |
| PostgreSQL allocated storage | 1 GB, separate storage charge visible on this account | $0.30 |
| Frontend | `srv-d25d0k49c44c73d6msb0`, courierlifts-web static site | $0.00 |
| **Courier Lifts existing subtotal** | Before usage overages/tax | **$13.30** |

The same workspace also has existing NavGlass compute and a 1 GB disk totaling
about **$7.25/month**. Those remain untouched. The approximate steady monthly
baseline for the entire existing workspace is therefore **$20.55**, before
usage overages and this proposal. Free staging/test and suspended legacy
services showed no additional active compute charge. The billing page's
month-to-date **$8.91** and projected current-month **$14.90** reflect timing in
the current billing period; neither is the steady monthly forecast.

The account showed Hobby's **5 GB shared monthly outbound allowance** and
**500 included pipeline minutes**, with 20 MB and 6 minutes used at the observed
time. Those observations do not reserve the allowance for Courier Lifts. The
notifications UI showed Email / Only failure notifications. Actual recipients,
new-service inheritance and delivered test alerts remain release gates.

## Proposed resources and account binding

| Proposed resource | Exact scope / planned name | Approval boundary |
| --- | --- | --- |
| Private proof bucket | `courierlifts-pilot-proofs-<approved-12-digit-account-id>-us-west-2` | AWS account and final available name must be confirmed before creation |
| Encrypted backup bucket | `courierlifts-pilot-backups-<approved-12-digit-account-id>-us-west-2` | Separate bucket and scoped identities; no public access |
| Daily backup cron | `courierlifts-pilot-backup`, same Render workspace / Oregon, 0.5 CPU / 512 MB, `0 7 * * *` UTC | New paid service, at least $1/month |
| Independent hourly monitor | `courierlifts-pilot-backup-watch`, same workspace / Oregon, same compute, `15 * * * *` UTC | New paid service, at least $1/month; daily retention also runs here |
| Isolated recovery bucket | `courierlifts-pilot-recovery-<approved-12-digit-account-id>-us-west-2`, or a separately approved unique suffix if occupied | Empty, private, versioned; no existing objects may be overwritten |
| Isolated recovery database | `courierlifts_recovery_<approved-checkpoint-id>` on the existing database instance, subject to capacity/permission check | No new paid database assumed; any needed plan increase/new instance requires approval |
| Recovery execution | One-off job using the reviewed backup cron artifact and temporary scoped recovery environment | Per-second compute; observed to completion because one-off failures do not generate Render email alerts |

Bucket names are intentionally templates until the AWS account is known; they
are not existing resources. Same-account separate buckets improve permission
separation, but do not create an independent cross-account disaster boundary.
Versioning and SSE-S3 protect stored objects; `age` adds client-side encryption
to database/photo backup artifacts with the private recovery key held offline.
Proposed accountable recovery owner: **Will (Courier Lifts owner)**, subject to
explicit acceptance and an agreed delivered alert destination. No unnamed team
or uncontacted deputy has been treated as having accepted recovery duties.

## Rates and what is included

Current public rates and source identifiers are recorded in
[`pricing-2026-10-22.json`](../ops/pilot-storage/pricing-2026-10-22.json).
The S3 price-list feeds include publication dates and downloaded SHA-256 values.
The main sources are [AWS S3 pricing](https://aws.amazon.com/s3/pricing/),
[Render pricing](https://render.com/pricing),
[Render cron jobs](https://render.com/docs/cronjobs), and
[Render outbound bandwidth](https://render.com/docs/outbound-bandwidth).

| Component | Planning rate / treatment |
| --- | --- |
| S3 Standard Oregon storage | $0.023 per GiB-month, including current objects, retained encrypted copies and noncurrent versions |
| S3 PUT / COPY / POST / LIST | $0.005 per 1,000 requests |
| S3 GET / HEAD | $0.0004 per 1,000 requests |
| S3 outbound transfer | Conservative paid rate $0.09 per GiB; no account-specific free allowance assumed |
| Render cron compute | $0.00016/minute on 0.5 CPU / 512 MB, at least $1 per cron service each month |
| Render browser responses | $0.15 per GB above the shared workspace's 5 GB allowance |
| Render service-to-S3 traffic | Same-region S3 traffic is exempt from Render outbound charges; private in-region Render database traffic is also not charged as public outbound |
| SSE-S3 / `age` | No KMS key/request charge or paid encryption service proposed |
| Built-in failure notifications | Existing Render email channel; no additional paid alerting service proposed |

AWS lists 100 GB/month of free internet transfer aggregated across its services
and regions, plus same-region AWS-service transfer exemptions. The chosen
account's available allowance and actual Render/S3 routing are not verified, so
the conservative total charges all modeled S3 outbound bytes. A free allowance
or eligible route can lower the invoice. Render's S3 outbound exemption does
not by itself prove AWS's side of the transfer is free. Browser photo responses
from Render still consume Render's shared outbound allowance.

## Reproducible monthly scenarios

Run from the backend repository:

```sh
python ops/pilot-storage/cost_model.py \
  --output ops/pilot-storage/cost-estimates-20261022.json
```

The calculations and all inputs are in
[`cost-estimates-20261022.json`](../ops/pilot-storage/cost-estimates-20261022.json).
They assume 500 deliveries per 30-day month, one saved photo each, uniform
growth, a 100 MiB compressed database dump, a conservative upper bound of 14
retained checkpoints and 1% ciphertext-size allowance. A 1 MB or 4 MB photo is a
**stored-size planning input**, not a measured hosted average or a new upload
limit. No assumption of “all photos stay tiny after compression” is needed.

| Monthly component | First month, 1 MB photos, 1,500 views | Month 12, 1 MB photos, 1,500 views | Month 12, 4 MB photos, 2,000 views |
| --- | ---: | ---: | ---: |
| Existing Courier hosting | $13.30 | $13.30 | $13.30 |
| Two cron services | $2.00 | $2.00 | $2.00 |
| S3 storage | $0.04 | $0.28 | $1.02 |
| S3 requests, including verification and monitoring | $0.03 | $0.30 | $0.30 |
| S3 outbound, no free allowance assumed | $0.49 | $0.78 | $1.58 |
| Additional Render browser outbound | $0.00 | $0.00 | $0.60 |
| **Courier Lifts total** | **$15.86** | **$16.66** | **$18.80** |
| **Increase above existing $13.30** | **$2.56** | **$3.36** | **$5.50** |
| **Entire workspace plus proposal, including unchanged NavGlass** | **$23.11** | **$23.91** | **$26.05** |

The Render browser line reserves 1 GB/month of the shared allowance for other
responses, leaving 4 GB for these photo views. If other services have already
used the entire allowance, add approximately **$0.23 / $0.23 / $0.60** to the
three respective Courier totals. If all modeled AWS transfer is free on the
selected account/route, the respective Courier totals fall to approximately
**$15.37 / $15.88 / $17.22**. These are transparent scenarios, not bill promises.

Backup runtime is budgeted at 10 minutes/day; monitor runtime at 1 minute/hour
plus 10 minutes/day for retention. Both remain below their individual $1
minimums in these examples. If actual runs are slower, the formula uses the
larger of actual compute charge and the minimum. Hosted runtime must be measured.

### Storage and traffic grow with saved history

The 7 distinct daily / 4 weekly / 3 monthly retention policy keeps their union,
at most 14 database checkpoints. It does **not** make 14 full copies of every
photo. An unchanged proof is uploaded to the encrypted archive once, and future
checkpoints reference that version. Current saved photos and encrypted archive
photos have no automatic age deletion. They therefore grow with cumulative
delivery history. The month-12 example holds an average of 5,750 photos during
that month and 6,000 by month end; it does not pretend storage resets monthly.

The model includes source-photo reads on first archival, full readback of each
fresh encrypted upload, daily dump/manifest/marker readback, browser photo
fetches, and verification HEAD requests for existing archives. It also includes
the growing checkpoint metadata: the hourly monitor reads only the newest
complete marker, while daily retention reads all retained markers. Each marker
has per-photo references, budgeted at 512 bytes each; encrypted manifests are
budgeted at 1 KiB per proof. These are measured-input candidates for the first
hosted run, not exact serialization guarantees.

Extra noncurrent versions, failed-run orphan objects, recipient rotation,
repeated network retries, unexpected traffic, paid pipeline overages and taxes
can increase the total. The ordinary application and backup writer cannot
delete archived versions merely to enforce a budget. Cost alerts are not a
spending cap and are not treated as configured until tested. Review bill and
bucket size after the first successful hosted cycle, then regularly as part of
the recovery owner's operating review; routine daily manual backups are not
required by this design.

### Recovery usage is additional

An isolated full recovery incurs read requests, photo/dump transfer and one-off
compute. The model estimates a 30-minute one-off job and archive download plus
two restored-photo verification reads. Without an AWS free allowance, the
one-time increment is about **$0.14** for the first-month scenario, **$1.58** for
the month-12 1 MB scenario, or **$6.12** for the larger-photo scenario. Keeping
the additional recovery photo copy for a whole month adds approximately
**$0.01 / $0.13 / $0.51** respectively. Real runtime, repeated verification and
retention duration change those figures.

These estimates do not purchase or price a new recovery database. Recovery on
the existing instance is contingent on safe capacity and access. Render's
point-in-time recovery creates a new instance, so any such paid recovery
resource needs the applicable approval and a revised cost statement.

## Decision and activation gates

The release approval must bind the AWS account and final bucket names, approve
the two paid cron services and usage-based S3 charges, accept the named owner,
and authorize the coordinated credential configuration/migration/deployment.
The prepared $10 extra planning target is plausible for the illustrated pilot
usage but cannot serve as a hard cloud-billing limit.

Automation remains **prepared, not operational** until a real scheduled backup
finishes, independent missed-run detection and failure alerts are received, and
an isolated hosted restore verifies database rows, proof hashes and saved
history. Physical iPhone checks and hosted full-delivery acceptance are separate
release gates. Keep invitations closed until those gates pass.
