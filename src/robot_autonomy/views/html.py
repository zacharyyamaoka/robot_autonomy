"""One self-contained HTML report (inline CSS and SVG, no network): the ladder, the KPI over time, reliability
growth, why they asked, the todo list crossed off with measured effects, open blocks, sources and recent interventions.
"""

from __future__ import annotations

import html
import math
from collections import Counter
from pathlib import Path

from .. import _format
from ..agent import AgentConfig, AutonomyAgent
from ..backlog import OPEN
from ..hours import days_with_hours
from ..metrics import ROBOT_LADDER, causes, cumulative_hours, daily
from ..schema import READINGS
from .terminal import level_line, readings_line

GOODHART = ("An intervention count can be gamed by the agents being measured: they can stop asking and stall, or "
            "relabel an ask. Two guards: the level is the lower of two readings (what the agents write, and what they "
            "cannot: a person's own log, the robot's faults, field tickets), and the KPI divides only by hours the "
            "robot actually worked, so an idle robot never looks autonomous.")

CSS = """
:root{color-scheme:light;--surface:#fcfcfb;--surface-2:#f3f2ef;--ink:#0b0b0b;--ink-2:#52514e;--ink-3:#77766f;
--rule:#e4e3df;--series-1:#2a78d6;--series-2:#eb6834;--good:#0a8a0a;--critical:#c03434;--accent-bg:#eaf2fc}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--surface:#1a1a19;
--surface-2:#242422;--ink:#f0efec;--ink-2:#c3c2b7;--ink-3:#97968d;--rule:#383835;--series-1:#3987e5;
--series-2:#d95926;--good:#3cc23c;--critical:#e66767;--accent-bg:#1f2b3a}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--surface-2:#242422;--ink:#f0efec;--ink-2:#c3c2b7;
--ink-3:#97968d;--rule:#383835;--series-1:#3987e5;--series-2:#d95926;--good:#3cc23c;--critical:#e66767;
--accent-bg:#1f2b3a}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,
sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.5rem;margin:0 0 4px}
h2{font-size:1.1rem;margin:36px 0 8px;padding-top:12px;border-top:1px solid var(--rule)}
p{margin:6px 0}
.sub,.note{color:var(--ink-2)}
.note{font-size:.9rem}
.hero{margin:16px 0 8px;padding:14px 16px;border-radius:10px;background:var(--accent-bg)}
.hero .level{font-size:1.35rem;font-weight:650}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{text-align:left;padding:6px 10px 6px 0;border-bottom:1px solid var(--rule);vertical-align:top}
th{color:var(--ink-2);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.now td{font-weight:650}
.now-mark{color:var(--series-1);font-weight:700;white-space:nowrap}
figure{margin:8px 0;overflow-x:auto;-webkit-overflow-scrolling:touch}
figure svg{display:block;width:100%;min-width:520px;height:auto}
figcaption{color:var(--ink-2);font-size:.88rem}
svg text{fill:var(--ink-2);font-size:13px}
svg .grid{stroke:var(--rule);stroke-width:1}
svg .gate{stroke:var(--ink-3);stroke-width:1;stroke-dasharray:4 4}
svg .line{fill:none;stroke:var(--series-1);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
svg .dot{fill:var(--series-1);stroke:var(--surface);stroke-width:2}
svg .fit{fill:none;stroke:var(--series-2);stroke-width:2}
svg .bar{fill:var(--series-1)}
svg .hit{fill:transparent}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:.88rem;color:var(--ink-2);margin:4px 0}
.swatch{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
ul.todo{list-style:none;padding:0;margin:0}
ul.todo li{padding:10px 0;border-bottom:1px solid var(--rule)}
.chip{display:inline-block;font-size:.78rem;font-weight:600;padding:1px 8px;border-radius:999px;
background:var(--surface-2);color:var(--ink-2);margin-right:8px;white-space:nowrap}
.chip.verified{color:var(--good)}
.chip.ineffective{color:var(--critical)}
li.verified .title{text-decoration:line-through;text-decoration-thickness:1.5px}
.meta{color:var(--ink-2);font-size:.86rem}
.effect{font-size:.9rem}
details{margin:6px 0}
summary{cursor:pointer;color:var(--ink-2);font-size:.9rem}
code{font-size:.88em}
footer{margin-top:40px;color:var(--ink-2);font-size:.88rem}
"""


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


# --------------------------------------------------------------------------------------------------------- charts

def _log_ticks(low: float, high: float) -> list[float]:
    ticks = []
    exponent = math.floor(math.log10(low))
    while 10 ** exponent <= high * 1.0001:
        for multiple in (1, 2, 5):
            value = multiple * 10 ** exponent
            if low * 0.9999 <= value <= high * 1.0001:
                ticks.append(value)
        exponent += 1
    return ticks


def _nice_floor(value: float) -> float:
    """The largest 1-2-5 step at or below value (the bottom of a log axis)."""
    exponent = math.floor(math.log10(value))
    for multiple in (5, 2, 1):
        if multiple * 10 ** exponent <= value:
            return multiple * 10 ** exponent
    return 10 ** exponent


def _x_labels(count: int) -> list[int]:
    """Indices to label on a day axis: about six, always the last, never two crowding each other."""
    every = max(1, count // 6)
    picked = [index for index in range(0, count, every)]
    if picked and count - 1 - picked[-1] < max(1, every // 2):
        picked.pop()
    return picked + [count - 1] if count else []


def _tick_label(value: float) -> str:
    return f"{value:g}"


def _kpi_chart(rows: list[dict], ladder, window_days: int) -> str:
    """MTBI of the window ending each day, log scale, with the ladder's gates as dashed thresholds."""
    points = [(index, row) for index, row in enumerate(rows) if row["mtbi_h"]]
    if not points:
        return '<p class="note">No window with working hours yet.</p>'
    width, height = 640, 260
    left, right, top, bottom = 48, 92, 14, 34
    values = [row["mtbi_h"] for _, row in points]
    gates = [gate for gate in ladder if gate.hours > 0]
    low = _nice_floor(min(values + [1.0]) * 0.8)
    upper_gate = next((gate.hours for gate in gates if gate.hours > max(values)), None)
    high = max(values + ([upper_gate] if upper_gate else [])) * 1.25
    span_x = max(1, len(rows) - 1)

    def x_of(index: int) -> float:
        return left + (width - left - right) * index / span_x

    def y_of(value: float) -> float:
        fraction = (math.log10(value) - math.log10(low)) / (math.log10(high) - math.log10(low))
        return top + (height - top - bottom) * (1 - fraction)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Working hours between interventions, '
             f'{window_days}-day window, per day">']
    for tick in _log_ticks(low, high):
        y = y_of(tick)
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{_tick_label(tick)}</text>')
    for gate in gates:
        if low <= gate.hours <= high:
            y = y_of(gate.hours)
            parts.append(f'<line class="gate" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
            parts.append(f'<text x="{width - right + 6}" y="{y + 4:.1f}">{_e(gate.level)} {_e(gate.name)}</text>')
    for index in _x_labels(len(rows)):
        parts.append(f'<text x="{x_of(index):.1f}" y="{height - 10}" text-anchor="middle">'
                     f'{_e(rows[index]["day"][5:])}</text>')
    segments, current = [], []
    for index, row in enumerate(rows):
        if row["mtbi_h"]:
            current.append(f"{x_of(index):.1f},{y_of(row['mtbi_h']):.1f}")
        elif current:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    for segment in segments:
        parts.append(f'<polyline class="line" points="{" ".join(segment)}"/>')
    last_index, last_row = points[-1]
    parts.append(f'<circle class="dot" cx="{x_of(last_index):.1f}" cy="{y_of(last_row["mtbi_h"]):.1f}" r="5"/>')
    for index, row in points:
        tip = f'{row["day"]} · {_format.hours(row["mtbi_h"])} ({window_days} d) · {row["level"]}'
        parts.append(f'<circle class="hit" cx="{x_of(index):.1f}" cy="{y_of(row["mtbi_h"]):.1f}" r="9">'
                     f'<title>{_e(tip)}</title></circle>')
    parts.append(f'<text x="{left}" y="{top - 2}" style="font-size:12px">hours (log)</text>')
    parts.append("</svg>")
    return "".join(parts)


def _hours_chart(rows: list[dict]) -> str:
    """Working hours per day: the denominator, its own chart (never a second axis)."""
    if not rows:
        return ""
    width, height = 640, 150
    left, right, top, bottom = 48, 92, 12, 30
    high = max([row["working_h"] for row in rows] + [1.0]) * 1.1
    slot = (width - left - right) / len(rows)
    bar_width = max(1.0, slot - 2.0)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Working hours per day">']
    for tick in (0.0, high / 2, high / 1.1):
        y = top + (height - top - bottom) * (1 - tick / high)
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{tick:.0f}</text>')
    labelled = set(_x_labels(len(rows)))
    for index, row in enumerate(rows):
        x = left + slot * index + 1
        bar_height = (height - top - bottom) * row["working_h"] / high
        y = height - bottom - bar_height
        if bar_height > 0:
            radius = min(2.0, bar_width / 2)
            parts.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
                         f'height="{bar_height:.1f}" rx="{radius:.1f}"/>')
        parts.append(f'<rect class="hit" x="{x:.1f}" y="{top}" width="{slot:.1f}" height="{height - top - bottom}">'
                     f'<title>{_e(row["day"])} · {_format.hours(row["working_h"])} worked · {row["episodes"]} counted'
                     f'</title></rect>')
        if index in labelled:
            parts.append(f'<text x="{x + bar_width / 2:.1f}" y="{height - 9}" text-anchor="middle">'
                         f'{_e(row["day"][5:])}</text>')
    parts.append(f'<text x="{left}" y="{top - 1}" style="font-size:12px">hours</text>')
    parts.append("</svg>")
    return "".join(parts)


def _growth_chart(event_h: list[float], total_h: float, fit) -> str:
    """Cumulative counted episodes vs cumulative working hours, log-log, with the Crow-AMSAA expected count."""
    times = sorted(t for t in event_h if 0 < t <= total_h)
    if len(times) < 2:
        return '<p class="note">Not enough counted episodes for a reliability-growth fit yet.</p>'
    width, height = 640, 280
    left, right, top, bottom = 52, 24, 14, 38
    x_low = 10 ** math.floor(math.log10(times[0]))
    x_high = total_h * 1.15
    y_low, y_high = 0.8, len(times) * 1.3

    def x_of(value: float) -> float:
        return left + (width - left - right) * (math.log10(value) - math.log10(x_low)) / (
            math.log10(x_high) - math.log10(x_low))

    def y_of(value: float) -> float:
        fraction = (math.log10(value) - math.log10(y_low)) / (math.log10(y_high) - math.log10(y_low))
        return top + (height - top - bottom) * (1 - fraction)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Reliability growth, log-log">']
    for tick in _log_ticks(y_low, y_high):
        y = y_of(tick)
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{_tick_label(tick)}</text>')
    for tick in _log_ticks(x_low, x_high):
        x = x_of(tick)
        parts.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{height - bottom}"/>')
        parts.append(f'<text x="{x:.1f}" y="{height - 20}" text-anchor="middle">{_tick_label(tick)}</text>')
    parts.append(f'<text x="{(left + width - right) / 2:.0f}" y="{height - 4}" text-anchor="middle" '
                 f'style="font-size:12px">cumulative working hours (log)</text>')
    parts.append(f'<text x="{left}" y="{top - 2}" style="font-size:12px">cumulative episodes (log)</text>')
    if fit is not None:
        steps = 48
        start = math.log10(times[0])
        stop = math.log10(total_h)
        coordinates = []
        for step in range(steps + 1):
            hours_value = 10 ** (start + (stop - start) * step / steps)
            expected = fit.lam * hours_value ** fit.beta
            if y_low <= expected <= y_high:
                coordinates.append(f"{x_of(hours_value):.1f},{y_of(expected):.1f}")
        if coordinates:
            parts.append(f'<polyline class="fit" points="{" ".join(coordinates)}"/>')
    for count, hours_value in enumerate(times, start=1):
        x, y = x_of(hours_value), y_of(count)
        parts.append(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="4"/>')
        parts.append(f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="8"><title>episode {count} at '
                     f'{_format.hours(hours_value)} worked</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


# -------------------------------------------------------------------------------------------------------- sections

def _ladder_table(kpi, ladder, window_days: int) -> str:
    rows = []
    for gate in ladder:
        now = gate.level == kpi.level
        rows.append(f'<tr class="{"now" if now else ""}"><td>{_e(gate.level)}</td><td>{_e(gate.name)}</td>'
                    f'<td class="num">{gate.hours:g} h</td><td>{_e(gate.meaning)}</td>'
                    f'<td class="now-mark">{"← now" if now else ""}</td></tr>')
    return ('<div class="scroll"><table><thead><tr><th>level</th><th>name</th>'
            f'<th class="num">gate (hours between interventions, {window_days} days)</th><th>what it means</th>'
            '<th></th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def _effect_html(effect: dict | None) -> str:
    if not effect:
        return ""
    before, after = effect.get("before") or {}, effect.get("after") or {}
    drop = effect.get("drop")
    change = _format.percent(None if drop is None else -drop)
    return (f'<div class="effect">measured: {_format.rate(before.get("per_100h"))} → '
            f'{_format.rate(after.get("per_100h"))} per 100 working h ({_e(change)}, '
            f'p {_e(_format.p_value(effect.get("p_value")))}; {before.get("episodes", 0)} in '
            f'{_e(_format.hours(before.get("hours")))} before, {after.get("episodes", 0)} in '
            f'{_e(_format.hours(after.get("hours")))} after)</div>')


CHIPS = {"doing": "▶ doing", "proposed": "○ proposed", "accepted": "○ accepted", "done": "… done, verifying",
         "verified": "✓ verified", "ineffective": "✗ ineffective", "dropped": "– dropped"}
TODO_ORDER = ("doing", "accepted", "proposed", "done", "verified", "ineffective", "dropped")


def _todo_list(items, tz) -> str:
    if not items:
        return '<p class="note">Nothing proposed yet.</p>'
    ordered = sorted(items, key=lambda item: (TODO_ORDER.index(item.status), item.proposed_t, item.id))
    rows = []
    for item in ordered:
        evidence = ", ".join(item.evidence)
        rows.append(
            f'<li class="{_e(item.status)}"><span class="chip {_e(item.status)}">{_e(CHIPS[item.status])}</span>'
            f'<span class="title">{_e(item.title)}</span>'
            f'<div class="meta"><code>{_e(item.id)}</code> · {_e(", ".join(item.causes))} · owner {_e(item.owner)}'
            f' · proposed {_e(_format.when(item.proposed_t, tz))}'
            + (f' · done {_e(_format.when(item.done_t, tz))}' if item.done_t else "")
            + (f' · evidence: {_e(evidence)}' if evidence else "") + "</div>"
            + (f'<div class="meta">why: {_e(item.why)}</div>' if item.why else "")
            + _effect_html(item.effect) + "</li>")
    return '<ul class="todo">' + "".join(rows) + "</ul>"


def _table(headers, rows, numeric=()) -> str:
    head = "".join(f'<th class="{"num" if index in numeric else ""}">{_e(header)}</th>'
                   for index, header in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td class="{"num" if index in numeric else ""}">{cell}</td>'
                                    for index, cell in enumerate(row)) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def report(root, *, config: AgentConfig, now, out_path, hours=None) -> Path:
    """Write the report to out_path and return it."""
    now = float(now)
    agent = AutonomyAgent(root, config, hours=hours)
    tz = config.tz
    hours_table = agent.hours()
    interventions = agent.ledger.interventions()
    all_episodes = agent.episodes()
    blocks = agent.ledger.blocks()
    kpi = agent._kpi(all_episodes, hours_table, blocks, now)
    window_days = config.window_days
    rows = daily(all_episodes, hours_table, window_days=window_days, count_levels=config.count_levels, tz=tz)
    # WHY trim: interventions may be labelled from long before the hours clock started; days before the first
    # worked day have no ratio, and drawing them squeezes the real series into the right edge.
    first_worked = next((index for index, row in enumerate(rows) if row["working_h"]), len(rows))
    rows = rows[first_worked:]
    counted = agent.counted_episodes(all_episodes, hours_table)
    event_h, total_h = cumulative_hours(counted, hours_table, tz=tz)
    fit = agent.growth(all_episodes, hours_table)
    cause_rows = causes(all_episodes, now=now)
    items = agent.backlog.items()

    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<title>Robot Autonomy</title><style>{CSS}</style></head><body><main>']
    parts.append(f'<h1>Robot Autonomy</h1><p class="sub">{_e(config.remit)}</p>')
    parts.append(f'<div class="hero"><div class="level">{_e(level_line(kpi))}</div>'
                 f'<div>{_e(readings_line(kpi))}</div>'
                 f'<div class="note">window {_e(kpi.start)} – {_e(kpi.end)} · {_e(_format.hours(kpi.working_h))} '
                 f'worked · {kpi.counted} counted · {kpi.hands_on} hands-on, {kpi.remote} remote, '
                 f'{kpi.commissioning} commissioning, {kpi.unclocked} unclocked · blocked '
                 f'{_e(_format.hours(kpi.blocked_h))}'
                 + (f' · {kpi.human_s_per_h:.0f} person-seconds per working hour' if kpi.human_s_per_h is not None
                    else "") + "</div></div>")
    open_items = [item for item in items if item.status in OPEN]
    verified = [item for item in items if item.status == "verified"]
    parts.append(f'<p class="note">Todo: {len(open_items)} open, {len(verified)} verified by measurement, '
                 f'{sum(1 for item in items if item.status == "ineffective")} ineffective · '
                 f'{len(interventions)} interventions in {len(all_episodes)} episodes · '
                 f'as of {_e(_format.when(now, tz))}{" " + _e(tz) if tz else ""}</p>')

    parts.append(f'<h2>The ladder</h2>{_ladder_table(kpi, ROBOT_LADDER, window_days)}'
                 '<p class="note">Each gate is a stretch of unattended time a person can picture: an hour, a shift, '
                 'a night, a week. The level held is the lower of the two readings.</p>')

    table_rows = [(_e(row["day"]), _e(_format.hours(row["working_h"])), str(row["episodes"]),
                   _e(_format.hours(row["mtbi_h"])), _e(row["level"])) for row in rows]
    parts.append('<h2>The KPI over time</h2>'
                 f'<figure>{_kpi_chart(rows, ROBOT_LADDER, window_days)}<figcaption>Working hours between counted '
                 f'interventions over the {window_days} days ending each day (log scale); dashed lines are the '
                 'ladder\'s gates.</figcaption></figure>'
                 f'<figure>{_hours_chart(rows)}<figcaption>Working hours per day, the denominator (the Robot '
                 'Activity Agent\'s activity hours): an idle robot never needs a hand.</figcaption></figure>'
                 '<details><summary>Table view</summary>'
                 + _table(("day", "worked", "counted", f"MTBI ({window_days} d)", "level"), table_rows,
                          numeric=(1, 2, 3)) + "</details>")

    if fit is not None:
        trend = ("interventions are thinning out" if fit.improving
                 else "interventions are not thinning out yet")
        growth_note = (f'Crow-AMSAA fit over {fit.n} counted episodes in {_format.hours(fit.total_h)}: β = '
                       f'{fit.beta:.2f} ({trend}); instantaneous MTBI {_format.hours(fit.mtbi_now_h)}, average '
                       f'{_format.hours(fit.mtbi_avg_h)}.')
    elif len(days_with_hours(hours_table)) < config.growth_min_days:
        growth_note = (f"Too early for a trend: the hours clock has run {len(days_with_hours(hours_table))} days, "
                       f"and the fit waits for {config.growth_min_days}.")
    else:
        growth_note = "No fit yet (it needs at least two counted episodes)."
    parts.append('<h2>Reliability growth</h2>'
                 + ('' if growth_note.startswith("Too early") else
                    '<div class="legend"><span><span class="swatch" style="background:var(--series-1)"></span>'
                    'counted episodes</span><span><span class="swatch" style="background:var(--series-2)"></span>'
                    'Crow-AMSAA expected count</span></div>'
                    f'<figure>{_growth_chart(event_h, total_h, fit)}</figure>')
                 + f'<p class="note">{_e(growth_note)} '
                 'A slope below 1 on this log-log plot (β &lt; 1) means each working hour needs fewer hands.</p>')

    cause_table = []
    for row in cause_rows:
        live = agent.backlog.live(row.cause)
        cause_table.append((_e(row.level.replace("_", "-")), _e(row.cause), str(row.episodes), str(row.asked),
                            str(row.observed), str(row.avoidable), _e("; ".join(row.fixes[:3])),
                            _e(live[0].id if live else "-")))
    parts.append('<h2>Why they asked</h2>'
                 + (_table(("kind", "cause", "episodes", "asked", "observed", "avoidable", "what would remove it",
                            "live improvement"), cause_table, numeric=(2, 3, 4, 5)) if cause_table
                    else '<p class="note">No interventions recorded.</p>')
                 + '<p class="note">All history. Episodes = asked + observed: the two readings can see the same real '
                   'event, so read each column on its own too. The agent works the avoidable causes in order and '
                   'keeps a live improvement on the top ones.</p>')

    parts.append('<h2>The todo list</h2>' + _todo_list(items, tz)
                 + '<p class="note">A fix is crossed off only when its cause measurably went down after it landed '
                   '(an exact test of two Poisson rates), never because someone says it is done.</p>')

    open_blocks = [block for block in blocks if block.until_t is None]
    block_rows = [(_e(block.robot), _e(block.item or "-"), _e(_format.when(block.since_t, tz)),
                   _e(_format.duration(block.seconds(now))), _e(f"{block.level}, {block.cause}"), _e(block.why))
                  for block in open_blocks]
    parts.append('<h2>Open blocks</h2>'
                 + (_table(("robot", "item", "since", "waiting", "kind", "why"), block_rows) if block_rows
                    else '<p class="note">Nothing waits on a person right now.</p>'))

    by_source_iv = Counter((intervention.source, intervention.reading) for intervention in interventions)
    by_source_ep = Counter((episode.source, episode.reading) for episode in all_episodes)
    hands_on_ep = Counter((episode.source, episode.reading) for episode in all_episodes
                          if episode.level == "hands_on")
    keys = sorted(set(by_source_iv) | set(by_source_ep), key=lambda key: (READINGS.index(key[1])
                                                                          if key[1] in READINGS else 9, key[0]))
    source_rows = [(_e(source), _e(reading), str(by_source_iv[(source, reading)]),
                    str(by_source_ep[(source, reading)]), str(hands_on_ep[(source, reading)]))
                   for source, reading in keys]
    parts.append('<h2>Interventions by source and reading</h2>'
                 + (_table(("source", "reading", "interventions", "episodes", "hands-on episodes"), source_rows,
                           numeric=(2, 3, 4)) if source_rows else '<p class="note">None yet.</p>')
                 + '<p class="note"><b>asked</b>: written by the agents or robots being measured. <b>observed</b>: '
                   'written by something they cannot write.</p>')

    recent = sorted(interventions, key=lambda intervention: (-intervention.t, intervention.id))[:25]
    recent_rows = [(_e(_format.when(intervention.t, tz)), _e(intervention.robot),
                    _e(intervention.level.replace("_", "-")), _e(intervention.cause),
                    _e(f"{intervention.source} / {intervention.reading}"), _e(intervention.what))
                   for intervention in recent]
    parts.append('<h2>Recent interventions</h2>'
                 + (_table(("when", "robot", "kind", "cause", "source / reading", "what"), recent_rows)
                    if recent_rows else '<p class="note">None yet.</p>'))

    parts.append(f'<footer><p><b>Goodhart caveat.</b> {_e(GOODHART)}</p>'
                 '<p>Stock vocabulary: FRACAS (MIL-HDBK-2155) for the cause → fix → verify loop; MTBI, the '
                 'disengagement report\'s "miles per disengagement"; Crow-AMSAA reliability growth (MIL-HDBK-189); '
                 'a Pareto of causes for the backlog order.</p></footer>')
    parts.append("</main></body></html>\n")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    return out
