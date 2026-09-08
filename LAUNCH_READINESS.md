# Courier Lifts Launch Readiness

## Feature freeze

Launch Readiness is active. Product feature expansion is frozen until the launch gate is explicitly cleared.

Do not add sender-choice interest selection, community, gamification, referrals, AI dispatch, route optimization, bidding, price negotiation, advanced analytics, advanced retailer tooling, or cosmetic marketplace expansion during this phase.

The canonical release target remains the existing FastAPI + SQLAlchemy monolith.

## Backend launch gate

### Verified in code / CI

- [x] Alembic migration baseline exists.
- [x] PostgreSQL is the intended production relational database.
- [x] SQLite remains supported for local development/tests.
- [x] Full transaction-core suite passes on SQLite.
- [x] Full transaction-core suite passes on PostgreSQL 16.
- [x] PostgreSQL upgrade -> downgrade -> upgrade migration cycle passes.
- [x] Simultaneous-claim race is covered against PostgreSQL.
- [x] Production refuses insecure/default application secrets.
- [x] Production refuses non-PostgreSQL database URLs.
- [x] Production requires S3-compatible proof storage.
- [x] Production frontend origin is required to be HTTPS and non-localhost.
- [x] Production CORS does not automatically whitelist localhost.
- [x] `/health` provides process liveness.
- [x] `/ready` verifies database connectivity.

### Deployment requirements

- [ ] Provision managed PostgreSQL and set `CL_DATABASE_URL`.
- [ ] Confirm automated PostgreSQL backups and retention policy.
- [ ] Complete one restore drill before public launch.
- [ ] Provision S3-compatible proof storage and set `CL_OBJECT_STORAGE_BACKEND=s3` plus bucket/region configuration.
- [ ] Generate and securely store a new production `CL_SECRET_KEY` of at least 32 characters.
- [ ] Set `CL_FRONTEND_ORIGIN` to the final HTTPS frontend origin.
- [ ] Run `alembic upgrade head` as an explicit release step before starting the new application version.
- [ ] Configure HTTPS/TLS at the deployment edge.
- [ ] Monitor `/health` and `/ready` separately.
- [ ] Centralize application/error logs with request correlation suitable for incident review.
- [ ] Configure alerting for elevated 5xx responses, readiness failures, database saturation, and object-storage errors.
- [ ] Run the canonical sender -> courier staging smoke transaction against production-style infrastructure.
- [ ] Document rollback procedure for application version and database migration failure.

## Launch acceptance

The application is not launch-ready merely because CI is green. Launch acceptance requires the verified transaction core plus successful staging deployment, production configuration validation, backup/restore posture, monitoring, and one production-style smoke transaction.
