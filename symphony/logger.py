"""Structured logger with key=value formatting (Section 13.1).

Required context fields:
- issue_id, issue_identifier for issue-related logs
- session_id for session lifecycle logs

We use Python's stdlib logging with a custom formatter so sink failures cannot
crash the orchestrator (Section 13.2).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Mapping, Optional

_LEVEL_FROM_ENV = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    if any(c.isspace() for c in s) or "=" in s or '"' in s:
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{s}"'
    return s


class KeyValueFormatter(logging.Formatter):
    """`time=... level=... msg=... key=value...` output."""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z")
        parts = [
            f"time={ts}",
            f"level={record.levelname.lower()}",
            f"logger={record.name}",
            f"msg={_format_value(record.getMessage())}",
        ]
        # Pull structured context from extras.
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, Mapping):
            for k, v in ctx.items():
                parts.append(f"{k}={_format_value(v)}")
        if record.exc_info:
            parts.append(f"exc={_format_value(self.formatException(record.exc_info))}")
        return " ".join(parts)


def configure_logging(level: Optional[str] = None) -> None:
    """Idempotent setup. Multiple calls are safe."""
    root = logging.getLogger("symphony")
    if getattr(root, "_symphony_configured", False):
        return
    chosen = level or os.environ.get("SYMPHONY_LOG_LEVEL", "INFO")
    root.setLevel(_LEVEL_FROM_ENV.get(chosen.upper(), logging.INFO))
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(KeyValueFormatter())
    root.addHandler(handler)
    root.propagate = False
    root._symphony_configured = True  # type: ignore[attr-defined]


class SymphonyLogger:
    """Thin wrapper that emits structured `ctx` extras."""

    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(name)

    def _emit(self, level: int, msg: str, **ctx: Any) -> None:
        # Drop None values so logs don't clutter with `key=`.
        clean = {k: v for k, v in ctx.items() if v is not None}
        try:
            self._log.log(level, msg, extra={"ctx": clean})
        except Exception:
            # Section 13.2: a log-sink failure must not crash the orchestrator.
            pass

    def debug(self, msg: str, **ctx: Any) -> None:
        self._emit(logging.DEBUG, msg, **ctx)

    def info(self, msg: str, **ctx: Any) -> None:
        self._emit(logging.INFO, msg, **ctx)

    def warning(self, msg: str, **ctx: Any) -> None:
        self._emit(logging.WARNING, msg, **ctx)

    def error(self, msg: str, **ctx: Any) -> None:
        self._emit(logging.ERROR, msg, **ctx)

    def exception(self, msg: str, **ctx: Any) -> None:
        clean = {k: v for k, v in ctx.items() if v is not None}
        try:
            self._log.exception(msg, extra={"ctx": clean})
        except Exception:
            pass


def get_logger(name: str) -> SymphonyLogger:
    return SymphonyLogger(f"symphony.{name}")
