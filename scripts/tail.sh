#!/usr/bin/env bash
# Live-tail symphony.log with the same filter/colors as run.sh.
# Use in a second terminal pane during a demo.
#
# Usage:
#   scripts/tail.sh           # filtered + colored
#   scripts/tail.sh --raw     # everything

set -euo pipefail
cd "$(dirname "$0")/.."

filter=1
for arg in "$@"; do
  case "$arg" in
    --raw) filter=0 ;;
  esac
done

log_file="symphony.log"
if [ ! -f "$log_file" ]; then
  echo "no $log_file yet — start symphony with scripts/run.sh first." >&2
  exit 1
fi

if [ "$filter" = "1" ]; then
  # Drop only fully-idle ticks (dispatched=0 AND running=0). Active ticks are
  # heartbeats — keep them visible.
  tail -f "$log_file" \
    | awk '!(/msg=tick/ && /dispatched=0/ && /running=0/) { print; fflush() }' \
    | grep --line-buffered --color=always -E \
      'msg=dispatched|msg=tick|hook=after_create|hook=before_run|hook=after_run|exited|reloaded|level=warning|level=error|$'
else
  tail -f "$log_file"
fi
