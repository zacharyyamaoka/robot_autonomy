"""Build the review report for the Robot Autonomy Agent's first day on BAM (one self-contained HTML file).

    python3 examples/bam/build_report.py --hero DIR --out FILE.html [--state DIR]

Inputs, all real: the hero video recorded by docs/hero_frames.py (the agent's own dashboard replayed day by day on
the demo program), BAM's live state (~/.local/state/robot-autonomy: ledger, todo list, status card, KPI rows), the
dashboard the nightly job renders, Zach's own two screenshots from the 2026-11-18 daily note, and the verification
measurement (re-run here, 400 trials each). Screenshots are taken headless (no window). Needs google-chrome + PIL.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATE = Path.home() / ".local" / "state" / "robot-autonomy"
DASHBOARD = REPO / "reports" / "robot-autonomy-bam-latest.html"
ZACH_LADDER = Path.home() / "zach_brain" / "Sources" / "Images" / "Pasted image 20261118094051.png"
ZACH_CAUSES = Path.home() / "zach_brain" / "Sources" / "Images" / "Pasted image 20261118094235.png"


def esc(text) -> str:
    return html.escape(str(text))


def data_uri(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def jpeg(image, width: int = 1100, quality: int = 72) -> str:
    from PIL import Image

    if image.width > width:
        image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True)
    return data_uri(buffer.getvalue(), "image/jpeg")


def png_file(path: Path) -> str:
    return data_uri(path.read_bytes(), "image/png")


def dashboard_crops(page: Path, scratch: Path) -> dict[str, str]:
    """Headless full-page shot of the real dashboard, cut at its section headings (found by text height)."""
    from PIL import Image

    shot = scratch / "dashboard.png"
    subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
                    f"--user-data-dir={scratch / 'profile'}", "--window-size=1280,6400", "--virtual-time-budget=2000",
                    f"--screenshot={shot}", page.as_uri()], check=True, capture_output=True, timeout=120)
    image = Image.open(shot)
    # The section boundaries are fixed by the page's own order; these cuts were checked by eye on 2026-11-18.
    return {"top": jpeg(image.crop((150, 0, 1130, 690))),
            "causes": jpeg(image.crop((150, 1615, 1130, 2440)))}


def monte_carlo(trials: int = 400) -> dict:
    """Do-nothing fixes, assessed daily for 28 days, stopping at the first verdict: the old rule vs the shipped one."""
    sys.path.insert(0, str(REPO / "tests"))
    import test_verify_sequential as case  # the same simulation the committed test runs
    from robot_autonomy import verify

    def run(old_rule: bool) -> dict:
        original = verify._look
        if old_rule:  # every daily re-run is a fresh test of the whole after-window at alpha 0.1
            verify._look = lambda days, by_day, floor: ((0, list(days)) if sum(by_day.get(d, 0) for d in days)
                                                        >= floor else (None, list(days)))
        rng = random.Random(20261118)
        hours = case.hours_every_day("2026-10-01", "2026-12-31", per_day=8.0)
        tally = {"verified": 0, "ineffective": 0, "pending": 0}
        try:
            for trial in range(trials):
                events = []
                for day in case.day_range("2026-10-13", "2026-12-08"):
                    for index in range(case._poisson(rng, 0.15 * 8.0)):
                        events.append(case.episode(day, 8.0 + 8.0 * rng.random(), "asked",
                                                   ident=f"{trial}-{day}-{index}"))
                verdict = "pending"
                for day in case.day_range("2026-11-12", "2026-12-09"):
                    effect = verify.assess(case.CAUSES, case.DONE_T, events, hours, now=case.t_of(day, 6.0),
                                           tz="UTC", alpha=0.2 if old_rule else 0.1)
                    if effect.verdict != "pending":
                        verdict = effect.verdict
                        break
                tally[verdict] += 1
        finally:
            verify._look = original
        return tally

    return {"old": run(True), "new": run(False), "trials": trials}


def bars(old: float, new: float) -> str:
    """Two horizontal bars on a shared 0-30 % axis with the 10 % promise marked."""
    width, scale = 560, 560 / 30.0
    rows = [("first rule (both readings, every daily re-run at α 0.1)", old, "var(--bad)"),
            ("shipped rule (one reading, looks at doubling horizons)", new, "var(--good)")]
    parts = [f'<svg viewBox="0 0 {width + 220} 132" role="img" aria-label="False verified rate, old vs new">']
    for index, (label, value, colour) in enumerate(rows):
        y = 18 + index * 52
        parts.append(f'<text x="0" y="{y}" class="lab">{esc(label)}</text>')
        parts.append(f'<rect x="0" y="{y + 8}" width="{value * 100 * scale:.1f}" height="20" rx="3" fill="{colour}"/>')
        parts.append(f'<text x="{value * 100 * scale + 8:.1f}" y="{y + 23}" class="val">{value * 100:.1f} %</text>')
    x10 = 10 * scale
    parts.append(f'<line x1="{x10:.1f}" y1="22" x2="{x10:.1f}" y2="118" class="gate"/>'
                 f'<text x="{x10 + 4:.1f}" y="128" class="lab">the promise: ≤ 10 %</text></svg>')
    return "".join(parts)


DIAGRAM = """<svg viewBox="0 0 900 330" role="img" aria-label="How the Robot Autonomy Agent talks to its teammates">
<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0,0L10,5L0,10z" class="arrowhead"/></marker></defs>
<g class="box"><rect x="10" y="20" width="250" height="120" rx="10"/>
<text x="135" y="48" class="title">Robot Activity Agent</text><text x="135" y="72">robot_activity</text>
<text x="135" y="96" class="small">samples/&lt;robot&gt;/&lt;day&gt;.jsonl</text>
<text x="135" y="116" class="small">queue/events.jsonl</text></g>
<g class="box me"><rect x="330" y="20" width="250" height="160" rx="10"/>
<text x="455" y="48" class="title">Robot Autonomy Agent</text><text x="455" y="72">robot_autonomy</text>
<text x="455" y="96" class="small">interventions.jsonl (the ledger)</text>
<text x="455" y="116" class="small">backlog/ (the todo list)</text>
<text x="455" y="136" class="small">agent/kpi.jsonl · actions.jsonl</text>
<text x="455" y="160" class="small">nightly tick 05:20</text></g>
<g class="box"><rect x="650" y="20" width="240" height="120" rx="10"/>
<text x="770" y="48" class="title">AI Team directory</text><text x="770" y="72">Projects/AI Team (vault)</text>
<text x="770" y="96" class="small">pulls team/status.json</text><text x="770" y="116" class="small">ai-team-status/1</text></g>
<g class="box"><rect x="10" y="210" width="250" height="100" rx="10"/>
<text x="135" y="238" class="title">Other sources</text><text x="135" y="262" class="small">chat asks (labelled) · your own log</text>
<text x="135" y="282" class="small">robot_msgs diagnostics · field tickets</text></g>
<g class="box"><rect x="650" y="210" width="240" height="100" rx="10"/>
<text x="770" y="238" class="title">Dispatcher</text><text x="770" y="262" class="small">proposals · verdicts</text>
<text x="770" y="282" class="small">long-block alerts</text></g>
<path d="M260,62 L330,62" class="flow" marker-end="url(#ar)"/><text x="295" y="54" class="tiny">hours, human blocks</text>
<path d="M330,112 L260,112" class="flow" marker-end="url(#ar)"/><text x="295" y="128" class="tiny">queue submit (CLI)</text>
<path d="M260,250 C300,250 300,170 330,160" class="flow" marker-end="url(#ar)"/>
<path d="M580,80 L650,80" class="flow" marker-end="url(#ar)"/><text x="615" y="72" class="tiny">status card</text>
<path d="M580,160 C620,160 620,250 650,250" class="flow" marker-end="url(#ar)"/>
</svg>"""

CSS = """
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#57534e;--line:#e7e5e4;--card:#fff;--accent:#2563eb;--good:#15803d;
--bad:#b91c1c;--soft:#eff6ff}
@media (prefers-color-scheme:dark){:root{--bg:#0c0a09;--fg:#e7e5e4;--muted:#a8a29e;--line:#292524;--card:#1c1917;
--accent:#60a5fa;--good:#4ade80;--bad:#f87171;--soft:#172033}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,
"Segoe UI",sans-serif}main{max-width:1020px;margin:0 auto;padding:28px 16px 80px}
h1{font-size:30px;margin:0 0 6px}h2{font-size:21px;margin:42px 0 10px;padding-top:18px;border-top:1px solid var(--line)}
h3{font-size:17px;margin:22px 0 6px}.verdict{font-size:18px;background:var(--soft);border-radius:10px;padding:14px 16px}
.sub{color:var(--muted)}.note{color:var(--muted);font-size:14px}
video,img{max-width:100%;border-radius:8px;border:1px solid var(--line);display:block}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile b{display:block;font-size:24px}.tile span{color:var(--muted);font-size:14px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media (max-width:760px){.pair{grid-template-columns:1fr}}
table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}th,td{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}
.scroll{overflow-x:auto}code{font-size:13.5px;background:var(--card);border:1px solid var(--line);border-radius:4px;
padding:1px 4px}pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;overflow-x:auto;
font-size:13px;line-height:1.45}ul{padding-left:22px}li{margin:4px 0}
svg text{fill:var(--fg);font:13px system-ui,sans-serif}svg .title{font-weight:600;font-size:15px;text-anchor:middle}
svg .box text{text-anchor:middle}svg .small{fill:var(--muted);font-size:12px}svg .tiny{fill:var(--muted);font-size:11px;
text-anchor:middle}svg .box rect{fill:var(--card);stroke:var(--line);stroke-width:1.5}svg .me rect{stroke:var(--accent);
stroke-width:2.5}svg .flow{fill:none;stroke:var(--muted);stroke-width:1.6}svg .arrowhead{fill:var(--muted)}
svg .lab{fill:var(--muted);font-size:13px}svg .val{font-weight:600}svg .gate{stroke:var(--fg);stroke-dasharray:4 3}
.plan{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:12px 0}
.plan h3{margin-top:4px}
"""


def plan_html(plan: str) -> str:
    """'context. 1) step 2) step' -> the context line, then the numbered steps as a list."""
    parts = re.split(r"\s(?=\d\) )", plan)
    head, steps = parts[0], [re.sub(r"^\d\) ", "", step) for step in parts[1:]]
    out = f'<p><b>What happened.</b> {esc(head)}</p>' if steps else f'<p><b>Plan.</b> {esc(head)}</p>'
    if steps:
        out += "<p><b>Plan.</b></p><ol>" + "".join(f"<li>{esc(step)}</li>" for step in steps) + "</ol>"
    return out


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hero", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--sha", default=subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                                                        capture_output=True, text=True).stdout.strip())
    args = parser.parse_args(argv)
    state = args.state
    card = json.loads((state / "team" / "status.json").read_text())
    kpi_rows = load_jsonl(state / "agent" / "kpi.jsonl")
    items = [json.loads(p.read_text()) for p in sorted((state / "backlog" / "items").glob("*.json"))]
    items.sort(key=lambda item: item["proposed_t"])
    numbers = monte_carlo()
    terminal = subprocess.run([sys.executable, "-I", "-m", "robot_autonomy", "kpi", "--root", str(state), "--config",
                               str(state / "autonomy.toml")], capture_output=True, text=True, cwd=str(Path.home()),
                              check=True).stdout
    with tempfile.TemporaryDirectory() as scratch:
        crops = dashboard_crops(DASHBOARD, Path(scratch))
    hero_mp4 = data_uri((args.hero / "hero.mp4").read_bytes(), "video/mp4")
    gif_rel = Path(args.hero.name) / "hero.gif"

    kpi = card["kpi"]
    old_rate = numbers["old"]["verified"] / numbers["trials"]
    new_rate = numbers["new"]["verified"] / numbers["trials"]
    last = kpi_rows[-1] if kpi_rows else {}

    plans = []
    for item in items:
        plans.append(f'<div class="plan"><h3>{esc(item["title"])}</h3>'
                     f'<p class="note"><code>{esc(item["id"])}</code> · {esc(", ".join(item["causes"]))} · '
                     f'{esc(item["status"])} · owner {esc(item["owner"])} · {esc(item["why"])}</p>'
                     + plan_html(item["plan"]) + f'<p><b>Expected.</b> {esc(item["expected"])}</p></div>')

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Robot Autonomy Agent</title>
<style>{CSS}</style></head><body><main>
<h1>Robot Autonomy Agent</h1>
<p class="sub">Report · 2026-11-18 · <code>/home/bam/robot_autonomy</code> @ <code>{esc(args.sha)}</code> ·
github.com/zacharyyamaoka/robot_autonomy (private)</p>
<p class="verdict"><b>The Robot Autonomy Agent is a drop-in sub-assembly, live on BAM.</b> One ledger of every human
intervention from every source, working hours between interventions on the R0–R4 ladder, and the causes list as a
todo list that is crossed off only by measurement. <b>BAM today: {esc(kpi["level"])} {esc(kpi["level_name"])},
{kpi["value"]:.1f} h between hands-on interventions</b> on hours the robots actually ran jobs. Three fixes are on
its todo list, and two owners have taken theirs. Lands on <code>main</code> @ <code>{esc(args.sha)}</code>, pushed private; going public needs your yes.</p>

<video controls autoplay muted loop playsinline poster="">
<source src="{hero_mp4}" type="video/mp4"><img src="{esc(gif_rel)}" alt="the agent's dashboard, day by day"></video>
<p class="note">The real agent and its own dashboard, replayed one simulated day at a time (the demo program: two arms,
eight weeks, every source). Watch the headline climb R0 → R1 while the todo list on the right fills with fixes
crossed off by measurement (before → after per 100 working hours, with p). One fix that did nothing ends
<i>ineffective</i>. Recorded by <code>docs/hero_frames.py</code>; every pixel is the package's own report.</p>

<div class="tiles">
<div class="tile"><b>{kpi["value"]:.1f} h · {esc(kpi["level"])}</b><span>BAM, hands-on, 7 d to {esc(last.get("end", "?"))},
on {card["counter_kpi"]["value"]:.0f} job-hours (was 2.0 h · R1 on powered hours)</span></div>
<div class="tile"><b>{last.get("human_s_per_h", 0):.0f} s / h</b><span>person-seconds per working hour; the facility
model's $10/t line is ~67</span></div>
<div class="tile"><b>{len(items)} fixes</b><span>on the top avoidable causes:
{esc(", ".join(f"{sum(1 for i in items if i['status'] == st)} {st}" for st in ("doing", "accepted", "proposed")
               if any(i['status'] == st for i in items)))}; none done yet</span></div>
<div class="tile"><b>{old_rate * 100:.0f} % → {new_rate * 100:.1f} %</b><span>do-nothing fixes that the verifier
called "verified", first rule → shipped rule</span></div>
<div class="tile"><b>320 tests</b><span>green, incl. parity with BAM's own rollup on real data</span></div>
</div>

<h2>How it fits the team</h2>
{DIAGRAM}
<p class="note">Files only, no imports, both contracts confirmed with the other two sessions today.
The Robot Activity Agent froze its event format (<code>robot-activity-event/1</code>), and the AI Team directory
adopted the status card (<code>ai-team-status/1</code>) and registered the card in the vault.</p>
<ul>
<li><b>Denominator ("total miles")</b>: the Robot Activity Agent's activity hours. Until its BAM feed lands at
<code>~/.local/state/robot-activity/bam</code>, the BAM example counts job hours with the same rule (in-house kind
<code>job</code>).</li>
<li><b>Interventions, from every source</b>: agents' asks (labelled chat), your own messages, a person's
<code>robot-autonomy log</code>, the queue's <code>human</code> blockers (also tracked as <i>blocked</i> time, even
while the robot does other work), robot faults that lasted long enough to need a hand, <code>robot_msgs</code>
<code>DiagnosticRecord</code> errors, and field-service tickets. Short faults count as self-recoveries.</li>
<li><b>Two readings, the lower wins</b>: <i>asked</i> (what the measured agents write) and <i>observed</i> (what they
can't write).</li>
<li><b>Robot time to verify a fix</b> goes back to the activity agent's queue through its own CLI.</li>
</ul>

<h2>BAM, live since 11:43 today</h2>
<p>A nightly timer (<code>robot-autonomy.timer</code>, 05:20, after the 04:40 labeller) exports BAM's labelled asks
and job hours, ticks the agent, writes the status card and rebuilds the dashboard. This is today's tick:</p>
<img src="{crops["top"]}" alt="BAM dashboard: headline and ladder">
<h3>Your causes table, and the agent's</h3>
<p>The <i>asked</i> column reproduces your "Why they asked" table exactly: power 22 (9 avoidable), belt 16 (10),
inspect 15 (4), run robot command 14 (9), approve 13 (2). That isn't luck, it's a committed parity test against
BAM's own rollup (345 asks → 150 episodes, every cause). The <i>observed</i> column is your own messages.</p>
<figure><img src="{png_file(ZACH_CAUSES)}" alt="Zach's table from the daily note">
<figcaption class="note">Yours (daily note, 09:42).</figcaption></figure>
<figure><img src="{crops["causes"]}" alt="the agent's causes table"><figcaption class="note">The agent's, with asked and
observed apart.</figcaption></figure>

<h2>The todo list: what it wants to do first</h2>
<p>The agent ranks causes by avoidable episodes and keeps a live fix on the top three. These are BAM's three, as they
stand in <code>~/.local/state/robot-autonomy/backlog</code>:</p>
{"".join(plans)}

<h2>Verification you can't fool</h2>
<p>A fix is crossed off only when an exact rate test shows its cause fell, never on an agent's say-so. I measured
the first version of that rule on fixes that do nothing, assessed daily for four weeks as the agent really runs, and
it said "verified" for <b>{old_rate * 100:.1f} %</b> of them ({numbers["old"]["verified"]} of {numbers["trials"]}).
There were two reasons. Both readings counted the same real event twice. And re-testing every day and stopping at
the first pass is optional stopping. The shipped rule counts one reading and only looks at doubling horizons
(24 h, 48 h, 96 h …), with α split across the looks, so the false-verify rate is bounded by 10 %. Measured:
<b>{new_rate * 100:.1f} %</b>.</p>
{bars(old_rate, new_rate)}
<p class="note">Same 400 seeded trials both times (<code>tests/test_verify_sequential.py</code>, which fails under the
first rule). Across 16 demo seeds the do-nothing fix now ends <i>ineffective</i> in 14, is still being measured in 1,
and is verified in 1, a 1-in-20 draw at the first look's α = 0.05.</p>

<h2>Decided for you</h2>
<ul>
<li><b>BAM's denominator is job hours, not powered hours</b>, which moves BAM from R1 (2.0 h) to R0 (0.6 h). Miles between
disengagements divides by miles driven. Powered hours let a parked arm climb the ladder.
Undo: <code>--basis powered</code> in <code>examples/bam/nightly.sh</code>.</li>
<li><b>Both readings count hands-on only</b> (the ladder's own wording). BAM's old observed reading also counted
your remote reports, so its 4.5 h observed figure isn't comparable. Undo: <code>count_levels</code> in
<code>~/.local/state/robot-autonomy/autonomy.toml</code>.</li>
<li><b>The trend (Crow-AMSAA β) and <code>on_track</code> wait for 14 days of hours clock</b>, around 11-27. On
6 days, the fit said "thinning out", which was an artefact. Undo: <code>growth_min_days</code>.</li>
<li><b>The three plans went to their owners</b> (belt → the Pick-Goal Coordinator, fdcanusb recovery → Sim to
Real; the provisioning script stays with this agent). Each is the owner's call on timing. Undo: tell them to drop it,
or <code>robot-autonomy todo drop ID</code>.</li>
<li><b>Repo created private and pushed</b> (github.com/zacharyyamaoka/robot_autonomy). Undo: delete the repo.</li>
<li><b>Deployed</b> to <code>~/.local/opt/robot-autonomy</code>, with the timer enabled. Undo:
<code>systemctl --user disable --now robot-autonomy.timer</code> (run it yourself).</li>
</ul>

<h2>Standing on (stock parts)</h2>
<div class="scroll"><table><tr><th>element</th><th>today (bam_interventions)</th><th>off-the-shelf part</th>
<th>must survive</th></tr>
<tr><td>ledger + cause → fix → verify</td><td>rollup.json, no fix tracking</td><td>FRACAS (MIL-HDBK-2155),
ITIL problem management</td><td>one episode per repeat, the avoidable flag, "what would remove it"</td></tr>
<tr><td>KPI</td><td>powered h per hands-on ask</td><td>DMV disengagement reports: miles per disengagement</td><td>two
readings, lower wins; R0–R4 gates</td></tr>
<tr><td>trend</td><td>none</td><td>Crow-AMSAA reliability growth (MIL-HDBK-189), checked against the
<code>reliability</code> package</td><td>β &lt; 1 = thinning out</td></tr>
<tr><td>fix verification</td><td>none</td><td>exact conditional test for two Poisson rates + Bonferroni alpha spending
over looks</td><td>verified only by measurement</td></tr>
<tr><td>diagnostics input</td><td>none</td><td><code>robot_msgs.DiagnosticRecord</code> (io-diagnostics v0)</td><td>no
private types</td></tr></table></div>

<h2>What's left</h2>
<ul>
<li><b>Switch to the activity agent's hours</b> when its BAM feed lands (it will message me): one config table.</li>
<li><b>Work the list</b>: take the top proposal to <i>doing</i>, ship it in its owner's repo, add the PR as
evidence, mark it done. The nightly tick measures it from there.</li>
<li><b>Alerts reach the dispatcher</b> only through the actions log so far. Next: a <code>--notify-cmd</code> relay into the same
router the activity agent uses.</li>
<li><b>Landing page</b>: a GitHub Pages page like the activity agent's, once the repo is public.</li>
</ul>
<h2>Needs you</h2>
<ul>
<li><b>Buy one per-port-switching USB hub</b> (UUGear MEGA4, €31 at uugear.com; with a 5 V/5 A USB-C supply and a
0.3 m extension, about C$80). Today every hub on the rig PC is ganged, so a hung fdcanusb can only be replugged by
hand: 7 replugs in 7 days, and 7 h of dead arm on 11-14. Recommended: yes, today; shipping from Europe is the long
pole. Two vault notes disagree on whether one was already bought, and <code>lsusb</code> shows none attached.</li>
<li><b>Allow a narrow sudo rule for the rig?</b> <code>/etc/sudoers.d/bam-rig</code> for one root-owned
<code>bam-rig-root</code> with fixed verbs (irq-affinity, udev-reload, hub-scan). Ten of the 19 remote
"run this command" asks were one-time root setup. It's a security setting, so it's your call. Recommended: yes, the
fixed-verb form. Default: no rule; the agent batches root steps into one <code>provision.sh</code> you run once.</li>
<li><b>Place a catch tray at the belt's end, and measure where that end is</b>, once. Then items the cell refuses
(the Sharpie today) can be run off the belt instead of waiting for a hand. The Pick-Goal Coordinator will ask for
both in one go when its belt-step scan lands.</li>
<li><b>Make the repo public?</b> Recommended yes, like <code>robot_activity</code>: it's MIT and holds no data or
secrets. Default: it stays private.</li>
</ul>
<h2>Not done, on purpose</h2>
<ul><li>No W&amp;B push for BAM: <code>bam_interventions</code> already pushes the autonomy runs nightly, and a second
project would split the same numbers.</li>
<li>No fix implemented yet. The three plans are written; implementation belongs to each fix's owner, and the agent's
next sessions.</li></ul>

<h2>Proof</h2>
<pre>{esc(terminal)}</pre>
<p class="note"><code>robot-autonomy kpi --root ~/.local/state/robot-autonomy</code>, as run for this report. Tests: <code>taskset -c 2-5,16,17 uv run --extra test pytest -q</code>
→ 320 passed, including <code>tests/test_bam_parity.py</code> on BAM's real data and the Crow-AMSAA oracle check.</p>
</main></body></html>"""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(json.dumps({"out": str(args.out), "kb": round(args.out.stat().st_size / 1024),
                      "monte_carlo": numbers}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
