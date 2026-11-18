# Robot Autonomy Agent — the contract (v0.1)

The acceptance tests in `tests/` are written from this file, before the implementation. Change this file first, then
the tests, then the code. Owner of the spec: Zach (2026-11-18 daily note, "Robot Autonomy agent").

## The remit, in one paragraph

A robot that needs a person is not yet autonomous. Robotaxis count *miles between disengagements* against *total
miles*; this agent counts **working hours between human interventions** against **working hours**. It is a stable
sub-assembly with one clear remit: **find every reason a person had to step in, from every source, and drive each
reason to zero.** It keeps one ledger of interventions (chat asks, the robot's own faults, diagnostics, field-service
tickets, an operator's log), turns their causes into a ranked backlog (the Pareto list *is* the todo list), proposes
fixes for the top avoidable causes, and verifies each finished fix by measuring whether its cause actually went down.
Zach sees progress two ways: the KPI rising, and a todo list being crossed off with evidence and a measured effect.

It works beside two teammates, through files only:
- the **Robot Activity Agent** (`robot_activity`) owns *working*: its activity hours are this agent's denominator,
  its queue's `human` blockers are interventions, and it runs this agent's verification jobs on the robot;
- the **AI Team directory** reads this agent's status card (`ai-team-status/1`).

Goodhart caveat (keep it on the page and in the code): an intervention count can be gamed by the agents being
measured. They can stop asking and stall, or relabel an ask. Two guards: (1) **two readings**, one the measured agents
write (`asked`) and one they cannot (`observed`: a person's own log, the robot's own faults, field tickets); the level
is the lower of the two. (2) **Activity is the counterweight**: an idle robot never needs a hand, so the KPI only
divides by hours the robot actually worked.

## Stock vocabulary this maps onto (docs + report, not code)

| piece | stock name |
|---|---|
| intervention ledger + cause → fix → verify loop | FRACAS (Failure Reporting, Analysis and Corrective Action System, MIL-HDBK-2155); ITIL problem management |
| KPI | mean time between interventions (MTBI), the DMV disengagement report's "miles per disengagement" |
| KPI trend | Crow-AMSAA reliability growth (MIL-HDBK-189): β < 1 means interventions are thinning out |
| backlog order | Pareto of causes (Juran), weighted to the avoidable ones |
| verification | exact conditional test of two Poisson rates (before vs after the fix) |
| cost per hour | human-seconds per robot-hour (vault: C - Human-Seconds per Robot-Hour) |

## Vocabulary

| word | meaning |
|---|---|
| **intervention** | one time a person had to act so a robot could carry on. `hands_on` (a person at the robot: hands or eyes on hardware) or `remote` (a person at a computer: run a command, give the go, answer a question the robot is parked on). Decisions about code, reviews and sign-ins for non-robot services are **not** interventions. |
| **cause** | why it happened: a short snake_case category (`power_or_replug`). `DEFAULT_CAUSES` is the suggested taxonomy; any non-empty string is allowed. |
| **source** | where the intervention was seen: `chat`, `agent`, `queue`, `operator`, `human`, `field_service`, `robot`, `diagnostics`, or any other string. |
| **reading** | `asked` (written by the agents or robots being measured) or `observed` (written by something they cannot write). Resolved from the source when not given. |
| **episode** | repeats merged: the same thread, level, cause and reading within 12 h is one episode. An agent that writes "still needs you: replug" in nine wrap-ups made one ask. |
| **working hours** | the denominator ("total miles"): hours each robot worked, per day. Default basis: `robot_activity` activity hours. |
| **MTBI** | working hours ÷ counted episodes in a window. With no episodes: the whole span (at least that long). With no hours: `None`. |
| **level** | the highest ladder gate the MTBI clears; with two readings, the lower of the two. |
| **block** | an interval in which work on a robot waits on a person. A robot can be blocked while busy on something else; blocked hours are tracked apart from activity. |
| **improvement** | one todo item: a fix aimed at one or more causes, with a FRACAS lifecycle and the evidence of the work. |
| **self-recovery** | a fault that cleared before a person was needed; counted as the good twin of an intervention. |

## Package layout (`src/robot_autonomy/`)

Runtime dependencies: `robot-msgs>=0.3.0` (PyPI; used for `DiagnosticRecord` in `sources/diagnostics.py`) and the
stdlib. `import robot_autonomy` must not import wandb, numpy, matplotlib or robot_msgs (only `sources.diagnostics`
imports robot_msgs). `wandb` is an extra imported only inside `robot_autonomy.wandb_log`. Never import `robot_activity`:
its data is read through its frozen files.

Times are unix seconds (float). Days are `"YYYY-MM-DD"` strings in a timezone `tz` (IANA name, `None` = system local).
Every function that maps a time to a day takes `tz`.

### `schema.py`

```python
SCHEMA = "robot-autonomy/1"
LEVELS = ("hands_on", "remote")
READINGS = ("asked", "observed")
AVOIDABLE = ("yes", "no", "unclear")

DEFAULT_CAUSES = {
    "hands_on": ("rehome_or_reposition_arm", "power_or_replug", "belt_or_items", "clear_workcell",
                 "lights_or_cameras", "mechanical", "inspect", "other_hands_on"),
    "remote": ("run_robot_command", "approve_robot_run", "answer_robot_question", "other_remote"),
}
COMMISSIONING_CAUSES = ("mechanical",)   # building new hardware is not the robot failing to run itself
SOURCE_READING = {"chat": "asked", "agent": "asked", "queue": "asked",
                  "operator": "observed", "human": "observed", "field_service": "observed",
                  "robot": "observed", "diagnostics": "observed"}   # any other source -> "asked"

def day_of(t: float, tz: str | None = None) -> str     # "YYYY-MM-DD" of t in tz

@dataclass(frozen=True, kw_only=True)
class Intervention:
    id: str                       # unique; ingest is idempotent on it
    t: float                      # when the person was asked, or acted
    robot: str
    level: str                    # one of LEVELS, else ValueError
    cause: str                    # non-empty, else ValueError
    source: str
    reading: str | None = None    # None -> SOURCE_READING.get(source, "asked"); must end in READINGS (ValueError)
    what: str = ""                # what the person was asked to do or did
    avoidable: str = "unclear"    # one of AVOIDABLE, else ValueError
    fix: str = ""                 # what would remove it, in the labeller's words
    thread: str | None = None     # chat / session / ticket id: repeats in one thread merge into one episode
    by: str | None = None         # who reported it (agent, person, detector)
    human_s: float | None = None  # person-seconds spent, when known
    downtime_s: float | None = None
    commissioning: bool | None = None   # None -> cause in COMMISSIONING_CAUSES
    evidence: str | None = None   # a link, path or short quote
    def to_dict(self) -> dict     # adds "schema": SCHEMA; reading and commissioning are stored resolved;
                                  # keys whose value is None are omitted
    @classmethod
    def from_dict(cls, d) -> "Intervention"   # ignores unknown keys ("schema" included); round-trips to_dict

@dataclass(frozen=True, kw_only=True)
class Block:
    id: str
    robot: str
    cause: str
    why: str
    since_t: float
    until_t: float | None = None  # None = still blocked
    level: str = "remote"         # one of LEVELS
    source: str = "queue"
    item: str | None = None       # the work item that waits
    by: str | None = None
    def seconds(self, now: float) -> float   # max(0, (until_t if closed else now) - since_t)
    def to_dict(self) -> dict; @classmethod from_dict(cls, d) -> "Block"   # same rules as Intervention
```

`__post_init__` resolves `reading` and `commissioning` (frozen: via `object.__setattr__`) and validates. `t`,
`since_t` and `until_t` are floats.

### `ledger.py` — files a human can read

```
<root>/interventions.jsonl   append-only: one Intervention.to_dict() per line, or a retraction
                             {"schema": SCHEMA, "retract": <id>, "t", "by", "why"}
<root>/blocks.jsonl          append-only: {"schema": SCHEMA, "event": "open", "block": Block.to_dict()}
                             or {"schema": SCHEMA, "event": "close", "id", "until_t", "by"}
<root>/.lock                 fcntl lock held during every write
```

```python
class Ledger:
    def __init__(self, root: str | Path)
    def record(self, iv: Intervention) -> bool          # False and no write when the id is already recorded
                                                         # (retracted ids included); True when written
    def record_many(self, ivs) -> int                    # number written
    def retract(self, iv_id, *, why: str, by: str | None = None, now: float | None = None) -> None
                                                         # KeyError when the id was never recorded
    def interventions(self, *, since=None, until=None, robot=None) -> list[Intervention]
        # sorted by (t, id); retracted ids excluded; since <= t < until
    def open_block(self, block: Block, *, count: bool = True) -> bool
        # False and no write when the id is already open or closed. count=True also records the ask as an
        # Intervention: id f"block:{block.id}", t=since_t, robot, level, cause, source, what=why, thread=block.id, by
    def close_block(self, block_id, *, until_t: float, by: str | None = None) -> Block
        # KeyError when unknown; closing a closed block returns it unchanged, no write
    def blocks(self, *, open_only: bool = False, robot: str | None = None) -> list[Block]
        # the current state replayed from blocks.jsonl, sorted by (since_t, id)
```

Unparsable lines are skipped. A second `Ledger` on the same root sees every change (the state is in the files).

### `episodes.py`

```python
EPISODE_GAP_S = 12 * 3600   # WHY 12 h: hourly "still needs you" wrap-ups ran ~10 h on 2026-11-14 (BAM)

@dataclass(frozen=True)
class Episode:
    key: tuple                  # (thread or id, level, cause, reading)
    robot: str; level: str; cause: str; reading: str; source: str
    first_t: float; last_t: float
    mentions: int
    avoidable: str              # the first mention's
    what: str                   # the first mention's
    fixes: tuple[str, ...]      # distinct non-empty fix strings over all mentions, first-seen order
    human_s: float | None       # sum of the mentions' known human_s; None when none known
    commissioning: bool         # the first mention's
    ids: tuple[str, ...]        # every mention's id, in time order

def episodes(ivs, gap_s: float = EPISODE_GAP_S) -> list[Episode]
```

Walk the interventions sorted by `(t, id)`. A mention joins the open episode with the same key when
`t - that episode's last_t <= gap_s`; otherwise it opens a new one (and becomes the open one for its key). An
intervention with no thread keys on its own id, so it is always its own episode. Output sorted by `(first_t, key)`.

### `hours.py` — the denominator

```python
HoursTable = dict[tuple[str, str], float]    # {(robot, day): working hours}

def from_robot_activity(root, *, basis: str = "activity", tz: str | None = None) -> HoursTable
def from_jsonl(path) -> HoursTable           # rows {"robot", "day", "hours"}; rows for the same key add up
def total(table, *, robots=None, days=None) -> float
def days_with_hours(table, *, robots=None) -> set[str]   # days whose hours (summed over robots) > 0
```

`from_robot_activity` reads `<root>/samples/<robot>/<YYYY-MM-DD>.jsonl` (schema `robot-activity/1`, frozen in
robot_activity's SPEC). Each row weighs `sample_s` seconds (default 10). A missing key reads as null. `basis`:
- `"activity"`: rows where `claimed_by` and `item` are not null and `health` (default `"ok"`) is `"ok"`;
- `"tracked"`: every row.
The day is `day_of(row["t"], tz)`, not the file name. Unparsable lines are skipped. A missing root gives `{}`.

### `metrics.py`

```python
@dataclass(frozen=True)
class Gate:
    level: str; name: str; hours: float; meaning: str

ROBOT_LADDER = (
    Gate("R0", "Bring-up", 0.0, "a person is part of every run; commissioning asks are expected"),
    Gate("R1", "Assisted", 1.0, "at least a working hour between hands-on interventions"),
    Gate("R2", "Shift", 8.0, "a working shift without hands"),
    Gate("R3", "Overnight", 24.0, "a full day and night; every recovery self-served"),
    Gate("R4", "Lights-out", 168.0, "a week without anyone touching the robot"),
)
# WHY these thresholds: each is a stretch of unattended time a person can picture (an hour, a shift, a night,
# a week), not a tuned number (Zach, 2026-11-17).

def level(ladder, hours: float | None) -> Gate        # highest gate with hours >= gate.hours; None -> ladder[0]
def held(ladder, *hours: float | None) -> Gate        # the lowest of level(h) over the readings given
def next_gate(ladder, current: Gate) -> Gate | None
def mtbi(hours: float | None, episodes: int) -> float | None
    # None when hours is falsy; hours / episodes when episodes > 0; else hours (at least that long)

@dataclass(frozen=True)
class Reading:
    name: str                   # "asked" | "observed"
    episodes: int               # counted episodes of this reading in the window
    mtbi_h: float | None

@dataclass(frozen=True)
class Kpi:
    start: str; end: str        # the window's first and last day, inclusive
    working_h: float | None     # None when the window has no hours
    counted: int                # episodes counted toward the KPI (see rule), both readings
    hands_on: int; remote: int; commissioning: int; avoidable: int; unclocked: int
    readings: tuple[Reading, ...]   # one per reading in READINGS, in that order
    mtbi_h: float | None        # the lower of the readings' mtbi_h (None if any is None)
    level: str; level_name: str
    next_level: str | None; next_gate_h: float | None
    human_s_per_h: float | None # person-seconds per working hour (see rule); None when no hours
    blocked_h: float            # hours of blocks (any robot in scope) overlapping the window

def kpi(episodes, hours: HoursTable, *, end: str, days: int = 7, ladder=ROBOT_LADDER,
        count_levels=("hands_on",), robots=None, blocks=(), now: float | None = None,
        human_s_default={"hands_on": 300.0, "remote": 30.0}, tz=None) -> Kpi
```

Rules for `kpi`:
- The window is the `days` days ending on `end`, inclusive. An episode is in the window when `day_of(first_t, tz)` is.
  `robots` (an iterable or None for all) filters both the hours and the episodes.
- `working_h` = total hours in the window; `None` when that total is 0.
- **Clocked**: an episode counts only when its day has hours (`days_with_hours`). Episodes in the window on a day with
  no hours are `unclocked` and never counted. WHY: a ratio may only divide events the hours clock also saw.
- **Counted** = in the window, clocked, `level in count_levels`, and not commissioning.
- `hands_on`, `remote`, `commissioning`, `avoidable` count every in-window, clocked episode (avoidable = `"yes"`);
  `commissioning` counts commissioning episodes of any level.
- Each reading's MTBI = `mtbi(working_h, counted episodes of that reading)`. The level = `held(ladder, *readings)`.
  `mtbi_h` = the min of the readings' values, or None if any is None.
- `human_s_per_h` = (sum over in-window clocked episodes, all levels, of `human_s`, or `human_s_default[level]`
  when unknown) / working_h.
- `blocked_h` = the part of each block (robot in scope) that falls inside the window's days (from the start of
  `start` to the end of `end` in tz), up to `now` for an open block (`now` defaults to the end of the window), in hours.

```python
@dataclass(frozen=True)
class Cause:
    level: str; cause: str
    episodes: int; avoidable: int
    share: float                # episodes / all episodes in the table
    fixes: tuple[str, ...]      # distinct fixes, most frequent first (ties: first seen), at most 8
    recent: int                 # episodes with first_t >= now - recent_days
    last_t: float
    commissioning: bool         # every episode of the cause is commissioning
    asked: int = 0              # of `episodes`, on the asked reading
    observed: int = 0           # of `episodes`, on the observed reading (shown apart: both can see one real event)

def causes(episodes, *, since=None, until=None, now=None, recent_days: float = 7) -> list[Cause]
    # grouped by (level, cause); since <= first_t < until; sorted by (-episodes, -avoidable, level, cause)

def backlog_order(rows: list[Cause]) -> list[Cause]
    # the order the agent works in: avoidable > 0 only, sorted by (-avoidable, -episodes, -recent, cause)
```

```python
@dataclass(frozen=True)
class Growth:                    # Crow-AMSAA (power-law NHPP), time-terminated MLE
    n: int; total_h: float
    beta: float; lam: float
    mtbi_now_h: float            # instantaneous: 1 / (lam * beta * total_h ** (beta - 1))
    mtbi_avg_h: float            # total_h / n
    improving: bool              # beta < 1

def growth(event_h, total_h: float) -> Growth | None
    # event_h: cumulative working hours at each event (0 < t_i <= total_h; others dropped).
    # beta = n / sum(ln(total_h / t_i)), lam = n / total_h ** beta. None when n < 2 or the sum is 0.

def cumulative_hours(episodes, hours: HoursTable, *, robots=None, tz=None) -> tuple[list[float], float]
    # each episode's cumulative working hours at first_t: the hours of every earlier day plus that day's hours
    # times the fraction of the day elapsed at first_t (in tz). Returns (per-episode list in input order, total).

def daily(episodes, hours: HoursTable, *, window_days=7, count_levels=("hands_on",), ladder=ROBOT_LADDER,
          robots=None, tz=None) -> list[dict]
    # one row per day from the first day with hours or episodes to the last: {"day", "working_h", "episodes"
    # (counted that day), "mtbi_h" and "level" of the window ending that day (kpi(... end=day, days=window_days))}
```

### `verify.py` — did the fix work?

```python
@dataclass(frozen=True)
class Rate:
    episodes: int; hours: float
    per_100h: float | None      # 100 * episodes / hours; None when hours == 0

@dataclass(frozen=True)
class Effect:
    before: Rate; after: Rate
    p_value: float | None       # None when there is nothing to test
    drop: float | None          # 1 - after.per_100h / before.per_100h; None when either rate is None or before is 0
    verdict: str                # "verified" | "ineffective" | "pending"
    def to_dict(self) -> dict

def rate_test(n_before: int, h_before: float, n_after: int, h_after: float) -> float
    # one-sided exact conditional test that the after-rate is lower: X ~ Binomial(n_before + n_after,
    # h_after / (h_before + h_after)); returns P(X <= n_after). 1.0 when n_before + n_after == 0.

def assess(causes, done_t: float, episodes, hours: HoursTable, *, now: float, robots=None, baseline_days=28,
           min_after_h=24.0, alpha=0.1, count_levels=("hands_on", "remote"), tz=None) -> Effect
```

Rules for `assess` (signature adds `reading: str | None = None`; `Effect` adds `reading`, `look`, `alpha`):
- `D = day_of(done_t)`. **Before** = the `baseline_days` days ending the day before `D`. **After** = the days after
  `D` up to the day before `day_of(now)` (complete days only), cut to the current look's prefix (below). The done day
  itself is in neither.
- Each side counts clocked episodes (day has hours) whose cause is in `causes` and level in `count_levels`, by
  `day_of(first_t)`, and the hours of its days (robots filter on both), **on one reading only**: `reading`, or when
  None the reading with the most before-window episodes (ties in `READINGS` order). WHY: the two readings often see
  the same real event twice, and counting both makes the exact test over-confident.
- **Looks** (group-sequential; WHY: the agent re-runs `assess` daily, and stopping at the first p < alpha inflates
  false "verified" far past alpha). Look `k` is the largest `k` with after-hours `>= min_after_h * 2**k`; its after
  side is the shortest prefix of after-days whose hours reach `min_after_h * 2**k`, and it spends
  `alpha / 2**(k + 1)` (the looks' alphas sum to `alpha`). Before the first horizon, `look` is None.
- Verdict, first match wins:
  1. `look is None` (after-hours `< min_after_h`) → `"pending"`;
  2. `before.episodes == 0` → `"ineffective"` if `after.episodes > 0`, else `"pending"` (no drop can be shown from 0);
  3. `p_value < alpha / 2**(look + 1)` → `"verified"`;
  4. `after.per_100h >= before.per_100h` and `before.per_100h * after.hours / 100 >= 3` → `"ineffective"`;
  5. else `"pending"`.
- `Effect.to_dict()` adds `"reading"`, `"look"` and `"alpha"` (the look's alpha).

### `backlog.py` — the agent's todo list (one writer API, many readers)

```
<root>/backlog/items/<id>.json   one Improvement each (current state)
<root>/backlog/events.jsonl      append-only {"schema": "robot-autonomy-backlog-event/1", "t", "kind", "id", "by", ...}
<root>/backlog/.lock             fcntl lock held during every write
```

```python
STATUSES = ("proposed", "accepted", "doing", "done", "verified", "ineffective", "dropped")
OPEN = ("proposed", "accepted", "doing")
TRANSITIONS = {
    "proposed": ("accepted", "doing", "dropped"),
    "accepted": ("doing", "dropped"),
    "doing": ("done", "dropped"),
    "done": ("verified", "ineffective", "doing"),
    "ineffective": ("doing", "dropped"),
    "verified": (),
    "dropped": ("proposed",),
}

@dataclass(frozen=True, kw_only=True)
class Improvement:
    id: str; title: str
    causes: tuple[str, ...]          # non-empty
    owner: str                       # who does the work
    proposed_by: str
    robot: str = "any"
    status: str = "proposed"
    why: str = ""                    # the numbers that motivated it
    plan: str = ""                   # what will be done
    expected: str = ""               # what should change, measurably
    evidence: tuple[str, ...] = ()   # commits, PRs, reports, links: the proof of the work
    proposed_t: float; updated_t: float
    done_t: float | None = None
    verified_t: float | None = None  # set when it becomes verified or ineffective
    effect: dict | None = None       # Effect.to_dict() at verification
    note: str | None = None
    def to_dict(self) -> dict; @classmethod from_dict(cls, d) -> "Improvement"

class BacklogError(Exception): ...

class Backlog:
    def __init__(self, root: str | Path)
    def propose(self, *, title, causes, owner, proposed_by, robot="any", why="", plan="", expected="",
                note=None, now=None, id=None) -> Improvement
        # id default: "imp-" + 8 hex chars; BacklogError if causes is empty or the id exists
    def get(self, item_id) -> Improvement                 # KeyError
    def items(self, *, status=None, cause=None) -> list[Improvement]
        # status: a str or a tuple of str; cause: items whose causes include it; sorted by (proposed_t, id)
    def move(self, item_id, status, *, by, note=None, now=None) -> Improvement
        # BacklogError on a transition not in TRANSITIONS (the message names the allowed ones).
        # Moving to "done" goes through mark_done's rule.
    def edit(self, item_id, *, by, title=None, plan=None, expected=None, why=None, owner=None, now=None) -> Improvement
    def add_evidence(self, item_id, ref: str, *, by, now=None) -> Improvement   # appended once (no duplicates)
    def mark_done(self, item_id, *, by, evidence=(), now=None) -> Improvement
        # from "doing" only (BacklogError otherwise); BacklogError "done needs evidence" when the item has no
        # evidence after adding `evidence`. Sets done_t.
    def record_effect(self, item_id, effect: dict, verdict: str, *, by, now=None) -> Improvement
        # from "done" only; verdict "verified" | "ineffective"; sets effect and verified_t
    def live(self, cause) -> list[Improvement]            # items in OPEN or "done" whose causes include cause
    def events(self) -> list[dict]
```

Every mutation sets `updated_t` and appends one event: `propose` (+ `item`), `move` (+ `from`, `to`, `note`), `edit`
(+ the changed fields), `evidence` (+ `ref`), `done` (+ `evidence`), `effect` (+ `verdict`, `effect`).
A second `Backlog` on the same root sees every change.

### `sources/` — the many ways an intervention is seen

Each adapter is a function returning plain records; `ingest` writes them to the ledger. Adapters never write.

```python
# sources/labelled.py — any labelled log of asks (a chat labeller's output, a hook's ledger)
def read_labelled(rows_or_path, *, source="chat", reading=None, robot=None, tz=None) -> list[Intervention]
```
Rows (dicts, or a JSONL path) with: `id`; `t` (unix) or `ts` (ISO 8601; `Z` allowed; a naive one is read in `tz`);
`level`; `cause` or `category`; `robot` (or the `robot` argument as default); optional `what`, `avoidable`, `fix`,
`thread` or `session`, `by`, `human_s`, `downtime_s`, `evidence`. Rows whose level is not in `LEVELS` (`"none"`) are
skipped. An `avoidable` outside `AVOIDABLE` reads as `"unclear"`.

```python
# sources/robot_activity.py — the Robot Activity Agent's frozen files (never imports robot_activity)
def read_blocks(root, *, label=None) -> list[Block]
def read_faults(root, *, needs_human_after_s=600.0, tz=None) -> tuple[list[Intervention], list[dict]]
```
- `read_blocks` replays `<root>/queue/events.jsonl` (schema `robot-activity-event/1`). A `block` event whose
  `blocker.kind == "human"` opens `Block(id=f"{item id}@{t}", robot=event robot, item=item id, why=blocker.why,
  since_t=t, by=event by, source="queue", level, cause)`, where `(level, cause) = label(why)` and the default label is
  `("remote", "answer_robot_question")`. The open human block of an item closes (`until_t` = event t) on an `unblock`
  whose `cleared` list contains a blocker of kind `"human"`, or on that item's `finish` or `cancel`. A second human
  `block` on an item that already has one open does not open another.
- `read_faults` reads the samples. Per robot, a **fault streak** is a maximal run of consecutive rows (t gap
  `<= 1.5 * sample_s`) with `health` not `"ok"`. Its start is the first row's `t - sample_s`, its end the last row's
  `t`. A streak at least `needs_human_after_s` long becomes `Intervention(id=f"fault:{robot}:{start:.0f}",
  t=start, robot, level="hands_on", cause=<the first row's health>, source="robot", what=<the first row's reason or
  health>, downtime_s=<length>)`; a shorter one becomes a self-recovery `{"robot", "start_t", "end_t", "seconds",
  "health"}`. A streak still open at the last row counts only once it is long enough.

```python
# sources/diagnostics.py — robot_msgs DiagnosticRecord streams (JSONL of DiagnosticRecord.to_dict())
def read_diagnostics(path, *, needs_human_after_s=600.0, robot_of=None) -> tuple[list[Intervention], list[dict]]
```
Each line is validated with `robot_msgs.std.DiagnosticRecord.from_dict` (invalid lines skipped). Per `path`, an
**error streak** is a maximal run of consecutive records with `level == "error"`; it starts at its first record's
stamp and ends at the stamp of the first later record that is not an error (or its last error record when none
follows). Same split as faults: long enough → `Intervention(id=f"diag:{path}:{start:.0f}", level="hands_on",
cause=<code>, source="diagnostics", what=<message or code>, robot=robot_of(path), downtime_s)`; shorter → a
self-recovery `{"robot", "path", "start_t", "end_t", "seconds", "code"}`. `robot_of` defaults to the first dotted
segment of the path.

```python
# sources/field_service.py — repair / service tickets from a deployed site (CSV with a header, or JSONL)
def read_tickets(path, *, tz=None) -> list[Intervention]
```
Columns: `id`, `robot`, `opened` (ISO or unix), `closed` (optional), `cause`, `what`, `onsite` (`true/false/1/0/yes/no`),
`technician_h` (optional), `site` (optional). → `Intervention(id=f"ticket:{id}", t=opened, level="hands_on" if
onsite else "remote", source="field_service", thread=f"ticket:{id}", human_s=technician_h * 3600,
downtime_s=closed - opened, evidence=site)`.

```python
# ingest.py
@dataclass(frozen=True, kw_only=True)
class SourceConfig:
    kind: str                    # "labelled" | "robot_activity" | "diagnostics" | "field_service"
    path: str | None = None      # labelled / diagnostics / field_service
    root: str | None = None      # robot_activity
    source: str | None = None    # labelled: the source name (default "chat")
    reading: str | None = None
    robot: str | None = None     # labelled: default robot
    faults_need_human_after_s: float | None = None   # robot_activity: None = do not read faults
    needs_human_after_s: float = 600.0               # diagnostics

def ingest(ledger, sources, *, tz=None) -> dict
    # runs every adapter; records interventions (idempotent), opens blocks (count=True) and closes the ones the
    # source shows closed; returns {"interventions": n_new, "blocks_opened": n, "blocks_closed": n,
    # "self_recoveries": n_seen}
```

### `agent.py` — the agent's one loop

```python
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
    growth_min_days: int = 14   # days with working hours before the growth trend (and on_track) is reported
    remind_s: float = 86400.0
    baseline_days: int = 28
    min_after_h: float = 24.0
    alpha: float = 0.1
    tz: str | None = None
    hours: dict | None = None    # {"kind": "robot_activity", "root", "basis"} or {"kind": "jsonl", "path"}
    sources: tuple[SourceConfig, ...] = ()
    status_path: str | None = None   # default <state_dir>/../team/status.json, i.e. <root>/team/status.json
    remit: str = "Find every reason a person had to step in, from every source, and drive each reason to zero."
    links: dict = field(default_factory=dict)   # absolute paths / URLs for the status card

def load_config(path) -> AgentConfig    # TOML: top-level keys as above, [hours], [[source]] tables, [links]

@dataclass(frozen=True, kw_only=True)
class Action:
    kind: str     # "propose" | "verified" | "ineffective" | "blocked" | "stale" | "level_up" | "level_down"
    to: tuple[str, ...]          # recipients, de-duplicated, order kept
    message: str
    robot: str | None = None
    improvement: str | None = None
    cause: str | None = None
    key: str = ""                # de-dup key
    def to_dict(self) -> dict

class AutonomyAgent:
    def __init__(self, root, config: AgentConfig = AgentConfig(), notifier=None, hours: HoursTable | None = None)
        # root holds the ledger (root), the backlog (root/backlog) and the agent state (root/agent);
        # hours overrides config.hours (tests and the demo pass a table)
    def tick(self, now: float) -> list[Action]
    def kpi(self, now: float) -> Kpi            # the window ending yesterday (day_of(now) - 1 day)
```

One `tick(now)`, in order:
1. **Ingest** `config.sources` (if any).
2. **KPI.** `kpi()` over the window ending the day before `day_of(now)`. If `<root>/agent/kpi.jsonl` has no row for
   that end day, append one: `{"t": now, "end", "level", "mtbi_h", "working_h", "counted", "readings": {name:
   mtbi_h}, "human_s_per_h", "blocked_h", "growth": {"beta", "mtbi_now_h"} or null}` (growth over all history). When
   the new row's level differs from the previous row's: `level_up` / `level_down` to `(dispatcher,)`.
3. **Verify.** For each improvement in `"done"`: `verify.assess(its causes, done_t, ...)`. On `"verified"` or
   `"ineffective"`: `Backlog.record_effect(...)` and an action of that kind to `(owner, dispatcher)`, naming the
   before and after rates and the p-value.
4. **Propose.** Live episodes of a cause = its episodes after the newest `done_t` among its verified improvements
   (all history when none). `rows = backlog_order(causes(live episodes))`, keeping rows with `episodes >=
   min_episodes` and not commissioning. Walk the rows in order until `top_causes` causes have a live improvement
   (`Backlog.live(cause)` non-empty counts): for a row with none, `propose(title=f"Remove {cause}: {fixes[0]}"`
   (or `f"Remove {cause}"` with no fixes), `causes=(cause,)`, `owner=config.owner`, `proposed_by=config.name`,
   `why=f"{episodes} episodes, {avoidable} avoidable, {recent} in the last 7 days"`, `expected=f"{cause} per 100
   working hours falls; verified by an exact rate test after {min_after_h:g} working hours"`, `plan` = the fixes
   joined with "; ")` and emit `propose` to `(owner, dispatcher)`.
5. **Blocks.** Each open block with `seconds(now) >= block_alert_s` → `blocked` to `(block.by or owner,
   activity_agent, dispatcher)`, naming the robot, the item, how long and why. Key `f"blocked:{block.id}"`.
6. **Stale.** Each improvement in `OPEN` with `now - updated_t >= stale_days * 86400` → `stale` to `(owner,)`.
7. **Status card**: write `team.status_card(...)` to the status path (atomic replace).

De-dup: an action whose `key` was emitted within `remind_s` is not emitted again (state in
`<root>/agent/state.json`, so a fresh agent object respects it). Keys: `propose:{improvement id}`,
`{verdict}:{improvement id}`, `blocked:{block id}`, `stale:{improvement id}:{day}`, `level:{end day}`. Every emitted
action is appended to `<root>/agent/actions.jsonl` (with `"t"`) and handed to the notifier.

The agent never moves a robot, never writes job content, never deletes or rewrites an intervention (a correction is a
retraction plus a new record), and never marks an improvement verified by assertion: only `verify.assess` does.

### `team.py` — the AI Team directory's status card

```python
TEAM_SCHEMA = "ai-team-status/1"

def status_card(kpi: Kpi, backlog: Backlog, *, config: AgentConfig, now: float, growth: Growth | None = None,
                claim: dict | None = None) -> dict
```

```json
{"schema": "ai-team-status/1", "member": "<config.name>", "team": "Robot Team", "remit": "<config.remit>",
 "kpi": {"name": "working hours between hands-on interventions (7 d)", "value": <kpi.mtbi_h>, "unit": "h",
         "direction": "up", "target": <kpi.next_gate_h>, "level": "<kpi.level>", "level_name": "...",
         "next_gate": "<kpi.next_level>", "on_track": <growth is not None and growth.improving>},
 "counter_kpi": {"name": "working hours (7 d)", "value": <kpi.working_h>, "unit": "h",
                 "owner": "<config.activity_agent>"},
 "status": "working" | "waiting_on_zach" | "blocked" | "idle",
 "blocked_on": null | "<text>",
 "todo": {"open": <items in OPEN>, "done_7d": <items with done_t in the last 7 days>,
          "verified": <items verified>, "top": [<titles of up to 3 OPEN items, doing first, then by proposed_t>]},
 "links": <config.links>, "updated_t": <now>}
```

`status` and `blocked_on` come from `claim` (`{"status", "blocked_on"}`, the chat's own claim, kept in
`<root>/agent/claim.json`) when given; otherwise `"working"` when an item is in `"doing"`, else `"idle"`.
`blocked_on` is required (ValueError) when status is `waiting_on_zach` or `blocked`. The kpi fields come from code
reading the ledger, never from the claim.

### `notify.py`

Same shape as robot_activity's, so one relay serves both agents:

```python
class Notifier(Protocol):
    def deliver(self, action: Action) -> bool
class Outbox:            # default: append the action JSON as one line to a file
    def __init__(self, path)
class CommandNotifier:   # run argv with the action JSON on stdin; True iff exit status 0
    def __init__(self, argv: list[str], timeout_s: float = 30.0)
```

### `activity_link.py` — asking the Robot Activity Agent for robot time

```python
def submit_verification(improvement: Improvement, *, root, est_min: float, policy_repo: str, policy_commit: str,
                        policy_path: str, robot: str = "any", runner=subprocess.run) -> dict
```
Runs `robot-activity queue submit --root ROOT --title "Verify: <title>" --owner <improvement.owner> --est-min M
--source agent --robot R --policy-repo U --policy-commit C --policy-path P --json` (robot_activity's rule: submit
through its CLI, never by writing its files) and returns the parsed JSON; raises `RuntimeError` with stderr on a
non-zero exit. `runner` is injectable for tests.

### `views/`

- `terminal.render(kpi, cause_rows, backlog, blocks, *, now) -> str`: one screen, plain text, no ANSI: the level line
  (`R1 Assisted · 1.8 h between hands-on interventions (7 d) · next R2 at 8 h`), the two readings, the causes
  (episodes, avoidable, the live improvement id or `-`), the todo list grouped `doing`, `proposed/accepted`,
  `done (verifying)`, `verified` (marked `[x]`), `ineffective`, then the open blocks with how long.
- `html.report(root, *, config, now, out_path, hours=None) -> Path`: one self-contained HTML file (no network): the
  ladder with a "← now" marker on the held level; the KPI over time (inline SVG from `daily`); reliability growth
  (log–log cumulative episodes vs cumulative working hours, with the Crow-AMSAA line); "Why they asked" (the causes
  table: kind, cause, episodes, avoidable, what would remove it, live improvement); the todo list with verified items
  crossed off and their measured effect; open blocks; interventions by source and reading; recent interventions.

### `wandb_log.py` (extra `[wandb]`)

```python
def payload(root, *, config, now, hours=None) -> dict
    # pure, no wandb import: {"run_id": f"autonomy-{end}", "summary": {kpi fields}, "steps": daily(...),
    #  "tables": {"causes": [...], "backlog": [...], "blocks": [...]}}
def push(root, *, config, now, project, entity=None, hours=None) -> dict   # idempotent per run id
```

### `demo.py`

```python
def simulate(root, *, days: int = 56, seed: int = 0, start: str = "2026-09-01",
             robots=("arm-1", "arm-2"), tz: str = "UTC") -> dict
```

A deterministic simulated program (same seed → byte-identical files) that exercises every source: working hours per
robot per day (`<root>/hours.jsonl`), labelled chat asks (`<root>/sources/chat.jsonl`) with repeats in threads,
an operator log, field-service tickets (`<root>/sources/tickets.csv`), diagnostics (`<root>/sources/diag.jsonl`,
robot_msgs `DiagnosticRecord` dicts, with both long errors and self-recoveries), and a robot_activity-shaped folder
(`<root>/activity/queue/events.jsonl` with human blockers, `robot-activity-event/1`). It writes
`<root>/autonomy.toml` naming all of them, then runs the agent once a day at 06:00 in `tz`. Along the way the
simulated team works the backlog: each proposal is started a few days later, marked done with evidence, and
removes most of its cause from then on, except one fix that does nothing (it ends `ineffective`). The KPI climbs at
least one rung over the run. Returns `{"days", "interventions", "episodes", "improvements": {status: n},
"level_start", "level_end"}`.

### `cli.py` — entry point `robot-autonomy`

```
robot-autonomy demo --root DIR [--days N] [--seed N]
robot-autonomy ask --root DIR --robot R --level L --cause C --what W [--fix F] [--avoidable A] [--thread T] [--by B] [--json]
robot-autonomy log --root DIR --robot R --level L --cause C --what W [--human-min M] [--by B] [--json]
robot-autonomy retract --root DIR ID --why W [--by B]
robot-autonomy block open --root DIR --robot R --cause C --why W [--level L] [--item I] [--by B]
robot-autonomy block close --root DIR ID [--by B]
robot-autonomy ingest --root DIR --config FILE [--json]
robot-autonomy kpi --root DIR [--config FILE] [--json]
robot-autonomy causes --root DIR [--json]
robot-autonomy todo [list] --root DIR [--json]
robot-autonomy todo propose --root DIR --title T --cause C [--cause C2] [--owner O] [--plan P] [--expected E] [--by B]
robot-autonomy todo accept|start|drop ID --root DIR [--by B] [--note N]
robot-autonomy todo edit ID --root DIR [--title T] [--plan P] [--expected E] [--why W] [--owner O] [--by B]
robot-autonomy todo evidence ID REF --root DIR [--by B]
robot-autonomy todo done ID --root DIR [--evidence REF ...] [--by B]
robot-autonomy agent tick --root DIR [--config FILE] [--notify-cmd "..."] [--json]
robot-autonomy agent run --root DIR [--config FILE] [--notify-cmd "..."] [--every MIN]
robot-autonomy status --root DIR [--config FILE] [--claim working|waiting_on_zach|blocked|idle] [--blocked-on T] [--json]
robot-autonomy report --root DIR [--config FILE] --out FILE.html
robot-autonomy wandb push --root DIR [--config FILE] --project P [--entity E]
```

`ask` writes `source="agent"` (reading `asked`); `log` writes `source="operator"` (reading `observed`). `--json`
output is one JSON document on stdout. Exit 0 on success, 2 on a usage, ledger or backlog error (message on stderr).
`status --claim` writes `<root>/agent/claim.json` and rewrites the card.

## Clarifications (v0.1, from the blind test lane)

These close readings the first draft left open. They bind the tests and the code alike.

1. An episode's `robot`, `source`, `what`, `avoidable` and `commissioning` are its first mention's.
2. `Cause.last_t` is the latest `last_t` among its episodes. `causes(now=None)` anchors "recent" on the latest
   `last_t` in the table (an empty table: no rows).
3. Clocked means the episode's day has hours summed over the robots in scope (`robots` filter applied). The check is
   per day, not per robot: a fleet clock.
4. `daily` reports `working_h = 0.0` for a day with no hours.
5. `ingest` counts: `interventions` = every new intervention line written this run, block asks included;
   `blocks_closed` = close events written this run (a block already closed on an earlier run is not counted again).
6. A fault or error streak still open at the last row that is shorter than the threshold is neither an intervention
   nor a self-recovery (it is not over yet).
7. `sources.diagnostics`: "path" is the record's dotted component `path` field, not the file.
8. `read_labelled`: the Intervention id is the row's `id` unchanged.
9. Backlog: a `move` to `"done"` behaves exactly like `mark_done` and logs a `done` event whose `evidence` is the item's
   whole evidence tuple; a duplicate `add_evidence` writes nothing; `record_effect` from a status other than `"done"`
   or with a verdict other than `"verified"`/`"ineffective"` raises `BacklogError`; `verified_t` is set only by
   `record_effect`.
10. `Effect.to_dict()` = `{"before": {"episodes", "hours", "per_100h"}, "after": {...}, "p_value", "drop", "verdict"}`.
11. `team` `done_7d` counts every item whose `done_t` is in the last 7 days, whatever its status now.
12. Actions carry `improvement` on propose / verified / ineffective / stale, `cause` on propose, `robot` on blocked.

## Drop-in for a coding agent

- `AGENT.md`: the remit as a role prompt any agent harness can load.
- `integrations/claude_code/robot-autonomy-agent.md`: a Claude Code subagent definition (copy into `.claude/agents/`).

## Worked example on a real robot

`examples/bam/` maps one lab's existing in-house KPI (`bam_interventions`: labelled chat asks and the person's own
reports, plus 10 s activity rows) into this package. The parity test (`tests/test_bam_parity.py`, marker `archive`,
skipped when the data is absent) checks this package's episodes, per-cause counts and avoidable counts against that
lab's own rollup, frozen in `tests/data/`.
