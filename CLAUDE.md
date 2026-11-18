# robot_autonomy — working notes for agents

The Robot Autonomy Agent: find every reason a person had to step in, from every source, and drive each reason to
zero. The contract is `docs/SPEC.md`; read it before changing anything. Change the spec first, then the tests, then
the code.

## Rules that are Zach's (keep the `WHY:` comments that carry them)

- Every time a human needs to intervene, it is bad (2026-11-17). Count every human, log the kind of help and why.
- Two readings, the lower one wins: what the measured agents write (`asked`) and what they cannot (`observed`).
- Activity is the counterweight: the KPI divides by hours the robot actually worked (the Robot Activity Agent's).
- The causes list is the backlog. Each fix is verified by measurement (`verify.assess`), never by assertion.
- Progress is visible two ways: the KPI rising, and the todo list crossed off with evidence and a measured effect.
- SI and unix seconds inside; human-readable units only at display.

## Layering

- Runtime deps: robot-msgs (PyPI) + stdlib. wandb only in `wandb_log.py`; robot_msgs only in `sources/diagnostics.py`.
- Never import robot_activity, bam_interventions or any lab's private code. Read the Robot Activity Agent through its
  frozen files (`robot-activity/1` samples, `robot-activity-event/1` events) and write to it only through its CLI.
- Files a human can read are the state: append-only JSONL ledgers, one JSON per todo item.

## Tests

```bash
cd ~/robot_autonomy && taskset -c 2-5,16,17 uv run --extra test pytest -q
```

- The acceptance tests were committed before the implementation. Don't edit a green test except to add.
- `archive` tests read one lab's real rows and skip cleanly without them; `oracle` tests need `--extra oracle`.

## Machine

- At most 2 worker processes, pinned `taskset -c 2-5,16,17`; never CPUs 22-23. Never touch /dev/ttyACM*, CAN, cameras.
- Never commit data, recordings, private paths or secrets: this repo is meant to go public.
- Reports: `/home/bam/robot_autonomy/reports/<name>-<date>.html`.
