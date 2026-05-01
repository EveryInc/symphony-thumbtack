#!/usr/bin/env bash
# Run Symphony in foreground with demo-friendly log output.
#
# Sources config.env (the ONE config file) before launch so:
#   - LINEAR_API_KEY / LINEAR_PROJECT_SLUG are resolvable in WORKFLOW.md
#   - TARGET_REPO / SYMPHONY_DIR are inherited by hook subprocesses
#
# Usage:
#   scripts/run.sh                # default: filtered + tee to symphony.log
#   scripts/run.sh --raw          # no filter, no color
#   scripts/run.sh --debug        # SYMPHONY_LOG_LEVEL=debug

set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f config.env ]; then
  echo "config.env missing. Copy config.env.example → config.env and fill it in." >&2
  exit 1
fi
# shellcheck disable=SC1091
source config.env

filter=1
debug=0
for arg in "$@"; do
  case "$arg" in
    --raw)   filter=0 ;;
    --debug) debug=1 ;;
    -h|--help)
      head -16 "$0" | tail -10
      exit 0
      ;;
  esac
done

if [ "$debug" = "1" ]; then
  export SYMPHONY_LOG_LEVEL=debug
fi

if ! command -v symphony >/dev/null 2>&1; then
  echo "symphony binary not on PATH. From the symphony source repo:" >&2
  echo "  pip install -e ." >&2
  exit 1
fi

log_file="symphony.log"
echo "==> Logs are also being written to $log_file (full unfiltered copy)."
echo "==> Press Ctrl-C to shut down."
echo

if [ "$filter" = "1" ]; then
  symphony 2>&1 \
    | tee -a "$log_file" \
    | awk '!/msg=tick.*dispatched=0/' \
    | grep --color=always -E \
      'msg=dispatched|hook=after_create|hook=before_run|hook=after_run|exited|reloaded|level=warning|level=error|$'
else
  symphony 2>&1 | tee -a "$log_file"
fi
