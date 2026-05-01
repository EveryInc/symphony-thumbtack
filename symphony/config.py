"""Typed config layer (Section 5.3, 6).

The `ServiceConfig` is the typed view of WORKFLOW.md front matter combined
with environment-variable indirection and defaults.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .errors import (
    ConfigValidationError,
    MissingTrackerApiKey,
    MissingTrackerProjectSlug,
    UnsupportedTrackerKind,
)


SUPPORTED_TRACKER_KINDS = {"linear"}
LINEAR_DEFAULT_ENDPOINT = "https://api.linear.app/graphql"
LINEAR_DEFAULT_API_KEY_ENV = "LINEAR_API_KEY"

DEFAULT_ACTIVE_STATES = ["Todo", "In Progress"]
DEFAULT_TERMINAL_STATES = ["Closed", "Cancelled", "Canceled", "Duplicate", "Done"]

DEFAULT_POLL_INTERVAL_MS = 30_000
DEFAULT_HOOK_TIMEOUT_MS = 60_000
DEFAULT_MAX_CONCURRENT_AGENTS = 10
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_RETRY_BACKOFF_MS = 300_000
DEFAULT_CODEX_COMMAND = "codex app-server"
DEFAULT_TURN_TIMEOUT_MS = 3_600_000
DEFAULT_READ_TIMEOUT_MS = 5_000
DEFAULT_STALL_TIMEOUT_MS = 300_000
DEFAULT_FALLBACK_PROMPT = "You are working on an issue from Linear."

SUPPORTED_AGENT_KINDS = {"codex", "claude"}
DEFAULT_AGENT_KIND = "codex"
DEFAULT_CLAUDE_COMMAND = "claude"
DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"

_VAR_REF_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")


def _resolve_var(value: Optional[str]) -> Optional[str]:
    """Resolve `$VAR_NAME` indirection. Returns the original value if not a $VAR.

    Per Section 5.3.1: "If `$VAR_NAME` resolves to an empty string, treat the key
    as missing." Returning `""` here propagates that.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return value  # let coercion fail later
    m = _VAR_REF_RE.match(value)
    if not m:
        return value
    return os.environ.get(m.group(1), "")


def _expand_path(value: Optional[str]) -> Optional[str]:
    """Apply `~` and `$VAR` expansion only to local filesystem paths.

    Per Section 6.1, do not rewrite URIs or arbitrary shell command strings.
    """
    if value is None:
        return None
    expanded = os.path.expanduser(value)
    expanded = os.path.expandvars(expanded)
    return expanded


def _coerce_int(value: Any, name: str, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        # bool is a subclass of int in Python; reject explicitly.
        raise ConfigValidationError(f"{name} must be an integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise ConfigValidationError(f"{name} must be an integer, got {value!r}")


def _coerce_positive_int(value: Any, name: str, *, default: int) -> int:
    n = _coerce_int(value, name, default=default)
    if n <= 0:
        raise ConfigValidationError(f"{name} must be > 0, got {n}")
    return n


def _coerce_str_list(value: Any, name: str, *, default: List[str]) -> List[str]:
    if value is None:
        return list(default)
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ConfigValidationError(f"{name} must be a list of strings")
    return list(value)


@dataclass
class TrackerConfig:
    kind: Optional[str]
    endpoint: str
    api_key: Optional[str]  # resolved via $VAR if applicable; "" -> None
    project_slug: Optional[str]
    active_states: List[str]
    terminal_states: List[str]


@dataclass
class HooksConfig:
    after_create: Optional[str]
    before_run: Optional[str]
    after_run: Optional[str]
    before_remove: Optional[str]
    timeout_ms: int


@dataclass
class AgentConfig:
    max_concurrent_agents: int
    max_turns: int
    max_retry_backoff_ms: int
    max_concurrent_agents_by_state: Dict[str, int]
    kind: str  # "codex" | "claude"


@dataclass
class CodexConfig:
    command: str
    approval_policy: Optional[str]
    thread_sandbox: Optional[str]
    turn_sandbox_policy: Optional[str]
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int


@dataclass
class ClaudeConfig:
    """Front-matter `claude:` block. Used when agent.kind == "claude".

    Maps to Claude Code CLI's headless `-p` mode with streaming JSON output.
    Each turn launches one `claude -p` subprocess; continuation turns reuse
    the captured `session_id` via `--resume`.
    """

    command: str
    permission_mode: str
    model: Optional[str]
    add_dirs: List[str]
    extra_args: List[str]
    turn_timeout_ms: int
    stall_timeout_ms: int


@dataclass
class WorkspaceConfig:
    root: str  # absolute path


@dataclass
class PollingConfig:
    interval_ms: int


@dataclass
class ServiceConfig:
    """Typed view of all front matter keys, plus the workflow source dir."""

    tracker: TrackerConfig
    polling: PollingConfig
    workspace: WorkspaceConfig
    hooks: HooksConfig
    agent: AgentConfig
    codex: CodexConfig
    claude: ClaudeConfig
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal_state(self):
        terminal = {s.lower() for s in self.tracker.terminal_states}
        return lambda s: s.lower() in terminal

    @property
    def is_active_state(self):
        active = {s.lower() for s in self.tracker.active_states}
        return lambda s: s.lower() in active


def _normalize_state_concurrency(raw: Any) -> Dict[str, int]:
    """Per Section 5.3.5, lowercase keys and ignore non-positive/non-numeric."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, int] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        out[k.lower()] = n
    return out


def _resolve_workspace_root(raw: Any, source_dir: str) -> str:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return os.path.join(tempfile.gettempdir(), "symphony_workspaces")
    if not isinstance(raw, str):
        raise ConfigValidationError(f"workspace.root must be a string, got {raw!r}")
    expanded = _expand_path(raw)
    if not expanded:
        raise ConfigValidationError("workspace.root resolved to empty string")
    if not os.path.isabs(expanded):
        # Relative to the directory containing WORKFLOW.md (Section 5.3.3).
        expanded = os.path.normpath(os.path.join(source_dir, expanded))
    return os.path.abspath(expanded)


def build_service_config(raw: Mapping[str, Any], source_path: str) -> ServiceConfig:
    """Build a typed `ServiceConfig` from raw front matter + source path."""
    source_dir = os.path.dirname(os.path.abspath(source_path))

    tracker_raw = raw.get("tracker") or {}
    if not isinstance(tracker_raw, dict):
        raise ConfigValidationError("`tracker` must be a map")

    kind_raw = tracker_raw.get("kind")
    kind = kind_raw if isinstance(kind_raw, str) and kind_raw.strip() else None
    endpoint_default = (
        LINEAR_DEFAULT_ENDPOINT if kind == "linear" else LINEAR_DEFAULT_ENDPOINT
    )
    endpoint = tracker_raw.get("endpoint") or endpoint_default

    api_key_raw = tracker_raw.get("api_key")
    if api_key_raw is None and kind == "linear":
        # Default canonical env when literal is missing.
        api_key = os.environ.get(LINEAR_DEFAULT_API_KEY_ENV) or None
    else:
        resolved = _resolve_var(api_key_raw)
        api_key = resolved if resolved else None  # "" -> None

    project_slug_raw = tracker_raw.get("project_slug")
    project_slug_resolved = (
        _resolve_var(project_slug_raw) if isinstance(project_slug_raw, str) else None
    )
    project_slug = (
        project_slug_resolved.strip()
        if isinstance(project_slug_resolved, str) and project_slug_resolved.strip()
        else None
    )

    active_states = _coerce_str_list(
        tracker_raw.get("active_states"), "tracker.active_states", default=DEFAULT_ACTIVE_STATES
    )
    terminal_states = _coerce_str_list(
        tracker_raw.get("terminal_states"),
        "tracker.terminal_states",
        default=DEFAULT_TERMINAL_STATES,
    )

    polling_raw = raw.get("polling") or {}
    if not isinstance(polling_raw, dict):
        raise ConfigValidationError("`polling` must be a map")
    polling = PollingConfig(
        interval_ms=_coerce_positive_int(
            polling_raw.get("interval_ms"),
            "polling.interval_ms",
            default=DEFAULT_POLL_INTERVAL_MS,
        )
    )

    workspace_raw = raw.get("workspace") or {}
    if not isinstance(workspace_raw, dict):
        raise ConfigValidationError("`workspace` must be a map")
    workspace = WorkspaceConfig(
        root=_resolve_workspace_root(workspace_raw.get("root"), source_dir)
    )

    hooks_raw = raw.get("hooks") or {}
    if not isinstance(hooks_raw, dict):
        raise ConfigValidationError("`hooks` must be a map")
    hooks = HooksConfig(
        after_create=hooks_raw.get("after_create") or None,
        before_run=hooks_raw.get("before_run") or None,
        after_run=hooks_raw.get("after_run") or None,
        before_remove=hooks_raw.get("before_remove") or None,
        timeout_ms=_coerce_positive_int(
            hooks_raw.get("timeout_ms"), "hooks.timeout_ms", default=DEFAULT_HOOK_TIMEOUT_MS
        ),
    )

    agent_raw = raw.get("agent") or {}
    if not isinstance(agent_raw, dict):
        raise ConfigValidationError("`agent` must be a map")
    kind_raw = agent_raw.get("kind", DEFAULT_AGENT_KIND)
    if not isinstance(kind_raw, str) or not kind_raw.strip():
        raise ConfigValidationError("agent.kind must be a non-empty string")
    agent_kind = kind_raw.strip().lower()
    if agent_kind not in SUPPORTED_AGENT_KINDS:
        raise ConfigValidationError(
            f"agent.kind must be one of {sorted(SUPPORTED_AGENT_KINDS)}, got {kind_raw!r}"
        )
    agent = AgentConfig(
        max_concurrent_agents=_coerce_positive_int(
            agent_raw.get("max_concurrent_agents"),
            "agent.max_concurrent_agents",
            default=DEFAULT_MAX_CONCURRENT_AGENTS,
        ),
        max_turns=_coerce_positive_int(
            agent_raw.get("max_turns"), "agent.max_turns", default=DEFAULT_MAX_TURNS
        ),
        max_retry_backoff_ms=_coerce_int(
            agent_raw.get("max_retry_backoff_ms"),
            "agent.max_retry_backoff_ms",
            default=DEFAULT_MAX_RETRY_BACKOFF_MS,
        ),
        max_concurrent_agents_by_state=_normalize_state_concurrency(
            agent_raw.get("max_concurrent_agents_by_state")
        ),
        kind=agent_kind,
    )

    codex_raw = raw.get("codex") or {}
    if not isinstance(codex_raw, dict):
        raise ConfigValidationError("`codex` must be a map")
    codex_command_raw = codex_raw.get("command")
    if codex_command_raw is None:
        codex_command = DEFAULT_CODEX_COMMAND
    elif isinstance(codex_command_raw, str) and codex_command_raw.strip():
        codex_command = codex_command_raw
    else:
        raise ConfigValidationError("codex.command must be a non-empty string")
    codex = CodexConfig(
        command=codex_command,
        approval_policy=codex_raw.get("approval_policy"),
        thread_sandbox=codex_raw.get("thread_sandbox"),
        turn_sandbox_policy=codex_raw.get("turn_sandbox_policy"),
        turn_timeout_ms=_coerce_positive_int(
            codex_raw.get("turn_timeout_ms"),
            "codex.turn_timeout_ms",
            default=DEFAULT_TURN_TIMEOUT_MS,
        ),
        read_timeout_ms=_coerce_positive_int(
            codex_raw.get("read_timeout_ms"),
            "codex.read_timeout_ms",
            default=DEFAULT_READ_TIMEOUT_MS,
        ),
        stall_timeout_ms=_coerce_int(
            codex_raw.get("stall_timeout_ms"),
            "codex.stall_timeout_ms",
            default=DEFAULT_STALL_TIMEOUT_MS,
        ),
    )

    claude_raw = raw.get("claude") or {}
    if not isinstance(claude_raw, dict):
        raise ConfigValidationError("`claude` must be a map")
    claude_command_raw = claude_raw.get("command")
    if claude_command_raw is None:
        claude_command = DEFAULT_CLAUDE_COMMAND
    elif isinstance(claude_command_raw, str) and claude_command_raw.strip():
        claude_command = claude_command_raw
    else:
        raise ConfigValidationError("claude.command must be a non-empty string")
    claude_perm_raw = claude_raw.get("permission_mode", DEFAULT_CLAUDE_PERMISSION_MODE)
    if not isinstance(claude_perm_raw, str) or not claude_perm_raw.strip():
        raise ConfigValidationError("claude.permission_mode must be a non-empty string")
    claude_model = claude_raw.get("model")
    if claude_model is not None and not isinstance(claude_model, str):
        raise ConfigValidationError("claude.model must be a string when set")
    add_dirs_raw = claude_raw.get("add_dirs") or []
    if not isinstance(add_dirs_raw, list) or not all(isinstance(x, str) for x in add_dirs_raw):
        raise ConfigValidationError("claude.add_dirs must be a list of strings")
    extra_args_raw = claude_raw.get("extra_args") or []
    if not isinstance(extra_args_raw, list) or not all(isinstance(x, str) for x in extra_args_raw):
        raise ConfigValidationError("claude.extra_args must be a list of strings")
    claude = ClaudeConfig(
        command=claude_command,
        permission_mode=claude_perm_raw,
        model=claude_model,
        add_dirs=list(add_dirs_raw),
        extra_args=list(extra_args_raw),
        turn_timeout_ms=_coerce_positive_int(
            claude_raw.get("turn_timeout_ms"),
            "claude.turn_timeout_ms",
            default=DEFAULT_TURN_TIMEOUT_MS,
        ),
        stall_timeout_ms=_coerce_int(
            claude_raw.get("stall_timeout_ms"),
            "claude.stall_timeout_ms",
            default=DEFAULT_STALL_TIMEOUT_MS,
        ),
    )

    known = {"tracker", "polling", "workspace", "hooks", "agent", "codex", "claude"}
    extras = {k: v for k, v in raw.items() if k not in known}

    return ServiceConfig(
        tracker=TrackerConfig(
            kind=kind,
            endpoint=endpoint,
            api_key=api_key,
            project_slug=project_slug,
            active_states=active_states,
            terminal_states=terminal_states,
        ),
        polling=polling,
        workspace=workspace,
        hooks=hooks,
        agent=agent,
        codex=codex,
        claude=claude,
        extras=extras,
    )


def validate_dispatch_config(cfg: ServiceConfig) -> Tuple[bool, Optional[Exception]]:
    """Section 6.3 dispatch preflight validation.

    Returns `(ok, error)`. If `ok` is False, `error` is the typed exception that
    callers SHOULD log/surface but not raise (so the orchestrator can keep
    reconciling).
    """
    try:
        if not cfg.tracker.kind:
            raise UnsupportedTrackerKind("tracker.kind is required")
        if cfg.tracker.kind not in SUPPORTED_TRACKER_KINDS:
            raise UnsupportedTrackerKind(
                f"unsupported tracker kind: {cfg.tracker.kind!r}"
            )
        if cfg.tracker.kind == "linear":
            if not cfg.tracker.api_key:
                raise MissingTrackerApiKey(
                    "tracker.api_key is required (set in front matter or "
                    f"the {LINEAR_DEFAULT_API_KEY_ENV} env var)"
                )
            if not cfg.tracker.project_slug:
                raise MissingTrackerProjectSlug("tracker.project_slug is required")
        if cfg.agent.kind == "claude":
            if not cfg.claude.command.strip():
                raise ConfigValidationError("claude.command must be non-empty")
        else:
            if not cfg.codex.command.strip():
                raise ConfigValidationError("codex.command must be non-empty")
    except Exception as e:
        return False, e
    return True, None
