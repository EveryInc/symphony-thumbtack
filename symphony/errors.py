"""Typed errors raised across Symphony components.

Error category names follow Section 5.5, 10.6, and 11.4 of SPEC.md.
"""

from __future__ import annotations

from typing import Any, Optional


class SymphonyError(Exception):
    """Base for all Symphony errors. Carries a stable category code."""

    code: str = "symphony_error"

    def __init__(self, message: str, *, code: Optional[str] = None, **details: Any) -> None:
        super().__init__(message)
        if code:
            self.code = code
        self.details = details

    def __repr__(self) -> str:
        return f"<{type(self).__name__} code={self.code} message={self.args[0]!r}>"


# Workflow / config errors (Section 5.5).
class MissingWorkflowFile(SymphonyError):
    code = "missing_workflow_file"


class WorkflowParseError(SymphonyError):
    code = "workflow_parse_error"


class WorkflowFrontMatterNotAMap(SymphonyError):
    code = "workflow_front_matter_not_a_map"


class TemplateParseError(SymphonyError):
    code = "template_parse_error"


class TemplateRenderError(SymphonyError):
    code = "template_render_error"


class ConfigValidationError(SymphonyError):
    code = "config_validation_error"


# Tracker errors (Section 11.4).
class TrackerError(SymphonyError):
    code = "tracker_error"


class UnsupportedTrackerKind(TrackerError):
    code = "unsupported_tracker_kind"


class MissingTrackerApiKey(TrackerError):
    code = "missing_tracker_api_key"


class MissingTrackerProjectSlug(TrackerError):
    code = "missing_tracker_project_slug"


class LinearApiRequest(TrackerError):
    code = "linear_api_request"


class LinearApiStatus(TrackerError):
    code = "linear_api_status"


class LinearGraphqlErrors(TrackerError):
    code = "linear_graphql_errors"


class LinearUnknownPayload(TrackerError):
    code = "linear_unknown_payload"


class LinearMissingEndCursor(TrackerError):
    code = "linear_missing_end_cursor"


# Agent errors (Section 10.6).
class CodexNotFound(SymphonyError):
    code = "codex_not_found"


class InvalidWorkspaceCwd(SymphonyError):
    code = "invalid_workspace_cwd"


class ResponseTimeout(SymphonyError):
    code = "response_timeout"


class TurnTimeout(SymphonyError):
    code = "turn_timeout"


class PortExit(SymphonyError):
    code = "port_exit"


class ResponseError(SymphonyError):
    code = "response_error"


class TurnFailed(SymphonyError):
    code = "turn_failed"


class TurnCancelled(SymphonyError):
    code = "turn_cancelled"


class TurnInputRequired(SymphonyError):
    code = "turn_input_required"


# Workspace / hook errors.
class WorkspaceError(SymphonyError):
    code = "workspace_error"


class HookFailure(SymphonyError):
    code = "hook_failure"


class HookTimeout(SymphonyError):
    code = "hook_timeout"
