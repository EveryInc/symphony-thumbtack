"""Polling orchestrator (Sections 7-8, 16).

Single-authority design: every mutation of `OrchestratorState` happens in
`_main_loop`. Workers, timers, and codex event callbacks post messages to a
queue; the main loop reads and applies them.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Dict, List, Optional, Tuple

from .agent import CodexClient, CodexEvent, EventCallback, run_with_subprocess_supervision
from .agent_claude import ClaudeClient
from .config import ServiceConfig, validate_dispatch_config
from .domain import (
    BlockerRef,
    CodexTotals,
    Issue,
    LiveSession,
    OrchestratorState,
    RetryEntry,
    RunningEntry,
    now_utc,
)
from .errors import (
    HookFailure,
    HookTimeout,
    SymphonyError,
    TemplateRenderError,
    TrackerError,
)
from .hooks import run_hook, run_hook_best_effort
from .logger import get_logger
from .prompt import render_prompt
from .tracker import IssueTracker
from .workflow import WorkflowDefinition
from .workspace import WorkspaceManager, assert_cwd_is_workspace


_log = get_logger("orchestrator")

CONTINUATION_RETRY_DELAY_MS = 1000
RETRY_BASE_DELAY_MS = 10_000
RECENT_EVENT_LIMIT = 50


# ---------- Sorting and eligibility (Section 8.2) ----------

def sort_for_dispatch(issues: List[Issue]) -> List[Issue]:
    """priority asc (None last), created_at oldest first, identifier lex."""

    def key(i: Issue):
        prio = i.priority if isinstance(i.priority, int) else 1_000_000
        ts = i.created_at.replace(tzinfo=timezone.utc).timestamp() if i.created_at else float("inf")
        return (prio, ts, i.identifier or "")

    return sorted(issues, key=key)


def todo_blockers_resolved(issue: Issue, terminal_states_lower: set) -> bool:
    """Section 8.2: a Todo issue with any non-terminal blocker is ineligible."""
    if (issue.state or "").lower() != "todo":
        return True
    for b in issue.blocked_by:
        if not b.state:
            return False
        if b.state.lower() not in terminal_states_lower:
            return False
    return True


# ---------- Backoff (Section 8.4) ----------

def compute_backoff_ms(attempt: int, max_backoff_ms: int) -> int:
    """`min(10000 * 2^(attempt - 1), max)` for attempt >= 1."""
    if attempt < 1:
        return RETRY_BASE_DELAY_MS
    raw = RETRY_BASE_DELAY_MS * (1 << (attempt - 1))
    return min(raw, max_backoff_ms)


# ---------- Internal message types ----------

@dataclass
class _MsgPollTick: ...

@dataclass
class _MsgWorkerExit:
    issue_id: str
    normal: bool
    reason: str

@dataclass
class _MsgCodexUpdate:
    issue_id: str
    event: CodexEvent

@dataclass
class _MsgRetryTimer:
    issue_id: str

@dataclass
class _MsgConfigReload:
    config: ServiceConfig
    workflow: WorkflowDefinition

@dataclass
class _MsgShutdown: ...


class Orchestrator:
    def __init__(
        self,
        *,
        config: ServiceConfig,
        workflow: WorkflowDefinition,
        tracker: IssueTracker,
        workspaces: WorkspaceManager,
    ) -> None:
        self._config = config
        self._workflow = workflow
        self._tracker = tracker
        self._workspaces = workspaces
        self._state = OrchestratorState(
            poll_interval_ms=config.polling.interval_ms,
            max_concurrent_agents=config.agent.max_concurrent_agents,
        )
        self._queue: asyncio.Queue = asyncio.Queue()
        self._poll_task: Optional[asyncio.Task] = None
        self._worker_tasks: Dict[str, asyncio.Task] = {}
        self._timer_handles: Dict[str, asyncio.TimerHandle] = {}
        self._stopping = False
        self._last_validation_error: Optional[str] = None

    # ---- public API ----

    async def run(self) -> None:
        """Run until shutdown is requested. Blocks the caller."""
        ok, err = validate_dispatch_config(self._config)
        if not ok:
            _log.error("startup validation failed", code=getattr(err, "code", None), error=str(err))
            raise err  # type: ignore[misc]

        await self._startup_terminal_workspace_cleanup()

        # Schedule immediate first tick.
        await self._queue.put(_MsgPollTick())
        self._poll_task = asyncio.create_task(
            self._poll_scheduler(), name="symphony-poll-scheduler"
        )

        try:
            await self._main_loop()
        finally:
            await self._shutdown()

    async def shutdown(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        await self._queue.put(_MsgShutdown())

    async def reload_workflow(
        self, *, config: ServiceConfig, workflow: WorkflowDefinition
    ) -> None:
        await self._queue.put(_MsgConfigReload(config=config, workflow=workflow))

    def snapshot(self) -> Dict[str, Any]:
        """Synchronous read-only snapshot (Section 13.3)."""
        running_rows = []
        for entry in self._state.running.values():
            running_rows.append(
                {
                    "issue_id": entry.issue_id,
                    "issue_identifier": entry.identifier,
                    "state": entry.issue.state,
                    "session_id": entry.session.session_id,
                    "turn_count": entry.session.turn_count,
                    "last_event": entry.session.last_codex_event,
                    "last_message": entry.session.last_codex_message or "",
                    "started_at": entry.started_at.isoformat(),
                    "last_event_at": (
                        entry.session.last_codex_timestamp.isoformat()
                        if entry.session.last_codex_timestamp
                        else None
                    ),
                    "tokens": {
                        "input_tokens": entry.session.codex_input_tokens,
                        "output_tokens": entry.session.codex_output_tokens,
                        "total_tokens": entry.session.codex_total_tokens,
                    },
                }
            )

        retry_rows = []
        loop = asyncio.get_event_loop()
        now_mono = loop.time() * 1000.0
        for r in self._state.retry_attempts.values():
            retry_rows.append(
                {
                    "issue_id": r.issue_id,
                    "issue_identifier": r.identifier,
                    "attempt": r.attempt,
                    "due_in_ms": max(0, int(r.due_at_ms - now_mono)),
                    "error": r.error,
                }
            )

        # Active-session elapsed time added live (Section 13.5).
        active_seconds = 0.0
        for entry in self._state.running.values():
            active_seconds += max(
                0.0, (now_utc() - entry.started_at).total_seconds()
            )

        return {
            "counts": {
                "running": len(self._state.running),
                "retrying": len(self._state.retry_attempts),
            },
            "running": running_rows,
            "retrying": retry_rows,
            "codex_totals": {
                "input_tokens": self._state.codex_totals.input_tokens,
                "output_tokens": self._state.codex_totals.output_tokens,
                "total_tokens": self._state.codex_totals.total_tokens,
                "seconds_running": self._state.codex_totals.seconds_running + active_seconds,
            },
            "rate_limits": self._state.codex_rate_limits,
            "validation_error": self._last_validation_error,
        }

    # ---- main loop ----

    async def _main_loop(self) -> None:
        while True:
            msg = await self._queue.get()
            try:
                if isinstance(msg, _MsgShutdown):
                    return
                if isinstance(msg, _MsgPollTick):
                    await self._on_poll_tick()
                elif isinstance(msg, _MsgWorkerExit):
                    await self._on_worker_exit(msg)
                elif isinstance(msg, _MsgCodexUpdate):
                    await self._on_codex_update(msg)
                elif isinstance(msg, _MsgRetryTimer):
                    await self._on_retry_timer(msg)
                elif isinstance(msg, _MsgConfigReload):
                    await self._on_config_reload(msg)
            except Exception as e:
                # Section 14.2: never crash the orchestrator.
                _log.exception(
                    "orchestrator loop swallowed error",
                    msg_type=type(msg).__name__,
                    error=str(e),
                )

    # ---- shutdown ----

    async def _shutdown(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass

        # Cancel pending retry timers.
        for handle in self._timer_handles.values():
            try:
                handle.cancel()
            except Exception:
                pass
        self._timer_handles.clear()

        # Cancel worker tasks (active runs).
        tasks = list(self._worker_tasks.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._worker_tasks.clear()

        try:
            await self._tracker.aclose()
        except Exception:
            pass

    # ---- poll scheduler ----

    async def _poll_scheduler(self) -> None:
        try:
            while True:
                interval = max(0.05, self._state.poll_interval_ms / 1000.0)
                await asyncio.sleep(interval)
                await self._queue.put(_MsgPollTick())
        except asyncio.CancelledError:
            return

    # ---- handlers ----

    async def _on_poll_tick(self) -> None:
        await self._reconcile_running_issues()

        ok, err = validate_dispatch_config(self._config)
        if not ok:
            self._last_validation_error = str(err)
            _log.error(
                "dispatch validation failed; skipping dispatch this tick",
                code=getattr(err, "code", None),
                error=str(err),
            )
            return
        self._last_validation_error = None

        try:
            issues = await self._tracker.fetch_candidate_issues()
        except TrackerError as e:
            _log.error("candidate fetch failed", code=e.code, error=str(e))
            return
        except Exception as e:
            _log.exception("candidate fetch crashed", error=str(e))
            return

        terminal_lower = {s.lower() for s in self._config.tracker.terminal_states}
        active_lower = {s.lower() for s in self._config.tracker.active_states}

        dispatched_this_tick = 0
        for issue in sort_for_dispatch(issues):
            if not self._has_global_slot():
                break
            if not self._is_dispatchable(issue, terminal_lower, active_lower):
                continue
            if not self._has_state_slot(issue.state):
                continue
            self._dispatch_issue(issue, attempt=None)
            dispatched_this_tick += 1

        # Heartbeat log: visible cadence so operators can see the service is
        # alive and what the orchestrator believes about current state.
        _log.info(
            "tick",
            candidates=len(issues),
            dispatched=dispatched_this_tick,
            running=len(self._state.running),
            retrying=len(self._state.retry_attempts),
            completed=len(self._state.completed),
        )

    def _is_dispatchable(
        self, issue: Issue, terminal_lower: set, active_lower: set
    ) -> bool:
        if not issue.id or not issue.identifier or not issue.title or not issue.state:
            return False
        s = issue.state.lower()
        if s in terminal_lower:
            return False
        if s not in active_lower:
            return False
        if issue.id in self._state.running:
            return False
        if issue.id in self._state.claimed:
            return False
        if not todo_blockers_resolved(issue, terminal_lower):
            return False
        return True

    def _has_global_slot(self) -> bool:
        return len(self._state.running) < self._state.max_concurrent_agents

    def _has_state_slot(self, state: str) -> bool:
        cap = self._config.agent.max_concurrent_agents_by_state.get((state or "").lower())
        if cap is None:
            return self._has_global_slot()
        in_state = sum(
            1
            for e in self._state.running.values()
            if (e.issue.state or "").lower() == (state or "").lower()
        )
        return in_state < cap

    # ---- dispatch ----

    def _dispatch_issue(self, issue: Issue, *, attempt: Optional[int]) -> None:
        entry = RunningEntry(
            issue_id=issue.id,
            identifier=issue.identifier,
            issue=issue,
            started_at=now_utc(),
            retry_attempt=attempt,
        )
        self._state.running[issue.id] = entry
        self._state.claimed.add(issue.id)
        self._state.retry_attempts.pop(issue.id, None)
        # Cancel any pending retry timer for this issue.
        handle = self._timer_handles.pop(issue.id, None)
        if handle:
            try:
                handle.cancel()
            except Exception:
                pass

        worker = asyncio.create_task(
            self._run_worker(entry, attempt=attempt),
            name=f"symphony-worker-{issue.identifier}",
        )
        self._worker_tasks[issue.id] = worker
        entry.worker_task = worker

        _log.info(
            "dispatched",
            issue_id=issue.id,
            issue_identifier=issue.identifier,
            attempt=attempt,
            state=issue.state,
        )

    # ---- worker (Section 16.5) ----

    async def _run_worker(self, entry: RunningEntry, *, attempt: Optional[int]) -> None:
        issue_id = entry.issue_id
        identifier = entry.identifier
        issue = entry.issue
        normal_exit = False
        reason = ""

        try:
            workspace = await self._workspaces.ensure_for(identifier)
            entry.workspace_path = workspace.path
            assert_cwd_is_workspace(workspace.path, workspace.path)

            # before_run hook.
            try:
                await run_hook(
                    "before_run",
                    self._config.hooks.before_run,
                    cwd=workspace.path,
                    timeout_ms=self._config.hooks.timeout_ms,
                    issue_identifier=identifier,
                )
            except (HookFailure, HookTimeout) as e:
                reason = f"before_run hook error: {e.code}"
                return

            if self._config.agent.kind == "claude":
                client = ClaudeClient(config=self._config.claude)
            else:
                client = CodexClient(config=self._config.codex)
            try:
                await client.launch(workspace_path=workspace.path)
            except Exception as e:
                reason = f"codex launch error: {e}"
                await run_hook_best_effort(
                    "after_run",
                    self._config.hooks.after_run,
                    cwd=workspace.path,
                    timeout_ms=self._config.hooks.timeout_ms,
                    issue_identifier=identifier,
                )
                return

            event_cb = self._make_event_callback(issue_id)

            try:
                await client.initialize_session(
                    workspace_path=workspace.path,
                    on_event=event_cb,
                )
            except Exception as e:
                reason = f"agent session startup error: {e}"
                await client.stop()
                await run_hook_best_effort(
                    "after_run",
                    self._config.hooks.after_run,
                    cwd=workspace.path,
                    timeout_ms=self._config.hooks.timeout_ms,
                    issue_identifier=identifier,
                )
                return

            try:
                await self._run_turn_loop(entry, issue, attempt, client, event_cb)
                normal_exit = True
            except _WorkerFailure as e:
                reason = e.reason
            finally:
                try:
                    await client.stop()
                except Exception:
                    pass
                await run_hook_best_effort(
                    "after_run",
                    self._config.hooks.after_run,
                    cwd=workspace.path,
                    timeout_ms=self._config.hooks.timeout_ms,
                    issue_identifier=identifier,
                )

        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except Exception as e:
            reason = f"worker crashed: {e}"
            _log.exception(
                "worker crashed", issue_id=issue_id, issue_identifier=identifier, error=str(e)
            )
        finally:
            await self._queue.put(
                _MsgWorkerExit(issue_id=issue_id, normal=normal_exit, reason=reason)
            )

    async def _run_turn_loop(
        self,
        entry: RunningEntry,
        issue: Issue,
        attempt: Optional[int],
        client: CodexClient,
        event_cb: EventCallback,
    ) -> None:
        max_turns = self._config.agent.max_turns
        turn_number = 1
        active_lower = {s.lower() for s in self._config.tracker.active_states}

        while True:
            extra = {"turn_number": turn_number, "max_turns": max_turns}
            try:
                if turn_number == 1:
                    prompt = render_prompt(
                        self._workflow.prompt_template,
                        issue,
                        attempt=attempt,
                        extra=extra,
                    )
                else:
                    # Continuation guidance (Section 7.1, 16.5).
                    prompt = (
                        f"Continue working on {issue.identifier}: {issue.title}. "
                        "The previous turn completed normally. Pick up where you left off, "
                        "make any remaining progress, and stop when there is nothing useful "
                        "to do without operator input."
                    )
            except TemplateRenderError as e:
                raise _WorkerFailure(reason=f"prompt error: {e}")

            entry.session.turn_count = turn_number

            try:
                turn_result = await run_with_subprocess_supervision(
                    client,
                    client.run_turn(
                        prompt=prompt,
                        issue_title=issue.title,
                        issue_identifier=issue.identifier,
                        on_event=event_cb,
                    ),
                )
            except SymphonyError as e:
                raise _WorkerFailure(reason=f"agent turn error: {e.code}: {e}")
            except Exception as e:
                raise _WorkerFailure(reason=f"agent turn error: {e}")

            if not turn_result.success:
                raise _WorkerFailure(
                    reason=f"agent turn error: {turn_result.code}: {turn_result.error}"
                )

            # Refresh issue state after every successful turn (Section 16.5).
            try:
                refreshed = await self._tracker.fetch_issue_states_by_ids([issue.id])
            except TrackerError as e:
                raise _WorkerFailure(reason=f"issue state refresh error: {e.code}")
            if refreshed:
                issue = refreshed[0]
                entry.issue = issue

            if (issue.state or "").lower() not in active_lower:
                return  # normal exit, orchestrator will release claim.

            if turn_number >= max_turns:
                return  # normal exit; orchestrator will continuation-retry.
            turn_number += 1

    def _make_event_callback(self, issue_id: str) -> EventCallback:
        async def _cb(event: CodexEvent) -> None:
            await self._queue.put(_MsgCodexUpdate(issue_id=issue_id, event=event))
        return _cb

    # ---- worker-exit / retry handlers (Section 16.6) ----

    async def _on_worker_exit(self, msg: _MsgWorkerExit) -> None:
        entry = self._state.running.pop(msg.issue_id, None)
        self._worker_tasks.pop(msg.issue_id, None)

        if entry is not None:
            run_seconds = (now_utc() - entry.started_at).total_seconds()
            self._state.codex_totals.seconds_running += max(0.0, run_seconds)

        if msg.normal:
            self._state.completed.add(msg.issue_id)
            _log.info(
                "worker exited normally; scheduling continuation retry",
                issue_id=msg.issue_id,
                issue_identifier=entry.identifier if entry else None,
            )
            self._schedule_retry(
                issue_id=msg.issue_id,
                identifier=entry.identifier if entry else None,
                attempt=1,
                delay_ms=CONTINUATION_RETRY_DELAY_MS,
                error=None,
            )
        else:
            next_attempt = self._next_attempt_after(entry)
            delay = compute_backoff_ms(next_attempt, self._config.agent.max_retry_backoff_ms)
            _log.warning(
                "worker exited abnormally; scheduling backoff retry",
                issue_id=msg.issue_id,
                issue_identifier=entry.identifier if entry else None,
                attempt=next_attempt,
                delay_ms=delay,
                reason=msg.reason,
            )
            self._schedule_retry(
                issue_id=msg.issue_id,
                identifier=entry.identifier if entry else None,
                attempt=next_attempt,
                delay_ms=delay,
                error=msg.reason or "worker exited",
            )

    def _next_attempt_after(self, entry: Optional[RunningEntry]) -> int:
        if entry is None or entry.retry_attempt is None:
            return 1
        return max(1, entry.retry_attempt + 1)

    def _schedule_retry(
        self,
        *,
        issue_id: str,
        identifier: Optional[str],
        attempt: int,
        delay_ms: int,
        error: Optional[str],
    ) -> None:
        loop = asyncio.get_event_loop()
        due_at_ms = loop.time() * 1000.0 + delay_ms

        # Cancel any existing timer for this issue.
        old_handle = self._timer_handles.pop(issue_id, None)
        if old_handle:
            try:
                old_handle.cancel()
            except Exception:
                pass

        def _fire():
            self._timer_handles.pop(issue_id, None)
            try:
                self._queue.put_nowait(_MsgRetryTimer(issue_id=issue_id))
            except Exception:
                # Best-effort; if the queue cannot accept (extremely unlikely
                # with default unbounded queue), drop the timer fire.
                pass

        handle = loop.call_later(delay_ms / 1000.0, _fire)
        self._timer_handles[issue_id] = handle

        self._state.retry_attempts[issue_id] = RetryEntry(
            issue_id=issue_id,
            identifier=identifier,
            attempt=attempt,
            due_at_ms=due_at_ms,
            timer_handle=handle,
            error=error,
        )
        # Keep the issue claimed while a retry is pending.
        self._state.claimed.add(issue_id)

    async def _on_retry_timer(self, msg: _MsgRetryTimer) -> None:
        retry_entry = self._state.retry_attempts.pop(msg.issue_id, None)
        if retry_entry is None:
            return

        # Validate config; if invalid, requeue.
        ok, err = validate_dispatch_config(self._config)
        if not ok:
            self._schedule_retry(
                issue_id=msg.issue_id,
                identifier=retry_entry.identifier,
                attempt=retry_entry.attempt + 1,
                delay_ms=compute_backoff_ms(
                    retry_entry.attempt + 1, self._config.agent.max_retry_backoff_ms
                ),
                error=f"validation_error: {err}",
            )
            return

        try:
            candidates = await self._tracker.fetch_candidate_issues()
        except TrackerError as e:
            self._schedule_retry(
                issue_id=msg.issue_id,
                identifier=retry_entry.identifier,
                attempt=retry_entry.attempt + 1,
                delay_ms=compute_backoff_ms(
                    retry_entry.attempt + 1, self._config.agent.max_retry_backoff_ms
                ),
                error=f"retry poll failed: {e.code}",
            )
            return

        issue = next((i for i in candidates if i.id == msg.issue_id), None)
        if issue is None:
            self._state.claimed.discard(msg.issue_id)
            _log.info(
                "retry released claim; issue no longer a candidate",
                issue_id=msg.issue_id,
                issue_identifier=retry_entry.identifier,
            )
            return

        terminal_lower = {s.lower() for s in self._config.tracker.terminal_states}
        active_lower = {s.lower() for s in self._config.tracker.active_states}
        if (issue.state or "").lower() not in active_lower or (issue.state or "").lower() in terminal_lower:
            self._state.claimed.discard(msg.issue_id)
            _log.info(
                "retry released claim; issue not active",
                issue_id=msg.issue_id,
                issue_identifier=issue.identifier,
                state=issue.state,
            )
            return

        if not self._has_global_slot() or not self._has_state_slot(issue.state):
            self._schedule_retry(
                issue_id=msg.issue_id,
                identifier=issue.identifier,
                attempt=retry_entry.attempt + 1,
                delay_ms=compute_backoff_ms(
                    retry_entry.attempt + 1, self._config.agent.max_retry_backoff_ms
                ),
                error="no available orchestrator slots",
            )
            return

        self._dispatch_issue(issue, attempt=retry_entry.attempt)

    # ---- codex updates (token accounting, last events) ----

    async def _on_codex_update(self, msg: _MsgCodexUpdate) -> None:
        entry = self._state.running.get(msg.issue_id)
        if entry is None:
            return  # may have been terminated since the event was queued.
        ev = msg.event
        entry.session.last_codex_event = ev.event
        entry.session.last_codex_timestamp = ev.timestamp
        if ev.message:
            entry.session.last_codex_message = ev.message
        if ev.pid is not None:
            entry.session.codex_app_server_pid = ev.pid
        if ev.rate_limits:
            self._state.codex_rate_limits = ev.rate_limits
        if ev.usage:
            # Section 13.5: prefer absolute totals; track delta vs last reported.
            in_now = ev.usage.get("input_tokens", 0)
            out_now = ev.usage.get("output_tokens", 0)
            tot_now = ev.usage.get("total_tokens", 0)
            d_in = max(0, in_now - entry.session.last_reported_input_tokens)
            d_out = max(0, out_now - entry.session.last_reported_output_tokens)
            d_tot = max(0, tot_now - entry.session.last_reported_total_tokens)
            entry.session.codex_input_tokens = in_now
            entry.session.codex_output_tokens = out_now
            entry.session.codex_total_tokens = tot_now
            entry.session.last_reported_input_tokens = in_now
            entry.session.last_reported_output_tokens = out_now
            entry.session.last_reported_total_tokens = tot_now
            self._state.codex_totals.input_tokens += d_in
            self._state.codex_totals.output_tokens += d_out
            self._state.codex_totals.total_tokens += d_tot

        # Keep a small recent-events buffer for /api/v1/<id> snapshots.
        entry.recent_events.append(
            {
                "at": ev.timestamp.isoformat(),
                "event": ev.event,
                "message": ev.message or "",
            }
        )
        if len(entry.recent_events) > RECENT_EVENT_LIMIT:
            entry.recent_events = entry.recent_events[-RECENT_EVENT_LIMIT:]

    # ---- reconciliation (Section 8.5) ----

    async def _reconcile_running_issues(self) -> None:
        await self._reconcile_stalled_runs()

        ids = list(self._state.running.keys())
        if not ids:
            return
        try:
            refreshed = await self._tracker.fetch_issue_states_by_ids(ids)
        except TrackerError as e:
            _log.warning(
                "running-state refresh failed; keeping workers running",
                code=e.code,
                error=str(e),
            )
            return

        terminal_lower = {s.lower() for s in self._config.tracker.terminal_states}
        active_lower = {s.lower() for s in self._config.tracker.active_states}
        by_id = {i.id: i for i in refreshed}

        for issue_id in ids:
            entry = self._state.running.get(issue_id)
            if entry is None:
                continue
            issue = by_id.get(issue_id)
            if issue is None:
                continue
            s = (issue.state or "").lower()
            if s in terminal_lower:
                await self._terminate_running_issue(issue_id, cleanup_workspace=True)
            elif s in active_lower:
                # Update in-memory snapshot.
                entry.issue = issue
            else:
                await self._terminate_running_issue(issue_id, cleanup_workspace=False)

    async def _reconcile_stalled_runs(self) -> None:
        stall_ms = self._config.codex.stall_timeout_ms
        if stall_ms <= 0:
            return
        loop_now_ts = now_utc().timestamp()
        for issue_id, entry in list(self._state.running.items()):
            if entry.session.last_codex_timestamp is not None:
                last = entry.session.last_codex_timestamp.timestamp()
            else:
                last = entry.started_at.timestamp()
            elapsed_ms = (loop_now_ts - last) * 1000.0
            if elapsed_ms > stall_ms:
                _log.warning(
                    "stall detected; terminating worker",
                    issue_id=issue_id,
                    issue_identifier=entry.identifier,
                    elapsed_ms=int(elapsed_ms),
                )
                # Schedule a retry on stall by terminating WITHOUT cleanup.
                await self._terminate_running_issue(
                    issue_id, cleanup_workspace=False, force_retry=True
                )

    async def _terminate_running_issue(
        self,
        issue_id: str,
        *,
        cleanup_workspace: bool,
        force_retry: bool = False,
    ) -> None:
        entry = self._state.running.get(issue_id)
        if entry is None:
            return
        # Cancel the worker task so its exit handler runs and posts WorkerExit.
        # We mark the eventual exit as abnormal when force_retry=True so the
        # orchestrator schedules a backoff retry; otherwise the cancellation
        # exit flows through `_on_worker_exit` where reason=="cancelled" path
        # treats it as abnormal anyway.
        task = self._worker_tasks.get(issue_id)
        if task and not task.done():
            task.cancel()
        # Wait for the worker to actually exit so reconciliation cleanup runs
        # serially.
        if task is not None:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        if cleanup_workspace:
            try:
                await self._workspaces.remove_for(entry.identifier)
            except Exception as e:
                _log.warning(
                    "workspace cleanup failed",
                    issue_id=issue_id,
                    issue_identifier=entry.identifier,
                    error=str(e),
                )

        # Cancel any retry that the worker exit handler may have scheduled,
        # then drop the claim. Without this the issue would otherwise remain
        # claimed via continuation/backoff retry after we just terminated it
        # because it became terminal/non-active.
        if not force_retry:
            self._state.claimed.discard(issue_id)
            handle = self._timer_handles.pop(issue_id, None)
            if handle:
                try:
                    handle.cancel()
                except Exception:
                    pass
            self._state.retry_attempts.pop(issue_id, None)

    # ---- startup cleanup (Section 8.6) ----

    async def _startup_terminal_workspace_cleanup(self) -> None:
        try:
            terminal_issues = await self._tracker.fetch_issues_by_states(
                self._config.tracker.terminal_states
            )
        except Exception as e:
            _log.warning("startup terminal cleanup fetch failed", error=str(e))
            return
        for issue in terminal_issues:
            if not issue.identifier:
                continue
            try:
                await self._workspaces.remove_for(issue.identifier)
            except Exception as e:
                _log.warning(
                    "startup terminal cleanup failed",
                    issue_identifier=issue.identifier,
                    error=str(e),
                )

    # ---- config reload (Section 6.2) ----

    async def _on_config_reload(self, msg: _MsgConfigReload) -> None:
        new_cfg = msg.config
        ok, err = validate_dispatch_config(new_cfg)
        if not ok:
            self._last_validation_error = str(err)
            _log.error(
                "ignoring invalid workflow reload; keeping last good config",
                code=getattr(err, "code", None),
                error=str(err),
            )
            return

        self._config = new_cfg
        self._workflow = msg.workflow
        # Live-apply numeric settings to the existing state.
        self._state.poll_interval_ms = new_cfg.polling.interval_ms
        self._state.max_concurrent_agents = new_cfg.agent.max_concurrent_agents
        # Update the workspace manager so future workspaces use the new
        # root/hooks/timeout.
        self._workspaces.update_config(workspace=new_cfg.workspace, hooks=new_cfg.hooks)
        # Update the tracker config (endpoint, key, project, states).
        try:
            self._tracker.update_config(new_cfg.tracker)  # type: ignore[attr-defined]
        except AttributeError:
            pass
        self._last_validation_error = None
        _log.info(
            "workflow reloaded",
            poll_interval_ms=new_cfg.polling.interval_ms,
            max_concurrent_agents=new_cfg.agent.max_concurrent_agents,
        )


class _WorkerFailure(Exception):
    """Internal worker failure carrier; the worker translates this into
    a non-normal exit reason for the orchestrator."""

    def __init__(self, *, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
