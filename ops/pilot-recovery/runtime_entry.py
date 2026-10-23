#!/usr/bin/env python3
"""Remove image-level libpq defaults before invoking reviewed task commands."""
import os
import sys

if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("A reviewed task command is required")
    image_metadata = {"PG_MAJOR", "PG_VERSION", "PGDATA"}
    if any(key.startswith("PG") and key not in image_metadata for key in os.environ):
        raise SystemExit("Unreviewed libpq PG* overrides are not allowed")
    environment = {key: value for key, value in os.environ.items() if key not in image_metadata}
    os.execvpe(sys.argv[1], sys.argv[1:], environment)
