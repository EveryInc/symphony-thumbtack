"""Tiny, dependency-free `.env` loader.

Loaded from the directory containing WORKFLOW.md at startup and on hot
reload, so `$VAR` indirection in the front matter resolves against
project-local secrets without requiring shell exports.

Behavior:
- Lines beginning with `#` and blank lines are ignored.
- Optional leading `export ` is stripped.
- Values may be unquoted, single-quoted, or double-quoted; surrounding
  quotes are stripped without further interpretation.
- Variables already present in `os.environ` are NOT overridden, so a
  shell-exported value always wins over `.env`.
"""

from __future__ import annotations

import os
from typing import List

from .logger import get_logger

_log = get_logger("dotenv")


def load_dotenv(path: str) -> int:
    """Apply `path` to `os.environ` if the file exists. Returns the number
    of variables that were newly set.
    """
    if not path or not os.path.isfile(path):
        return 0
    set_count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                key, value, ok = _parse_line(raw)
                if not ok:
                    continue
                if key in os.environ:
                    continue
                os.environ[key] = value
                set_count += 1
    except OSError as e:
        _log.warning("failed to read .env", path=path, error=str(e))
        return set_count
    if set_count:
        _log.debug("loaded .env", path=path, count=set_count)
    return set_count


def _parse_line(raw: str):
    line = raw.strip()
    if not line or line.startswith("#"):
        return "", "", False
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    if "=" not in line:
        return "", "", False
    key, value = line.split("=", 1)
    key = key.strip()
    if not key or not _is_valid_key(key):
        return "", "", False
    value = value.strip()
    # Strip an inline trailing comment for unquoted values.
    if value and value[0] not in ("'", '"'):
        # Split on ` #` to keep `#` inside a value if not whitespace-prefixed.
        hash_at = value.find(" #")
        if hash_at >= 0:
            value = value[:hash_at].rstrip()
    # Strip surrounding quotes.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value, True


def _is_valid_key(k: str) -> bool:
    if not k:
        return False
    if not (k[0].isalpha() or k[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in k)
