#!/usr/bin/env bash
# Wipe ALL runtime state so the repo is ready for a fresh bootstrap.
#
# Use this BEFORE switching git branches between demo variants (live Linear
# vs. offline). Each branch creates a different `promatch/` git layout and
# a different task store, and leftovers from one cause confusing failures
# in the other. Running this once between branch switches keeps things
# clean.
#
# What it removes:
#   - Any running symphony process.
#   - The promatch/ runtime repo (will be re-materialized from
#     promatch.template/ on next bootstrap).
#   - _workspaces/ (per-issue git worktrees).
#   - tasks.json + tasks.json.lock (offline-demo task store).
#   - symphony.log and any *.log in this folder.
#   - Claude Code session transcripts under ~/.claude/projects/ scoped to
#     this demo's _workspaces path.
#
# What it leaves alone:
#   - .venv/ (saves ~30s on the next bootstrap; pip install -e picks up
#     the new branch's source automatically).
#   - config.env (may hold Linear API keys / paths you want to keep).
#   - promatch.template/ (committed source; this is the master copy).
#   - The Linear project state on linear.app (the live demo's source of
#     truth — only the user can reset it from the Linear UI).
#
# Usage:
#   scripts/clean.sh              # interactive confirm
#   scripts/clean.sh --yes        # skip confirm (CI / scripted)

set -euo pipefail
cd "$(dirname "$0")/.."

confirm=1
for arg in "$@"; do
  case "$arg" in
    --yes|-y) confirm=0 ;;
    -h|--help)
      head -32 "$0" | tail -28
      exit 0
      ;;
  esac
done

# Read paths from config.env if present, otherwise fall back to defaults
# next to this script. Either way, this script must work even when no
# config.env exists yet.
SYMPHONY_DIR="$(pwd)"
TARGET_REPO_DEFAULT="$SYMPHONY_DIR/promatch"
if [ -f config.env ]; then
  # shellcheck disable=SC1091
  source config.env
fi
: "${TARGET_REPO:=$TARGET_REPO_DEFAULT}"
: "${SYMPHONY_DIR:=$(pwd)}"
WORKSPACES="$SYMPHONY_DIR/_workspaces"

echo "About to wipe runtime state in:"
echo "  SYMPHONY_DIR = $SYMPHONY_DIR"
echo "  TARGET_REPO  = $TARGET_REPO"
echo "  WORKSPACES   = $WORKSPACES"
echo

if [ "$confirm" = "1" ]; then
  read -rp "Proceed? [y/N] " ans
  case "${ans:-n}" in [yY]*) ;; *) echo "aborted."; exit 0 ;; esac
fi

echo "==> Stopping any running symphony process..."
pkill -f 'symphony/cli\.py\|symphony.cli\|/symphony$' 2>/dev/null || true
sleep 1

if [ -d "$TARGET_REPO/.git" ]; then
  echo "==> Pruning worktrees registered in $TARGET_REPO..."
  git -C "$TARGET_REPO" worktree list --porcelain 2>/dev/null | awk '
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
    git -C "$TARGET_REPO" worktree remove --force "$wt" 2>/dev/null || true
  done
  git -C "$TARGET_REPO" worktree prune 2>/dev/null || true
fi

echo "==> Removing $TARGET_REPO ..."
rm -rf "$TARGET_REPO"

echo "==> Removing $WORKSPACES ..."
rm -rf "$WORKSPACES"

echo "==> Removing offline-demo task store ..."
rm -f "$SYMPHONY_DIR"/tasks.json "$SYMPHONY_DIR"/tasks.json.lock

echo "==> Removing logs ..."
rm -f "$SYMPHONY_DIR"/*.log

# Wipe Claude Code session transcripts for THIS demo's workspaces. Claude
# flattens the cwd path with `/` and `_` both replaced by `-`, so the dir
# name is e.g. `-Users-foo-thumbtack--workspaces-ENG-1`.
flat_prefix="$(echo "$WORKSPACES" | sed -e 's|/|-|g' -e 's|_|-|g')"
sessions_root="$HOME/.claude/projects"
if [ -d "$sessions_root" ]; then
  echo "==> Removing Claude session transcripts for this demo ..."
  found=0
  while IFS= read -r dir; do
    [ -z "$dir" ] && continue
    echo "    removing $dir"
    rm -rf "$dir"
    found=1
  done < <(find "$sessions_root" -maxdepth 1 -type d -name "*${flat_prefix}*" 2>/dev/null)
  [ "$found" = "0" ] && echo "    (no session transcripts found)"
fi

echo
echo "==> Done. Repo is at a clean pre-bootstrap state."
echo "    Next: scripts/bootstrap.sh"
