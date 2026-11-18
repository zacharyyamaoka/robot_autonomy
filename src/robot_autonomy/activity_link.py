"""Asking the Robot Activity Agent for robot time to verify a fix, through its CLI only.

WHY the CLI: robot_activity's rule is that other programs submit through `robot-activity queue submit`, never by
writing its queue files; this package never imports robot_activity.
"""

from __future__ import annotations

import json
import subprocess


def submit_verification(improvement, *, root, est_min: float, policy_repo: str, policy_commit: str, policy_path: str,
                        robot: str = "any", runner=subprocess.run) -> dict:
    """Submit a "Verify: <title>" queue item owned by the improvement's owner; returns the CLI's parsed JSON.
    RuntimeError (with stderr) on a non-zero exit."""
    argv = ["robot-activity", "queue", "submit", "--root", str(root), "--title", f"Verify: {improvement.title}",
            "--owner", improvement.owner, "--est-min", str(est_min), "--source", "agent", "--robot", robot,
            "--policy-repo", policy_repo, "--policy-commit", policy_commit, "--policy-path", policy_path, "--json"]
    result = runner(argv, capture_output=True, text=True)
    stdout, stderr = _text(result.stdout), _text(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"robot-activity queue submit failed ({result.returncode}): {stderr}")
    return json.loads(stdout)


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
