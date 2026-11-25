#!/usr/bin/env python3
"""Materialize reviewed nonsecret config and run backup or independent watchdog."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=("backup", "monitor"))
    args = parser.parse_args()
    # Requiring the explicit reviewed commit avoids a scheduled job drifting
    # silently to a newer branch or image. Local Docker smoke tests use --help.
    expected = os.environ.get("CL_RELEASE_SHA")
    actual = os.environ.get("RENDER_GIT_COMMIT")
    if not expected or (actual and actual != expected):
        print("STOP: scheduled artifact differs from the approved commit", file=sys.stderr)
        return 1
    try:
        config = json.loads(os.environ["CL_BACKUP_CONFIG_JSON"])
        if not isinstance(config, dict):
            raise ValueError("config")
        with tempfile.TemporaryDirectory(prefix="cl-cron-config-") as temporary:
            path = Path(temporary) / "config.json"
            with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as output:
                json.dump(config, output)
            runner = [sys.executable, str(ROOT / "ops/pilot-backup/pilot_backup.py")]
            command = "run" if args.job == "backup" else "freshness"
            arguments = [*runner, command, "--config", str(path)]
            if args.job == "backup":
                arguments.append("--allow-writes")
            result = subprocess.run(arguments, timeout=3600)
            if result.returncode:
                return 1
            # Independent hourly freshness checks; retention once daily at
            # 08:15 UTC, with separate credentials and a healthy recent backup.
            if args.job == "monitor" and datetime.now(timezone.utc).hour == 8:
                result = subprocess.run([*runner, "retention", "--config", str(path), "--apply"], timeout=600)
                return 0 if result.returncode == 0 else 1
            return 0
    except Exception as error:
        print(f"STOP: {type(error).__name__}; scheduled task failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
