"""The Robot Autonomy Agent's one loop: ingest, KPI, verify, propose, blocks, stale, status card.

The agent never moves a robot, never writes job content, never deletes or rewrites an intervention (a correction is a
retraction plus a new record), and never marks an improvement verified by assertion: only verify.assess does.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import _format
from ._files import append_jsonl, read_json, read_jsonl, write_json_atomic
from .backlog import OPEN, Backlog
from .episodes import Episode, episodes as merge_episodes
from .hours import days_with_hours, from_jsonl, from_robot_activity
from .ingest import SourceConfig, ingest
from .ledger import Ledger
from .metrics import ROBOT_LADDER, Growth, Kpi, _rank, backlog_order, causes, cumulative_hours, growth, kpi
from .notify import Outbox
from .schema import day_of, shift_day
from .team import status_card
from .verify import assess

ACTION_KINDS = ("propose", "verified", "ineffective", "blocked", "stale", "level_up", "level_down")


@dataclass(frozen=True, kw_only=True)
class AgentConfig:
    name: str = "Robot Autonomy Agent"
    owner: str = "Robot Autonomy Agent"      # default owner of the improvements it proposes
    dispatcher: str = "dispatcher"
    activity_agent: str = "Robot Activity Agent"
    count_levels: tuple[str, ...] = ("hands_on",)
    window_days: int = 7
    top_causes: int = 3          # keep a live improvement on the top N avoidable causes
    min_episodes: int = 3        # a cause needs this many live episodes before it gets a proposal
    block_alert_s: float = 1800.0
    stale_days: float = 7.0
    remind_s: float = 86400.0
    baseline_days: int = 28
    min_after_h: float = 24.0
    alpha: float = 0.1
    tz: str | None = None
    hours: dict | None = None    # {"kind": "robot_activity", "root", "basis"} or {"kind": "jsonl", "path"}
    sources: tuple[SourceConfig, ...] = ()
    status_path: str | None = None   # default <root>/team/status.json
    remit: str = "Find every reason a person had to step in, from every source, and drive each reason to zero."
    links: dict = field(default_factory=dict)   # absolute paths / URLs for the status card

    def __post_init__(self) -> None:
        object.__setattr__(self, "count_levels", tuple(self.count_levels))
        object.__setattr__(self, "sources", tuple(self.sources))


def _resolve(base: Path, value):
    if value is None:
        return None
    path = Path(str(value)).expanduser()
    return str(path if path.is_absolute() else base / path)


def load_config(path) -> AgentConfig:
    """TOML: top-level keys as AgentConfig, a [hours] table, [[source]] tables and [links].

    SPEC: relative paths (hours.path / hours.root, source path / root, status_path) are read relative to the config
    file's folder, so a program folder can be moved or shared as a whole.
    """
    config_path = Path(path)
    with open(config_path, "rb") as handle:
        data = tomllib.load(handle)
    base = config_path.resolve().parent
    known = {spec.name for spec in fields(AgentConfig)}
    values: dict = {}
    for key, value in data.items():
        if key == "source":
            continue
        if key not in known:
            raise ValueError(f"{config_path}: unknown key {key!r} (known: {', '.join(sorted(known))})")
        values[key] = value
    if "hours" in values:
        hours = dict(values["hours"])
        for key in ("path", "root"):
            if key in hours:
                hours[key] = _resolve(base, hours[key])
        values["hours"] = hours
    sources = []
    for table in data.get("source", []):
        table = dict(table)
        for key in ("path", "root"):
            if key in table:
                table[key] = _resolve(base, table[key])
        try:
            sources.append(SourceConfig(**table))
        except TypeError as error:
            raise ValueError(f"{config_path}: bad [[source]] table {table}: {error}") from error
    values["sources"] = tuple(sources)
    if "status_path" in values:
        values["status_path"] = _resolve(base, values["status_path"])
    if "count_levels" in values:
        values["count_levels"] = tuple(values["count_levels"])
    return AgentConfig(**values)


@dataclass(frozen=True, kw_only=True)
class Action:
    kind: str     # "propose" | "verified" | "ineffective" | "blocked" | "stale" | "level_up" | "level_down"
    to: tuple[str, ...]          # recipients, de-duplicated, order kept
    message: str
    robot: str | None = None
    improvement: str | None = None
    cause: str | None = None
    key: str = ""                # de-dup key

    def __post_init__(self) -> None:
        recipients = (self.to,) if isinstance(self.to, str) else tuple(self.to)
        object.__setattr__(self, "to", tuple(dict.fromkeys(recipient for recipient in recipients if recipient)))

    def to_dict(self) -> dict:
        out = asdict(self)
        out["to"] = list(self.to)
        return out


def load_hours(config: AgentConfig) -> dict:
    """The hours table named by config.hours ({} when none)."""
    spec = config.hours
    if not spec:
        return {}
    kind = spec.get("kind")
    if kind == "robot_activity":
        return from_robot_activity(spec["root"], basis=spec.get("basis", "activity"), tz=config.tz)
    if kind == "jsonl":
        return from_jsonl(spec["path"])
    raise ValueError(f"hours.kind must be robot_activity or jsonl, got {kind!r}")


class AutonomyAgent:
    """root holds the ledger (root), the backlog (root/backlog) and the agent state (root/agent)."""

    def __init__(self, root, config: AgentConfig = AgentConfig(), notifier=None, hours: dict | None = None):
        self.root = Path(root)
        self.config = config
        self.ledger = Ledger(self.root)
        self.backlog = Backlog(self.root)
        self.state_dir = self.root / "agent"
        # SPEC: no notifier given -> the default Outbox at <root>/agent/outbox.jsonl (actions.jsonl is the record).
        self.notifier = notifier if notifier is not None else Outbox(self.state_dir / "outbox.jsonl")
        self._hours_override = hours
        self.status_path = Path(config.status_path) if config.status_path else self.root / "team" / "status.json"

    # -------------------------------------------------------------------------------------------------------- reads

    def hours(self) -> dict:
        """The working-hours table: the one passed in, else the one config.hours names."""
        if self._hours_override is not None:
            return self._hours_override
        return load_hours(self.config)

    def episodes(self) -> list[Episode]:
        return merge_episodes(self.ledger.interventions())

    def window_end(self, now: float) -> str:
        """The KPI window ends yesterday: the last complete day before day_of(now)."""
        return shift_day(day_of(now, self.config.tz), -1)

    def _kpi(self, episodes, hours, blocks, now: float) -> Kpi:
        return kpi(episodes, hours, end=self.window_end(now), days=self.config.window_days,
                   count_levels=self.config.count_levels, blocks=blocks, now=now, tz=self.config.tz)

    def kpi(self, now: float) -> Kpi:
        """The KPI over the window ending the day before day_of(now)."""
        return self._kpi(self.episodes(), self.hours(), self.ledger.blocks(), float(now))

    def counted_episodes(self, episodes, hours) -> list[Episode]:
        """Episodes the KPI counts, over all history: clocked, a counted level, not commissioning."""
        clocked = days_with_hours(hours)
        return [episode for episode in episodes
                if episode.level in self.config.count_levels and not episode.commissioning
                and day_of(episode.first_t, self.config.tz) in clocked]

    def growth(self, episodes=None, hours=None) -> Growth | None:
        """Crow-AMSAA over all history of counted episodes vs cumulative working hours."""
        episodes = self.episodes() if episodes is None else episodes
        hours = self.hours() if hours is None else hours
        event_h, total_h = cumulative_hours(self.counted_episodes(episodes, hours), hours, tz=self.config.tz)
        return growth(event_h, total_h)

    def claim(self) -> dict | None:
        data = read_json(self.state_dir / "claim.json")
        return data if isinstance(data, dict) else None

    def status_card(self, now: float, *, claim: dict | None = None, kpi_value: Kpi | None = None,
                    growth_value: Growth | None = None, compute_growth: bool = True) -> dict:
        kpi_value = self.kpi(now) if kpi_value is None else kpi_value
        if growth_value is None and compute_growth:
            growth_value = self.growth()
        return status_card(kpi_value, self.backlog, config=self.config, now=float(now), growth=growth_value,
                           claim=self.claim() if claim is None else claim)

    def write_status(self, card: dict) -> Path:
        write_json_atomic(self.status_path, card)
        return self.status_path

    def live_episodes(self, episodes) -> list[Episode]:
        """Per cause, its episodes after the newest done_t among its verified improvements (all history if none)."""
        cut: dict[str, float] = {}
        for item in self.backlog.items(status="verified"):
            if item.done_t is None:
                continue
            for cause in item.causes:
                cut[cause] = max(cut.get(cause, -math.inf), item.done_t)
        return [episode for episode in episodes if episode.first_t > cut.get(episode.cause, -math.inf)]

    # ------------------------------------------------------------------------------------------------------ actions

    def _load_state(self) -> dict:
        state = read_json(self.state_dir / "state.json", default={})
        if not isinstance(state, dict):
            state = {}
        state.setdefault("emitted", {})
        return state

    def _emit(self, action: Action, now: float, state: dict, out: list[Action]) -> None:
        last = state["emitted"].get(action.key)
        if last is not None and now - float(last) < self.config.remind_s:
            return
        state["emitted"][action.key] = now
        write_json_atomic(self.state_dir / "state.json", state)
        append_jsonl(self.state_dir / "actions.jsonl", [{**action.to_dict(), "t": now}])
        self.notifier.deliver(action)
        out.append(action)

    # --------------------------------------------------------------------------------------------------------- tick

    def tick(self, now: float) -> list[Action]:
        now = float(now)
        config = self.config
        tz = config.tz
        state = self._load_state()
        actions: list[Action] = []

        # 1. Ingest every configured source.
        if config.sources:
            ingest(self.ledger, config.sources, tz=tz)
        hours = self.hours()
        all_episodes = self.episodes()
        blocks = self.ledger.blocks()

        # 2. KPI: one row per window end day in <root>/agent/kpi.jsonl; a level change goes to the dispatcher.
        current = self._kpi(all_episodes, hours, blocks, now)
        current_growth = self.growth(all_episodes, hours)
        kpi_path = self.state_dir / "kpi.jsonl"
        kpi_rows = read_jsonl(kpi_path)
        if not any(row.get("end") == current.end for row in kpi_rows):
            row = {"t": now, "end": current.end, "level": current.level, "mtbi_h": current.mtbi_h,
                   "working_h": current.working_h, "counted": current.counted,
                   "readings": {reading.name: reading.mtbi_h for reading in current.readings},
                   "human_s_per_h": current.human_s_per_h, "blocked_h": current.blocked_h,
                   "growth": None if current_growth is None else {"beta": current_growth.beta,
                                                                  "mtbi_now_h": current_growth.mtbi_now_h}}
            append_jsonl(kpi_path, [row])
            previous = kpi_rows[-1] if kpi_rows else None
            if previous is not None and previous.get("level") != current.level:
                self._emit(self._level_action(previous, current), now, state, actions)

        # 3. Verify every done improvement by measurement (verify.assess), never by assertion.
        for item in self.backlog.items(status="done"):
            if item.done_t is None:
                continue
            robots = None if item.robot == "any" else (item.robot,)
            effect = assess(item.causes, item.done_t, all_episodes, hours, now=now, robots=robots,
                            baseline_days=config.baseline_days, min_after_h=config.min_after_h, alpha=config.alpha,
                            tz=tz)
            if effect.verdict not in ("verified", "ineffective"):
                continue
            self.backlog.record_effect(item.id, effect.to_dict(), effect.verdict, by=config.name, now=now)
            causes_text = ", ".join(item.causes)
            message = (f"{item.id} {effect.verdict}: {causes_text} went from {_format.rate(effect.before.per_100h)} "
                       f"to {_format.rate(effect.after.per_100h)} per 100 working hours "
                       f"({effect.before.episodes} in {_format.hours(effect.before.hours)} before, "
                       f"{effect.after.episodes} in {_format.hours(effect.after.hours)} after; "
                       f"p = {_format.p_value(effect.p_value)}). {item.title}")
            self._emit(Action(kind=effect.verdict, to=(item.owner, config.dispatcher), message=message,
                              robot=None if item.robot == "any" else item.robot, improvement=item.id,
                              cause=item.causes[0], key=f"{effect.verdict}:{item.id}"), now, state, actions)

        # 4. Propose: keep a live improvement on the top avoidable causes (the Pareto list is the todo list).
        rows = backlog_order(causes(self.live_episodes(all_episodes), now=now))
        rows = [row for row in rows if row.episodes >= config.min_episodes and not row.commissioning]
        covered: set[str] = set()
        for row in rows:
            if len(covered) >= config.top_causes:
                break
            if row.cause in covered:
                continue
            covered.add(row.cause)
            if self.backlog.live(row.cause):
                continue
            title = f"Remove {row.cause}: {row.fixes[0]}" if row.fixes else f"Remove {row.cause}"
            item = self.backlog.propose(
                title=title, causes=(row.cause,), owner=config.owner, proposed_by=config.name,
                why=f"{row.episodes} episodes, {row.avoidable} avoidable, {row.recent} in the last 7 days",
                plan="; ".join(row.fixes),
                expected=(f"{row.cause} per 100 working hours falls; verified by an exact rate test after "
                          f"{config.min_after_h:g} working hours"),
                now=now)
            self._emit(Action(kind="propose", to=(config.owner, config.dispatcher),
                              message=f"Proposed {item.id}: {item.title} ({item.why})", improvement=item.id,
                              cause=row.cause, key=f"propose:{item.id}"), now, state, actions)

        # 5. Blocks: work waiting on a person for too long goes to whoever can unblock it, and to the activity agent.
        for block in self.ledger.blocks(open_only=True):
            waited_s = block.seconds(now)
            if waited_s < config.block_alert_s:
                continue
            message = (f"{block.robot}: {block.item or 'work'} has waited {_format.duration(waited_s)} on a person "
                       f"({block.level}, {block.cause}): {block.why}")
            self._emit(Action(kind="blocked", to=(block.by or config.owner, config.activity_agent, config.dispatcher),
                              message=message, robot=block.robot, cause=block.cause, key=f"blocked:{block.id}"),
                       now, state, actions)

        # 6. Stale: an open improvement nobody touched for stale_days.
        today = day_of(now, tz)
        for item in self.backlog.items(status=OPEN):
            idle_s = now - item.updated_t
            if idle_s < config.stale_days * 86400.0:
                continue
            message = f"{item.id} has not moved in {_format.duration(idle_s)} (still {item.status}): {item.title}"
            self._emit(Action(kind="stale", to=(item.owner,), message=message,
                              robot=None if item.robot == "any" else item.robot, improvement=item.id,
                              cause=item.causes[0], key=f"stale:{item.id}:{today}"), now, state, actions)

        # 7. Status card for the AI Team directory (atomic replace).
        self.write_status(self.status_card(now, kpi_value=current, growth_value=current_growth,
                                           compute_growth=False))
        return actions

    def _level_action(self, previous: dict, current: Kpi) -> Action:
        ladder = ROBOT_LADDER
        by_level = {gate.level: gate for gate in ladder}
        before = by_level.get(previous.get("level"))
        after = by_level.get(current.level)
        rising = before is not None and after is not None and _rank(ladder, after) > _rank(ladder, before)
        kind = "level_up" if rising else "level_down"
        message = (f"Autonomy level {'up' if rising else 'down'}: {previous.get('level')} -> {current.level} "
                   f"{current.level_name}; {_format.hours(current.mtbi_h)} between "
                   f"{' or '.join(level.replace('_', '-') for level in self.config.count_levels)} interventions over "
                   f"the {self.config.window_days} d ending {current.end}")
        return Action(kind=kind, to=(self.config.dispatcher,), message=message, key=f"level:{current.end}")
