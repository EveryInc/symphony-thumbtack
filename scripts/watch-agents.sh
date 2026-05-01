#!/usr/bin/env bash
# Live per-agent activity dashboard.
#
# Run in a second terminal pane during a demo to make agent work visible.
# Reads Claude Code's session transcripts (~/.claude/projects/<flattened-cwd>/)
# for each issue worktree, extracts the latest event, and prints a refreshing
# table showing what every agent is doing right now.
#
# Usage:
#   scripts/watch-agents.sh                # default 2s refresh
#   scripts/watch-agents.sh --interval 5   # slower refresh

set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f config.env ]; then
  echo "config.env missing. cp config.env.example → config.env first." >&2
  exit 1
fi
# shellcheck disable=SC1091
source config.env
: "${SYMPHONY_DIR:?SYMPHONY_DIR not set in config.env}"

interval=2
for ((i=1; i<=$#; i++)); do
  case "${!i}" in
    --interval) j=$((i+1)); interval="${!j}" ;;
    -h|--help)
      head -10 "$0" | tail -8
      exit 0
      ;;
  esac
done

# Flatten the workspaces path the way Claude Code names project folders:
# `/Users/foo/bar` -> `-Users-foo-bar`.
FLAT_PREFIX="$(echo "$SYMPHONY_DIR/_workspaces" | sed 's|/|-|g')"
SESSIONS_ROOT="$HOME/.claude/projects"

# Use python (already in the venv) to render each frame — easier than jq+awk
# gymnastics for parsing JSONL.
PYTHON="python3"
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

render_frame() {
  "$PYTHON" - "$SESSIONS_ROOT" "$FLAT_PREFIX" <<'PY'
import json, os, sys, time, glob, datetime

sessions_root = sys.argv[1]
flat_prefix   = sys.argv[2]
now = time.time()

# Find every project folder that belongs to this demo.
folders = sorted(glob.glob(os.path.join(sessions_root, f"*{flat_prefix}*")))

if not folders:
    print("\033[2J\033[H", end="")
    print("watch-agents — no Claude sessions found yet for this demo.")
    print(f"  scanning: {sessions_root}/*{flat_prefix}*")
    print("  start `scripts/run.sh` and let Symphony dispatch at least one issue.")
    sys.exit(0)

rows = []
for folder in folders:
    # The issue identifier is the trailing path component after the flat prefix.
    issue = os.path.basename(folder).split(flat_prefix.lstrip("-"))[-1].lstrip("-") or os.path.basename(folder)
    # Newest jsonl in that folder is the active session.
    jsonl_files = sorted(
        glob.glob(os.path.join(folder, "*.jsonl")),
        key=os.path.getmtime,
        reverse=True,
    )
    if not jsonl_files:
        rows.append({"issue": issue, "age": "—", "kind": "(no session)", "detail": ""})
        continue
    newest = jsonl_files[0]
    age_s = now - os.path.getmtime(newest)
    # Read the last useful event from the JSONL. Walk backward to skip empty
    # lines and pick the most recent assistant/tool entry.
    last_event = None
    try:
        with open(newest, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(8192, size)
            f.seek(size - chunk)
            tail = f.read().decode("utf-8", errors="ignore").splitlines()
        for line in reversed(tail):
            line = line.strip()
            if not line:
                continue
            try:
                last_event = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    except OSError:
        pass

    kind, detail = "(unknown)", ""
    if last_event:
        msg = last_event.get("message") or {}
        content = msg.get("content")
        # Claude Code session events usually look like:
        # {"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash",...}]}}
        if isinstance(content, list):
            for block in reversed(content):
                btype = block.get("type")
                if btype == "tool_use":
                    kind = f"tool: {block.get('name', '?')}"
                    inp = block.get("input") or {}
                    if "command" in inp:
                        detail = str(inp["command"])[:80]
                    elif "file_path" in inp:
                        detail = str(inp["file_path"])[:80]
                    elif "query" in inp:
                        detail = str(inp["query"])[:80]
                    else:
                        detail = ", ".join(list(inp.keys())[:3])
                    break
                elif btype == "text":
                    kind = "text"
                    detail = (block.get("text") or "").strip().splitlines()[0][:80] if block.get("text") else ""
                    break
            else:
                kind = msg.get("role") or last_event.get("type") or "(?)"
        else:
            kind = last_event.get("type") or "(?)"
    rows.append({
        "issue": issue,
        "age": f"{int(age_s)}s" if age_s < 60 else f"{int(age_s/60)}m",
        "kind": kind,
        "detail": detail,
    })

# Render the frame. Clear screen + move home.
print("\033[2J\033[H", end="")
print(f"watch-agents — {datetime.datetime.now().strftime('%H:%M:%S')}  "
      f"({len(rows)} session(s))")
print()
header = f"{'ISSUE':<10} {'AGE':>6}  {'LAST ACTION':<22}  DETAIL"
print(header)
print("-" * len(header))
for r in rows:
    # Color recent activity green, stale red.
    try:
        age_n = int(r["age"].rstrip("sm"))
        is_minutes = r["age"].endswith("m")
    except ValueError:
        age_n, is_minutes = 0, False
    color = "\033[32m"  # green
    if is_minutes and age_n >= 5:
        color = "\033[31m"  # red — likely stuck
    elif (is_minutes and age_n >= 1) or (not is_minutes and age_n >= 30):
        color = "\033[33m"  # yellow — quiet
    reset = "\033[0m"
    print(f"{color}{r['issue']:<10} {r['age']:>6}{reset}  {r['kind']:<22}  {r['detail']}")
print()
print("\033[90mgreen <30s · yellow <5m · red >=5m  ·  ctrl-c to stop\033[0m")
PY
}

while true; do
  render_frame
  sleep "$interval"
done
