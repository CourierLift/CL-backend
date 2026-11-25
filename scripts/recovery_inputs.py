"""Turn temporary Render secret environment values into private process files.

No /etc/secrets permissions or mounted-volume assumptions. Never echo values.
Every file is removed before the web process is exec'd, or when a job exits.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile

from scripts.postgres_cutover import CutoverError


@contextmanager
def materialized_inputs(specifications: dict[str, tuple[Path | None, str, int]]):
    """Map label to optional CLI path, environment name, and byte limit."""
    with tempfile.TemporaryDirectory(prefix="cl-recovery-input-") as directory:
        root = Path(directory)
        root.chmod(0o700)
        result = {}
        values = {variable: os.environ.pop(variable, None)
                  for _explicit, variable, _maximum in specifications.values()}
        for label, (explicit, variable, maximum) in specifications.items():
            value = values[variable]
            if explicit is not None and value is not None:
                raise CutoverError("Ambiguous recovery input: select a protected path or temporary environment value")
            if explicit is not None:
                if explicit.is_symlink() or not explicit.is_file() or explicit.stat().st_mode & 0o077:
                    raise CutoverError("Recovery input path must be a private regular file")
                if not 0 < explicit.stat().st_size <= maximum:
                    raise CutoverError("Recovery input path exceeds its size limit or is empty")
                result[label] = explicit
                continue
            if value is None:
                result[label] = None
                continue
            data = value.encode("utf-8")
            if not 0 < len(data) <= maximum:
                raise CutoverError("Recovery input exceeds its size limit or is empty")
            path = root / label
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                stream.write(data)
            result[label] = path
        yield result
