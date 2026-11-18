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
"$PY" "$OPT/examples/bam/export.py" --out "$STATE"
"$OPT/.venv/bin/robot-autonomy" agent tick --root "$STATE" --config "$STATE/autonomy.toml" --json > "$STATE/agent/last_tick.json"
mkdir -p "$HOME/robot_autonomy/reports"
"$OPT/.venv/bin/robot-autonomy" report --root "$STATE" --config "$STATE/autonomy.toml" \
  --out "$HOME/robot_autonomy/reports/robot-autonomy-bam-latest.html"
