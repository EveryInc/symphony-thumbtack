"""Domain entities for Symphony orchestration (Section 4)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set


_WORKSPACE_KEY_RE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_workspace_key(identifier: str) -> str:
    """Section 4.2 / 9.5: replace any char outside [A-Za-z0-9._-] with '_'."""
    return _WORKSPACE_KEY_RE.sub("_", identifier)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class BlockerRef:
    id: Optional[str] = None
    identifier: Optional[str] = None
    state: Optional[str] = None


@dataclass
class Issue:
    """Normalized issue record (Section 4.1.1)."""

    id: str
    identifier: str
    title: str
    state: str
    description: Optional[str] = None
    priority: Optional[int] = None
    branch_name: Optional[str] = None
    url: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    blocked_by: List[BlockerRef] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def for_template(self) -> Dict[str, Any]:
        """Render-friendly view: dicts and primitives only."""
        return {
            "id": self.id,
            "identifier": self.identifier,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "state": self.state,
            "branch_name": self.branch_name,
            "url": self.url,
            "labels": list(self.labels),
            "blocked_by": [
                {"id": b.id, "identifier": b.identifier, "state": b.state}
                for b in self.blocked_by
            ],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class Workspace:
    """Per-issue filesystem workspace (Section 4.1.4)."""

    path: str
    workspace_key: str
    created_now: bool


@dataclass
class LiveSession:
    """Codex session metadata (Section 4.1.6)."""

    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    turn_id: Optional[str] = None
    codex_app_server_pid: Optional[int] = None
    last_codex_event: Optional[str] = None
    last_codex_timestamp: Optional[datetime] = None
    last_codex_message: Optional[str] = None
    codex_input_tokens: int = 0
    codex_output_tokens: int = 0
    codex_total_tokens: int = 0
    last_reported_input_tokens: int = 0
    last_reported_output_tokens: int = 0
    last_reported_total_tokens: int = 0
    turn_count: int = 0


@dataclass
class RunningEntry:
    """A live worker entry in `state.running` (Section 16.4)."""

    issue_id: str
    identifier: str
    issue: Issue
    started_at: datetime
    retry_attempt: Optional[int]
    session: LiveSession = field(default_factory=LiveSession)
    worker_task: Optional[Any] = None  # asyncio.Task
    workspace_path: Optional[str] = None
    last_error: Optional[str] = None
    recent_events: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RetryEntry:
    """Scheduled retry state (Section 4.1.7)."""

    issue_id: str
    identifier: Optional[str]
    attempt: int
    due_at_ms: float  # monotonic clock ms
    timer_handle: Any = None  # asyncio.TimerHandle / Task
    error: Optional[str] = None


@dataclass
class CodexTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    seconds_running: float = 0.0  # cumulative ended-session runtime


@dataclass
class OrchestratorState:
    """Single authoritative in-memory state (Section 4.1.8)."""

    poll_interval_ms: int
    max_concurrent_agents: int
    running: Dict[str, RunningEntry] = field(default_factory=dict)
    claimed: Set[str] = field(default_factory=set)
    retry_attempts: Dict[str, RetryEntry] = field(default_factory=dict)
    completed: Set[str] = field(default_factory=set)
    codex_totals: CodexTotals = field(default_factory=CodexTotals)
    codex_rate_limits: Optional[Dict[str, Any]] = None
