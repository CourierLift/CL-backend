#!/usr/bin/env python3
"""Restore the frozen legacy bundle before starting the exact old SQLite backend.

This is a prepared recovery bootstrap, not a Render rollback button. The reviewed
checkout, protected bundle and protected original environment snapshot must be
retrieved before invoking it in the same instance/start invocation. Maintenance
must remain enabled. It neither fetches credentials/artifacts nor changes hosted
settings. Never use after new PostgreSQL writes without approved reconciliation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.postgres_cutover import CutoverError, restore_sqlite, verify_bundle


EXPECTED_OLD_COMMIT = "7580a9ca95f0d0795b6362e0b638181359f1b3c5"


def verify_old_checkout(checkout: Path) -> None:
    checkout = checkout.resolve(strict=True)
    # python-dotenv 1.1.1 searches parent directories from old settings.py.
    # An ancestor .env is outside git status and could inject settings that
    # were absent from the original process environment snapshot.
    for parent in checkout.parents:
        dotenv = parent / ".env"
        if dotenv.exists() or dotenv.is_symlink():
            raise CutoverError("Rollback checkout has an ancestor .env; exact original environment cannot be guaranteed")
    git_environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git_command = ["git", "--no-replace-objects", "-C", str(checkout)]
    commands = (
        (["rev-parse", "--show-toplevel"], str(checkout)),
        (["rev-parse", "HEAD"], EXPECTED_OLD_COMMIT),
        (["status", "--porcelain", "--untracked-files=all", "--ignored"], ""),
    )
    for command, expected in commands:
        result = subprocess.run(
            [*git_command, *command], capture_output=True, text=True, timeout=15,
            env=git_environment,
        )
        if result.returncode or result.stdout.strip() != expected:
            raise CutoverError("Rollback requires a clean, complete checkout of the exact approved old commit")
    # git status alone trusts assume-unchanged/skip-worktree index flags. Hash
    # every actual tracked file as a Git blob against the pinned commit tree,
    # independently of those flags and of Git clean/smudge filters.
    tree = subprocess.run(
        [*git_command, "ls-tree", "-r", "-z", EXPECTED_OLD_COMMIT],
        capture_output=True, timeout=15, env=git_environment,
    )
    if tree.returncode or not tree.stdout:
        raise CutoverError("Unable to verify the pinned old source tree")
    for entry in tree.stdout.rstrip(b"\0").split(b"\0"):
        metadata, relative = entry.split(b"\t", 1)
        mode, kind, expected_hash = metadata.split()
        path = checkout / relative.decode("utf-8")
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise CutoverError("Pinned rollback tree contains an unsupported source entry")
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != checkout):
            raise CutoverError("Tracked rollback source paths must not be symlinks")
        if not path.is_file() or bool(path.stat().st_mode & 0o111) != (mode == b"100755"):
            raise CutoverError("Tracked rollback source file type or mode differs from the pinned tree")
        data = path.read_bytes()
        actual_hash = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest().encode()
        if actual_hash != expected_hash:
            raise CutoverError("Tracked rollback source file content differs from the pinned tree")


def original_environment(snapshot: Path) -> dict[str, str]:
    if snapshot.is_symlink() or not snapshot.is_file() or snapshot.stat().st_mode & 0o077:
        raise CutoverError("Original environment snapshot must be a private regular file (mode 0600)")
    environment = json.loads(snapshot.read_text())
    if not isinstance(environment, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        or not (key.startswith("CL_") or key == "DATABASE_URL")
        for key, value in environment.items()
    ):
        raise CutoverError("Original environment snapshot contains unsupported keys or values")
    if environment.get("CL_APP_ENV", "development") not in {"development", "test"}:
        raise CutoverError("The old SQLite backend requires its original non-production environment")
    if not environment.get("CL_SECRET_KEY"):
        raise CutoverError("The protected original signing secret is required for exact rollback")
    for key in ("CL_DATABASE_URL", "DATABASE_URL"):
        if key in environment and not environment[key].startswith("sqlite:"):
            raise CutoverError("Original rollback database configuration must be SQLite")
    if environment.get("CL_OBJECT_STORAGE_BACKEND", "local") != "local":
        raise CutoverError("This legacy rollback requires the original local proof storage")
    return environment


def prepare_rollback(checkout: Path, bundle: Path, snapshot: Path, state_parent: Path) -> tuple[dict, Path]:
    verify_old_checkout(checkout)
    preserved = original_environment(snapshot)
    manifest, _rows = verify_bundle(bundle)
    if manifest["source_schema"] != "legacy_0004" or manifest["source_revision"] is not None:
        raise CutoverError("Old-code rollback requires the frozen original legacy SQLite bundle without migration history")
    state = Path(tempfile.mkdtemp(prefix="courier-sqlite-rollback-", dir=state_parent)).resolve()
    database = state / "courier_lifts.db"
    photos = state / "object_storage"
    restore_sqlite(bundle, database, photos)
    # Clear candidate settings and object/database credentials before old code is
    # imported. Keep process/system variables; only the saved original CL_* set
    # may influence this old application, with explicit restored absolute paths.
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("CL_", "AWS_", "PG"))
        and key not in {"DATABASE_URL", "PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(preserved)
    environment.update({
        "CL_DATABASE_URL": f"sqlite:///{database}",
        "CL_OBJECT_STORAGE_BACKEND": "local",
        "CL_OBJECT_STORAGE_PATH": str(photos),
        "WEB_CONCURRENCY": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    verify_old_checkout(checkout)
    return environment, state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-checkout", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--original-environment", type=Path, required=True)
    parser.add_argument("--state-parent", type=Path, default=Path("/tmp"))
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--maintenance-confirmed", action="store_true", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    arguments = parser.parse_args()
    try:
        if not 1 <= arguments.port <= 65535:
            raise CutoverError("Invalid application port")
        environment, _state = prepare_rollback(
            arguments.old_checkout, arguments.bundle, arguments.original_environment, arguments.state_parent,
        )
        print(json.dumps({"restore_verified": True, "old_commit": EXPECTED_OLD_COMMIT, "database_backend": "sqlite", "app_started": False}), flush=True)
        if arguments.prepare_only:
            return 0
        os.chdir(arguments.old_checkout.resolve())
        os.execve(sys.executable, [
            sys.executable, "-m", "uvicorn", "backend.main:app", "--host", arguments.bind,
            "--port", str(arguments.port), "--workers", "1",
        ], environment)
        return 0
    except CutoverError as error:
        print(f"STOP: {error}", file=sys.stderr)
    except Exception as error:
        print(f"STOP: {type(error).__name__}; inspect protected recovery artifacts securely", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
