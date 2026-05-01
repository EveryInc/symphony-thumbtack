"""WORKFLOW.md change watcher (Section 6.2).

We poll mtime+size at a low cadence to avoid pulling in `watchdog` as a
dependency. The watcher fires the orchestrator's reload callback on changes.
"""

from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Optional, Tuple

from .config import build_service_config
from .dotenv import load_dotenv
from .errors import SymphonyError
from .logger import get_logger
from .workflow import WorkflowDefinition, load_workflow

_log = get_logger("watcher")

_POLL_INTERVAL_S = 1.0


async def watch_workflow(
    path: str,
    *,
    on_reload: Callable[[WorkflowDefinition, "object"], Awaitable[None]],
    stop_event: asyncio.Event,
) -> None:
    """Watch `path` for changes; call `on_reload(workflow, config)` when it changes.

    `on_reload` callbacks should return promptly. Failed reloads are logged
    and the watcher keeps running with the last known good (in-memory) state.
    """
    last_signature: Optional[Tuple[float, int]] = _read_signature(path)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_POLL_INTERVAL_S)
            return
        except asyncio.TimeoutError:
            pass

        sig = _read_signature(path)
        if sig is None:
            # File was removed/inaccessible; keep last known good.
            if last_signature is not None:
                _log.warning("workflow file disappeared; keeping last known good", path=path)
                last_signature = None
            continue

        if sig == last_signature:
            continue

        last_signature = sig
        _log.info("workflow file changed; reloading", path=path)
        try:
            # Re-source `.env` before parsing so newly added secrets take
            # effect on the same reload cycle.
            load_dotenv(os.path.join(os.path.dirname(path), ".env"))
            wf = load_workflow(path)
            cfg = build_service_config(wf.config, source_path=wf.source_path)
        except SymphonyError as e:
            _log.error(
                "workflow reload parse/validation failed; keeping last good",
                code=e.code,
                error=str(e),
                path=path,
            )
            continue
        except Exception as e:
            _log.exception("workflow reload crashed; keeping last good", path=path, error=str(e))
            continue

        try:
            await on_reload(wf, cfg)
        except Exception as e:
            _log.exception("workflow reload callback failed", error=str(e), path=path)


def _read_signature(path: str) -> Optional[Tuple[float, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime, st.st_size)
