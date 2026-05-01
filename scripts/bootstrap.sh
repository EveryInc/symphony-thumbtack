#!/usr/bin/env bash
# One-shot first-time setup. Idempotent — safe to re-run.
#
# Reads everything from config.env. Walks you through:
#   1. Validate config.env is filled in.
#   2. Initialize promatch as a git repo (if not already).
#   3. Optionally create the GitHub remote via `gh repo create`.
#   4. Optionally seed the Linear project with demo issues.
#
# Usage:
#   scripts/bootstrap.sh                 # interactive
#   scripts/bootstrap.sh --no-prompt     # skip optional prompts (CI/scripted)

set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f config.env ]; then
  echo "config.env not found." >&2
  echo "  cp config.env.example config.env" >&2
  echo "  then fill in the four ALL-CAPS values, then re-run this script." >&2
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
for var in LINEAR_API_KEY LINEAR_PROJECT_SLUG LINEAR_TEAM_KEY TARGET_REPO; do
  val="${!var:-}"
  if [ -z "$val" ] || [[ "$val" == *REPLACE_ME* ]]; then
    echo "  ✗ $var is not set (still '$val')."
    err=1
  else
    echo "  ✓ $var"
  fi
done
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

echo "==> Installing the bundled Symphony orchestrator..."
python -m pip install --quiet -e . 2>&1 | tail -3
if ! command -v symphony >/dev/null 2>&1; then
  echo "  ✗ symphony still not on PATH after install." >&2
  exit 1
fi
echo "  ✓ symphony installed at $(command -v symphony)"

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
  echo "  ✓ initialized git repo on 'main'"
else
  echo "  ✓ git repo already exists"
fi
cd - >/dev/null

# Now that the runtime promatch source exists, pip-install it editable so
# `promatch serve` etc. resolve via the venv.
echo "==> Installing promatch (so you can run \`promatch …\` from this shell)..."
python -m pip install --quiet -e "$TARGET_REPO" 2>&1 | tail -3
echo "  ✓ promatch installed at $(command -v promatch)"
cd "$TARGET_REPO"

# ── Step 3: GitHub remote ────────────────────────────────────────────────────
if git remote get-url origin >/dev/null 2>&1; then
  echo "  ✓ origin remote: $(git remote get-url origin)"
else
  echo
  echo "==> No GitHub origin remote yet."
  if command -v gh >/dev/null 2>&1; then
    if ask "  Create a private GitHub repo via gh now?" n; then
      default_name="$(basename "$TARGET_REPO")"
      read -rp "    repo name (default: $default_name): " repo_name
      repo_name="${repo_name:-$default_name}"
      gh repo create "$repo_name" --private --source=. --remote=origin --push
      echo "  ✓ created + pushed to GitHub"
    else
      echo "  ⚠ skipped. Add a remote later with:  gh repo create … --source=. --push"
    fi
  else
    echo "  ⚠ gh CLI not found. Install it (brew install gh) and re-run, or"
    echo "    create the remote manually:  git remote add origin <url> && git push -u origin main"
  fi
fi

cd - >/dev/null

# ── Step 4: Linear seed ──────────────────────────────────────────────────────
echo
if ask "==> Seed Linear with the dashboard-buildout demo issues?" y; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "  ⚠ python3 not found, skipping seed." ; exit 0
  fi
  python3 scripts/seed-linear.py
fi

echo
echo "Done. Next:"
echo "  1. Move issues to 'Todo' in Linear (the seed script leaves them there)."
echo "  2. scripts/run.sh"
