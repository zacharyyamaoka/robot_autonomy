#!/usr/bin/env bash
# Deploy BAM's Robot Autonomy Agent from the repo's main branch to its own copy and (re)install the nightly timer.
#
#   bash ~/robot_autonomy/examples/bam/deploy.sh [--ref main] [--no-timer]
#
# WHY its own copy (~/.local/opt/robot-autonomy), not the checkout: the timer must not depend on files a peer's
# branch switch or stash can remove. The state (ledger, todo list, status card) lives in ~/.local/state/robot-autonomy
# and survives every redeploy.
set -euo pipefail
REF=main
TIMER=1
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="$2"; shift 2 ;;
    --no-timer) TIMER=0; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$HOME/.local/opt/robot-autonomy"
STATE="$HOME/.local/state/robot-autonomy"
mkdir -p "$HOME/.local/opt" "$STATE/agent" "$STATE/sources" "$STATE/team"
STAGE="$(mktemp -d "$HOME/.local/opt/.robot-autonomy.XXXXXX")"
git -C "$REPO" archive "$REF" | tar -x -C "$STAGE"
[ -f "$STAGE/src/robot_autonomy/agent.py" ] || { echo "no robot_autonomy at $REF" >&2; rm -rf "$STAGE"; exit 1; }
if [ -d "$DEST/.venv" ]; then mv "$DEST/.venv" "$STAGE/.venv"; fi
(cd "$STAGE" && env -u VIRTUAL_ENV nice -n 19 uv sync -q --no-dev)
OLD="$DEST.old.$$"
[ -d "$DEST" ] && mv "$DEST" "$OLD"
mv "$STAGE" "$DEST"
rm -rf "$OLD"
sed -e "s|@STATE@|$STATE|g" -e "s|@HOME@|$HOME|g" "$DEST/examples/bam/autonomy.toml.in" > "$STATE/autonomy.toml"
echo "deployed $(git -C "$REPO" rev-parse --short "$REF") -> $DEST (state $STATE)"
if [ "$TIMER" = 1 ]; then
  mkdir -p "$HOME/.config/systemd/user"
  cp "$DEST/examples/bam/systemd/robot-autonomy.service" "$DEST/examples/bam/systemd/robot-autonomy.timer" \
    "$HOME/.config/systemd/user/"
  systemctl --user daemon-reload
  systemctl --user enable --now robot-autonomy.timer
  systemctl --user list-timers robot-autonomy.timer --no-pager | sed -n 1,2p
fi
