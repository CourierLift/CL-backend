# Courier Lifts MVP Runbook

This runbook applies during Launch Readiness. Feature expansion remains frozen.

## Deployment model

Launch the current MVP as one FastAPI application process/worker backed by managed PostgreSQL and S3-compatible proof storage.

Do not horizontally scale the application during MVP launch. Authentication rate-limit counters and WebSocket tracking rooms are process-local.

Recommended application command after migrations:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
```

## Pre-release gate

Before deploying a candidate:

1. Backend PR CI is green on both SQLite and PostgreSQL.
2. PostgreSQL migration cycle CI is green.
3. Simultaneous-claim test is green.
4. React launch-readiness build is green.
5. Production environment variables have been reviewed without printing secret values.
6. Managed PostgreSQL backup is current.
7. S3-compatible proof bucket is reachable by the deployment identity.
8. `CL_GOOGLE_MAPS_API_KEY` is configured in the backend environment and restricted to the Google Routes API.
9. The production sender flow returns `distance_source=google_routes`; `development_fallback` must never appear in production.

## Release sequence

1. Freeze writes/deployment changes to the release candidate.
2. Record the application commit SHA being deployed.
3. Confirm the most recent database backup and its retention timestamp.
4. Run the reviewed schema migration:

   ```bash
   alembic upgrade head
   ```

5. Start exactly one application worker.
6. Verify liveness:

   ```bash
   curl -fsS "$BACKEND_URL/health"
   ```

7. Verify readiness:

   ```bash
   curl -fsS "$BACKEND_URL/ready"
   ```

8. Deploy the frontend with `BACKEND_URL` set to the canonical HTTPS backend origin and `VITE_COURIER_LIFTS_API_URL=/api`.
9. Verify frontend `/api/health` and `/api/ready` reach the backend through the production proxy.
10. Run a real address quote and confirm the response uses `google_routes` distance rather than the development fallback.
11. Run the staging/production-style transaction smoke test from separate sender and courier sessions.

## Transaction smoke test

Do not mark a release healthy until all of the following are observed from canonical backend state:

1. Sender authenticates.
2. Sender enters real pickup/dropoff addresses and receives backend-authoritative pricing using Google Routes distance.
3. Sender creates one Lift and its pricing snapshot records `distance_source=google_routes`.
4. Courier authenticates in a separate browser/device session.
5. Courier discovers and claims the Lift.
6. A competing claim is rejected if tested.
7. Sender and courier reload and see matching `assigned` state.
8. Courier progresses `picked_up -> in_transit`.
9. Courier uploads proof through the deployed object-storage path.
10. Attempting `delivered` without proof is rejected; with proof it succeeds.
11. Sender and courier start fresh sessions and retrieve the same completed status, assignment, proof metadata, timestamps, and pricing snapshot.

## Google Routes incident

Production address quote/create depends on Google Routes. If Google is unavailable, the API key is invalid/restricted incorrectly, or Google returns no usable route, Courier Lifts returns HTTP 503 rather than substituting fixed mileage.

1. Do not re-enable `CL_DEVELOPMENT_FALLBACK_MILES` in production.
2. Verify Google Routes API status and the backend-only key configuration without printing the key.
3. Confirm the key is enabled/restricted for Routes API use and the project has valid billing.
4. Retry a known-valid address pair.
5. Keep address-based Lift creation unavailable until real route distance is restored.

The Courier Lifts pricing engine remains authoritative for the customer price; Google supplies route distance only.

## Proof-storage incident

If proof upload fails:

1. Do not manually mark the order delivered.
2. Check object-storage credentials, bucket, region, endpoint, and provider health.
3. Confirm the application identity can write the configured bucket.
4. Preserve the order in `in_transit`; the server must continue rejecting completion without proof.
5. Retry proof upload only after storage health is restored.

## Database incident

If `/health` is 200 but `/ready` is 503, treat the application as unavailable for transactions.

1. Check managed PostgreSQL availability and connection limits.
2. Check `CL_DATABASE_URL` configuration without exposing credentials.
3. Do not route new transaction traffic until `/ready` is healthy.
4. Preserve database logs/provider incident data for review.

## Rollback

Application rollback and database rollback are separate decisions.

### Application-only rollback

Prefer rolling the application version back to the last known-good commit when the deployed schema remains compatible.

### Database migration rollback

Do **not** automatically run `alembic downgrade` on a production database containing real transaction/proof/pricing data. Some downgrades are destructive.

For a migration-related production incident:

1. Stop new transaction traffic.
2. Preserve the failed database state and logs.
3. Determine whether a forward-fix migration is safer.
4. If restoration is required, restore from the verified pre-release backup according to the managed database procedure.
5. Re-run `/ready` and the canonical transaction smoke test before reopening traffic.

## Launch decision

CI green is necessary but not sufficient. Public launch requires the remaining unchecked items in `LAUNCH_READINESS.md`, including production infrastructure, backup/restore verification, monitoring/alerting, configured Google Routes credentials, and a successful deployed smoke transaction.
