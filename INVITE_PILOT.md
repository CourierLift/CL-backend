# Manual-invite pilot (local candidate, not deployed)

Registration defaults to `CL_REGISTRATION_MODE=manual-invite`, including when
configuration is absent. The only other accepted value is `public`. Invalid
values fail import/startup; no request parameter, role, header or origin selects
the mode. Keep manual-invite for the pilot. Public mode is an explicit isolated
development compatibility option, not an invitation mechanism.

Validated public registrations receive HTTP 403 with the same explanation before
account lookup, password hashing or database writes. Validation still rejects
admin registration and malformed inputs; rate limiting still applies. Existing
login, JWT verification, account roles and quote/order recovery are unchanged.
The gate is not retroactive: it does not disable existing accounts or rotate keys.

## Private operator provisioning

This is a private terminal operation, not an API route. It does not send an email,
issue an invitation link/token, verify email ownership, or offer password reset.
Never temporarily enable public registration to provision participants.

For the current scope use fictional disposable accounts only:

1. Select an explicitly disposable migrated database using `CL_DATABASE_URL`.
   Do not reuse production secrets or a production database.
2. Keep `CL_APP_ENV=development` and `CL_REGISTRATION_MODE=manual-invite`.
3. From this backend checkout, run:

   ```bash
   .venv/bin/python -m backend.provision_invite --email fictional-customer@example.com --role customer
   ```

4. Enter and confirm the password at the hidden terminal prompts. Never put a
   password in arguments, environment variables, logs, a shell history or a ticket.

Roles are restricted to customer, merchant and courier. Courier creation reuses
the registration validation/defaults; optional `--transportation-mode`,
`--capability` (repeatable), and `--max-weight-lb`, `--max-length-in`,
`--max-width-in`, `--max-height-in`, `--max-volume-cu-ft` support capacity
overrides. Duplicate emails (case-normalized) refuse creation without hashing,
overwriting users or changing their roles. Racing duplicates roll back.
The command requires explicit database configuration and echo-free TTY input,
and sanitizes validation/database errors so credentials are not printed.

Provisioning a real participant or delivering credentials needs explicit user
approval and a verified delivery process. An email allowlist is not sufficient
because email ownership is not verified. Self-service invite tokens would be a
separate approved feature with email/role binding, expiry, secure delivery and
atomic one-time consumption.

## Safe tests

The pytest fixture drops/recreates tables. Always isolate it before imports:

```bash
CL_DATABASE_URL=sqlite:///:memory: .venv/bin/python -m pytest -q
CL_DATABASE_URL=sqlite:///:memory: .venv/bin/python -m pytest -q tests/test_invite_gate.py
```

Legacy fixtures explicitly opt into public test compatibility. Gate tests set
manual-invite and separately prove rejection, no lookup/hash/write effects,
input validation, admin exclusion, rate limits, invalid/missing configuration,
no client bypass, private provisioning, preserved login/JWTs, authenticated
quotes, immutable prices, same-key replay, read-only history and account isolation.

## Release boundaries

The `.env.example` and Render template specify manual-invite locally. They have
not been applied to Render. Do not push, merge, deploy, migrate a live database,
change secrets/DNS, create real accounts or send invitations without approval.
Real-browser acceptance and approved production-style PostgreSQL/routing/storage
checks remain separate gates. Payments are unchanged.
