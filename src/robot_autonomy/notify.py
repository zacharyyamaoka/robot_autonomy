"""Where the agent's actions go. Same shape as robot_activity's notify, so one relay serves both agents."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Protocol

from ._files import append_jsonl


def action_dict(action) -> dict:
    if hasattr(action, "to_dict"):
        return action.to_dict()
    if is_dataclass(action):
        return asdict(action)
    return dict(action)


class Notifier(Protocol):
    def deliver(self, action) -> bool: ...


class Outbox:
    """The default notifier: append the action JSON as one line to a file."""

    def __init__(self, path):
        self.path = Path(path)

    def deliver(self, action) -> bool:
        append_jsonl(self.path, [action_dict(action)])
        return True


class CommandNotifier:
    """Run argv with the action JSON on stdin (a Slack webhook script, `claude -p`, ...); True iff exit status 0."""

    def __init__(self, argv: list[str], timeout_s: float = 30.0):
        self.argv = list(argv)
        self.timeout_s = timeout_s

    def deliver(self, action) -> bool:
        try:
            result = subprocess.run(self.argv, input=json.dumps(action_dict(action), sort_keys=True), text=True,
                                    capture_output=True, timeout=self.timeout_s, check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0
