"""Workspace manager (Section 9)."""

from __future__ import annotations

import os
import shutil
from typing import Optional

from .config import HooksConfig, ServiceConfig, WorkspaceConfig
from .domain import Workspace, sanitize_workspace_key
from .errors import HookFailure, HookTimeout, WorkspaceError
from .hooks import run_hook, run_hook_best_effort
from .logger import get_logger

_log = get_logger("workspace")


class WorkspaceManager:
    def __init__(self, *, workspace: WorkspaceConfig, hooks: HooksConfig) -> None:
        self._workspace = workspace
        self._hooks = hooks

    def update_config(self, *, workspace: WorkspaceConfig, hooks: HooksConfig) -> None:
        self._workspace = workspace
        self._hooks = hooks

    @property
    def root(self) -> str:
        return self._workspace.root

    def _path_for(self, identifier: str) -> str:
        key = sanitize_workspace_key(identifier)
        return os.path.normpath(os.path.join(self._workspace.root, key))

    def _validate_within_root(self, path: str) -> None:
        """Section 9.5 invariant 2: workspace path stays under workspace root."""
        root = os.path.abspath(self._workspace.root)
        target = os.path.abspath(path)
        # Ensure the prefix is a real parent directory boundary, not just a
        # string prefix (e.g., /tmp/sym vs /tmp/symphony).
        try:
            common = os.path.commonpath([root, target])
        except ValueError:
            raise WorkspaceError(
                f"workspace path {target!r} not under root {root!r}"
            )
        if common != root:
            raise WorkspaceError(
                f"workspace path {target!r} not under root {root!r}"
            )

    async def ensure_for(self, identifier: str) -> Workspace:
        """Create or reuse the per-issue workspace directory."""
        if not identifier or not isinstance(identifier, str):
            raise WorkspaceError("issue identifier required for workspace creation")

        os.makedirs(self._workspace.root, exist_ok=True)
        path = self._path_for(identifier)
        self._validate_within_root(path)

        # Section 17.2: handle existing non-directory at workspace location safely.
        if os.path.exists(path) and not os.path.isdir(path):
            raise WorkspaceError(
                f"workspace path exists but is not a directory: {path}",
                path=path,
            )

        created_now = False
        if not os.path.isdir(path):
            try:
                os.makedirs(path, exist_ok=False)
            except FileExistsError:
                # Race against another worker - safe to reuse.
                pass
            else:
                created_now = True

        ws = Workspace(
            path=path,
            workspace_key=sanitize_workspace_key(identifier),
            created_now=created_now,
        )

        if created_now and self._hooks.after_create:
            try:
                await run_hook(
                    "after_create",
                    self._hooks.after_create,
                    cwd=path,
                    timeout_ms=self._hooks.timeout_ms,
                    issue_identifier=identifier,
                )
            except (HookFailure, HookTimeout) as e:
                # Section 9.4: after_create failure is fatal to creation.
                # Roll back the partial directory so the next attempt can retry.
                _log.warning(
                    "after_create failed, removing partial workspace",
                    path=path,
                    issue_identifier=identifier,
                    error_code=e.code,
                )
                shutil.rmtree(path, ignore_errors=True)
                raise WorkspaceError(
                    f"after_create hook failed for {identifier}: {e}",
                    code=e.code,
                ) from e

        return ws

    async def remove_for(self, identifier: str) -> None:
        """Run before_remove (best-effort) and delete the directory tree."""
        path = self._path_for(identifier)
        self._validate_within_root(path)
        if not os.path.isdir(path):
            return
        if self._hooks.before_remove:
            await run_hook_best_effort(
                "before_remove",
                self._hooks.before_remove,
                cwd=path,
                timeout_ms=self._hooks.timeout_ms,
                issue_identifier=identifier,
            )
        try:
            shutil.rmtree(path, ignore_errors=False)
        except OSError as e:
            _log.warning(
                "workspace cleanup failed",
                path=path,
                issue_identifier=identifier,
                error=str(e),
            )

    def expected_path(self, identifier: str) -> str:
        return self._path_for(identifier)


def assert_cwd_is_workspace(workspace_path: str, requested_cwd: str) -> None:
    """Section 9.5 invariant 1: validate cwd before launching the agent."""
    if os.path.abspath(workspace_path) != os.path.abspath(requested_cwd):
        raise WorkspaceError(
            f"agent cwd {requested_cwd!r} does not match workspace path {workspace_path!r}"
        )
