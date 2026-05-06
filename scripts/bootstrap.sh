#!/usr/bin/env bash
# One-shot first-time setup (offline-demo). Idempotent — safe to re-run.
#
# Reads everything from config.env. Walks you through:
#   1. Validate config.env is present.
#   2. Install the bundled Symphony orchestrator into a local venv.
#   3. Materialize the promatch runtime repo from promatch.template/.
#   4. Initialize promatch as a local git repo on `main` (no remote).
#   5. Seed the local tasks.json with the demo issues.
#
# Usage:
#   scripts/bootstrap.sh                 # interactive
#   scripts/bootstrap.sh --no-prompt     # skip optional prompts (CI/scripted)

set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f config.env ]; then
  echo "config.env not found." >&2
  echo "  cp config.env.example config.env" >&2
  echo "  then re-run this script. (No fields require editing for the default layout.)" >&2
  exit 1
fi
# shellcheck disable=SC1091
source config.env

prompt=1
for arg in "$@"; do
  case "$arg" in
    --no-prompt) prompt=0 ;;
  esac
done

ask() {
  # ask "Question" "default-y-or-n"
  local q="$1" def="${2:-n}"
  if [ "$prompt" = "0" ]; then
    [ "$def" = "y" ] && return 0 || return 1
  fi
  local hint="[y/N]"
  [ "$def" = "y" ] && hint="[Y/n]"
  read -rp "$q $hint " ans
  ans="${ans:-$def}"
  case "$ans" in [yY]*) return 0 ;; *) return 1 ;; esac
}

# ── Step 1: validate config.env ───────────────────────────────────────────────
echo "==> Validating config.env..."
err=0
for var in TARGET_REPO SYMPHONY_DIR; do
  val="${!var:-}"
  if [ -z "$val" ]; then
    echo "  ✗ $var is not set."
    err=1
  else
    echo "  ✓ $var=$val"
  fi
done
# SYMPHONY_TASKS_FILE is optional — defaults to ./tasks.json next to this
# script. Export the resolved value so child processes (seed-local.py,
# symphony, the agent's `tasks` CLI) all agree on the same path.
if [ -z "${SYMPHONY_TASKS_FILE:-}" ]; then
  SYMPHONY_TASKS_FILE="$SYMPHONY_DIR/tasks.json"
  echo "  ✓ SYMPHONY_TASKS_FILE=$SYMPHONY_TASKS_FILE  (default)"
else
  echo "  ✓ SYMPHONY_TASKS_FILE=$SYMPHONY_TASKS_FILE"
fi
export SYMPHONY_TASKS_FILE
[ "$err" = "1" ] && exit 1

# ── Set up a local venv so the system Python stays untouched ────────────────
echo "==> Setting up local virtualenv at .venv/..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
  echo "  ✓ created .venv"
else
  echo "  ✓ .venv already exists"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "  ✓ activated ($(python -V 2>&1))"

# Modern editable installs (PEP-660) need pip ≥ 21.3. macOS system pip is
# stuck at 21.2.4 — upgrade inside the venv unconditionally.
echo "==> Upgrading pip inside the venv..."
python -m pip install --quiet --upgrade pip 2>&1 | tail -2

echo "==> Installing the bundled Symphony orchestrator (and \`tasks\` CLI)..."
python -m pip install --quiet -e . 2>&1 | tail -3
if ! command -v symphony >/dev/null 2>&1; then
  echo "  ✗ symphony still not on PATH after install." >&2
  exit 1
fi
echo "  ✓ symphony installed at $(command -v symphony)"
echo "  ✓ tasks    installed at $(command -v tasks)"

# ── Step 2: materialize the promatch runtime repo from the template ─────────
echo
echo "==> Setting up promatch git repo at $TARGET_REPO..."
template_dir="$(pwd)/promatch.template"
if [ ! -d "$template_dir" ]; then
  echo "  ✗ promatch.template/ missing at $template_dir." >&2
  echo "    This demo expects the template directory to be committed in" >&2
  echo "    symphony-thumbtack. Reclone the upstream repo." >&2
  exit 1
fi

if [ ! -d "$TARGET_REPO" ]; then
  echo "  copying promatch.template/ → $TARGET_REPO"
  cp -R "$template_dir" "$TARGET_REPO"
elif [ ! "$(ls -A "$TARGET_REPO" 2>/dev/null)" ]; then
  echo "  $TARGET_REPO is empty — populating from promatch.template/"
  cp -R "$template_dir/." "$TARGET_REPO/"
else
  echo "  ✓ $TARGET_REPO already populated (leaving as-is)"
fi

cd "$TARGET_REPO"
if [ ! -d .git ]; then
  git init -q -b main
  git add -A
  git -c user.name="Symphony Bootstrap" -c user.email="symphony@local" \
    commit -q -m "Initial promatch scaffold"
  echo "  ✓ initialized git repo on 'main' (no remote — offline demo)"
else
  echo "  ✓ git repo already exists"
fi
cd - >/dev/null

# Now that the runtime promatch source exists, pip-install it editable so
# `promatch serve` etc. resolve via the venv.
echo "==> Installing promatch (so you can run \`promatch …\` from this shell)..."
python -m pip install --quiet -e "$TARGET_REPO" 2>&1 | tail -3
echo "  ✓ promatch installed at $(command -v promatch)"

# ── Step 3: seed the local task store ────────────────────────────────────────
echo
if [ -f "$SYMPHONY_TASKS_FILE" ] && [ -s "$SYMPHONY_TASKS_FILE" ]; then
  echo "==> $SYMPHONY_TASKS_FILE already exists."
  if ask "  Re-seed it with the demo issues (overwrites comments + PRs)?" n; then
    python3 scripts/seed-local.py --force
  else
    echo "  ✓ keeping existing tasks.json"
  fi
else
  echo "==> Seeding $SYMPHONY_TASKS_FILE with the dashboard-buildout demo issues..."
  python3 scripts/seed-local.py
fi

echo
echo "Done. Next:"
echo "  1. (optional) Edit $SYMPHONY_TASKS_FILE if you want to start with a"
echo "     subset of issues in 'Todo'."
echo "  2. scripts/run.sh"
