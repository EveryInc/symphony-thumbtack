"""Codex app-server stdio client (Section 10).

The Codex app-server protocol is the source of truth for wire shape; this
module's job is to fulfil Symphony's structural responsibilities:

- Launch the subprocess via `bash -lc <codex.command>` with workspace cwd.
- Speak line-delimited JSON over stdio with stderr kept separate.
- Initialize the session, start a thread, run turns with prompts.
- Reuse one thread across continuation turns, sending continuation guidance
  rather than the full original prompt on subsequent turns.
- Extract thread_id / turn_id and emit `session_id = <thread>-<turn>`.
- Track token usage from absolute thread totals (not deltas) and the latest
  rate-limit payload.
- Auto-approve command/file approvals (high-trust default policy).
- Treat user-input-required as a hard failure.
- Reject unsupported dynamic tool calls without stalling.

Method names referenced by the spec or app-server documentation are isolated
as module-level constants so adapting to a different Codex protocol version
is a one-file change.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from .config import CodexConfig
from .domain import LiveSession
from .errors import (
    CodexNotFound,
    PortExit,
    ResponseError,
    ResponseTimeout,
    TurnCancelled,
    TurnFailed,
    TurnInputRequired,
    TurnTimeout,
)
from .logger import get_logger

_log = get_logger("agent")


# Methods we send to the app-server.
M_INITIALIZE = "initialize"
M_THREAD_START = "thread.start"
M_TURN_START = "turn.start"
M_TURN_INTERRUPT = "turn.interrupt"
M_SHUTDOWN = "shutdown"

# Notifications the app-server sends to us. We match by suffix to remain
# tolerant of small protocol-version differences.
N_TURN_COMPLETED_SUFFIXES = ("turn.completed", "turn/completed", "turnCompleted")
N_TURN_FAILED_SUFFIXES = ("turn.failed", "turn/failed", "turnFailed")
N_TURN_CANCELLED_SUFFIXES = ("turn.cancelled", "turn/cancelled", "turnCancelled")
N_AGENT_MESSAGE_SUFFIXES = (
    "agent.message",
    "agentMessage",
    "thread.message",
    "thread/message",
)
N_TOKEN_USAGE_SUFFIXES = (
    "thread/tokenUsage/updated",
    "tokenUsage.updated",
    "tokenCount",
    "tokenUsage",
)
N_RATE_LIMIT_SUFFIXES = ("rateLimit", "rate_limit", "rateLimitsUpdated")
N_NOTIFICATION_SUFFIXES = ("notification",)
N_INPUT_REQUIRED_SUFFIXES = ("inputRequired", "input_required", "userInputRequired")


# Request methods the app-server can call on us.
R_APPROVAL_COMMAND_SUFFIXES = (
    "approval/command",
    "exec.approve",
    "approveCommand",
    "approve_command",
)
R_APPROVAL_FILE_SUFFIXES = (
    "approval/file",
    "fileChange.approve",
    "approveFileChange",
    "approve_file_change",
)
R_TOOL_CALL_SUFFIXES = ("tool.call", "tool/call", "toolCall")


_MAX_LINE_BYTES = 10 * 1024 * 1024  # Section 10.1 RECOMMENDED 10 MB max line size.


@dataclass
class TurnResult:
    success: bool
    turn_id: Optional[str]
    final_message: Optional[str] = None
    error: Optional[str] = None
    code: Optional[str] = None


@dataclass
class CodexEvent:
    """An update emitted upstream to the orchestrator (Section 10.4)."""

    event: str
    timestamp: datetime
    pid: Optional[int] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    usage: Optional[Dict[str, int]] = None
    rate_limits: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


EventCallback = Callable[[CodexEvent], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _suffix_match(method: str, suffixes: tuple) -> bool:
    if not method:
        return False
    return any(method == s or method.endswith("/" + s) or method.endswith("." + s) for s in suffixes)


def _extract_token_usage(payload: Mapping[str, Any]) -> Optional[Dict[str, int]]:
    """Extract absolute thread totals (Section 13.5).

    Prefer `total_token_usage`/`thread_total` containers; fall back to common
    field names. Returns None if no usable totals are present.
    """
    candidates: List[Mapping[str, Any]] = []
    if isinstance(payload.get("total_token_usage"), Mapping):
        candidates.append(payload["total_token_usage"])
    if isinstance(payload.get("thread_total"), Mapping):
        candidates.append(payload["thread_total"])
    if isinstance(payload.get("totals"), Mapping):
        candidates.append(payload["totals"])
    if isinstance(payload.get("usage"), Mapping):
        # Generic usage maps are NOT trusted as cumulative unless inside a
        # token-usage event - that gating happens in the caller.
        candidates.append(payload["usage"])
    candidates.append(payload)  # last-resort: maybe at top level.

    def _read(d: Mapping[str, Any], *keys: str) -> Optional[int]:
        for k in keys:
            v = d.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.lstrip("-").isdigit():
                return int(v)
        return None

    for c in candidates:
        inp = _read(c, "input_tokens", "inputTokens", "prompt_tokens", "input")
        out = _read(c, "output_tokens", "outputTokens", "completion_tokens", "output")
        tot = _read(c, "total_tokens", "totalTokens", "total")
        if inp is None and out is None and tot is None:
            continue
        if tot is None and inp is not None and out is not None:
            tot = inp + out
        return {
            "input_tokens": inp or 0,
            "output_tokens": out or 0,
            "total_tokens": tot or 0,
        }
    return None


def _extract_rate_limits(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    for k in ("rate_limits", "rateLimits", "rate_limit", "rateLimit"):
        v = payload.get(k)
        if isinstance(v, Mapping):
            return dict(v)
    return None


def _extract_text(payload: Mapping[str, Any]) -> Optional[str]:
    for k in ("message", "text", "content", "summary"):
        v = payload.get(k)
        if isinstance(v, str) and v:
            return v
    return None


class CodexClient:
    """A single live coding-agent subprocess + JSON-RPC session."""

    def __init__(self, *, config: CodexConfig) -> None:
        self._config = config
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._stdin_lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._notification_queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self.session = LiveSession()

    # ---- subprocess lifecycle ----

    async def launch(self, *, workspace_path: str) -> None:
        if not os.path.isdir(workspace_path):
            raise CodexNotFound(f"workspace cwd does not exist: {workspace_path}")
        cmd = self._config.command
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "bash",
                "-lc",
                cmd,
                cwd=workspace_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_MAX_LINE_BYTES,
            )
        except FileNotFoundError as e:
            raise CodexNotFound(f"failed to launch codex: {e}") from e
        except OSError as e:
            raise CodexNotFound(f"failed to launch codex: {e}") from e

        self.session.codex_app_server_pid = self._proc.pid
        _log.info(
            "codex app-server launched",
            pid=self._proc.pid,
            cwd=workspace_path,
            command=shlex.quote(cmd),
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(), name="codex-stdout-reader"
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(), name="codex-stderr"
        )

    async def stop(self) -> None:
        self._closed = True
        proc = self._proc
        if not proc:
            return
        # Try a graceful shutdown first.
        try:
            await asyncio.wait_for(self._send_request(M_SHUTDOWN, {}), timeout=2.0)
        except Exception:
            pass
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
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
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        # Fail any still-pending requests.
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(PortExit("codex app-server exited"))
        self._pending.clear()

    # ---- IO loops ----

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        stdout = self._proc.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    _log.warning("codex emitted non-JSON line", preview=text[:200])
                    continue
                await self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log.warning("codex stdout reader crashed", error=str(e))
        finally:
            # Wake up any pending requests on EOF.
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(PortExit("codex app-server stdout closed"))

    async def _stderr_loop(self) -> None:
        assert self._proc and self._proc.stderr
        stderr = self._proc.stderr
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    _log.debug("codex stderr", line=text[:800])
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    # ---- protocol dispatch ----

    async def _dispatch(self, msg: Dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        # Response to a request we sent.
        if "id" in msg and ("result" in msg or "error" in msg):
            mid = str(msg.get("id"))
            fut = self._pending.pop(mid, None)
            if fut and not fut.done():
                if "error" in msg and msg["error"] is not None:
                    fut.set_exception(
                        ResponseError(
                            f"codex request error: {msg['error']}",
                            error=msg["error"],
                        )
                    )
                else:
                    fut.set_result(msg.get("result"))
            return
        # Server-initiated request that wants a response from us.
        if "id" in msg and "method" in msg:
            await self._handle_server_request(msg)
            return
        # Notification.
        if "method" in msg:
            await self._notification_queue.put(msg)
            return
        # Unknown shape - ignore.
        _log.debug("codex emitted unknown message shape", keys=list(msg.keys()))

    async def _handle_server_request(self, msg: Dict[str, Any]) -> None:
        method = str(msg.get("method") or "")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        # Auto-approval policy (Section 10.5 high-trust example).
        if _suffix_match(method, R_APPROVAL_COMMAND_SUFFIXES):
            await self._respond(req_id, {"approved": True, "decision": "approved_for_session"})
            await self._notification_queue.put(
                {"method": "approval_auto_approved", "params": {"kind": "command", "request": params}}
            )
            return
        if _suffix_match(method, R_APPROVAL_FILE_SUFFIXES):
            await self._respond(req_id, {"approved": True, "decision": "approved_for_session"})
            await self._notification_queue.put(
                {"method": "approval_auto_approved", "params": {"kind": "file_change", "request": params}}
            )
            return

        # Unsupported dynamic tool calls -> failure response, do not stall.
        if _suffix_match(method, R_TOOL_CALL_SUFFIXES):
            tool_name = (
                params.get("name")
                or params.get("tool")
                or params.get("toolName")
                or "<unknown>"
            )
            await self._respond_error(
                req_id,
                code=-32601,
                message=f"tool {tool_name!r} is not implemented",
            )
            await self._notification_queue.put(
                {"method": "unsupported_tool_call", "params": {"name": tool_name}}
            )
            return

        # Anything else: fail it explicitly so the agent does not stall.
        _log.debug("codex requested unhandled method", method=method)
        await self._respond_error(
            req_id, code=-32601, message=f"method {method!r} is not implemented"
        )

    async def _respond(self, req_id: Any, result: Any) -> None:
        await self._write_message({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def _respond_error(self, req_id: Any, *, code: int, message: str) -> None:
        await self._write_message(
            {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        )

    async def _write_message(self, msg: Dict[str, Any]) -> None:
        proc = self._proc
        if not proc or proc.stdin is None or proc.stdin.is_closing():
            raise PortExit("codex stdin is closed")
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        if len(data) > _MAX_LINE_BYTES:
            raise ResponseError("outbound message exceeds 10MB line limit")
        async with self._stdin_lock:
            try:
                proc.stdin.write(data)
                await proc.stdin.drain()
            except (ConnectionResetError, BrokenPipeError) as e:
                raise PortExit(f"codex stdin closed: {e}") from e

    async def _send_request(
        self, method: str, params: Any, *, timeout_ms: Optional[int] = None
    ) -> Any:
        rid = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self._write_message(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        )
        timeout_s = (
            (timeout_ms if timeout_ms is not None else self._config.read_timeout_ms) / 1000.0
        )
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError as e:
            self._pending.pop(rid, None)
            raise ResponseTimeout(f"codex {method} timed out after {timeout_s:.1f}s") from e

    # ---- public API used by the worker ----

    async def initialize_session(
        self,
        *,
        workspace_path: str,
        on_event: EventCallback,
        client_tools: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Run `initialize` + `thread.start` according to Section 10.2."""
        init_params = {
            "clientInfo": {"name": "symphony", "version": "0.1.0"},
            "capabilities": {"clientTools": client_tools or []},
        }
        try:
            await self._send_request(M_INITIALIZE, init_params)
        except ResponseTimeout:
            raise
        except ResponseError as e:
            await on_event(CodexEvent(event="startup_failed", timestamp=_now(), payload=e.details))
            raise

        thread_params: Dict[str, Any] = {
            "cwd": workspace_path,
        }
        if self._config.approval_policy is not None:
            thread_params["approval_policy"] = self._config.approval_policy
        if self._config.thread_sandbox is not None:
            thread_params["sandbox"] = self._config.thread_sandbox

        try:
            result = await self._send_request(M_THREAD_START, thread_params)
        except ResponseError as e:
            await on_event(CodexEvent(event="startup_failed", timestamp=_now(), payload=e.details))
            raise

        thread_id = None
        if isinstance(result, dict):
            thread_id = (
                result.get("thread_id")
                or result.get("threadId")
                or (result.get("thread") or {}).get("id")
            )
        self.session.thread_id = thread_id
        await on_event(
            CodexEvent(
                event="session_started",
                timestamp=_now(),
                pid=self.session.codex_app_server_pid,
                payload={"thread_id": thread_id},
            )
        )

    async def run_turn(
        self,
        *,
        prompt: str,
        issue_title: str,
        issue_identifier: str,
        on_event: EventCallback,
    ) -> TurnResult:
        if not self.session.thread_id:
            raise ResponseError("cannot start turn before thread is initialized")

        turn_params: Dict[str, Any] = {
            "thread_id": self.session.thread_id,
            "prompt": prompt,
            "title": f"{issue_identifier}: {issue_title}",
            "cwd": None,  # set below if we know workspace; orchestrator hands it via parent context
        }
        if self._config.turn_sandbox_policy is not None:
            turn_params["sandbox_policy"] = self._config.turn_sandbox_policy
        # Drop the None placeholder if we did not set cwd.
        if turn_params["cwd"] is None:
            turn_params.pop("cwd")

        try:
            start_result = await self._send_request(M_TURN_START, turn_params)
        except ResponseError as e:
            return TurnResult(success=False, turn_id=None, error=str(e), code="response_error")
        except ResponseTimeout:
            return TurnResult(
                success=False, turn_id=None, error="turn start timed out", code="response_timeout"
            )

        turn_id: Optional[str] = None
        if isinstance(start_result, dict):
            turn_id = start_result.get("turn_id") or start_result.get("turnId") or (
                (start_result.get("turn") or {}).get("id") if isinstance(start_result.get("turn"), dict) else None
            )
        self.session.turn_id = turn_id
        if self.session.thread_id and turn_id:
            self.session.session_id = f"{self.session.thread_id}-{turn_id}"

        # Stream notifications until the turn ends or we timeout.
        deadline = time.monotonic() + (self._config.turn_timeout_ms / 1000.0)
        last_message: Optional[str] = None

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Try to interrupt the turn cooperatively before failing.
                try:
                    await asyncio.wait_for(
                        self._send_request(M_TURN_INTERRUPT, {"turn_id": turn_id}),
                        timeout=2.0,
                    )
                except Exception:
                    pass
                return TurnResult(
                    success=False, turn_id=turn_id, error="turn timed out", code="turn_timeout"
                )

            try:
                msg = await asyncio.wait_for(self._notification_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                continue

            method = str(msg.get("method") or "")
            params = msg.get("params") or {}
            if not isinstance(params, Mapping):
                params = {}

            event_kind = "other_message"
            usage = None
            rate_limits = _extract_rate_limits(params if isinstance(params, Mapping) else {})
            text = _extract_text(params if isinstance(params, Mapping) else {})

            if _suffix_match(method, N_TOKEN_USAGE_SUFFIXES):
                event_kind = "token_usage_updated"
                usage = _extract_token_usage(params)
            elif _suffix_match(method, N_AGENT_MESSAGE_SUFFIXES):
                event_kind = "notification"
                if text:
                    last_message = text
            elif _suffix_match(method, N_NOTIFICATION_SUFFIXES):
                event_kind = "notification"
                if text:
                    last_message = text
            elif _suffix_match(method, N_RATE_LIMIT_SUFFIXES):
                event_kind = "rate_limit_updated"
            elif _suffix_match(method, N_INPUT_REQUIRED_SUFFIXES):
                # Section 10.5: high-trust default fails the run.
                await on_event(
                    CodexEvent(
                        event="turn_input_required",
                        timestamp=_now(),
                        pid=self.session.codex_app_server_pid,
                        payload=dict(params),
                        message=text,
                    )
                )
                try:
                    await asyncio.wait_for(
                        self._send_request(M_TURN_INTERRUPT, {"turn_id": turn_id}),
                        timeout=2.0,
                    )
                except Exception:
                    pass
                return TurnResult(
                    success=False,
                    turn_id=turn_id,
                    error="user input required",
                    code="turn_input_required",
                )
            elif _suffix_match(method, N_TURN_COMPLETED_SUFFIXES):
                completion_usage = _extract_token_usage(params)
                if completion_usage:
                    usage = completion_usage
                event_kind = "turn_completed"
                completion_text = text
                await on_event(
                    CodexEvent(
                        event=event_kind,
                        timestamp=_now(),
                        pid=self.session.codex_app_server_pid,
                        payload=dict(params),
                        usage=usage,
                        rate_limits=rate_limits,
                        message=completion_text or last_message,
                    )
                )
                return TurnResult(
                    success=True, turn_id=turn_id, final_message=completion_text or last_message
                )
            elif _suffix_match(method, N_TURN_FAILED_SUFFIXES):
                err = text or (params.get("error") if isinstance(params, Mapping) else None) or "turn failed"
                await on_event(
                    CodexEvent(
                        event="turn_failed",
                        timestamp=_now(),
                        pid=self.session.codex_app_server_pid,
                        payload=dict(params),
                        message=str(err),
                    )
                )
                return TurnResult(
                    success=False, turn_id=turn_id, error=str(err), code="turn_failed"
                )
            elif _suffix_match(method, N_TURN_CANCELLED_SUFFIXES):
                err = text or "turn cancelled"
                await on_event(
                    CodexEvent(
                        event="turn_cancelled",
                        timestamp=_now(),
                        pid=self.session.codex_app_server_pid,
                        payload=dict(params),
                        message=str(err),
                    )
                )
                return TurnResult(
                    success=False, turn_id=turn_id, error=str(err), code="turn_cancelled"
                )
            elif method == "approval_auto_approved":
                event_kind = "approval_auto_approved"
            elif method == "unsupported_tool_call":
                event_kind = "unsupported_tool_call"
            else:
                # Unknown notification - emit as 'other_message' but do not stall.
                event_kind = "other_message"

            await on_event(
                CodexEvent(
                    event=event_kind,
                    timestamp=_now(),
                    pid=self.session.codex_app_server_pid,
                    payload=dict(params) if isinstance(params, Mapping) else {},
                    usage=usage,
                    rate_limits=rate_limits,
                    message=text,
                )
            )


async def run_with_subprocess_supervision(
    client: Any, work: Awaitable
) -> Any:
    """Run `work` while watching the subprocess. Raise PortExit if it exits early.

    Accepts any object that exposes a `_proc` attribute. When `_proc` is None
    (e.g. ClaudeClient between turns), the helper degrades to a plain await.
    """
    # `work` is an awaitable - wrap it as a task so we can race it.
    work_task = asyncio.create_task(work, name="codex-work")
    proc = getattr(client, "_proc", None)
    if proc is None:
        return await work_task

    async def _watch_exit():
        rc = await proc.wait()
        raise PortExit(f"codex app-server exited with code {rc}", returncode=rc)

    watch_task = asyncio.create_task(_watch_exit(), name="codex-watch")
    done, pending = await asyncio.wait(
        {work_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for t in pending:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    for d in done:
        if d is watch_task:
            # Subprocess exited before work completed.
            try:
                d.result()
            except PortExit:
                raise
            raise PortExit("codex app-server exited unexpectedly")
        return d.result()
    raise PortExit("codex app-server exited unexpectedly")
