"""A deterministic simulated program that exercises every source, runs the agent once a day, and works the backlog.

Same seed -> byte-identical files: a seeded random.Random, fixed timestamps, sorted JSON keys, ids derived from the
seed, day and a counter; no wall clock and no uuid anywhere.

The story: two arms ramp from bring-up (3 working hours a day) to long shifts (11 h). Interventions happen at a true
rate per working hour per cause; each is seen by some of the sources (a chat ask, the operator's own log, a CAN-bus
diagnostic, a fault streak in the robot's samples, a field-service ticket, a human blocker in the work queue). The
simulated team accepts each proposal, starts it a few days later (two at a time), marks it done with evidence, and from
then on its cause mostly goes away, except the first power fix, which does nothing (it ends "ineffective").
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import math
import random
from dataclasses import dataclass
from pathlib import Path

from ._files import append_jsonl, read_jsonl
from .backlog import STATUSES, Backlog
from .schema import day_start, shift_day

TEAM = "robot-team"
PICK_AGENT = "pick-agent"
SITE = "pilot cell"
FIX_KEEPS = 0.12            # an effective fix removes 88 % of its cause from the next day on
DUD_CAUSE = "power_or_replug"
# The dud does nothing: its cause keeps 100 %. Measured over seeds 0-15, the verifier still "verifies" it for about
# one seed in twelve (seed 4): a daily exact test at alpha 0.1, where each event is counted by two readings, so the
# Poisson test is over-confident. Seeds 0-3 end it ineffective. 125 % and 150 % did not rescue seed 4 either.
DUD_KEEPS = 1.0
CAPACITY = 2                # the team works two improvements at a time


@dataclass(frozen=True)
class CauseModel:
    level: str
    cause: str
    rate: float             # true interventions per working hour per robot, before any fix
    avoidable: str
    what: str
    fix: str


CAUSES = (
    CauseModel("hands_on", "rehome_or_reposition_arm", 0.80, "yes", "lift the arm out of a fold and re-home it",
               "a homing routine that backs off a hard stop by itself"),
    CauseModel("hands_on", "power_or_replug", 0.60, "yes", "replug the CAN adapter",
               "a powered USB hub the agent can power-cycle"),
    CauseModel("hands_on", "belt_or_items", 0.40, "yes", "re-seed items on the belt",
               "a belt jog command and an item re-seeding script"),
    CauseModel("hands_on", "clear_workcell", 0.20, "yes", "pick a fallen item off the cell floor",
               "a fallen-item check before each run"),
    CauseModel("hands_on", "lights_or_cameras", 0.12, "yes", "turn the workcell lights back on",
               "lights on a relay the agent can switch"),
    CauseModel("hands_on", "inspect", 0.06, "unclear", "look whether the claw is closed", ""),
    CauseModel("hands_on", "mechanical", 0.05, "no", "tighten the wrist clamp", ""),
    CauseModel("remote", "approve_robot_run", 0.20, "yes", "give the go for a powered run",
               "standing approval for sim-verified runs"),
    CauseModel("remote", "run_robot_command", 0.12, "yes", "run the calibration command on the rig",
               "let the agent run rig commands itself"),
)
BLOCK_WHYS = ("approve the first powered run of the new grasp", "which bin does the dark-glass vial go in?",
              "confirm the new joint limit before the run", "is the belt clear to start?")

CONFIG_TOML = """# Robot Autonomy Agent: the simulated program written by `robot-autonomy demo` (seed {seed}).
# Paths are relative to this file's folder.
name = "Robot Autonomy Agent"
tz = "{tz}"
window_days = 7
min_after_h = 24.0

[hours]
kind = "jsonl"
path = "hours.jsonl"

[[source]]
kind = "labelled"
path = "sources/chat.jsonl"
source = "chat"

[[source]]
kind = "labelled"
path = "sources/operator.jsonl"
source = "operator"

[[source]]
kind = "field_service"
path = "sources/tickets.csv"

[[source]]
kind = "diagnostics"
path = "sources/diag.jsonl"
needs_human_after_s = 600.0

[[source]]
kind = "robot_activity"
root = "activity"
faults_need_human_after_s = 600.0
"""

TICKET_COLUMNS = ("id", "robot", "opened", "closed", "cause", "what", "onsite", "technician_h", "site")


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short_hash(*parts) -> str:
    return hashlib.sha1("|".join(str(part) for part in parts).encode()).hexdigest()[:10]


class _Program:
    """The simulated world: one random stream, the files it writes, and the team working the backlog."""

    def __init__(self, root: Path, *, seed: int, start: str, robots, tz: str):
        self.root = root
        self.seed = seed
        self.start = start
        self.robots = tuple(robots)
        self.tz = tz
        self.rng = random.Random(seed)
        self.backlog = Backlog(root)
        self.fixes: dict[str, list[tuple[int, float]]] = {}   # cause -> [(from day index, factor)]
        self.plans: dict[str, dict] = {}
        self.dud_id: str | None = None
        self.ticket_count = 0
        self.pr_count = 100
        self.chat_session = 0

    # ---------------------------------------------------------------------------------------------------- files

    def write_config(self) -> None:
        (self.root / "autonomy.toml").write_text(CONFIG_TOML.format(seed=self.seed, tz=self.tz), encoding="utf-8")
        tickets = self.root / "sources" / "tickets.csv"
        tickets.parent.mkdir(parents=True, exist_ok=True)
        tickets.write_text(",".join(TICKET_COLUMNS) + "\n", encoding="utf-8")
        for name in ("chat.jsonl", "operator.jsonl", "diag.jsonl"):
            (self.root / "sources" / name).touch()
        (self.root / "hours.jsonl").touch()
        (self.root / "activity" / "queue").mkdir(parents=True, exist_ok=True)
        (self.root / "activity" / "queue" / "events.jsonl").touch()

    # ------------------------------------------------------------------------------------------------- the world

    def poisson(self, mean: float) -> int:
        if mean <= 0:
            return 0
        limit, count, product = math.exp(-mean), 0, self.rng.random()
        while product > limit:
            count += 1
            product *= self.rng.random()
        return count

    def factor(self, cause: str, day_index: int) -> float:
        value = 1.0
        for from_day, step in self.fixes.get(cause, ()):
            if day_index >= from_day:
                value *= step
        return value

    def working_hours(self, day_index: int, days: int) -> float:
        ramp = 3.0 + 8.0 * day_index / max(1, days - 1)
        return round(min(12.0, max(1.0, ramp + self.rng.uniform(-0.75, 0.75))), 2)

    def work_day(self, day_index: int, days: int) -> None:
        day = shift_day(self.start, day_index)
        midnight = day_start(day, self.tz)
        rows = {"chat": [], "operator": [], "diag": [], "tickets": [], "queue": [], "hours": []}
        samples: dict[str, list[dict]] = {}
        counter = {"chat": 0, "operator": 0}
        # Nobody runs the arms on the seventh day (its asks are unclocked); the run's last day is always worked.
        weekend = day_index % 7 == 6 and day_index != days - 1
        for robot in self.robots:
            hours = 0.0 if weekend else self.working_hours(day_index, days)
            rows["hours"].append({"robot": robot, "day": day, "hours": hours})
            if hours <= 0:
                continue
            begin = midnight + 8 * 3600 + round(self.rng.uniform(0, 1800))
            end = begin + hours * 3600
            events = []
            for model in CAUSES:
                for _ in range(self.poisson(model.rate * self.factor(model.cause, day_index) * hours)):
                    events.append((round(self.rng.uniform(begin, end)), model))
            events.sort(key=lambda event: (event[0], event[1].cause))
            for t, model in events:
                self.observe(day_index, robot, t, model, rows, samples, counter, day_end=midnight + 86400)
            self.background(day_index, robot, begin, end, rows, samples,
                            leave_open=day_index == days - 1 and robot == self.robots[0])
        if weekend and self.rng.random() < 0.6:
            # A stray ask on a day nobody logged hours: unclocked, so it never enters the ratio.
            t = midnight + 11 * 3600
            counter["chat"] += 1
            self.chat_session += 1
            rows["chat"].append({"id": f"chat-{day_index:03d}-{counter['chat']:04d}", "t": t, "level": "hands_on",
                                 "category": "inspect", "robot": self.robots[0], "what": "check the arm is parked",
                                 "avoidable": "unclear", "fix": "", "session": f"s-{self.chat_session:05d}",
                                 "by": PICK_AGENT})
        self.flush(day, rows, samples)

    def observe(self, day_index, robot, t, model, rows, samples, counter, *, day_end) -> None:
        """One true intervention, seen (or not) by each source."""
        rng = self.rng
        seen_by_chat = rng.random() < (0.85 if model.level == "hands_on" else 0.9)
        if seen_by_chat:
            self.chat_session += 1
            session = f"s-{self.chat_session:05d}"
            avoidable = model.avoidable if rng.random() < 0.9 else "unclear"
            mentions = 1 + (rng.randint(1, 4) if rng.random() < 0.4 else 0)
            for mention in range(mentions):
                mention_t = t + mention * 3600
                if mention_t >= day_end - 3600:
                    break
                counter["chat"] += 1
                what = model.what if mention == 0 else f"still needs you: {model.what}"
                rows["chat"].append({"id": f"chat-{day_index:03d}-{counter['chat']:04d}", "t": mention_t,
                                     "level": model.level, "category": model.cause, "robot": robot, "what": what,
                                     "avoidable": avoidable, "fix": model.fix, "session": session,
                                     "by": PICK_AGENT})
            if rng.random() < 0.08:
                counter["chat"] += 1
                rows["chat"].append({"id": f"chat-{day_index:03d}-{counter['chat']:04d}", "t": t + 60,
                                     "level": "none", "category": "none", "robot": robot,
                                     "what": "review the run report", "session": session, "by": PICK_AGENT})
        if model.level == "hands_on" and rng.random() < 0.75:
            counter["operator"] += 1
            rows["operator"].append({"id": f"op-{day_index:03d}-{counter['operator']:04d}",
                                     "ts": _iso(t + rng.randint(60, 600)), "level": model.level,
                                     "cause": model.cause, "robot": robot, "what": model.what,
                                     "avoidable": "unclear" if rng.random() < 0.5 else model.avoidable,
                                     "human_s": round(rng.uniform(60, 900)), "by": "operator"})
        if model.cause == "power_or_replug" and rng.random() < 0.5:
            self.diag_streak(rows, f"{robot}.can", t, rng.uniform(12, 35) * 60, "CanBusOff",
                             "CAN bus off: no reply from the joint boards")
        if model.cause == "rehome_or_reposition_arm" and rng.random() < 0.35:
            self.fault_streak(samples, robot, t, rng.uniform(11, 25) * 60, "fault", "arm folded past a joint limit")
        if model.cause == "mechanical" and rng.random() < 0.7:
            self.ticket(rows, robot, t, model, onsite=True, technician_h=round(rng.uniform(0.5, 2.0), 2))
        if model.cause == "lights_or_cameras" and rng.random() < 0.2:
            self.ticket(rows, robot, t, model, onsite=True, technician_h=round(rng.uniform(0.25, 1.0), 2))
        if model.cause == "run_robot_command" and rng.random() < 0.1:
            self.ticket(rows, robot, t, model, onsite=False, technician_h=None)

    def diag_streak(self, rows, path, t, seconds, code, message) -> None:
        from robot_msgs.std import DiagnosticRecord, Header

        def record(stamp, level, record_code="", record_message=""):
            return DiagnosticRecord(header=Header(stamp=float(stamp)), path=path, level=level, code=record_code,
                                    message=record_message, mode="operational").to_dict()

        start, stop = float(t), float(round(t + seconds))
        rows["diag"].append(record(start - 60, "ok"))
        stamp = start
        while stamp < stop:
            rows["diag"].append(record(stamp, "error", code, message))
            stamp += 300.0
        rows["diag"].append(record(stop, "ok"))

    def fault_streak(self, samples, robot, t, seconds, health, reason) -> None:
        rows = samples.setdefault(robot, [])
        start = float(t)
        steps = max(1, int(seconds // 10))
        rows.append({"schema": "robot-activity/1", "t": start - 10.0, "robot": robot, "sample_s": 10.0,
                     "claimed_by": PICK_AGENT, "item": f"q-{robot}-run", "health": "ok"})
        for step in range(steps):
            rows.append({"schema": "robot-activity/1", "t": start + 10.0 * step, "robot": robot, "sample_s": 10.0,
                         "claimed_by": PICK_AGENT, "item": f"q-{robot}-run", "health": health, "reason": reason})
        rows.append({"schema": "robot-activity/1", "t": start + 10.0 * steps, "robot": robot, "sample_s": 10.0,
                     "claimed_by": PICK_AGENT, "item": f"q-{robot}-run", "health": "ok"})

    def ticket(self, rows, robot, t, model, *, onsite, technician_h) -> None:
        self.ticket_count += 1
        closed = t + round(self.rng.uniform(1, 6) * 3600)
        rows["tickets"].append({"id": f"T{self.ticket_count:04d}", "robot": robot, "opened": _iso(t),
                                "closed": _iso(closed), "cause": model.cause, "what": model.what,
                                "onsite": "yes" if onsite else "no",
                                "technician_h": "" if technician_h is None else technician_h, "site": SITE})

    def background(self, day_index, robot, begin, end, rows, samples, *, leave_open) -> None:
        """The day's queue (with human blockers) and the short faults and glitches that cleared by themselves."""
        rng = self.rng
        span = end - begin
        for k in range(3):
            item = f"q-{robot}-{day_index:03d}-{k}"
            submit_t = round(begin + span * k / 3)
            self.queue_event(rows, submit_t, "submit", item, robot, PICK_AGENT,
                             item={"id": item, "title": f"pick run {k + 1}", "owner": PICK_AGENT,
                                   "executor": PICK_AGENT, "robot": robot, "source": "agent", "priority": 5,
                                   "est_min": round(span / 3 / 60), "status": "queued", "submitted_t": submit_t})
            start_t = submit_t + 60
            hold_open = leave_open and k == 2       # still waiting on a person when the run ends
            if rng.random() < 0.3 or hold_open:
                why = BLOCK_WHYS[rng.randrange(len(BLOCK_WHYS))]
                blocker = {"kind": "human", "why": why, "eta_t": None}
                self.queue_event(rows, submit_t + 30, "block", item, robot, PICK_AGENT, blocker=blocker)
                if hold_open:
                    continue
                waited = round(rng.uniform(10, 120) * 60)
                self.queue_event(rows, submit_t + 30 + waited, "unblock", item, robot, "zach", blocker_kind="human",
                                 cleared=[blocker])
                start_t = submit_t + 60 + waited
            elif rng.random() < 0.15:
                blocker = {"kind": "code", "why": "grasp planner refuses the pose", "eta_t": None}
                self.queue_event(rows, submit_t + 30, "block", item, robot, PICK_AGENT, blocker=blocker)
                self.queue_event(rows, submit_t + 600, "unblock", item, robot, PICK_AGENT, blocker_kind=None,
                                 cleared=[blocker])
                start_t = submit_t + 660
            finish_t = max(start_t + 60, round(begin + span * (k + 1) / 3))
            self.queue_event(rows, start_t, "start", item, robot, PICK_AGENT)
            self.queue_event(rows, finish_t, "finish", item, robot, PICK_AGENT, result="ok", note=None)
        if rng.random() < 0.6:
            t = round(rng.uniform(begin, end))
            self.fault_streak(samples, robot, t, rng.uniform(30, 180), "estop", "e-stop pressed and released")
        if rng.random() < 0.7:
            t = round(rng.uniform(begin, end))
            self.diag_streak(rows, f"{robot}.wrist.encoder", t, rng.uniform(20, 120), "EncoderGlitch",
                             "wrist encoder lost its index")

    @staticmethod
    def queue_event(rows, t, kind, item_id, robot, by, **extra) -> None:
        event = {"schema": "robot-activity-event/1", "t": float(t), "kind": kind, "id": item_id, "robot": robot,
                 "by": by}
        event.update(extra)
        rows["queue"].append(event)

    def flush(self, day, rows, samples) -> None:
        sources = self.root / "sources"
        append_jsonl(self.root / "hours.jsonl", rows["hours"])
        append_jsonl(sources / "chat.jsonl", sorted(rows["chat"], key=lambda row: (row["t"], row["id"])))
        append_jsonl(sources / "operator.jsonl", sorted(rows["operator"], key=lambda row: (row["ts"], row["id"])))
        append_jsonl(sources / "diag.jsonl", sorted(rows["diag"], key=lambda row: (row["header"]["stamp"],
                                                                                    row["path"])))
        append_jsonl(self.root / "activity" / "queue" / "events.jsonl",
                     sorted(rows["queue"], key=lambda event: (event["t"], event["id"])))
        if rows["tickets"]:
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=TICKET_COLUMNS, lineterminator="\n")
            writer.writerows(rows["tickets"])
            with open(sources / "tickets.csv", "a", encoding="utf-8") as handle:
                handle.write(buffer.getvalue())
        for robot, robot_rows in samples.items():
            append_jsonl(self.root / "activity" / "samples" / robot / f"{day}.jsonl",
                         sorted(robot_rows, key=lambda row: row["t"]))

    # -------------------------------------------------------------------------------------------------- the team

    def team(self, day_index: int) -> None:
        """At 20:00 the team works the backlog: accept, start (two at a time), commit, mark done with evidence."""
        now = day_start(shift_day(self.start, day_index), self.tz) + 20 * 3600
        for item in self.backlog.items(status="proposed"):
            self.plans[item.id] = {"accepted_day": day_index, "start_delay": self.rng.randint(1, 3),
                                   "work_days": self.rng.randint(2, 4), "started_day": None}
            if self.dud_id is None and DUD_CAUSE in item.causes:
                self.dud_id = item.id
            self.backlog.move(item.id, "accepted", by=TEAM, note="worth doing", now=now)
        for item in self.backlog.items(status="doing"):
            plan = self.plans[item.id]
            if day_index == plan["started_day"] + 1:
                self.backlog.add_evidence(item.id, f"commit {_short_hash(self.seed, item.id, 'wip')}", by=TEAM,
                                          now=now)
            if day_index >= plan["started_day"] + plan["work_days"]:
                self.pr_count += 1
                self.backlog.mark_done(item.id, by=TEAM, now=now, evidence=(
                    f"PR #{self.pr_count} merged ({_short_hash(self.seed, item.id, 'merge')})",))
                keeps = DUD_KEEPS if item.id == self.dud_id else FIX_KEEPS
                for cause in item.causes:
                    self.fixes.setdefault(cause, []).append((day_index + 1, keeps))
        free = CAPACITY - len(self.backlog.items(status="doing"))
        for item in self.backlog.items(status="accepted"):
            if free <= 0:
                break
            plan = self.plans[item.id]
            if day_index >= plan["accepted_day"] + plan["start_delay"]:
                self.backlog.move(item.id, "doing", by=TEAM, now=now)
                plan["started_day"] = day_index
                free -= 1


def simulate(root, *, days: int = 56, seed: int = 0, start: str = "2026-09-01", robots=("arm-1", "arm-2"),
             tz: str = "UTC") -> dict:
    """Write the simulated program into `root` (an empty or missing folder), run the agent once a day at 06:00 in tz,
    and return {"days", "interventions", "episodes", "improvements": {status: n}, "level_start", "level_end"}."""
    from .agent import AutonomyAgent, load_config

    root = Path(root)
    if root.exists() and any(root.iterdir()):
        # SPEC: the demo refuses a folder that already has files, so it never mixes with (or clobbers) a real program.
        raise FileExistsError(f"demo needs an empty folder: {root}")
    root.mkdir(parents=True, exist_ok=True)
    program = _Program(root, seed=seed, start=start, robots=robots, tz=tz)
    program.write_config()
    agent = AutonomyAgent(root, load_config(root / "autonomy.toml"))
    for day_index in range(days):
        program.work_day(day_index, days)
        program.team(day_index)
        # The agent runs once a day at 06:00, after each simulated day (its KPI window ends yesterday).
        agent.tick(day_start(shift_day(start, day_index + 1), tz) + 6 * 3600)

    kpi_rows = read_jsonl(root / "agent" / "kpi.jsonl")
    counts = {status: 0 for status in STATUSES}
    for item in program.backlog.items():
        counts[item.status] += 1
    return {"days": days, "interventions": len(agent.ledger.interventions()), "episodes": len(agent.episodes()),
            "improvements": counts, "level_start": kpi_rows[0]["level"] if kpi_rows else None,
            "level_end": kpi_rows[-1]["level"] if kpi_rows else None}
