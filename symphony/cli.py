"""CLI entry point (Section 17.7)."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from typing import Optional

from . import __version__
from .config import ServiceConfig, build_service_config
from .dotenv import load_dotenv
from .errors import MissingWorkflowFile, SymphonyError
from .logger import configure_logging, get_logger
from .orchestrator import Orchestrator
from .tracker import build_tracker
from .watcher import watch_workflow
from .workflow import WorkflowDefinition, load_workflow
from .workspace import WorkspaceManager

_log = get_logger("cli")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="symphony",
        description="Orchestrate coding agents against issue tracker work.",
    )
    p.add_argument(
        "workflow_path",
        nargs="?",
        default=None,
        help="Path to WORKFLOW.md. Defaults to ./WORKFLOW.md when omitted.",
    )
    p.add_argument(
        "--version", action="version", version=f"symphony {__version__}"
    )
    return p


def _resolve_workflow_path(arg: Optional[str]) -> str:
    """Section 5.1 / 17.7: explicit path wins, otherwise ./WORKFLOW.md.

    Both are validated to exist; missing path is a CLI error.
    """
    if arg:
        path = os.path.abspath(os.path.expanduser(arg))
        if not os.path.isfile(path):
            raise MissingWorkflowFile(f"workflow path not found: {path}", path=path)
        return path
    default = os.path.abspath("WORKFLOW.md")
    if not os.path.isfile(default):
        raise MissingWorkflowFile(
            f"no WORKFLOW.md in current directory ({os.getcwd()}); pass an explicit path",
            path=default,
        )
    return default


async def _run(workflow_path: str) -> int:
    wf: WorkflowDefinition = load_workflow(workflow_path)
    cfg: ServiceConfig = build_service_config(wf.config, source_path=wf.source_path)

    tracker = build_tracker(cfg.tracker)
    workspaces = WorkspaceManager(workspace=cfg.workspace, hooks=cfg.hooks)
    orchestrator = Orchestrator(
        config=cfg, workflow=wf, tracker=tracker, workspaces=workspaces
    )

    stop_event = asyncio.Event()

    def _on_signal():
        if not stop_event.is_set():
            _log.info("shutdown signal received")
            stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows / non-POSIX hosts.
            pass

    async def _on_reload(new_wf: WorkflowDefinition, new_cfg: ServiceConfig) -> None:
        await orchestrator.reload_workflow(config=new_cfg, workflow=new_wf)

    watcher_task = asyncio.create_task(
        watch_workflow(workflow_path, on_reload=_on_reload, stop_event=stop_event),
        name="symphony-workflow-watcher",
    )

    orch_task = asyncio.create_task(orchestrator.run(), name="symphony-orchestrator")

    async def _shutdown_on_signal():
        await stop_event.wait()
        await orchestrator.shutdown()

    shutdown_task = asyncio.create_task(_shutdown_on_signal(), name="symphony-shutdown")

    try:
        await orch_task
    except SymphonyError as e:
        _log.error("startup failed", code=e.code, error=str(e))
        stop_event.set()
        return 1
    finally:
        stop_event.set()
        for t in (watcher_task, shutdown_task):
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    return 0


def main(argv: Optional[list] = None) -> int:
    configure_logging()
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        workflow_path = _resolve_workflow_path(args.workflow_path)
    except MissingWorkflowFile as e:
        print(f"symphony: {e}", file=sys.stderr)
        return 2

    # Auto-load `.env` from the workflow file's directory before resolving
    # `$VAR` indirection in front matter. Shell-exported values still win.
    load_dotenv(os.path.join(os.path.dirname(workflow_path), ".env"))

    try:
        return asyncio.run(_run(workflow_path))
    except KeyboardInterrupt:
        return 130
    except SymphonyError as e:
        print(f"symphony: startup failed: {e.code}: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"symphony: unexpected failure: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
