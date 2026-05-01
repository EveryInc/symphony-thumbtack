"""Claude Code adapter for Symphony.

Bridges Symphony's Section-10 agent contract onto Claude Code's headless
print mode. Each turn launches:

    claude -p --output-format stream-json --verbose [--resume <session_id>] ...

reading the prompt from stdin and streaming line-delimited JSON events on
stdout. The first turn captures `session_id` from the `system/init` event;
subsequent continuation turns pass `--resume <session_id>` so they execute
on the same Claude session.

This matches the Codex-equivalent semantics required by the Symphony spec:

- One thread per worker run, reused across continuation turns (Section 7.1).
- Workspace `cwd` set to the per-issue workspace path (Section 9.5).
- Stdout carries the protocol stream; stderr is logged separately (Section 10.3).
- `turn.completed`, `turn.failed`, and `turn.cancelled` map to TurnResult
  outcomes; user-input-required is rejected by Claude's permission_mode
  rather than surfaced as an interactive prompt (Section 10.5 high-trust).
- Token totals come from the `result` event's absolute usage map (Section 13.5).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from .agent import CodexEvent, EventCallback, TurnResult
from .config import ClaudeConfig
from .domain import LiveSession
from .errors import (
    CodexNotFound,
    PortExit,
    ResponseError,
)
from .logger import get_logger

_log = get_logger("agent.claude")


_MAX_LINE_BYTES = 10 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_usage(u: Mapping[str, Any]) -> Optional[Dict[str, int]]:
    """Pull `input_tokens`, `output_tokens`, and `total_tokens` from a
    Claude usage map, including cache fields when present.
    """

    def _i(v: Any) -> int:
        if isinstance(v, bool):
            return 0
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.lstrip("-").isdigit():
            return int(v)
        return 0

    inp = _i(u.get("input_tokens")) + _i(u.get("cache_creation_input_tokens")) + _i(
        u.get("cache_read_input_tokens")
    )
    out = _i(u.get("output_tokens"))
    tot = inp + out
    if inp == 0 and out == 0 and tot == 0:
        return None
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": tot}


class ClaudeClient:
    """One-shot-per-turn Claude Code client.

    Implements the same shape as `CodexClient` so the orchestrator's worker
    loop is unchanged. The `_proc` attribute is None between turns because
    Claude does not run as a long-lived app-server.
    """

    def __init__(self, *, config: ClaudeConfig) -> None:
        self._config = config
        self._workspace_path: Optional[str] = None
        self._session_id: Optional[str] = None
        self._current_proc: Optional[asyncio.subprocess.Process] = None
        self._proc = None  # supervision helper checks this and skips when None.
        self.session = LiveSession()

    # ---- subprocess lifecycle (same shape as CodexClient) ----

    async def launch(self, *, workspace_path: str) -> None:
        if not os.path.isdir(workspace_path):
            raise CodexNotFound(f"workspace cwd does not exist: {workspace_path}")
        self._workspace_path = workspace_path

    async def initialize_session(
        self,
        *,
        workspace_path: str,
        on_event: EventCallback,
        client_tools: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        # No long-running session to initialize. The first run_turn populates
        # session_id from Claude's `system/init` event.
        await on_event(
            CodexEvent(
                event="session_started",
                timestamp=_now(),
                payload={"thread_id": None, "agent": "claude"},
            )
        )

    async def stop(self) -> None:
        proc = self._current_proc
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass

    # ---- turn execution ----

    def _build_command(self) -> str:
        args: List[str] = [
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", self._config.permission_mode,
        ]
        if self._config.model:
            args += ["--model", self._config.model]
        for d in self._config.add_dirs:
            args += ["--add-dir", d]
        if self._session_id:
            args += ["--resume", self._session_id]
        args += list(self._config.extra_args)
        return self._config.command + " " + " ".join(shlex.quote(a) for a in args)

    async def run_turn(
        self,
        *,
        prompt: str,
        issue_title: str,
        issue_identifier: str,
        on_event: EventCallback,
    ) -> TurnResult:
        if not self._workspace_path:
            return TurnResult(
                success=False, turn_id=None, error="no workspace path", code="invalid_workspace_cwd"
            )

        full_cmd = self._build_command()
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-lc", full_cmd,
                cwd=self._workspace_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_MAX_LINE_BYTES,
            )
        except FileNotFoundError as e:
            return TurnResult(
                success=False, turn_id=None, error=str(e), code="codex_not_found"
            )
        except OSError as e:
            return TurnResult(
                success=False, turn_id=None, error=str(e), code="codex_not_found"
            )

        self._current_proc = proc
        self.session.codex_app_server_pid = proc.pid

        # Send the prompt on stdin and close the write end so claude knows
        # the input is complete.
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            # Subprocess died before reading input.
            await proc.wait()
            return TurnResult(
                success=False,
                turn_id=None,
                error="claude subprocess closed stdin before prompt was delivered",
                code="port_exit",
            )

        stderr_task = asyncio.create_task(self._drain_stderr(proc), name="claude-stderr")
        deadline = time.monotonic() + (self._config.turn_timeout_ms / 1000.0)

        last_message: Optional[str] = None
        last_usage: Optional[Dict[str, int]] = None
        captured_session_id: Optional[str] = None
        local_turn_id = str(uuid.uuid4())

        async def _emit(event_kind: str, payload: Mapping[str, Any], **extra: Any) -> None:
            await on_event(
                CodexEvent(
                    event=event_kind,
                    timestamp=_now(),
                    pid=proc.pid,
                    payload=dict(payload) if isinstance(payload, Mapping) else {},
                    usage=extra.get("usage"),
                    rate_limits=extra.get("rate_limits"),
                    message=extra.get("message"),
                )
            )

        result = TurnResult(success=False, turn_id=None, error="claude exited without result event", code="port_exit")

        try:
            assert proc.stdout is not None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await _emit("turn_failed", {}, message="turn timed out")
                    result = TurnResult(
                        success=False,
                        turn_id=captured_session_id,
                        error="turn timed out",
                        code="turn_timeout",
                    )
                    break

                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                except asyncio.TimeoutError:
                    continue

                if not line:
                    # EOF.
                    break

                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    _log.warning("claude emitted non-JSON line", preview=text[:200])
                    await _emit("malformed", {"line": text[:1000]})
                    continue

                kind = msg.get("type") if isinstance(msg, dict) else None

                if kind == "system":
                    sub = msg.get("subtype")
                    if sub == "init":
                        sid = msg.get("session_id")
                        if isinstance(sid, str) and sid:
                            if not self._session_id:
                                self._session_id = sid
                            captured_session_id = sid
                            self.session.thread_id = sid
                            if sid:
                                self.session.session_id = f"{sid}-{local_turn_id}"
                            await _emit(
                                "session_started",
                                msg,
                                message=f"claude session {sid}",
                            )
                        continue
                    await _emit("notification", msg)
                    continue

                if kind == "assistant":
                    message_obj = msg.get("message") or {}
                    content = message_obj.get("content") or []
                    text_parts: List[str] = []
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, Mapping) and c.get("type") == "text":
                                t = c.get("text")
                                if isinstance(t, str) and t:
                                    text_parts.append(t)
                    if text_parts:
                        last_message = " ".join(text_parts).strip()
                    usage = message_obj.get("usage")
                    if isinstance(usage, Mapping):
                        norm = _normalize_usage(usage)
                        if norm is not None:
                            last_usage = norm
                    await _emit(
                        "notification",
                        msg,
                        usage=last_usage,
                        message=last_message,
                    )
                    continue

                if kind == "user":
                    # Tool-result echoes from Claude after running its tools.
                    await _emit("other_message", msg)
                    continue

                if kind == "result":
                    sid = msg.get("session_id")
                    if isinstance(sid, str) and sid:
                        self._session_id = sid
                        captured_session_id = sid
                    usage = msg.get("usage")
                    if isinstance(usage, Mapping):
                        norm = _normalize_usage(usage)
                        if norm is not None:
                            last_usage = norm
                    is_error = bool(msg.get("is_error"))
                    sub = msg.get("subtype") or ""
                    final_text = msg.get("result") or last_message
                    if is_error or sub.startswith("error"):
                        err = final_text or sub or "claude turn failed"
                        await _emit(
                            "turn_failed",
                            msg,
                            usage=last_usage,
                            message=str(err),
                        )
                        result = TurnResult(
                            success=False,
                            turn_id=captured_session_id,
                            error=str(err),
                            code="turn_failed",
                        )
                        break
                    await _emit(
                        "turn_completed",
                        msg,
                        usage=last_usage,
                        message=final_text,
                    )
                    result = TurnResult(
                        success=True,
                        turn_id=captured_session_id,
                        final_message=final_text,
                    )
                    break

                # Unknown event types: surface but don't fail.
                await _emit("other_message", msg if isinstance(msg, Mapping) else {})

        finally:
            stderr_task.cancel()
            try:
                await stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await proc.wait()
                    except Exception:
                        pass
                except Exception:
                    pass
            self._current_proc = None

        return result

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    _log.debug("claude stderr", line=text[:800])
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
