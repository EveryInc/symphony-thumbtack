"""Local JSON task store — Linear replacement for offline-demo mode.

A single JSON file (`tasks.json` by default) holds every issue, comment, and
local "PR" record. Both the orchestrator's `JsonTracker` and the agent-facing
`tasks` CLI read/write this same file with an `fcntl` advisory lock so the
poll loop and concurrent agent writes don't clobber each other.

Schema (version 1):

```json
{
  "version": 1,
  "team": "ENG",
  "project": "Promatch Demo",
  "next_comment_id": 1,
  "next_pr_number": 1,
  "issues": [
    {
      "id": "ENG-1",                 # stable id (we use identifier)
      "identifier": "ENG-1",
      "title": "...",
      "description": "...",
      "state": "Todo",               # Todo | In Progress | Human Review | ...
      "priority": null,
      "branch_name": "symphony/eng-1",
      "url": "tasks://ENG-1",        # opaque local URL
      "labels": [],
      "blocked_by": ["ENG-2"],       # identifiers; states resolved on read
      "created_at": "...Z",
      "updated_at": "...Z",
      "comments": [
        {"id": "c1", "body": "## Workpad\\n...", "created_at": "...Z"}
      ],
      "pr": {
        "number": 1, "title": "...", "body": "...",
        "branch": "symphony/eng-1", "state": "OPEN",
        "created_at": "...Z", "merged_at": null,
        "merge_commit": null
      }
    }
  ]
}
```
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


@dataclass
class StoreLocation:
    """Where the JSON file lives. Held by tracker + CLI so they agree."""

    path: str  # absolute path to tasks.json

    @classmethod
    def resolve(cls, raw: Optional[str], *, source_dir: Optional[str] = None) -> "StoreLocation":
        candidate = raw or os.environ.get("SYMPHONY_TASKS_FILE")
        if not candidate:
            base = source_dir or os.getcwd()
            candidate = os.path.join(base, "tasks.json")
        candidate = os.path.expanduser(candidate)
        if not os.path.isabs(candidate) and source_dir:
            candidate = os.path.normpath(os.path.join(source_dir, candidate))
        return cls(path=os.path.abspath(candidate))


# ─────────────────────────────────────────────────────────────────────────────
# Read / write with advisory locking.
# ─────────────────────────────────────────────────────────────────────────────


def _empty_doc() -> Dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "team": "ENG",
        "project": "Promatch Demo",
        "next_comment_id": 1,
        "next_pr_number": 1,
        "issues": [],
    }


@contextlib.contextmanager
def _locked(path: str):
    """Open a sibling lockfile and hold an exclusive flock for the block."""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Ensure the lockfile exists before we open it.
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:
            pass  # non-POSIX: best-effort
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)


def load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return _empty_doc()
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"tasks store at {path} is not valid JSON: {e}") from e


def dump(path: str, doc: Dict[str, Any]) -> None:
    """Atomically replace the file via tmp + os.rename."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tasks.", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def transaction(path: str):
    """Read-modify-write under an advisory file lock."""
    with _locked(path):
        doc = load(path)
        yield doc
        dump(path, doc)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers used by both the tracker and the CLI.
# ─────────────────────────────────────────────────────────────────────────────


def find_issue(doc: Dict[str, Any], identifier: str) -> Optional[Dict[str, Any]]:
    ident = identifier.strip().upper()
    for issue in doc.get("issues") or []:
        if (issue.get("identifier") or "").upper() == ident:
            return issue
    return None


def resolve_blockers(doc: Dict[str, Any], issue: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return [{id, identifier, state}] for each blocker, with current state."""
    out: List[Dict[str, Any]] = []
    for blocker_id in issue.get("blocked_by") or []:
        b = find_issue(doc, blocker_id)
        if b is None:
            out.append({"id": blocker_id, "identifier": blocker_id, "state": None})
        else:
            out.append(
                {
                    "id": b.get("id") or b.get("identifier"),
                    "identifier": b.get("identifier"),
                    "state": b.get("state"),
                }
            )
    return out
