#!/usr/bin/env python3
"""Explicit, fail-closed SQLite -> PostgreSQL cutover tools for the unpaid pilot.

This tool never reads or changes the running backend's database environment.
Target connections use CL_CUTOVER_DATABASE_URL, with an explicit host/database
identity check. A snapshot requires stopped writers; migrations/import require
--apply. No command resets a database, downgrades schema, or overwrites a bundle.
See docs/POSTGRES_CUTOVER.md for the approval and rollback boundaries.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine, make_url


HEAD = "0005_quotes_idempotency"
LEGACY_HEAD = "0004_pricing_snapshot"
ROOT = Path(__file__).resolve().parents[1]
TABLE_COLUMNS = {
    "users": "id email password_hash role created_at".split(),
    "courier_profiles": (
        "id user_id transportation_mode max_weight_lb max_length_in max_width_in "
        "max_height_in max_volume_cu_ft capabilities is_active"
    ).split(),
    "quotes": (
        "id user_id kind details_hash pricing_conditions pricing_snapshot price_total "
        "eta_min distance_miles distance_estimated distance_source tier "
        "pricing_engine_version created_at expires_at"
    ).split(),
    "orders": (
        "id user_id assigned_courier_id origin destination pickup_lat pickup_lng "
        "dropoff_lat dropoff_lng vehicle item_type delivery_requirements quantity "
        "weight_lb length_in width_in height_in price eta_min distance_miles "
        "distance_estimated distance_source weather traffic surge_multiplier "
        "pricing_engine_version pricing_snapshot quote_id idempotency_key request_hash "
        "status created_at assigned_at completed_at"
    ).split(),
    "delivery_proofs": (
        "id order_id uploaded_by_user_id storage_key content_type size_bytes sha256 created_at"
    ).split(),
    "reward_events": "id user_id order_id type points reason created_at".split(),
}
NEW_ORDER_COLUMNS = {"quote_id", "idempotency_key", "request_hash"}
JSON_COLUMNS = {"capabilities", "delivery_requirements", "pricing_conditions", "pricing_snapshot"}
BOOL_COLUMNS = {"is_active", "distance_estimated"}
DATETIME_COLUMNS = {"created_at", "assigned_at", "completed_at", "expires_at"}
FLOAT_COLUMNS = {
    "max_weight_lb", "max_length_in", "max_width_in", "max_height_in", "max_volume_cu_ft",
    "pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng", "weight_lb", "length_in",
    "width_in", "height_in", "price", "price_total", "distance_miles", "surge_multiplier",
}


class CutoverError(RuntimeError):
    """A condition that must be resolved before any further cutover action."""


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_value(column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in JSON_COLUMNS:
        return json.loads(value) if isinstance(value, str) else value
    if column in BOOL_COLUMNS:
        if value not in (0, 1, False, True):
            raise CutoverError("Source contains an invalid boolean value")
        return bool(value)
    if column in DATETIME_COLUMNS:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        # The reviewed SQLite application writes UTC; SQLite discards tz offsets.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if column in FLOAT_COLUMNS:
        result = float(value)
        if not math.isfinite(result):
            raise CutoverError("Source contains a non-finite numeric value")
        return result
    return value


def normalized_rows(table: str, rows: list[dict]) -> list[dict]:
    normalized = [
        {column: normalized_value(column, row.get(column)) for column in TABLE_COLUMNS[table]}
        for row in rows
    ]
    return sorted(normalized, key=lambda row: row["id"])


def table_manifest(rows: dict[str, list[dict]]) -> dict:
    return {
        table: {"rows": len(values), "sha256": hashlib.sha256(json_bytes(values)).hexdigest()}
        for table, values in rows.items()
    }


def read_sqlite_snapshot(path: Path) -> tuple[dict, dict[str, list[dict]]]:
    """Validate an exact known source shape, without stamping or changing it."""
    path = path.resolve(strict=True)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise CutoverError("SQLite integrity_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise CutoverError("SQLite foreign_key_check failed")
        schema = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if any(row["type"] in {"view", "trigger"} for row in schema):
            raise CutoverError("Unreviewed SQLite views/triggers require manual reconciliation")
        tables = {row["name"] for row in schema if row["type"] == "table"}
        has_history = "alembic_version" in tables
        tables.discard("alembic_version")
        legacy_tables = set(TABLE_COLUMNS) - {"quotes"}
        if tables == legacy_tables:
            legacy = True
        elif tables == set(TABLE_COLUMNS):
            legacy = False
        else:
            raise CutoverError("SQLite table set does not match either reviewed application schema")
        revision = None
        if has_history:
            versions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
            if len(versions) != 1:
                raise CutoverError("SQLite migration history is missing or ambiguous")
            revision = versions[0][0]
            if revision != (LEGACY_HEAD if legacy else HEAD):
                raise CutoverError("SQLite migration history disagrees with the reviewed schema")
        rows: dict[str, list[dict]] = {}
        for table, columns in TABLE_COLUMNS.items():
            if table not in tables:
                rows[table] = []
                continue
            actual = {row["name"] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            expected = set(columns) - (NEW_ORDER_COLUMNS if legacy and table == "orders" else set())
            if actual != expected:
                raise CutoverError(f"Unreviewed columns in SQLite table {table}")
            source_rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
            rows[table] = normalized_rows(table, source_rows)
    # Also rejects NaN in JSON values before a bundle can be accepted.
    table_manifest(rows)
    return {"source_schema": "legacy_0004" if legacy else "current_0005", "source_revision": revision}, rows


def safe_key(value: str) -> Path:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or str(path) != value:
        raise CutoverError("Unsafe or non-canonical proof storage key")
    return Path(*path.parts)


def file_manifest(root: Path) -> list[dict]:
    result = []
    if root.is_symlink():
        raise CutoverError("Proof root must be a real directory")
    if not root.exists():
        return result
    if root.is_symlink() or not root.is_dir():
        raise CutoverError("Proof root must be a real directory")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CutoverError("Proof tree contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise CutoverError("Proof tree contains a special file")
        relative = path.relative_to(root).as_posix()
        safe_key(relative)
        result.append({"path": relative, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return result


def verify_proofs(proofs: list[dict], files: list[dict]) -> None:
    by_key = {item["path"]: item for item in files}
    for proof in proofs:
        safe_key(proof["storage_key"])
        actual = by_key.get(proof["storage_key"])
        if actual is None or any(actual[field] != proof[field] for field in ("size_bytes", "sha256")):
            raise CutoverError("A referenced proof file is missing or does not match database size/hash")


def snapshot(source: Path, photos: Path, bundle: Path) -> dict:
    """Preserve a consistent SQLite backup and every local proof file, including orphans."""
    source = source.resolve(strict=True)
    # Do not copy a live SQLite file: backup() incorporates a committed WAL.
    bundle.mkdir(mode=0o700, parents=False, exist_ok=False)
    backup = bundle / "source.sqlite3"
    backup.touch(mode=0o600)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(backup) as destination:
            original.backup(destination)
    source_info, rows = read_sqlite_snapshot(backup)
    original_files = file_manifest(photos)
    verify_proofs(rows["delivery_proofs"], original_files)
    copied = bundle / "proof-files"
    copied.mkdir(mode=0o700)
    for item in original_files:
        key = safe_key(item["path"])
        target = copied / key
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (photos / key).open("rb") as source_file, target.open("xb") as destination:
            os.chmod(target, 0o600)
            shutil.copyfileobj(source_file, destination)
    if file_manifest(copied) != original_files or file_manifest(photos) != original_files:
        raise CutoverError("Photo files changed during snapshot; stop writers and take a new bundle")
    current_info, current_rows = read_sqlite_snapshot(source)
    if current_info != source_info or table_manifest(current_rows) != table_manifest(rows):
        raise CutoverError("SQLite data changed during snapshot; stop writers and take a new bundle")
    manifest = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **source_info,
        "sqlite_file": "source.sqlite3",
        "sqlite_sha256": sha256_file(backup),
        "tables": table_manifest(rows),
        "proofs": rows["delivery_proofs"],
        "files": original_files,
        "photo_root_existed": photos.exists(),
        "tool_sha256": sha256_file(Path(__file__)),
    }
    manifest_path = bundle / "manifest.json"
    with manifest_path.open("x") as output:
        os.chmod(manifest_path, 0o600)
        json.dump(manifest, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")
    return manifest


def verify_bundle(bundle: Path) -> tuple[dict, dict[str, list[dict]]]:
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("format_version") != 1 or manifest.get("sqlite_file") != "source.sqlite3":
        raise CutoverError("Unrecognized backup bundle format")
    backup = bundle / "source.sqlite3"
    if sha256_file(backup) != manifest["sqlite_sha256"]:
        raise CutoverError("SQLite backup checksum differs from manifest")
    source_info, rows = read_sqlite_snapshot(backup)
    if any(manifest.get(key) != value for key, value in source_info.items()):
        raise CutoverError("Backup schema metadata differs from manifest")
    if table_manifest(rows) != manifest["tables"] or rows["delivery_proofs"] != manifest["proofs"]:
        raise CutoverError("Backup data digest differs from manifest")
    files = file_manifest(bundle / "proof-files")
    if files != manifest["files"]:
        raise CutoverError("Backup photo tree differs from manifest")
    verify_proofs(rows["delivery_proofs"], files)
    return manifest, rows


def restore_sqlite(bundle: Path, database: Path, photos: Path) -> dict:
    """Restore to new paths only; swapping a stopped service to them is a separate action."""
    manifest, _ = verify_bundle(bundle)
    if database.exists() or database.is_symlink() or photos.exists() or photos.is_symlink():
        raise CutoverError("SQLite recovery destinations must both be new; existing data is never overwritten")
    with (bundle / "source.sqlite3").open("rb") as source, database.open("xb") as output:
        os.chmod(database, 0o600)
        shutil.copyfileobj(source, output)
    photos.mkdir(mode=0o700, parents=False, exist_ok=False)
    for item in manifest["files"]:
        key = safe_key(item["path"])
        restored = photos / key
        restored.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (bundle / "proof-files" / key).open("rb") as source, restored.open("xb") as output:
            os.chmod(restored, 0o600)
            shutil.copyfileobj(source, output)
    _, rows = read_sqlite_snapshot(database)
    if sha256_file(database) != manifest["sqlite_sha256"] or table_manifest(rows) != manifest["tables"]:
        raise CutoverError("Restored SQLite data did not match the verified source bundle")
    files = file_manifest(photos)
    if files != manifest["files"]:
        raise CutoverError("Restored proof files did not match the verified source bundle")
    verify_proofs(rows["delivery_proofs"], files)
    return {"sqlite_restored": True, "tables": manifest["tables"], "proof_files": len(files)}


def target_engine(url: str, expected_host: str, expected_database: str) -> Engine:
    # libpq environment defaults (notably PGHOSTADDR) can override the host's
    # network destination even when a URL contains an explicit host.
    if any(key.startswith("PG") for key in os.environ):
        raise CutoverError("Inherited libpq PG* environment is not allowed; use only the reviewed cutover URL")
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg":
        raise CutoverError("Cutover target must use PostgreSQL with psycopg")
    if not expected_host or not expected_database or parsed.host != expected_host or parsed.database != expected_database:
        raise CutoverError("Cutover target does not match the explicitly confirmed host/database")
    # A libpq host/service query parameter can override the URL authority.
    if set(parsed.query) - {"sslmode", "connect_timeout", "application_name"}:
        raise CutoverError("Unreviewed database URL query parameters")
    return sa.create_engine(parsed, poolclass=sa.pool.NullPool, connect_args={"connect_timeout": 10})


def inspect_target(connection: Connection, *, empty_schema: bool = False) -> dict[str, sa.Table]:
    if connection.dialect.name != "postgresql":
        raise CutoverError("Only PostgreSQL targets are supported")
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names(schema="public"))
    if inspector.get_view_names(schema="public") or inspector.get_materialized_view_names(schema="public"):
        raise CutoverError("Target public schema contains unreviewed views")
    if empty_schema:
        if tables or inspector.get_enums(schema="public") or inspector.get_sequence_names(schema="public"):
            raise CutoverError("Migration requires an empty public schema; do not stamp or reset it")
        return {}
    if tables != set(TABLE_COLUMNS) | {"alembic_version"}:
        raise CutoverError("Target table set differs from the candidate migration schema")
    versions = connection.execute(sa.text("SELECT version_num FROM public.alembic_version")).scalars().all()
    if versions != [HEAD]:
        raise CutoverError("Target must be migrated to the reviewed 0005 head")
    metadata = sa.MetaData()
    metadata.reflect(bind=connection, schema="public", only=list(TABLE_COLUMNS))
    reflected = {table: metadata.tables[f"public.{table}"] for table in TABLE_COLUMNS}
    for name, table in reflected.items():
        if set(table.c.keys()) != set(TABLE_COLUMNS[name]):
            raise CutoverError(f"Unreviewed columns in target table {name}")
    return reflected


def preflight(engine: Engine, *, empty_schema: bool = False) -> dict:
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(sa.text("SET TRANSACTION READ ONLY"))
            tables = inspect_target(connection, empty_schema=empty_schema)
            counts = {
                name: connection.scalar(sa.select(sa.func.count()).select_from(table))
                for name, table in tables.items()
            }
    return {"public_schema": "empty" if empty_schema else HEAD, "row_counts": counts}


def migrate_empty_target(engine: Engine) -> dict:
    preflight(engine, empty_schema=True)
    environment = {
        **os.environ,
        "CL_APP_ENV": "development",
        "CL_DATABASE_URL": engine.url.render_as_string(hide_password=False),
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", HEAD],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        # Do not echo SQL, row data, or credentials from a driver exception.
        raise CutoverError("Candidate Alembic migration failed; keep traffic closed and inspect securely")
    return preflight(engine)


def database_rows(connection: Connection, tables: dict[str, sa.Table]) -> dict[str, list[dict]]:
    return {
        name: normalized_rows(name, [dict(row) for row in connection.execute(sa.select(table)).mappings()])
        for name, table in tables.items()
    }


def reset_sequences(connection: Connection, tables: dict[str, sa.Table], rows: dict[str, list[dict]]) -> None:
    # ALTER SEQUENCE RESTART is transactional; setval() would survive a rollback.
    quote = connection.dialect.identifier_preparer.quote
    for name in tables:
        if name == "quotes":
            continue
        sequence = connection.execute(sa.text(
            "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.oid=pg_get_serial_sequence(:table, 'id')::regclass"
        ), {"table": f"public.{name}"}).first()
        if sequence is None:
            raise CutoverError(f"Expected ID sequence missing from target table {name}")
        next_id = max((row["id"] for row in rows[name]), default=0) + 1
        connection.execute(sa.text(f"ALTER SEQUENCE {quote(sequence[0])}.{quote(sequence[1])} RESTART WITH {next_id}"))


def import_bundle(engine: Engine, bundle: Path) -> dict:
    manifest, rows = verify_bundle(bundle)
    with engine.begin() as connection:
        tables = inspect_target(connection)
        connection.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
        connection.execute(sa.text(
            "LOCK TABLE " + ", ".join(f'public."{name}"' for name in TABLE_COLUMNS) + " IN ACCESS EXCLUSIVE MODE"
        ))
        for table in tables.values():
            if connection.scalar(sa.select(sa.func.count()).select_from(table)):
                raise CutoverError("Target contains application data; import never appends, resets, or overwrites")
        for name, table in tables.items():
            typed_rows = [
                {column: datetime.fromisoformat(value) if column in DATETIME_COLUMNS and value is not None else value
                 for column, value in row.items()}
                for row in rows[name]
            ]
            if typed_rows:
                connection.execute(table.insert(), typed_rows)
        reset_sequences(connection, tables, rows)
        imported = table_manifest(database_rows(connection, tables))
        if imported != manifest["tables"]:
            raise CutoverError("Imported data digest mismatch; entire import transaction rolled back")
    return {"revision": HEAD, "tables": imported, "proofs_require_s3_verification": len(rows["delivery_proofs"])}


def verify_target(engine: Engine, bundle: Path) -> dict:
    manifest, _ = verify_bundle(bundle)
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            tables = inspect_target(connection)
            actual = table_manifest(database_rows(connection, tables))
    if actual != manifest["tables"]:
        raise CutoverError("Target differs from the frozen source bundle; do not open traffic or overwrite changes")
    return {"revision": HEAD, "tables": actual, "data_matches_bundle": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("snapshot")
    backup_parser.add_argument("--sqlite", type=Path, required=True)
    backup_parser.add_argument("--photos", type=Path, required=True)
    backup_parser.add_argument("--bundle", type=Path, required=True)
    backup_parser.add_argument("--writers-stopped", action="store_true", required=True)
    verify_parser = subparsers.add_parser("verify-bundle")
    verify_parser.add_argument("--bundle", type=Path, required=True)
    restore_parser = subparsers.add_parser("restore-sqlite")
    restore_parser.add_argument("--bundle", type=Path, required=True)
    restore_parser.add_argument("--sqlite-new", type=Path, required=True)
    restore_parser.add_argument("--photos-new", type=Path, required=True)
    for command in ("preflight", "migrate", "import", "verify-target"):
        target_parser = subparsers.add_parser(command)
        target_parser.add_argument("--expected-host", required=True)
        target_parser.add_argument("--expected-database", required=True)
        if command == "preflight":
            target_parser.add_argument("--empty-schema", action="store_true")
        if command in {"import", "verify-target"}:
            target_parser.add_argument("--bundle", type=Path, required=True)
        if command in {"migrate", "import"}:
            target_parser.add_argument("--apply", action="store_true", required=True)
            target_parser.add_argument("--writers-stopped", action="store_true", required=True)
    arguments = parser.parse_args()
    engine = None
    try:
        if arguments.command == "snapshot":
            manifest = snapshot(arguments.sqlite, arguments.photos, arguments.bundle)
            result = {"bundle_verified": True, "tables": manifest["tables"], "proof_files": len(manifest["files"]),
                      "manifest_sha256": sha256_file(arguments.bundle / "manifest.json")}
        elif arguments.command == "verify-bundle":
            manifest, _ = verify_bundle(arguments.bundle)
            result = {"bundle_verified": True, "tables": manifest["tables"], "proof_files": len(manifest["files"])}
        elif arguments.command == "restore-sqlite":
            result = restore_sqlite(arguments.bundle, arguments.sqlite_new, arguments.photos_new)
        else:
            url = os.environ.get("CL_CUTOVER_DATABASE_URL")
            if not url:
                raise CutoverError("CL_CUTOVER_DATABASE_URL is required; backend database settings are not reused")
            engine = target_engine(url, arguments.expected_host, arguments.expected_database)
            if arguments.command == "preflight":
                result = preflight(engine, empty_schema=arguments.empty_schema)
            elif arguments.command == "migrate":
                result = migrate_empty_target(engine)
            elif arguments.command == "import":
                result = import_bundle(engine, arguments.bundle)
            else:
                result = verify_target(engine, arguments.bundle)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except CutoverError as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Fail closed without logging secrets, SQL parameters, or customer records.
        print(f"STOP: {type(error).__name__}; inspect the protected local artifacts securely", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
