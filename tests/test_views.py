"""views/: the terminal screen and the self-contained HTML report (SPEC "views/")."""

from __future__ import annotations

import re

import pytest

from helpers import DAY_S, HOUR_S, day_range, hours_table, make_episode, make_intervention, t_of
from robot_autonomy import metrics
from robot_autonomy.agent import AgentConfig
from robot_autonomy.backlog import Backlog
from robot_autonomy.ledger import Ledger
from robot_autonomy.schema import Block
from robot_autonomy.views import html as html_view
from robot_autonomy.views import terminal

NOW = t_of("2026-11-18", 6.0)


def _episodes():
    # 5 asked hands_on episodes on 2026-11-17 (9 h that day): asked 9 / 5 = 1.8 h (R1); observed 0 -> 9 h (R2)
    power = [make_episode(f"p{hour}", t_of("2026-11-17", hour), avoidable="yes", fixes=("powered hub",))
             for hour in (1, 2, 3)]
    belt = [make_episode(f"b{hour}", t_of("2026-11-17", hour), cause="belt_or_items", avoidable="no")
            for hour in (4, 5)]
    return power + belt


def _board(root):
    board = Backlog(root)
    doing = board.propose(title="Add a powered USB hub", causes=("power_or_replug",), owner="dev agent",
                          proposed_by="Robot Autonomy Agent", now=NOW - 3 * DAY_S)
    board.move(doing.id, "doing", by="dev agent", now=NOW - 2 * DAY_S)
    verified = board.propose(title="Mount the camera cable clip", causes=("lights_or_cameras",), owner="dev agent",
                             proposed_by="Robot Autonomy Agent", now=NOW - 20 * DAY_S)
    board.move(verified.id, "doing", by="dev agent", now=NOW - 19 * DAY_S)
    board.mark_done(verified.id, by="dev agent", evidence=("abc1234",), now=NOW - 15 * DAY_S)
    board.record_effect(verified.id, {"before": {"per_100h": 12.0}, "after": {"per_100h": 1.0}, "p_value": 0.01,
                                      "drop": 0.92, "verdict": "verified"},
                        "verified", by="Robot Autonomy Agent", now=NOW - DAY_S)
    return board, doing, verified


def test_terminal_render(autonomy_root):
    hours = {("arm-1", "2026-11-17"): 9.0}
    kpi = metrics.kpi(_episodes(), hours, end="2026-11-17", days=7, tz="UTC")
    assert kpi.mtbi_h == pytest.approx(1.8)
    board, doing, verified = _board(autonomy_root)
    cause_rows = metrics.causes(_episodes(), now=NOW)
    blocks = [Block(id="b1", robot="arm-1", cause="approve_robot_run", why="approve the pick run",
                    since_t=NOW - 1.5 * HOUR_S, item="q7", by="pick agent")]
    screen = terminal.render(kpi, cause_rows, board, blocks, now=NOW)
    assert isinstance(screen, str)
    assert "\x1b" not in screen  # plain text, no ANSI
    assert "R1 Assisted · 1.8 h between hands-on interventions (7 d) · next R2 at 8 h" in screen
    assert "asked" in screen and "observed" in screen
    assert "power_or_replug" in screen
    assert "belt_or_items" in screen
    assert doing.id in screen  # the live improvement on power_or_replug
    verified_lines = [line for line in screen.splitlines() if "Mount the camera cable clip" in line]
    assert verified_lines and all("[x]" in line for line in verified_lines)
    doing_lines = [line for line in screen.splitlines() if "Add a powered USB hub" in line]
    assert doing_lines and not any("[x]" in line for line in doing_lines)
    assert "approve the pick run" in screen or "q7" in screen  # the open block


NETWORK_SOURCE = re.compile(
    r"""<(script|img|link|iframe|source|video|audio)\b[^>]*\b(src|href)\s*=\s*["']?\s*(https?:)?//""", re.IGNORECASE
)


@pytest.fixture
def report_root(autonomy_root):
    ledger = Ledger(autonomy_root)
    for day_index, day in enumerate(day_range("2026-11-04", "2026-11-17")):
        ledger.record(make_intervention(f"power-{day}", t_of(day, 9.0), avoidable="yes", fix="powered hub"))
        if day_index % 3 == 0:
            ledger.record(make_intervention(f"belt-{day}", t_of(day, 11.0), cause="belt_or_items", source="operator",
                                            avoidable="no"))
    ledger.open_block(Block(id="b1", robot="arm-1", cause="approve_robot_run", why="approve the pick run",
                            since_t=NOW - HOUR_S, item="q7"))
    _board(autonomy_root)
    return autonomy_root


def test_html_report_is_one_self_contained_file(report_root, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out_path = out_dir / "autonomy.html"
    hours = hours_table("arm-1", day_range("2026-11-01", "2026-11-17"), 10.0)
    written = html_view.report(report_root, config=AgentConfig(tz="UTC"), now=NOW, out_path=out_path, hours=hours)
    assert str(written) == str(out_path)
    assert [path.name for path in out_dir.iterdir()] == ["autonomy.html"]  # one file
    page = out_path.read_text(encoding="utf-8")
    assert NETWORK_SOURCE.search(page) is None  # no network: nothing fetched from http(s)
    assert not re.search(r"url\(\s*['\"]?https?:", page, re.IGNORECASE)
    assert not re.search(r"@import\s+['\"]?(url\()?\s*['\"]?https?:", page, re.IGNORECASE)
    for level, name in [("R0", "Bring-up"), ("R1", "Assisted"), ("R2", "Shift"), ("R3", "Overnight"),
                        ("R4", "Lights-out")]:
        assert level in page
        assert name in page
    assert any(marker in page for marker in ("← now", "&larr; now", "&#8592; now", "&#x2190; now"))
    assert "<svg" in page
    assert "power_or_replug" in page
    assert "belt_or_items" in page
    assert "Mount the camera cable clip" in page
    assert "Add a powered USB hub" in page
