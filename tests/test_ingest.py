"""ingest.py: every adapter into the ledger, idempotently (SPEC "ingest.py")."""

from __future__ import annotations

import csv
from datetime import datetime, timezone

import pytest

from helpers import file_lines, t_of, write_jsonl
from robot_autonomy.ingest import SourceConfig, ingest
from robot_autonomy.ledger import Ledger

T0 = t_of("2026-11-02", 8.0)


def _diag(stamp_offset, path, level, code=""):
    from robot_msgs.std import DiagnosticRecord, Header

    return DiagnosticRecord(header=Header(stamp=T0 + stamp_offset), path=path, level=level, code=code).to_dict()


@pytest.fixture
def file_sources(tmp_path):
    """A labelled log (2 asks + 1 level-none row), a diagnostics stream (1 long error, 1 short), one ticket."""
    chat = write_jsonl(
        tmp_path / "chat.jsonl",
        [
            {"id": "ask-1", "t": T0, "level": "hands_on", "cause": "power_or_replug", "what": "replug"},
            {"id": "ask-2", "t": T0 + 60, "level": "none", "cause": "code_review"},
            {"id": "ask-3", "t": T0 + 120, "level": "remote", "cause": "approve_robot_run", "robot": "arm-2"},
        ],
    )
    diagnostics = write_jsonl(
        tmp_path / "diag.jsonl",
        [
            _diag(0, "arm-1.can.bus", "error", "BusDown"),
            _diag(900, "arm-1.can.bus", "ok"),  # 900 s >= 600: an intervention
            _diag(1000, "arm-1.can.bus", "error", "BusDown"),
            _diag(1010, "arm-1.can.bus", "ok"),  # 10 s: a self-recovery
        ],
    )
    tickets = tmp_path / "tickets.csv"
    with tickets.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "robot", "opened", "closed", "cause", "what", "onsite", "technician_h", "site"])
        writer.writerow(["T-1", "arm-1", "2026-11-02T10:00:00Z", "", "belt_or_items", "belt", "yes", "1", "Site A"])
    return (
        SourceConfig(kind="labelled", path=str(chat), source="chat", robot="arm-1"),
        SourceConfig(kind="diagnostics", path=str(diagnostics)),
        SourceConfig(kind="field_service", path=str(tickets)),
    )


def test_source_config_defaults():
    config = SourceConfig(kind="labelled")
    assert config.path is None
    assert config.root is None
    assert config.source is None
    assert config.reading is None
    assert config.robot is None
    assert config.faults_need_human_after_s is None
    assert config.needs_human_after_s == 600.0


def test_ingest_file_sources_is_idempotent(autonomy_root, file_sources):
    ledger = Ledger(autonomy_root)
    first = ingest(ledger, file_sources, tz="UTC")
    assert first == {"interventions": 4, "blocks_opened": 0, "blocks_closed": 0, "self_recoveries": 1}
    ids = {iv.id for iv in ledger.interventions()}
    assert ids == {"ask-1", "ask-3", f"diag:arm-1.can.bus:{T0:.0f}", "ticket:T-1"}
    by_id = {iv.id: iv for iv in ledger.interventions()}
    assert by_id["ask-1"].robot == "arm-1"  # the source's default robot
    assert by_id["ask-3"].robot == "arm-2"
    assert by_id["ask-1"].source == "chat"
    lines_before = file_lines(autonomy_root / "interventions.jsonl")
    second = ingest(ledger, file_sources, tz="UTC")
    assert second["interventions"] == 0
    assert second["blocks_opened"] == 0
    assert second["self_recoveries"] == 1  # seen again on this run
    assert file_lines(autonomy_root / "interventions.jsonl") == lines_before


def test_ingest_labelled_source_name_and_reading(autonomy_root, tmp_path):
    path = write_jsonl(
        tmp_path / "operator.jsonl",
        [{"id": "op-1", "t": T0, "level": "hands_on", "cause": "clear_workcell", "what": "cleared the tray"}],
    )
    ledger = Ledger(autonomy_root)
    ingest(ledger, [SourceConfig(kind="labelled", path=str(path), source="operator", robot="arm-1")], tz="UTC")
    [operator] = ledger.interventions()
    assert operator.source == "operator"
    assert operator.reading == "observed"
    path_2 = write_jsonl(
        tmp_path / "hook.jsonl",
        [{"id": "hook-1", "t": T0, "level": "remote", "cause": "other_remote", "robot": "arm-1"}],
    )
    ingest(ledger, [SourceConfig(kind="labelled", path=str(path_2), source="hook", reading="observed")], tz="UTC")
    assert {iv.id: iv.reading for iv in ledger.interventions()}["hook-1"] == "observed"


def test_ingest_labelled_source_defaults_to_chat(autonomy_root, tmp_path):
    path = write_jsonl(tmp_path / "chat.jsonl", [{"id": "c-1", "t": T0, "level": "remote", "cause": "other_remote",
                                                  "robot": "arm-1"}])
    ledger = Ledger(autonomy_root)
    ingest(ledger, [SourceConfig(kind="labelled", path=str(path))], tz="UTC")
    assert ledger.interventions()[0].source == "chat"


def _activity_event(kind, item, t, *, robot="any", by=None, **extra):
    return {"schema": "robot-activity-event/1", "t": t, "kind": kind, "id": item, "robot": robot, "by": by, **extra}


def _write_sample(root, t, robot="arm-1", **fields):
    day = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
    row = {"schema": "robot-activity/1", "t": t, "robot": robot, "sample_s": 10.0}
    row.update(fields)
    write_jsonl(root / "samples" / robot / f"{day}.jsonl", [row])


@pytest.fixture
def activity_root(tmp_path):
    root = tmp_path / "activity"
    write_jsonl(
        root / "queue" / "events.jsonl",
        [
            _activity_event("block", "q1", T0 + 0.5, by="pick agent",
                            blocker={"kind": "human", "why": "replug the camera", "eta_t": None}),
            _activity_event("unblock", "q1", T0 + 600.5, by="Zach", blocker_kind="human",
                            cleared=[{"kind": "human", "why": "replug the camera", "eta_t": None}]),
            _activity_event("block", "q2", T0 + 700.5, robot="arm-2", by="sort agent",
                            blocker={"kind": "human", "why": "approve the run", "eta_t": None}),
        ],
    )
    # one long fault streak (start T0 + 990, end T0 + 1700: 710 s) and one short one (start T0 + 2990, end +3010: 20 s)
    for offset in range(1000, 1701, 10):
        _write_sample(root, T0 + offset, health="fault")
    _write_sample(root, T0 + 1710, health="ok")
    for offset in (3000, 3010):
        _write_sample(root, T0 + offset, health="estop")
    _write_sample(root, T0 + 3020, health="ok")
    return root


def test_ingest_robot_activity_opens_and_closes_blocks(autonomy_root, activity_root):
    ledger = Ledger(autonomy_root)
    source = SourceConfig(kind="robot_activity", root=str(activity_root), faults_need_human_after_s=600.0)
    result = ingest(ledger, [source], tz="UTC")
    assert result["blocks_opened"] == 2
    assert result["blocks_closed"] == 1
    assert result["self_recoveries"] == 1
    blocks = {block.id: block for block in ledger.blocks()}
    assert set(blocks) == {f"q1@{T0 + 0.5}", f"q2@{T0 + 700.5}"}
    assert blocks[f"q1@{T0 + 0.5}"].until_t == T0 + 600.5
    assert blocks[f"q2@{T0 + 700.5}"].until_t is None
    ids = {iv.id for iv in ledger.interventions()}
    # blocks are opened with count=True: each ask is an intervention; the long fault is one too
    assert f"block:q1@{T0 + 0.5}" in ids
    assert f"block:q2@{T0 + 700.5}" in ids
    assert f"fault:arm-1:{T0 + 990:.0f}" in ids
    assert len(ids) == 3


def test_ingest_robot_activity_twice_writes_nothing_new(autonomy_root, activity_root):
    ledger = Ledger(autonomy_root)
    source = SourceConfig(kind="robot_activity", root=str(activity_root), faults_need_human_after_s=600.0)
    ingest(ledger, [source], tz="UTC")
    interventions_before = (autonomy_root / "interventions.jsonl").read_bytes()
    blocks_before = (autonomy_root / "blocks.jsonl").read_bytes()
    second = ingest(ledger, [source], tz="UTC")
    assert second["interventions"] == 0
    assert second["blocks_opened"] == 0
    assert (autonomy_root / "interventions.jsonl").read_bytes() == interventions_before
    assert (autonomy_root / "blocks.jsonl").read_bytes() == blocks_before


def test_ingest_closes_a_block_the_source_closes_later(autonomy_root, activity_root):
    ledger = Ledger(autonomy_root)
    source = SourceConfig(kind="robot_activity", root=str(activity_root))
    ingest(ledger, [source], tz="UTC")
    assert [block.id for block in ledger.blocks(open_only=True)] == [f"q2@{T0 + 700.5}"]
    write_jsonl(
        activity_root / "queue" / "events.jsonl",
        [_activity_event("finish", "q2", T0 + 900.5, robot="arm-2", by="sort agent", result="ok", note=None)],
    )
    result = ingest(ledger, [source], tz="UTC")
    assert result["blocks_closed"] == 1
    assert result["blocks_opened"] == 0
    assert ledger.blocks(open_only=True) == []
    assert {block.id: block.until_t for block in ledger.blocks()}[f"q2@{T0 + 700.5}"] == T0 + 900.5


def test_ingest_robot_activity_faults_only_when_asked(autonomy_root, activity_root):
    ledger = Ledger(autonomy_root)
    result = ingest(ledger, [SourceConfig(kind="robot_activity", root=str(activity_root))], tz="UTC")
    assert not any(iv.id.startswith("fault:") for iv in ledger.interventions())
    assert result["self_recoveries"] == 0
