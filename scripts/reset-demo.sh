#!/usr/bin/env bash
# Reset Symphony's local state to a clean demo starting point (offline-demo).
#
# Reads TARGET_REPO, SYMPHONY_DIR, SYMPHONY_TASKS_FILE from config.env so paths
# live in ONE place.
#
# Default behavior (always):
#   - Stops any running symphony process.
#   - Removes every git worktree under _workspaces/ from $TARGET_REPO.
#   - Deletes local symphony/* branches in $TARGET_REPO.
#   - Wipes _workspaces/.
#   - Removes symphony.log and any *.log in this folder.
#   - Resets every issue in tasks.json back to its starting state (Todo for
#     stage 1, Backlog for stage 2), drops all comments and PR records.
#
# Optional flags:
#   --keep-tasks  Don't reset tasks.json (preserves comments + PR records).
#   --db          Drop and reseed the promatch SQLite DB.
#   --sessions    Delete Claude Code session transcripts for this workspace.
#   --all         Equivalent to --db --sessions (tasks.json is reset by default).

set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f config.env ]; then
  echo "config.env missing. Copy config.env.example → config.env first." >&2
  exit 1
fi
# shellcheck disable=SC1091
source config.env
: "${TARGET_REPO:?TARGET_REPO not set in config.env}"
: "${SYMPHONY_DIR:?SYMPHONY_DIR not set in config.env}"
: "${SYMPHONY_TASKS_FILE:=$SYMPHONY_DIR/tasks.json}"
export SYMPHONY_TASKS_FILE

# Activate the bootstrap-created venv so `promatch` is on PATH for --db reseed.
if [ -d "$SYMPHONY_DIR/.venv" ]; then
  # shellcheck disable=SC1091
  source "$SYMPHONY_DIR/.venv/bin/activate"
fi

reset_db=0
reset_sessions=0
keep_tasks=0
for arg in "$@"; do
  case "$arg" in
    --db)          reset_db=1 ;;
    --sessions)    reset_sessions=1 ;;
    --keep-tasks)  keep_tasks=1 ;;
    --all)         reset_db=1; reset_sessions=1 ;;
    -h|--help)
      head -22 "$0" | tail -18
      exit 0
      ;;
  esac
done

WORKSPACES="$SYMPHONY_DIR/_workspaces"

echo "==> Stopping any running symphony process..."
pkill -f 'symphony/cli\.py\|symphony.cli\|/symphony$' 2>/dev/null || true
sleep 1

if [ -d "$TARGET_REPO/.git" ]; then
  echo "==> Pruning worktrees registered in $TARGET_REPO..."
  git -C "$TARGET_REPO" worktree list --porcelain | awk '
    /^worktree / { path = substr($0, 10) }
    /^$/ {
      if (path != "" && path ~ /\/_workspaces\//) print path
      path = ""
    }
    END {
      if (path != "" && path ~ /\/_workspaces\//) print path
    }
  ' | while read -r wt; do
    echo "    removing worktree: $wt"
    git -C "$TARGET_REPO" worktree remove --force "$wt" || true
  done
  git -C "$TARGET_REPO" worktree prune

  echo "==> Deleting local symphony/* branches in $TARGET_REPO..."
  git -C "$TARGET_REPO" for-each-ref --format='%(refname:short)' refs/heads/symphony \
    | while read -r br; do
    echo "    deleting branch: $br"
    git -C "$TARGET_REPO" branch -D "$br" || true
  done
fi

echo "==> Removing $WORKSPACES..."
rm -rf "$WORKSPACES"
mkdir -p "$WORKSPACES"

echo "==> Removing logs in $SYMPHONY_DIR..."
rm -f "$SYMPHONY_DIR"/*.log

if [ "$keep_tasks" = "0" ]; then
  echo "==> Resetting $SYMPHONY_TASKS_FILE to starting demo state..."
  python3 scripts/seed-local.py --force
else
  echo "==> Keeping $SYMPHONY_TASKS_FILE as-is (--keep-tasks)."
fi

if [ "$reset_db" = "1" ]; then
  echo "==> Resetting promatch SQLite DB..."
  rm -f "${PROMATCH_DB:-$HOME/.promatch/promatch.db}"
  if command -v promatch >/dev/null 2>&1; then
    if promatch seed >/dev/null 2>&1; then
      echo "    db dropped + reseeded"
    else
      echo "    db dropped (reseed skipped — \`promatch\` errored;"
      echo "     fix with: source .venv/bin/activate && pip install -e ./promatch)"
    fi
  else
    echo "    db dropped (no \`promatch\` on PATH to reseed)"
  fi
fi

if [ "$reset_sessions" = "1" ]; then
  # Claude Code stores per-cwd session transcripts under
  # ~/.claude/projects/<flattened-path>/. Symphony spawns Claude with cwd
  # = each per-issue worktree, so every workspace gets its own folder.
  flat_prefix="$(echo "$WORKSPACES" | sed 's|/|-|g')"
  sessions_root="$HOME/.claude/projects"
  if [ -d "$sessions_root" ]; then
    echo "==> Removing Claude session transcripts for this demo..."
    found=0
    while IFS= read -r dir; do
      [ -z "$dir" ] && continue
      echo "    removing $dir"
      rm -rf "$dir"
      found=1
    done < <(find "$sessions_root" -maxdepth 1 -type d -name "*${flat_prefix}*" 2>/dev/null)
    [ "$found" = "0" ] && echo "    (none found)"
  fi
fi

echo
echo "==> Done. Local state is clean."
echo
echo "Next:"
echo "  scripts/run.sh"
