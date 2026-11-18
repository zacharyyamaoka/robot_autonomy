"""backlog.py: the agent's todo list (SPEC "backlog.py — the agent's todo list")."""

from __future__ import annotations

import json
import re

import pytest

from helpers import read_jsonl, t_of
from robot_autonomy import backlog as backlog_module
from robot_autonomy.backlog import Backlog, BacklogError, Improvement

T0 = t_of("2026-11-10", 9.0)
EVENT_SCHEMA = "robot-autonomy-backlog-event/1"


def _propose(board: Backlog, title="Add a powered USB hub", causes=("power_or_replug",), now=T0, **fields):
    defaults = {"owner": "dev agent", "proposed_by": "Robot Autonomy Agent"}
    defaults.update(fields)
    return board.propose(title=title, causes=causes, now=now, **defaults)


def _events(root):
    return read_jsonl(root / "backlog" / "events.jsonl")


def test_constants():
    assert backlog_module.STATUSES == ("proposed", "accepted", "doing", "done", "verified", "ineffective", "dropped")
    assert backlog_module.OPEN == ("proposed", "accepted", "doing")
    assert backlog_module.TRANSITIONS == {
        "proposed": ("accepted", "doing", "dropped"),
        "accepted": ("doing", "dropped"),
        "doing": ("done", "dropped"),
        "done": ("verified", "ineffective", "doing"),
        "ineffective": ("doing", "dropped"),
        "verified": (),
        "dropped": ("proposed",),
    }


def test_propose_writes_the_item_file_and_a_propose_event(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board, why="5 episodes", plan="powered hub", expected="fewer replugs", note="first try")
    assert isinstance(item, Improvement)
    assert re.fullmatch(r"imp-[0-9a-f]{8}", item.id)
    assert item.status == "proposed"
    assert item.causes == ("power_or_replug",)
    assert item.owner == "dev agent"
    assert item.proposed_by == "Robot Autonomy Agent"
    assert item.robot == "any"
    assert item.why == "5 episodes"
    assert item.plan == "powered hub"
    assert item.expected == "fewer replugs"
    assert item.note == "first try"
    assert item.evidence == ()
    assert item.proposed_t == T0
    assert item.updated_t == T0
    assert item.done_t is None
    assert item.verified_t is None
    assert item.effect is None
    item_file = autonomy_root / "backlog" / "items" / f"{item.id}.json"
    assert json.loads(item_file.read_text(encoding="utf-8")) == item.to_dict()
    [event] = _events(autonomy_root)
    assert event["schema"] == EVENT_SCHEMA
    assert event["kind"] == "propose"
    assert event["id"] == item.id
    assert event["t"] == T0
    assert "by" in event
    assert event["item"] == item.to_dict()
    assert (autonomy_root / "backlog" / ".lock").exists()


def test_propose_with_an_explicit_id_and_its_errors(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board, id="imp-fixed01")
    assert item.id == "imp-fixed01"
    with pytest.raises(BacklogError):
        _propose(board, id="imp-fixed01")
    with pytest.raises(BacklogError):
        _propose(board, causes=())


def test_to_dict_round_trip(autonomy_root):
    item = _propose(Backlog(autonomy_root), causes=("power_or_replug", "lights_or_cameras"))
    assert Improvement.from_dict(item.to_dict()) == item
    assert Improvement.from_dict(json.loads(json.dumps(item.to_dict()))) == item


def test_get_and_a_second_backlog_on_the_same_root(autonomy_root):
    writer = Backlog(autonomy_root)
    reader = Backlog(autonomy_root)
    item = _propose(writer)
    assert reader.get(item.id) == item
    with pytest.raises(KeyError):
        reader.get("imp-00000000")
    writer.move(item.id, "doing", by="dev agent", now=T0 + 60.0)
    assert reader.get(item.id).status == "doing"
    writer.add_evidence(item.id, "abc1234", by="dev agent", now=T0 + 120.0)
    assert reader.get(item.id).evidence == ("abc1234",)
    assert len(reader.events()) == 3


def test_items_filters_and_sorting(autonomy_root):
    board = Backlog(autonomy_root)
    late = _propose(board, title="late", causes=("belt_or_items",), now=T0 + 300.0, id="imp-late0000")
    first = _propose(board, title="first", causes=("power_or_replug",), now=T0, id="imp-zzzz0000")
    tie = _propose(board, title="tie", causes=("power_or_replug", "belt_or_items"), now=T0, id="imp-aaaa0000")
    board.move(late.id, "doing", by="dev agent", now=T0 + 400.0)
    assert [item.id for item in board.items()] == [tie.id, first.id, late.id]  # (proposed_t, id)
    assert [item.id for item in board.items(status="proposed")] == [tie.id, first.id]
    assert [item.id for item in board.items(status=("proposed", "doing"))] == [tie.id, first.id, late.id]
    assert [item.id for item in board.items(status="doing")] == [late.id]
    assert [item.id for item in board.items(cause="belt_or_items")] == [tie.id, late.id]
    assert [item.id for item in board.items(status="proposed", cause="belt_or_items")] == [tie.id]


def test_move_follows_the_transitions_and_logs_a_move_event(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board)
    moved = board.move(item.id, "accepted", by="Zach", note="looks right", now=T0 + 10.0)
    assert moved.status == "accepted"
    assert moved.updated_t == T0 + 10.0
    event = _events(autonomy_root)[-1]
    assert event["schema"] == EVENT_SCHEMA
    assert event["kind"] == "move"
    assert event["id"] == item.id
    assert event["by"] == "Zach"
    assert event["t"] == T0 + 10.0
    assert event["from"] == "proposed"
    assert event["to"] == "accepted"
    assert event["note"] == "looks right"
    assert board.move(item.id, "doing", by="dev agent", now=T0 + 20.0).status == "doing"
    assert board.move(item.id, "dropped", by="Zach", now=T0 + 30.0).status == "dropped"
    assert board.move(item.id, "proposed", by="Zach", now=T0 + 40.0).status == "proposed"


@pytest.mark.parametrize(
    ("path_to_status", "illegal_target", "allowed"),
    [
        ((), "done", ("accepted", "doing", "dropped")),
        ((), "verified", ("accepted", "doing", "dropped")),
        (("accepted",), "proposed", ("doing", "dropped")),
        (("doing",), "accepted", ("done", "dropped")),
    ],
)
def test_illegal_moves_raise_and_name_the_allowed_ones(autonomy_root, path_to_status, illegal_target, allowed):
    board = Backlog(autonomy_root)
    item = _propose(board)
    for status in path_to_status:
        board.move(item.id, status, by="dev agent", now=T0 + 1.0)
    events_before = len(_events(autonomy_root))
    with pytest.raises(BacklogError) as raised:
        board.move(item.id, illegal_target, by="dev agent", now=T0 + 2.0)
    for name in allowed:
        assert name in str(raised.value)
    assert len(_events(autonomy_root)) == events_before
    assert board.get(item.id).status == (path_to_status[-1] if path_to_status else "proposed")


def test_verified_is_final(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board)
    board.move(item.id, "doing", by="dev agent", now=T0)
    board.mark_done(item.id, by="dev agent", evidence=("abc1234",), now=T0 + 1.0)
    board.record_effect(item.id, {"verdict": "verified"}, "verified", by="Robot Autonomy Agent", now=T0 + 2.0)
    for target in ("doing", "dropped", "proposed", "done"):
        with pytest.raises(BacklogError):
            board.move(item.id, target, by="dev agent", now=T0 + 3.0)


def test_mark_done_needs_doing_and_evidence(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board)
    with pytest.raises(BacklogError):
        board.mark_done(item.id, by="dev agent", evidence=("abc1234",), now=T0 + 1.0)  # still proposed
    board.move(item.id, "doing", by="dev agent", now=T0 + 1.0)
    with pytest.raises(BacklogError, match="done needs evidence"):
        board.mark_done(item.id, by="dev agent", now=T0 + 2.0)
    assert board.get(item.id).status == "doing"
    done = board.mark_done(item.id, by="dev agent", evidence=("abc1234", "reports/hub.html"), now=T0 + 3.0)
    assert done.status == "done"
    assert done.done_t == T0 + 3.0
    assert done.updated_t == T0 + 3.0
    assert done.evidence == ("abc1234", "reports/hub.html")
    event = _events(autonomy_root)[-1]
    assert event["kind"] == "done"
    assert event["id"] == item.id
    assert event["by"] == "dev agent"
    assert "abc1234" in json.dumps(event["evidence"])


def test_mark_done_uses_evidence_already_on_the_item(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board)
    board.move(item.id, "doing", by="dev agent", now=T0 + 1.0)
    board.add_evidence(item.id, "https://github.com/example/pr/1", by="dev agent", now=T0 + 2.0)
    done = board.mark_done(item.id, by="dev agent", now=T0 + 3.0)
    assert done.status == "done"
    assert done.evidence == ("https://github.com/example/pr/1",)


def test_move_to_done_goes_through_mark_dones_rule(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board)
    board.move(item.id, "doing", by="dev agent", now=T0 + 1.0)
    with pytest.raises(BacklogError, match="done needs evidence"):
        board.move(item.id, "done", by="dev agent", now=T0 + 2.0)
    board.add_evidence(item.id, "abc1234", by="dev agent", now=T0 + 3.0)
    done = board.move(item.id, "done", by="dev agent", now=T0 + 4.0)
    assert done.status == "done"
    assert done.done_t == T0 + 4.0


def test_add_evidence_appends_once(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board)
    first = board.add_evidence(item.id, "abc1234", by="dev agent", now=T0 + 1.0)
    assert first.evidence == ("abc1234",)
    assert first.updated_t == T0 + 1.0
    event = _events(autonomy_root)[-1]
    assert event["kind"] == "evidence"
    assert event["ref"] == "abc1234"
    assert board.add_evidence(item.id, "abc1234", by="dev agent", now=T0 + 2.0).evidence == ("abc1234",)
    assert board.add_evidence(item.id, "def5678", by="dev agent", now=T0 + 3.0).evidence == ("abc1234", "def5678")


def test_edit_changes_the_given_fields_and_logs_them(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board, plan="old plan")
    edited = board.edit(item.id, by="Zach", plan="new plan: powered hub on the rack", owner="Zach", now=T0 + 5.0)
    assert edited.plan == "new plan: powered hub on the rack"
    assert edited.owner == "Zach"
    assert edited.title == item.title  # untouched
    assert edited.updated_t == T0 + 5.0
    event = _events(autonomy_root)[-1]
    assert event["kind"] == "edit"
    assert event["id"] == item.id
    assert event["by"] == "Zach"
    assert "new plan: powered hub on the rack" in json.dumps(event)


def test_record_effect_from_done_only(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board)
    effect = {"before": {"episodes": 9}, "after": {"episodes": 0}, "p_value": 0.002, "verdict": "verified"}
    with pytest.raises((BacklogError, ValueError)):
        board.record_effect(item.id, effect, "verified", by="Robot Autonomy Agent", now=T0 + 1.0)
    board.move(item.id, "doing", by="dev agent", now=T0 + 1.0)
    board.mark_done(item.id, by="dev agent", evidence=("abc1234",), now=T0 + 2.0)
    with pytest.raises((BacklogError, ValueError)):
        board.record_effect(item.id, effect, "pending", by="Robot Autonomy Agent", now=T0 + 3.0)
    verified = board.record_effect(item.id, effect, "verified", by="Robot Autonomy Agent", now=T0 + 4.0)
    assert verified.status == "verified"
    assert verified.effect == effect
    assert verified.verified_t == T0 + 4.0
    assert verified.updated_t == T0 + 4.0
    assert verified.done_t == T0 + 2.0
    event = _events(autonomy_root)[-1]
    assert event["kind"] == "effect"
    assert event["verdict"] == "verified"
    assert event["effect"] == effect
    assert Backlog(autonomy_root).get(item.id) == verified


def test_record_effect_ineffective_and_reopen(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board)
    board.move(item.id, "doing", by="dev agent", now=T0 + 1.0)
    board.mark_done(item.id, by="dev agent", evidence=("abc1234",), now=T0 + 2.0)
    ineffective = board.record_effect(item.id, {"verdict": "ineffective"}, "ineffective", by="agent", now=T0 + 3.0)
    assert ineffective.status == "ineffective"
    assert ineffective.verified_t == T0 + 3.0
    assert board.move(item.id, "doing", by="dev agent", now=T0 + 4.0).status == "doing"


def test_live_is_open_or_done_items_of_a_cause(autonomy_root):
    board = Backlog(autonomy_root)

    def at_status(title, status_path, causes=("power_or_replug",)):
        item = _propose(board, title=title, causes=causes)
        for status in status_path:
            if status == "done":
                board.mark_done(item.id, by="dev agent", evidence=("abc1234",), now=T0 + 2.0)
            elif status in ("verified", "ineffective"):
                board.record_effect(item.id, {"verdict": status}, status, by="agent", now=T0 + 3.0)
            else:
                board.move(item.id, status, by="dev agent", now=T0 + 1.0)
        return item

    proposed = at_status("proposed", ())
    accepted = at_status("accepted", ("accepted",))
    doing = at_status("doing", ("doing",))
    done = at_status("done", ("doing", "done"))
    at_status("verified", ("doing", "done", "verified"))
    at_status("ineffective", ("doing", "done", "ineffective"))
    at_status("dropped", ("dropped",))
    other_cause = at_status("other cause", ("doing",), causes=("belt_or_items",))
    assert {item.id for item in board.live("power_or_replug")} == {proposed.id, accepted.id, doing.id, done.id}
    assert {item.id for item in board.live("belt_or_items")} == {other_cause.id}
    assert board.live("lights_or_cameras") == []


def test_every_mutation_appends_exactly_one_event(autonomy_root):
    board = Backlog(autonomy_root)
    item = _propose(board)
    board.edit(item.id, by="Zach", title="Add a powered hub", now=T0 + 1.0)
    board.move(item.id, "doing", by="dev agent", now=T0 + 2.0)
    board.add_evidence(item.id, "abc1234", by="dev agent", now=T0 + 3.0)
    board.mark_done(item.id, by="dev agent", now=T0 + 4.0)
    board.record_effect(item.id, {"verdict": "verified"}, "verified", by="agent", now=T0 + 5.0)
    kinds = [event["kind"] for event in board.events()]
    assert kinds == ["propose", "edit", "move", "evidence", "done", "effect"]
    assert board.events() == _events(autonomy_root)
    assert all(event["schema"] == EVENT_SCHEMA and event["id"] == item.id for event in board.events())
    assert [event["t"] for event in board.events()] == [T0, T0 + 1.0, T0 + 2.0, T0 + 3.0, T0 + 4.0, T0 + 5.0]
