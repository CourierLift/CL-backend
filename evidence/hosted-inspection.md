# Read-only hosted inspection — October 21, 2026 America/Chicago

- courierlifts.com returned 200 with orange/charcoal theme; live HTML references
  index-CRHZm-ZX.js and index-7hZk4HBD.css. See frontend rollback asset hashes.
- Existing frontend deployment is dep-dbcdihbrjlhs73bl4ne0 at 39bdc934.
- Existing backend /health returned 200 with env=development; /ready returned 404.
- courierlifts-db dpg-dauihb893c1s73ecnihg-a is available on PostgreSQL 16,
  1 GB, with external access disabled. It was not altered.
- Read-only connector query for database identity and table presence was refused
  because its IP allowlist is empty. No IPs were added, no credentials were
  retrieved, and no production migrations or writes occurred. Verify from an
  authorized existing host; do not open the database publicly for inspection.
- Hosted application DB integration, installed revision, backup retention/restore,
  S3 durability/retrieval and real device acceptance remain unverified.
- No hosted fictional accounts/orders were created. No paid APIs, location
  collection, staging recreation or NavGlass changes occurred.
