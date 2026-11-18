"""notify.py: Outbox and CommandNotifier (SPEC "notify.py"); Action.to_dict (SPEC "agent.py")."""

from __future__ import annotations

import json
import sys

from robot_autonomy.agent import Action
from robot_autonomy.notify import CommandNotifier, Outbox


def _action(**overrides) -> Action:
    fields = {
        "kind": "blocked",
        "to": ("pick agent", "Robot Activity Agent", "dispatcher"),
        "message": "arm-1 has waited 1.0 h on a person for q7: approve the pick run",
        "robot": "arm-1",
        "improvement": None,
        "cause": "approve_robot_run",
        "key": "blocked:b-old",
    }
    fields.update(overrides)
    return Action(**fields)


def _as_json(action: Action) -> dict:
    return json.loads(json.dumps(action.to_dict()))


def test_action_to_dict_carries_every_field():
    as_dict = _as_json(_action())
    assert as_dict["kind"] == "blocked"
    assert as_dict["to"] == ["pick agent", "Robot Activity Agent", "dispatcher"]
    assert as_dict["message"].startswith("arm-1")
    assert as_dict["robot"] == "arm-1"
    assert as_dict["cause"] == "approve_robot_run"
    assert as_dict["key"] == "blocked:b-old"


def test_action_defaults():
    action = Action(kind="stale", to=("dev agent",), message="nudge")
    assert action.robot is None
    assert action.improvement is None
    assert action.cause is None
    assert action.key == ""


def test_outbox_appends_one_json_line_per_action(tmp_path):
    path = tmp_path / "outbox" / "actions.jsonl"
    outbox = Outbox(path)
    assert outbox.deliver(_action()) is True
    assert outbox.deliver(_action(kind="stale", to=("dev agent",), key="stale:imp-1:2026-11-18")) is True
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    for key, value in _as_json(_action()).items():
        assert first[key] == value
    assert second["kind"] == "stale"
    assert second["key"] == "stale:imp-1:2026-11-18"


STDIN_TO_FILE = "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(sys.stdin.read()); sys.exit(int(sys.argv[2]))"


def test_command_notifier_pipes_the_action_json_and_succeeds_on_exit_zero(tmp_path):
    received = tmp_path / "received.json"
    notifier = CommandNotifier([sys.executable, "-c", STDIN_TO_FILE, str(received), "0"])
    assert notifier.deliver(_action()) is True
    payload = json.loads(received.read_text())
    for key, value in _as_json(_action()).items():
        assert payload[key] == value


def test_command_notifier_fails_on_a_non_zero_exit(tmp_path):
    received = tmp_path / "received.json"
    notifier = CommandNotifier([sys.executable, "-c", STDIN_TO_FILE, str(received), "3"], timeout_s=30.0)
    assert notifier.deliver(_action()) is False
    assert json.loads(received.read_text())["key"] == "blocked:b-old"  # it still got the JSON
