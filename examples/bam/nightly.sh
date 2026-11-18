#!/usr/bin/env bash
# One nightly pass of BAM's Robot Autonomy Agent: map BAM's labelled data into the sources, run one agent tick (ingest,
# score, verify, propose, alert, status card), then rebuild the HTML report.
#
#   bash ~/.local/opt/robot-autonomy/examples/bam/nightly.sh
#
# Runs after bam-interventions.timer (04:40), which labels yesterday's chats.
set -euo pipefail
OPT="${ROBOT_AUTONOMY_OPT:-$HOME/.local/opt/robot-autonomy}"
STATE="${ROBOT_AUTONOMY_ROOT:-$HOME/.local/state/robot-autonomy}"
PY="$OPT/.venv/bin/python"
# WHY `python -m robot_autonomy`, never .venv/bin/robot-autonomy: deploy.sh builds the venv in a staging folder and
# moves it into place, so the entry script's shebang points at a folder that no longer exists (exit 127, 2026-11-18).
# WHY -I: from $HOME, a bare ~/robot_autonomy folder would shadow the installed package as a namespace package.
RA=("$PY" -I -m robot_autonomy)
"$PY" -I "$OPT/examples/bam/export.py" --out "$STATE"
"${RA[@]}" agent tick --root "$STATE" --config "$STATE/autonomy.toml" --json > "$STATE/agent/last_tick.json"
mkdir -p "$HOME/robot_autonomy/reports"
"${RA[@]}" report --root "$STATE" --config "$STATE/autonomy.toml" \
  --out "$HOME/robot_autonomy/reports/robot-autonomy-bam-latest.html"
