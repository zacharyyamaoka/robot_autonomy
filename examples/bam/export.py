"""BAM's in-house autonomy data → robot_autonomy's sources (a worked example on a real robot).

    python examples/bam/export.py --out ~/.local/state/robot-autonomy [--backfill DIR] [--activity DIR] [--through DAY]

BAM (Break and Make Robotics) already tracked human interventions before this package existed, in
`bam_ws/src/dev/bam_interventions`: every closing message of a robot chat and every message Zach typed is labelled
by a model (`hands_on` / `remote` / `none`, a category, avoidable, the fix), and a 10 s sampler logs whether each robot
is powered. This script maps those files onto the three things robot_autonomy reads, without importing BAM code:

- `<out>/sources/bam_asks.jsonl`: the agents' asks (reading `asked`, thread = the chat session, so repeats merge)
- `<out>/sources/bam_reports.jsonl`: Zach's own "I did X on the rig" messages (reading `observed`, each its own episode)
- `<out>/hours.jsonl`: working hours per robot per day, from the sampler rows (`--basis`)

WHY activity hours (rows whose in-house kind is "job") by default, not powered hours: the KPI is miles between
disengagements over total miles, and a robot parked in arm-hold for 20 h is not driving. Powered hours let an idle,
powered robot climb the ladder without doing anything, which is exactly the gaming activity is there to stop. "job"
is the same rule the Robot Activity Agent's BAM adapter uses (its examples/bam_rows.py), so the numbers carry over
when its `robot-activity/1` feed lands at ~/.local/state/robot-activity/bam; then switch the config's [hours] to
`kind = "robot_activity"`. `--basis powered` reproduces BAM's earlier published baseline (R1, 1.8 h on 2026-11-16).
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import sys
from pathlib import Path

BACKFILL = Path.home() / ".local" / "share" / "bam_interventions" / "backfill"
ACTIVITY = Path("/archive/datasets/bam_rig/activity")
LEVELS = ("hands_on", "remote")
UNPOWERED = {"disconnected", "no host"}  # the sampler's states for a robot with no power or no host process

# Which labelled pass each closing went through; every one of them is a real labelled closing.
ASK_FILES = (  # (rows file, label prefix, row filter)
    ("candidates.jsonl", "handback", lambda row: row.get("kind") == "handback"),
    ("unscreened_sample.jsonl", "unscreened", lambda row: True),
    ("broad.jsonl", "broad", lambda row: True),
    ("nightly_handbacks.jsonl", "nightly", lambda row: True),
)


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _labels(directory: Path, prefix: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(directory.glob(f"{prefix}_*.jsonl")):
        for row in _load(path):
            out[row["id"]] = row
    return out


def _local_day(ts: str) -> str:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()


def ask_rows(backfill: Path = BACKFILL, *, through: str | None = None) -> list[dict]:
    """Every labelled agent closing that asked Zach for a hand (hands_on or remote), as read_labelled rows."""
    rows, seen = [], set()
    for file_name, prefix, keep in ASK_FILES:
        labels = _labels(backfill / "labels", prefix)
        for row in _load(backfill / file_name):
            label = labels.get(row.get("id"))
            if not keep(row) or not label or label.get("level") not in LEVELS or not row.get("ts"):
                continue
            if through and _local_day(row["ts"]) > through:
                continue
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            quote = row.get("screen") or row.get("text") or ""
            rows.append({
                "id": f"bam:{row['id']}", "ts": row["ts"], "level": label["level"],
                "category": label.get("category") or "none", "robot": label.get("robot") or "none",
                "what": label.get("what", ""), "avoidable": label.get("avoidable", "unclear"),
                "fix": label.get("fix", ""), "session": row.get("session"),
                "by": f"{row.get('account', '?')}/{row.get('project', '?')}", "evidence": str(quote)[:240],
            })
    return rows


def report_rows(backfill: Path = BACKFILL, *, through: str | None = None) -> list[dict]:
    """Zach's own typed messages labelled as a hand on the rig. WHY each its own episode (thread = its id): that is
    how BAM's rollup counts them, and two "I replugged it" messages are usually two replugs."""
    humans = {row["id"]: row for row in _load(backfill / "humans.jsonl")}
    humans.update({row["id"]: row for row in _load(backfill / "nightly_humans.jsonl")})
    for row in _load(backfill / "candidates.jsonl"):
        if row.get("kind") == "human":
            humans.setdefault(row["id"], row)
    labels = {**_labels(backfill / "labels", "human"), **_labels(backfill / "labels", "nightlyhuman")}
    rows = []
    for ident, label in labels.items():
        row = humans.get(ident)
        if row is None or label.get("level") not in LEVELS or not row.get("ts"):
            continue
        if through and _local_day(row["ts"]) > through:
            continue
        rows.append({
            "id": f"bam-report:{ident}", "ts": row["ts"], "level": label["level"],
            "category": label.get("category") or "none", "robot": label.get("robot") or "none",
            "what": label.get("what", ""), "thread": f"bam-report:{ident}", "by": "zach",
            "evidence": (row.get("text") or "")[:240],
        })
    return sorted(rows, key=lambda r: (r["ts"], r["id"]))


def hours_rows(activity: Path = ACTIVITY, *, through: str | None = None, basis: str = "activity") -> list[dict]:
    """Working hours per robot per local day: `activity` = rows whose kind is "job", `powered` = rows not in an
    unpowered state. WHY de-duplicate by 10 s slot: the backfill and the live sampler overlap on some days, and a slot
    must count once (same rule as bam_interventions.series.robot_hours)."""
    if basis not in ("activity", "powered"):
        raise ValueError(f"basis must be 'activity' or 'powered', got {basis!r}")
    out = []
    for robot_dir in sorted(p for p in activity.glob("*") if p.is_dir() and p.name != "timeline"):
        # WHY keep (t, working, sample_s) and not the row: BAM's rows are ~1 KB each and 13 days of them held whole
        # peaked at 1.4 GB, over the nightly unit's 1 GB cap (2026-11-25). Three numbers per row give the same hours.
        rows: list[tuple[float, bool, float]] = []
        for path in sorted(glob.glob(str(robot_dir / "*.jsonl"))):
            with open(path) as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if "t" in row:
                        working = (row.get("kind") == "job" if basis == "activity"
                                   else (row.get("state") or "") not in UNPOWERED)
                        rows.append((row["t"], working, row.get("sample_s", 10.0)))
        seen: set[int] = set()
        days: dict[str, float] = collections.defaultdict(float)
        for t, working, sample_s in sorted(rows, key=lambda r: r[0]):
            slot = round(t / 10)
            if slot in seen:
                continue
            seen.add(slot)
            day = dt.datetime.fromtimestamp(t).date().isoformat()
            if through and day > through:
                continue
            if working:
                days[day] += sample_s / 3600
        out += [{"robot": robot_dir.name, "day": day, "hours": round(hours, 4)} for day, hours in sorted(days.items())]
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--backfill", type=Path, default=BACKFILL)
    parser.add_argument("--activity", type=Path, default=ACTIVITY)
    parser.add_argument("--through", help="last local day to export (default: everything)")
    parser.add_argument("--basis", choices=("activity", "powered"), default="activity")
    args = parser.parse_args(argv)
    asks = ask_rows(args.backfill, through=args.through)
    reports = report_rows(args.backfill, through=args.through)
    hours = hours_rows(args.activity, through=args.through, basis=args.basis)
    write_jsonl(args.out / "sources" / "bam_asks.jsonl", asks)
    write_jsonl(args.out / "sources" / "bam_reports.jsonl", reports)
    write_jsonl(args.out / "hours.jsonl", hours)
    print(json.dumps({"asks": len(asks), "reports": len(reports), "basis": args.basis, "hour_rows": len(hours),
                      "hours": round(sum(r["hours"] for r in hours), 2)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
