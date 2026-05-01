#!/usr/bin/env bash
# Run Symphony in foreground with demo-friendly log output.
#
# Sources config.env (the ONE config file) before launch so:
#   - LINEAR_API_KEY / LINEAR_PROJECT_SLUG are resolvable in WORKFLOW.md
#   - TARGET_REPO / SYMPHONY_DIR are inherited by hook subprocesses
#
# Output behavior:
#   - Logs stream to your terminal in real time (line-buffered through awk/grep).
#   - The same stream is also tee'd to symphony.log for replay.
#
# Usage:
#   scripts/run.sh                # default: filtered + colored + tee'd
#   scripts/run.sh --raw          # no filter, no color, just tee
#   scripts/run.sh --debug        # SYMPHONY_LOG_LEVEL=debug

set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f config.env ]; then
  echo "config.env missing. Copy config.env.example → config.env and fill it in." >&2
  exit 1
fi
# shellcheck disable=SC1091
source config.env

# Activate the bootstrap-created venv so `symphony` resolves to .venv/bin/symphony.
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

filter=1
debug=0
for arg in "$@"; do
  case "$arg" in
    --raw)   filter=0 ;;
    --debug) debug=1 ;;
    -h|--help)
      head -18 "$0" | tail -14
      exit 0
      ;;
  esac
done

if [ "$debug" = "1" ]; then
  export SYMPHONY_LOG_LEVEL=debug
fi

# Force Python to flush stderr per line so structured logs appear in real time
# rather than block-buffered chunks.
export PYTHONUNBUFFERED=1

if ! command -v symphony >/dev/null 2>&1; then
  echo "symphony binary not on PATH. Run scripts/bootstrap.sh first." >&2
  exit 1
fi

log_file="symphony.log"
echo "==> Logs stream live to this terminal AND tail to $log_file."
echo "==> Tip: in a second pane, run \`scripts/watch-agents.sh\` to see what"
echo "    each Claude agent is doing right now (live tool-call activity)."
echo "==> Press Ctrl-C to shut down."
echo

if [ "$filter" = "1" ]; then
  # Buffering: `awk … fflush()` + `grep --line-buffered` keep each line moving
  # through the pipeline immediately rather than getting trapped in 4 KB
  # pipe buffers.
  #
  # Filter: drop *only* fully-idle ticks (dispatched=0 AND running=0). When an
  # agent is running, we still want to see ticks every poll interval — they
  # are the heartbeat that proves the orchestrator is alive.
  #
  # The trailing `|$` in grep matches every line, so nothing is dropped after
  # the awk filter — only the listed patterns get colored.
  symphony 2>&1 \
    | tee -a "$log_file" \
    | awk '!(/msg=tick/ && /dispatched=0/ && /running=0/) { print; fflush() }' \
    | grep --line-buffered --color=always -E \
      'msg=dispatched|msg=tick|hook=after_create|hook=before_run|hook=after_run|exited|reloaded|level=warning|level=error|$'
else
  symphony 2>&1 | tee -a "$log_file"
fi
