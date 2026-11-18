"""Record the Robot Autonomy Agent's hero video from the real thing: the demo program replayed one day at a time,
the agent ticking each morning, and its own HTML dashboard rendered after every tick.

    .venv/bin/python docs/hero_frames.py --out DIR [--days 56] [--seed 0]

Writes DIR/day-NN.html (the agent's dashboard as of that morning), DIR/frame-NN.png (a 1600x900 view of it:
the headline, the KPI over time, and the todo list), DIR/hero.mp4 and DIR/hero.gif. Nothing is drawn by this
script: every pixel comes from `robot_autonomy.views.html.report` on the files the agent wrote that day.
Needs google-chrome (headless) and ffmpeg.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from robot_autonomy import demo
from robot_autonomy.agent import AutonomyAgent, load_config
from robot_autonomy.schema import day_start, shift_day
from robot_autonomy.views import html as html_view

START = "2026-09-01"
TZ = "UTC"

# Three panes onto the same dashboard page, each an iframe scrolled to a section (ids added to the h2s below).
FRAME = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#fafaf9;font:15px system-ui,sans-serif;color:#1c1917}}
.grid{{display:grid;grid-template-columns:800px 800px;grid-template-rows:250px 650px;width:1600px;height:900px}}
iframe{{border:0;width:100%;height:100%;display:block;background:#fafaf9}}
.top{{grid-column:1;grid-row:1}} .kpi{{grid-column:1;grid-row:2}} .todo{{grid-column:2;grid-row:1 / span 2;
border-left:1px solid #e7e5e4}}
.day{{position:absolute;right:14px;top:10px;font-weight:600;color:#57534e;background:#f5f5f4;padding:4px 10px;
border-radius:6px}}
</style></head><body><div class="grid">
<iframe class="top" src="{page}" scrolling="no"></iframe>
<iframe class="kpi" src="{page}#kpi" scrolling="no"></iframe>
<iframe class="todo" src="{page}#todo" scrolling="no"></iframe>
</div><div class="day">simulated day {day} of {days}</div></body></html>"""


def add_anchors(page: str) -> str:
    return (page.replace("<h2>The KPI over time</h2>", '<h2 id="kpi">The KPI over time</h2>')
            .replace("<h2>The todo list</h2>", '<h2 id="todo">The todo list</h2>'))


def shoot(html_path: Path, png_path: Path, profile: Path) -> None:
    subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
                    f"--user-data-dir={profile}", "--allow-file-access-from-files", "--window-size=1600,900",
                    "--virtual-time-budget=3000", f"--screenshot={png_path}", html_path.as_uri()],
                   check=True, capture_output=True, timeout=120)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--days", type=int, default=56)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = out / "program"
    if root.exists() and any(root.iterdir()):
        print(f"{root} is not empty; pick a fresh --out", file=sys.stderr)
        return 2
    root.mkdir(exist_ok=True)

    # The same loop as demo.simulate, with the dashboard rendered after every tick.
    program = demo._Program(root, seed=args.seed, start=START, robots=("arm-1", "arm-2"), tz=TZ)
    program.write_config()
    config = load_config(root / "autonomy.toml")
    agent = AutonomyAgent(root, config)
    levels = []
    for day_index in range(args.days):
        program.work_day(day_index, args.days)
        program.team(day_index)
        now = day_start(shift_day(START, day_index + 1), TZ) + 6 * 3600
        agent.tick(now)
        page = out / f"day-{day_index:02d}.html"
        html_view.report(root, config=config, now=now, out_path=page)
        page.write_text(add_anchors(page.read_text()))
        frame = out / f"frame-page-{day_index:02d}.html"
        frame.write_text(FRAME.format(page=page.name, day=day_index + 1, days=args.days))
        shoot(frame, out / f"frame-{day_index:02d}.png", out / "chrome-profile")
        levels.append(agent.kpi(now).level)
    (out / "levels.json").write_text(json.dumps(levels))

    # 4 frames a second, the last day held for 2.5 s; H.264 for the page, a GIF fallback beside it.
    last = out / f"frame-{args.days - 1:02d}.png"
    for extra in range(10):
        shutil.copyfile(last, out / f"frame-{args.days + extra:02d}.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "4", "-i", str(out / "frame-%02d.png"),
                    "-vf", "scale=1280:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "30",
                    "-preset", "slow", "-movflags", "+faststart", str(out / "hero.mp4")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "4", "-i", str(out / "frame-%02d.png"),
                    "-vf", "scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse",
                    str(out / "hero.gif")], check=True)
    print(json.dumps({"frames": args.days, "levels": [levels[0], levels[-1]],
                      "mp4_kb": round((out / "hero.mp4").stat().st_size / 1024),
                      "gif_kb": round((out / "hero.gif").stat().st_size / 1024)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
