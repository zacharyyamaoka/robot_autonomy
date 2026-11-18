"""cli.py: the `robot-autonomy` entry point (SPEC "cli.py — entry point robot-autonomy").

Every command runs in a fresh interpreter the way the console script does (`sys.exit(main())`).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helpers import day_range, run_cli, write_jsonl
from robot_autonomy.backlog import Backlog
from robot_autonomy.ledger import Ledger
from robot_autonomy.schema import Block


def ok(*args) -> subprocess.CompletedProcess:
    completed = run_cli(*args)
    assert completed.returncode == 0, f"exit {completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    return completed


def one_json_document(completed: subprocess.CompletedProcess):
    return json.loads(completed.stdout)


def test_ask_records_an_asked_intervention(autonomy_root):
    completed = ok(
        "ask", "--root", autonomy_root, "--robot", "arm-1", "--level", "hands_on", "--cause", "power_or_replug",
        "--what", "replug the overhead camera", "--fix", "powered hub", "--avoidable", "yes", "--thread", "chat-7",
        "--by", "pick agent", "--json",
    )
    one_json_document(completed)
    [intervention] = Ledger(autonomy_root).interventions()
    assert intervention.source == "agent"
    assert intervention.reading == "asked"
    assert intervention.robot == "arm-1"
    assert intervention.level == "hands_on"
    assert intervention.cause == "power_or_replug"
    assert intervention.what == "replug the overhead camera"
    assert intervention.fix == "powered hub"
    assert intervention.avoidable == "yes"
    assert intervention.thread == "chat-7"
    assert intervention.by == "pick agent"


def test_log_records_an_observed_intervention(autonomy_root):
    ok(
        "log", "--root", autonomy_root, "--robot", "arm-2", "--level", "hands_on", "--cause", "clear_workcell",
        "--what", "cleared a jam on the tray", "--human-min", "5", "--by", "Zach",
    )
    [intervention] = Ledger(autonomy_root).interventions()
    assert intervention.source == "operator"
    assert intervention.reading == "observed"
    assert intervention.human_s == pytest.approx(300.0)
    assert intervention.by == "Zach"


def test_retract(autonomy_root):
    ok("ask", "--root", autonomy_root, "--robot", "arm-1", "--level", "remote", "--cause", "approve_robot_run",
       "--what", "give the go")
    [intervention] = Ledger(autonomy_root).interventions()
    ok("retract", "--root", autonomy_root, intervention.id, "--why", "a review, not an intervention", "--by", "Zach")
    assert Ledger(autonomy_root).interventions() == []


def test_retract_unknown_id_exits_2(autonomy_root):
    completed = run_cli("retract", "--root", autonomy_root, "no-such-id", "--why", "typo")
    assert completed.returncode == 2
    assert completed.stderr.strip()


@pytest.mark.parametrize("command", ["ask", "log"])
def test_a_bad_level_exits_2_with_a_message(autonomy_root, command):
    completed = run_cli(command, "--root", autonomy_root, "--robot", "arm-1", "--level", "sideways", "--cause",
                        "inspect", "--what", "look")
    assert completed.returncode == 2
    assert completed.stderr.strip()
    assert Ledger(autonomy_root).interventions() == []


def test_block_open_and_close(autonomy_root):
    ok("block", "open", "--root", autonomy_root, "--robot", "arm-1", "--cause", "approve_robot_run", "--why",
       "approve the pick run", "--item", "q7", "--by", "pick agent")
    [block] = Ledger(autonomy_root).blocks(open_only=True)
    assert block.robot == "arm-1"
    assert block.cause == "approve_robot_run"
    assert block.why == "approve the pick run"
    assert block.item == "q7"
    assert block.level == "remote"
    ok("block", "close", "--root", autonomy_root, block.id, "--by", "Zach")
    assert Ledger(autonomy_root).blocks(open_only=True) == []
    assert Ledger(autonomy_root).blocks()[0].until_t is not None


def test_ingest_kpi_and_causes(autonomy_root, tmp_path):
    chat = write_jsonl(
        tmp_path / "chat.jsonl",
        [{"id": f"ask-{index}", "t": 1_763_000_000.0 + index * 3600.0, "level": "hands_on",
          "cause": "power_or_replug", "robot": "arm-1", "avoidable": "yes", "fix": "powered hub"}
         for index in range(4)],
    )
    hours_file = write_jsonl(
        tmp_path / "hours.jsonl",
        [{"robot": "arm-1", "day": day, "hours": 8.0} for day in day_range("2025-11-01", "2025-11-30")],
    )
    config = tmp_path / "autonomy.toml"
    config.write_text(
        f'tz = "UTC"\n\n[hours]\nkind = "jsonl"\npath = "{hours_file}"\n\n'
        f'[[source]]\nkind = "labelled"\npath = "{chat}"\nsource = "chat"\n',
        encoding="utf-8",
    )
    ingested = ok("ingest", "--root", autonomy_root, "--config", config, "--json")
    one_json_document(ingested)
    assert len(Ledger(autonomy_root).interventions()) == 4
    one_json_document(ok("kpi", "--root", autonomy_root, "--config", config, "--json"))
    one_json_document(ok("kpi", "--root", autonomy_root, "--json"))
    causes = ok("causes", "--root", autonomy_root, "--json")
    one_json_document(causes)
    assert "power_or_replug" in causes.stdout


def test_todo_lifecycle(autonomy_root):
    ok("todo", "propose", "--root", autonomy_root, "--title", "Add a powered USB hub", "--cause", "power_or_replug",
       "--cause", "lights_or_cameras", "--owner", "dev agent", "--plan", "hub on the rack", "--expected",
       "fewer replugs", "--by", "Zach")
    [item] = Backlog(autonomy_root).items()
    assert item.title == "Add a powered USB hub"
    assert item.causes == ("power_or_replug", "lights_or_cameras")
    assert item.owner == "dev agent"
    assert item.plan == "hub on the rack"
    assert item.expected == "fewer replugs"
    assert item.status == "proposed"
    listed = ok("todo", "list", "--root", autonomy_root, "--json")
    one_json_document(listed)
    assert "Add a powered USB hub" in listed.stdout
    one_json_document(ok("todo", "--root", autonomy_root, "--json"))  # `list` is the default
    ok("todo", "accept", item.id, "--root", autonomy_root, "--by", "Zach", "--note", "go")
    assert Backlog(autonomy_root).get(item.id).status == "accepted"
    ok("todo", "start", item.id, "--root", autonomy_root, "--by", "dev agent")
    assert Backlog(autonomy_root).get(item.id).status == "doing"
    ok("todo", "evidence", item.id, "abc1234", "--root", autonomy_root, "--by", "dev agent")
    assert Backlog(autonomy_root).get(item.id).evidence == ("abc1234",)
    ok("todo", "done", item.id, "--root", autonomy_root, "--evidence", "reports/hub.html", "--by", "dev agent")
    done = Backlog(autonomy_root).get(item.id)
    assert done.status == "done"
    assert done.evidence == ("abc1234", "reports/hub.html")
    assert done.done_t is not None


def test_todo_drop(autonomy_root):
    ok("todo", "propose", "--root", autonomy_root, "--title", "Maybe later", "--cause", "inspect")
    [item] = Backlog(autonomy_root).items()
    ok("todo", "drop", item.id, "--root", autonomy_root, "--note", "not worth it")
    assert Backlog(autonomy_root).get(item.id).status == "dropped"


def test_illegal_todo_transitions_exit_2(autonomy_root):
    ok("todo", "propose", "--root", autonomy_root, "--title", "Add a powered USB hub", "--cause", "power_or_replug")
    [item] = Backlog(autonomy_root).items()
    # proposed -> done is not a transition (done comes from doing only)
    completed = run_cli("todo", "done", item.id, "--root", autonomy_root, "--evidence", "abc1234")
    assert completed.returncode == 2
    assert completed.stderr.strip()
    assert Backlog(autonomy_root).get(item.id).status == "proposed"
    ok("todo", "start", item.id, "--root", autonomy_root)
    # doing -> accepted is not a transition
    completed = run_cli("todo", "accept", item.id, "--root", autonomy_root)
    assert completed.returncode == 2
    assert completed.stderr.strip()
    # done without evidence
    completed = run_cli("todo", "done", item.id, "--root", autonomy_root)
    assert completed.returncode == 2
    assert "evidence" in completed.stderr
    assert Backlog(autonomy_root).get(item.id).status == "doing"


def test_status_claim_writes_the_claim_and_the_card(autonomy_root):
    completed = ok("status", "--root", autonomy_root, "--claim", "waiting_on_zach", "--blocked-on",
                   "a W&B sign-in", "--json")
    one_json_document(completed)
    claim = json.loads((autonomy_root / "agent" / "claim.json").read_text(encoding="utf-8"))
    assert claim["status"] == "waiting_on_zach"
    assert claim["blocked_on"] == "a W&B sign-in"
    card = json.loads((autonomy_root / "team" / "status.json").read_text(encoding="utf-8"))
    assert card["schema"] == "ai-team-status/1"
    assert card["status"] == "waiting_on_zach"
    assert card["blocked_on"] == "a W&B sign-in"


def test_status_blocked_without_blocked_on_exits_2(autonomy_root):
    completed = run_cli("status", "--root", autonomy_root, "--claim", "blocked")
    assert completed.returncode == 2
    assert completed.stderr.strip()


def test_agent_tick(autonomy_root):
    ok("ask", "--root", autonomy_root, "--robot", "arm-1", "--level", "remote", "--cause", "approve_robot_run",
       "--what", "give the go")
    completed = ok("agent", "tick", "--root", autonomy_root, "--json")
    one_json_document(completed)
    assert (autonomy_root / "team" / "status.json").is_file()
    assert (autonomy_root / "agent" / "kpi.jsonl").is_file()


def test_agent_tick_with_a_notify_command(autonomy_root, tmp_path):
    # an open block old enough to alert -> one action piped to the notify command
    received = tmp_path / "received.jsonl"
    relay = tmp_path / "relay.py"
    relay.write_text(
        "import sys, pathlib\n"
        f"with pathlib.Path({str(received)!r}).open('a') as handle:\n"
        "    handle.write(sys.stdin.read().strip() + '\\n')\n",
        encoding="utf-8",
    )
    # `agent tick` runs on the wall clock, so the block is placed two hours before it
    Ledger(autonomy_root).open_block(
        Block(id="b1", robot="arm-1", cause="approve_robot_run", why="approve the pick run",
              since_t=time.time() - 7200.0, item="q7", by="pick agent"),
        count=False,
    )
    ok("agent", "tick", "--root", autonomy_root, "--notify-cmd", f"{sys.executable} {relay}")
    lines = [json.loads(line) for line in received.read_text().splitlines() if line.strip()]
    assert any(line.get("kind") == "blocked" and line.get("key") == "blocked:b1" for line in lines)


def test_report(autonomy_root, tmp_path):
    ok("ask", "--root", autonomy_root, "--robot", "arm-1", "--level", "hands_on", "--cause", "power_or_replug",
       "--what", "replug")
    out = tmp_path / "report.html"
    ok("report", "--root", autonomy_root, "--out", out)
    assert out.is_file()
    assert "power_or_replug" in out.read_text(encoding="utf-8")


def test_demo_command(tmp_path):
    root = tmp_path / "demo"
    ok("demo", "--root", root, "--days", "3", "--seed", "1")
    assert (root / "hours.jsonl").is_file()
    assert (root / "autonomy.toml").is_file()


def test_the_console_script_is_installed(autonomy_root):
    script = Path(sys.executable).parent / "robot-autonomy"
    if not script.exists():
        pytest.skip("console script not installed in this environment")
    completed = subprocess.run([str(script), "causes", "--root", str(autonomy_root), "--json"],
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    json.loads(completed.stdout)


def test_todo_edit_sharpens_a_proposal(tmp_path):
    # Added 2026-11-18: the agent rewrites an auto-proposal's plan once it has read the episodes behind it.
    root = str(tmp_path / "root")
    proposed = run_cli("todo", "propose", "--root", root, "--title", "Remove power_or_replug", "--cause",
                       "power_or_replug", "--json")
    assert proposed.returncode == 0, proposed.stderr
    item_id = json.loads(proposed.stdout)["id"]
    edited = run_cli("todo", "edit", item_id, "--root", root, "--plan", "per-port hub", "--expected", "fewer replugs",
                     "--by", "Robot Autonomy Agent", "--json")
    assert edited.returncode == 0, edited.stderr
    item = json.loads(edited.stdout)
    assert (item["plan"], item["expected"]) == ("per-port hub", "fewer replugs")
