"""Private operator workflow: python -m backend.provision_invite --help.

Requires an explicitly selected database and a terminal with hidden password
entry. Does not issue invitation tokens, send messages, or create admins.
"""

import argparse
import getpass
import os
import sys
import warnings


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        # Never echo unknown command arguments: they might contain a password.
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid options. Passwords belong only in the hidden prompt.\n")


def main(argv=None) -> int:
    parser = SafeParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=["customer", "merchant", "courier"], required=True)
    parser.add_argument("--transportation-mode", default=None)
    parser.add_argument("--capability", action="append", default=[])
    for dimension in ("weight-lb", "length-in", "width-in", "height-in", "volume-cu-ft"):
        parser.add_argument("--max-" + dimension, type=float)
    args = parser.parse_args(argv)
    if not os.environ.get("CL_DATABASE_URL", "").strip():
        print("Refusing to provision without an explicit CL_DATABASE_URL.", file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("A terminal is required for hidden password entry; no piped passwords.", file=sys.stderr)
        return 2
    try:
        # GetPassWarning means echo-free input is unavailable; fail closed.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Pilot account password (hidden): ")
            confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            print("Passwords do not match; no account created.", file=sys.stderr)
            return 2
        from .schemas import RegisterIn
        from .settings import settings
        if settings.CL_REGISTRATION_MODE != "manual-invite":
            print("Use manual-invite mode; this workflow never opens registration.", file=sys.stderr)
            return 2
        from .database import SessionLocal
        from .services.account_provisioning import provision_account
        if settings.CL_UNPAID_PILOT and args.role == "courier" and (
            args.transportation_mode not in {"car", "suv", "light_truck"}
            or any(value is None for value in (
                args.max_weight_lb, args.max_length_in, args.max_width_in,
                args.max_height_in, args.max_volume_cu_ft,
            ))
        ):
            print("Pilot courier provisioning requires car/suv/light_truck and all five measured capacity limits. Do not assume vehicle capacity.", file=sys.stderr)
            return 2
        payload = RegisterIn(
            email=args.email, password=password, role=args.role,
            transportation_mode=args.transportation_mode,
            capabilities=args.capability,
            max_weight_lb=args.max_weight_lb, max_length_in=args.max_length_in,
            max_width_in=args.max_width_in, max_height_in=args.max_height_in,
            max_volume_cu_ft=args.max_volume_cu_ft,
        )
        with SessionLocal() as db:
            user = provision_account(db, payload)
            print(f"Provisioned pilot account #{user.id} ({user.role.value}). No invitation sent.")
        return 0
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("Hidden password entry cancelled/unavailable; no account created.", file=sys.stderr)
        return 2
    except Exception as exc:
        # Sanitized messages only: Pydantic and DB exceptions can include input
        # values/connection strings. Never print exception payloads or tracebacks.
        if type(exc).__name__ == "DuplicateAccountError":
            message = "Account already exists; nothing was overwritten."
        elif type(exc).__name__ == "ValidationError":
            message = "Invalid configuration/account inputs; no account created."
        elif isinstance(exc, ValueError):
            message = "Invalid account/password input; no account created."
        else:
            message = "Provisioning failed; check database/configuration privately."
        print(message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
