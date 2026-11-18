# robot_autonomy

**The Robot Autonomy Agent: find every reason a person had to step in, from every source, and drive each reason to
zero.**

Robotaxis count miles between disengagements against total miles. This agent counts **working hours between human
interventions** against **working hours**, for any robot program. It is a stable sub-assembly that drops into an AI
team beside its sister, the [Robot Activity Agent](https://github.com/zacharyyamaoka/robot_activity), which keeps the
robots working.

- **One ledger, many sources.** Interventions arrive from agents asking in chat, a person's own log, robot faults
  that needed a hand, diagnostics errors, field-service tickets, and queue items blocked on a human.
- **One KPI with a ladder.** Hours between interventions over 7 days, on R0 Bring-up → R1 Assisted (1 h) → R2 Shift
  (8 h) → R3 Overnight (24 h) → R4 Lights-out (168 h).
- **The causes list is the todo list.** Causes are ranked by avoidable episodes. The top three always carry a live
  fix, and every fix is verified by measuring its cause go down, never by saying so.
- **Hard to game.** Two readings, the asks the agents write and the events they can't; the level is the lower one.
  Activity hours are the denominator, so idling a robot doesn't buy autonomy.

Runtime: Python ≥ 3.11, `robot-msgs` and the standard library. Files a human can read are the state.

## Try it

```bash
uv tool install git+https://github.com/zacharyyamaoka/robot_autonomy
```

```bash
robot-autonomy demo --root /tmp/autonomy-demo
```

```bash
robot-autonomy kpi --root /tmp/autonomy-demo --config /tmp/autonomy-demo/autonomy.toml
```

```bash
robot-autonomy report --root /tmp/autonomy-demo --config /tmp/autonomy-demo/autonomy.toml --out /tmp/autonomy.html
```

The demo simulates eight weeks of a two-arm program. Every source is exercised, the simulated team works the todo
list, and the KPI climbs the ladder. One fix measures as ineffective and goes back on the list.

## Drop it into your program

1. **Count hours.** Point `[hours]` at the Robot Activity Agent's folder (`kind = "robot_activity"`), or write
   `{"robot", "day", "hours"}` lines yourself (`kind = "jsonl"`).
2. **Count interventions.** Add a `[[source]]` per stream you have: `labelled` (any labelled JSONL of asks: a chat
   labeller, a hook), `robot_activity` (its queue's human blockers, plus faults that lasted long enough to need a
   person), `diagnostics` (robot_msgs `DiagnosticRecord` JSONL), `field_service` (service tickets, CSV or JSONL).
   Agents call `robot-autonomy ask` before asking a person to touch a robot; people call `robot-autonomy log`.
3. **Run the agent.** Run `robot-autonomy agent tick --root DIR --config autonomy.toml` daily, or `agent run`. Give
   your coding agent `AGENT.md` as its role, or copy `integrations/claude_code/robot-autonomy-agent.md` into
   `.claude/agents/`.

## What a tick does

1. **Ingest** every source into `interventions.jsonl`. It is idempotent: an id is recorded once.
2. **Score** the 7-day KPI and level, once per day, into `agent/kpi.jsonl`.
3. **Verify** each finished fix with an exact rate test: its causes per 100 working hours, before vs after.
4. **Propose** a fix for each top avoidable cause that has none, from the fixes the labellers wrote.
5. **Alert** the activity agent and the dispatcher when a robot has been blocked on a person for a long time.
6. **Publish** a status card (`team/status.json`, `ai-team-status/1`) for an AI team directory.

## Standing on

| piece | prior art |
|---|---|
| the ledger, and the cause → fix → verify loop | FRACAS (MIL-HDBK-2155); ITIL problem management |
| the KPI | the California DMV disengagement reports' miles per disengagement; human-seconds per robot-hour |
| the trend | Crow-AMSAA reliability growth (MIL-HDBK-189): β < 1 means interventions are thinning out |
| the order of the todo list | a Pareto of causes, weighted to the avoidable ones |
| verification | the exact conditional test for two Poisson rates |

The contract is [`docs/SPEC.md`](docs/SPEC.md). The role is [`AGENT.md`](AGENT.md). A worked example on a real
robot, BAM's waste-sorting arm and test rack, is in [`examples/bam/`](examples/bam/).

MIT licensed.
