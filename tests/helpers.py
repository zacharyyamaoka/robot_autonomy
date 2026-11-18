"""Small builders shared by the acceptance tests.

Every import of robot_autonomy happens inside a function, so `import helpers` never fails at collection time.
Times are unix seconds; every day is computed in UTC (tests pass tz="UTC" wherever a day is computed).
"""

from __future__ import annotations

import calendar
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

UTC = "UTC"
HOUR_S = 3600.0
DAY_S = 86400.0


def t_of(day: str, hour: float = 0.0) -> float:
    """Unix seconds of `day` ("YYYY-MM-DD") at `hour` hours past midnight UTC."""
    year, month, day_of_month = (int(part) for part in day.split("-"))
    return float(calendar.timegm((year, month, day_of_month, 0, 0, 0))) + hour * HOUR_S


def day_range(first_day: str, last_day: str) -> list[str]:
    """Every day from first_day to last_day, inclusive."""
    first = date.fromisoformat(first_day)
    last = date.fromisoformat(last_day)
    return [(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]


def hours_table(robot: str, days, hours_per_day: float) -> dict[tuple[str, str], float]:
    return {(robot, day): float(hours_per_day) for day in days}


def write_jsonl(path: Path, rows) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write((row if isinstance(row, str) else json.dumps(row)) + "\n")
    return path


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def file_lines(path: Path) -> list[str]:
    path = Path(path)
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def snapshot_tree(root: Path) -> dict[str, bytes]:
    """{relative path: bytes} of every file under root."""
    root = Path(root)
    return {
        str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()
    }


def make_intervention(
    intervention_id: str,
    t: float,
    *,
    robot: str = "arm-1",
    level: str = "hands_on",
    cause: str = "power_or_replug",
    source: str = "chat",
    **fields,
):
    from robot_autonomy.schema import Intervention

    return Intervention(id=intervention_id, t=t, robot=robot, level=level, cause=cause, source=source, **fields)


def make_episode(
    episode_id: str,
    first_t: float,
    *,
    robot: str = "arm-1",
    level: str = "hands_on",
    cause: str = "power_or_replug",
    reading: str = "asked",
    source: str = "chat",
    last_t: float | None = None,
    mentions: int = 1,
    avoidable: str = "unclear",
    what: str = "",
    fixes: tuple[str, ...] = (),
    human_s: float | None = None,
    commissioning: bool = False,
    ids: tuple[str, ...] | None = None,
):
    """An Episode with every field named (the SPEC lists them all); key = (id, level, cause, reading)."""
    from robot_autonomy.episodes import Episode

    return Episode(
        key=(episode_id, level, cause, reading),
        robot=robot,
        level=level,
        cause=cause,
        reading=reading,
        source=source,
        first_t=first_t,
        last_t=first_t if last_t is None else last_t,
        mentions=mentions,
        avoidable=avoidable,
        what=what,
        fixes=tuple(fixes),
        human_s=human_s,
        commissioning=commissioning,
        ids=(episode_id,) if ids is None else tuple(ids),
    )


def make_cause(
    cause: str,
    *,
    level: str = "hands_on",
    episodes: int = 1,
    avoidable: int = 0,
    share: float = 0.0,
    fixes: tuple[str, ...] = (),
    recent: int = 0,
    last_t: float = 0.0,
    commissioning: bool = False,
):
    from robot_autonomy.metrics import Cause

    return Cause(
        level=level,
        cause=cause,
        episodes=episodes,
        avoidable=avoidable,
        share=share,
        fixes=tuple(fixes),
        recent=recent,
        last_t=last_t,
        commissioning=commissioning,
    )


class RecordingNotifier:
    """A Notifier that keeps every action it is handed."""

    def __init__(self) -> None:
        self.delivered: list = []

    def deliver(self, action) -> bool:
        self.delivered.append(action)
        return True


# The CLI is run the way its console script runs it (`sys.exit(main())`), from a fresh interpreter.
CLI_BOOT = "import sys; from robot_autonomy.cli import main; sys.exit(main())"


def run_cli(*args, cwd: Path | None = None, timeout_s: float = 300.0) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        [sys.executable, "-c", CLI_BOOT, *(str(arg) for arg in args)],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=timeout_s,
    )
