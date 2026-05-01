"""Workspace lifecycle hook execution (Section 9.4)."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional

from .errors import HookFailure, HookTimeout
from .logger import get_logger

_log = get_logger("hooks")


@dataclass
class HookResult:
    name: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


def _truncate(s: str, limit: int = 4000) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"... <truncated {len(s) - limit} bytes>"


async def run_hook(
    name: str,
    script: Optional[str],
    *,
    cwd: str,
    timeout_ms: int,
    issue_identifier: Optional[str] = None,
) -> Optional[HookResult]:
    """Run a single hook script under `bash -lc`. Returns None when no script.

    Raises:
        HookTimeout: hook exceeded `timeout_ms`.
        HookFailure: hook exited non-zero.

    Caller decides whether to surface or swallow these (Section 9.4 failure
    semantics).
    """
    if not script or not script.strip():
        return None

    if not os.path.isdir(cwd):
        raise HookFailure(
            f"workspace cwd does not exist: {cwd}", hook=name, cwd=cwd
        )

    _log.info(
        "hook starting",
        hook=name,
        cwd=cwd,
        issue_identifier=issue_identifier,
        timeout_ms=timeout_ms,
    )

    loop = asyncio.get_event_loop()
    started = loop.time()
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-lc",
        script,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_ms / 1000.0
        )
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        _log.warning(
            "hook timed out",
            hook=name,
            cwd=cwd,
            issue_identifier=issue_identifier,
            timeout_ms=timeout_ms,
        )
        raise HookTimeout(
            f"hook {name!r} timed out after {timeout_ms}ms",
            hook=name,
            cwd=cwd,
        )

    duration_ms = int((loop.time() - started) * 1000)
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    result = HookResult(
        name=name,
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
    )

    if result.exit_code != 0:
        _log.warning(
            "hook failed",
            hook=name,
            exit_code=result.exit_code,
            duration_ms=duration_ms,
            issue_identifier=issue_identifier,
            stderr=_truncate(stderr, 800),
        )
        raise HookFailure(
            f"hook {name!r} exited with code {result.exit_code}",
            hook=name,
            exit_code=result.exit_code,
            stderr=_truncate(stderr, 4000),
        )

    _log.info(
        "hook completed",
        hook=name,
        duration_ms=duration_ms,
        issue_identifier=issue_identifier,
    )
    return result


async def run_hook_best_effort(
    name: str,
    script: Optional[str],
    *,
    cwd: str,
    timeout_ms: int,
    issue_identifier: Optional[str] = None,
) -> None:
    """Run a hook whose failure is logged-and-ignored (after_run, before_remove)."""
    try:
        await run_hook(
            name,
            script,
            cwd=cwd,
            timeout_ms=timeout_ms,
            issue_identifier=issue_identifier,
        )
    except (HookFailure, HookTimeout) as e:
        _log.warning(
            "best-effort hook failure ignored",
            hook=name,
            error_code=e.code,
            issue_identifier=issue_identifier,
        )
    except Exception as e:
        _log.warning(
            "best-effort hook crashed",
            hook=name,
            error=str(e),
            issue_identifier=issue_identifier,
        )
