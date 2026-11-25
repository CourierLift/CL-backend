#!/usr/bin/env python3
"""Build the reviewed old source/runtime and age into the candidate artifact.

Build-time preparation only: no database, bucket, secret or hosted configuration
is touched. Runtime restoration uses scripts/start_pilot.py in the same instance
before importing either application. Never run a recovery from a Render shell
and assume its ephemeral files will survive a later deployment.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import postgres_cutover as cutover
from scripts import start_sqlite_rollback as rollback

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SOURCE = "https://github.com/CourierLift/CL-backend.git"
LEGACY_LOCK = ROOT / "ops/pilot-recovery/legacy-requirements.lock"
INVENTORY_CODE = ("import sys,json,importlib.metadata as m; "
                  "print(json.dumps({'python':sys.version.split()[0],"
                  "'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))")


def module_at(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_packages() -> dict[str, str]:
    return {re.sub(r"[-_.]+", "-", name).lower(): version
            for name, version in (line.split("==") for line in LEGACY_LOCK.read_text().splitlines()
                                  if line and not line.startswith("#"))}


def runtime_inventory(python: Path, clean: dict) -> dict:
    result = json.loads(subprocess.check_output(
        [str(python), "-I", "-c", INVENTORY_CODE], env=clean, text=True, timeout=30,
    ))
    result["packages"] = {re.sub(r"[-_.]+", "-", name).lower(): version
                          for name, version in result["packages"].items()}
    if result["python"] != "3.13.4" or result["packages"] != expected_packages():
        raise cutover.CutoverError("Legacy interpreter or installed packages differ from the observed live runtime")
    return result


def verify_runtime(destination: Path) -> dict:
    receipt = json.loads((destination / "build-receipt.json").read_bytes())
    python = destination / "old-python/bin/python"
    if (receipt.get("kind") != "cl-reviewed-recovery-runtime" or receipt.get("format_version") != 1
            or receipt.get("legacy_commit") != rollback.EXPECTED_OLD_COMMIT
            or receipt.get("legacy_lock_sha256") != cutover.sha256_file(LEGACY_LOCK)
            or receipt.get("python_binary_sha256") != cutover.sha256_file(python)
            or receipt.get("age_binary_sha256") != cutover.sha256_file(destination / "bin/age")):
        raise cutover.CutoverError("Legacy recovery runtime receipt or executable integrity differs")
    inventory = runtime_inventory(python, {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"})
    if receipt.get("inventory") != inventory:
        raise cutover.CutoverError("Legacy recovery runtime inventory differs from its build receipt")
    rollback.verify_old_checkout(destination / "old-checkout")
    return receipt


def prepare(destination: Path, *, git_source: str = PUBLIC_SOURCE, age_archive: Path | None = None,
            python_archive: Path | None = None, uv_executable: str | None = None) -> dict:
    if destination.exists() or destination.is_symlink():
        raise cutover.CutoverError("Recovery build directory must be new; do not reuse unverified build caches")
    destination.mkdir(mode=0o700, parents=False)
    destination = destination.resolve()
    checkout = destination / "old-checkout"
    python = destination / "old-python/bin/python"
    clean = {key: value for key, value in os.environ.items()
             if not key.startswith(("GIT_", "CL_", "AWS_", "PG"))
             and key not in {"DATABASE_URL", "PYTHONPATH", "PYTHONHOME"}}
    clean["PYTHONDONTWRITEBYTECODE"] = "1"
    commands = [
        ["git", "init", "--quiet", str(checkout)],
        ["git", "--no-replace-objects", "-C", str(checkout), "-c", "core.hooksPath=/dev/null",
         "fetch", "--depth=1", "--no-tags", git_source, rollback.EXPECTED_OLD_COMMIT],
        ["git", "--no-replace-objects", "-C", str(checkout), "-c", "core.hooksPath=/dev/null",
         "checkout", "--quiet", "--detach", "FETCH_HEAD"],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, env=clean, timeout=180)
        if result.returncode:
            raise cutover.CutoverError("Unable to build the exact pinned legacy source checkout")
    rollback.verify_old_checkout(checkout)
    python_installer = module_at("cl_install_legacy_python", "ops/pilot-recovery/install_python.py")
    legacy_python = python_installer.install(destination / "interpreter", python_archive)
    package_command = (
        [uv_executable, "--cache-dir", str(destination / "package-cache"), "pip", "install", "--python", str(python),
         "--index-url", "https://pypi.org/simple", "--no-deps", "-r", str(LEGACY_LOCK)] if uv_executable else
        [str(python), "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--no-deps",
         "--index-url", "https://pypi.org/simple", "-r", str(LEGACY_LOCK)]
    )
    for command in ([str(legacy_python), "-m", "venv", str(destination / "old-python")], package_command):
        result = subprocess.run(command, capture_output=True, env=clean, timeout=600)
        if result.returncode:
            raise cutover.CutoverError("Pinned old-code runtime dependency build failed")
    installer = module_at("cl_install_age", "ops/pilot-backup/install_age.py")
    installer.install(destination / "bin", age_archive)
    rollback.verify_old_checkout(checkout)
    inventory = runtime_inventory(python, clean)
    result = {
        "kind": "cl-reviewed-recovery-runtime", "format_version": 1,
        "legacy_commit": rollback.EXPECTED_OLD_COMMIT,
        "legacy_requirements_sha256": cutover.sha256_file(checkout / "requirements.txt"),
        "legacy_lock_sha256": cutover.sha256_file(LEGACY_LOCK),
        "age_version": installer.VERSION, "age_archive_sha256": installer.SHA256,
        "age_binary_sha256": cutover.sha256_file(destination / "bin/age"),
        "python_archive_sha256": python_installer.SHA256,
        "python_binary_sha256": cutover.sha256_file(python), "inventory": inventory,
    }
    (destination / "build-receipt.json").write_bytes(cutover.json_bytes(result))
    verify_runtime(destination)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / ".pilot-recovery")
    parser.add_argument("--age-archive", type=Path)
    parser.add_argument("--python-archive", type=Path)
    parser.add_argument("--uv-executable", help="Optional local package installer; the identical observed package lock and inventory checks still apply")
    parser.add_argument("--git-source", default=PUBLIC_SOURCE,
                        help="Defaults to the public canonical repository; offline tests may use a local repository. Commit and every blob remain pinned.")
    args = parser.parse_args()
    try:
        result = prepare(args.destination, git_source=args.git_source, age_archive=args.age_archive,
                         python_archive=args.python_archive, uv_executable=args.uv_executable)
        print(json.dumps({"runtime_prepared": True, "legacy_commit": result["legacy_commit"], "age_version": result["age_version"]}))
        return 0
    except Exception as error:
        print(f"STOP: {type(error).__name__}; recovery runtime build incomplete", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
