"""`robot-autonomy`: the command line over the ledger, the backlog and the agent.

Exit 0 on success, 2 on a usage, ledger or backlog error (message on stderr). `--json` prints one JSON document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .backlog import Backlog, BacklogError
from .schema import AVOIDABLE, LEVELS, Block, Intervention, parse_time

CONFIG_NAME = "autonomy.toml"


class UsageError(Exception):
    """A command line that cannot be carried out (exit 2)."""


# ----------------------------------------------------------------------------------------------------------- helpers

def _now(args) -> float:
    value = getattr(args, "now", None)
    return time.time() if value is None else parse_time(value, None)


def _config(args):
    from .agent import AgentConfig, load_config

    path = getattr(args, "config", None)
    if path is None:
        # SPEC: with no --config, <root>/autonomy.toml is used when it exists (the demo writes one).
        default = Path(args.root) / CONFIG_NAME
        path = default if default.exists() else None
    if path is None:
        return AgentConfig()
    if not Path(path).exists():
        raise UsageError(f"config file not found: {path}")
    return load_config(path)


def _print_json(document) -> None:
    print(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))


def _derived_id(prefix: str, *parts) -> str:
    digest = hashlib.sha1("|".join(repr(part) for part in parts).encode()).hexdigest()
    return f"{prefix}-{digest[:12]}"


def _agent(args, notifier=None):
    from .agent import AutonomyAgent

    return AutonomyAgent(args.root, _config(args), notifier=notifier)


# ---------------------------------------------------------------------------------------------------------- commands

def cmd_demo(args) -> int:
    from .demo import simulate

    summary = simulate(args.root, days=args.days, seed=args.seed, start=args.start)
    _print_json(summary)
    return 0


def _record(args, *, source: str, human_s=None, fix="", avoidable="unclear", thread=None) -> int:
    from .ledger import Ledger

    now = _now(args)
    prefix = "ask" if source == "agent" else "log"
    intervention = Intervention(
        id=_derived_id(prefix, args.robot, args.level, args.cause, args.what, args.by, now), t=now, robot=args.robot,
        level=args.level, cause=args.cause, source=source, what=args.what, fix=fix, avoidable=avoidable,
        thread=thread, by=args.by, human_s=human_s)
    written = Ledger(args.root).record(intervention)
    if args.json:
        _print_json({"recorded": written, "intervention": intervention.to_dict()})
    else:
        print(f"{'recorded' if written else 'already recorded'} {intervention.id}: {intervention.level} "
              f"{intervention.cause} on {intervention.robot} ({intervention.source}, {intervention.reading})")
    return 0


def cmd_ask(args) -> int:
    return _record(args, source="agent", fix=args.fix or "", avoidable=args.avoidable, thread=args.thread)


def cmd_log(args) -> int:
    human_s = None if args.human_min is None else args.human_min * 60.0
    return _record(args, source="operator", human_s=human_s)


def cmd_retract(args) -> int:
    from .ledger import Ledger

    Ledger(args.root).retract(args.id, why=args.why, by=args.by, now=_now(args))
    print(f"retracted {args.id}")
    return 0


def cmd_block_open(args) -> int:
    from .ledger import Ledger

    now = _now(args)
    block = Block(id=_derived_id("blk", args.robot, args.cause, args.why, args.item, now), robot=args.robot,
                  cause=args.cause, why=args.why, since_t=now, level=args.level, source="queue", item=args.item,
                  by=args.by)
    opened = Ledger(args.root).open_block(block, count=True)
    if args.json:
        _print_json({"opened": opened, "block": block.to_dict()})
    else:
        print(f"{'opened' if opened else 'already open'} {block.id}")
    return 0


def cmd_block_close(args) -> int:
    from .ledger import Ledger

    block = Ledger(args.root).close_block(args.id, until_t=_now(args), by=args.by)
    if args.json:
        _print_json(block.to_dict())
    else:
        print(f"closed {block.id}")
    return 0


def cmd_ingest(args) -> int:
    from .agent import load_config
    from .ingest import ingest
    from .ledger import Ledger

    if not Path(args.config).exists():
        raise UsageError(f"config file not found: {args.config}")
    config = load_config(args.config)
    counts = ingest(Ledger(args.root), config.sources, tz=config.tz)
    if args.json:
        _print_json(counts)
    else:
        print(" · ".join(f"{key} {value}" for key, value in counts.items()))
    return 0


def _kpi_dict(kpi) -> dict:
    return asdict(kpi)


def cmd_kpi(args) -> int:
    from .metrics import causes
    from .views.terminal import render

    agent = _agent(args)
    now = _now(args)
    kpi = agent.kpi(now)
    if args.json:
        _print_json(_kpi_dict(kpi))
    else:
        print(render(kpi, causes(agent.episodes(), now=now), agent.backlog, agent.ledger.blocks(), now=now), end="")
    return 0


def cmd_causes(args) -> int:
    from .episodes import episodes
    from .ledger import Ledger
    from .metrics import causes

    # With no --now, "recent" is anchored on the latest episode (SPEC clarification 2), not on the clock.
    rows = causes(episodes(Ledger(args.root).interventions()), now=None if args.now is None else _now(args))
    if args.json:
        _print_json([asdict(row) for row in rows])
        return 0
    if not rows:
        print("no interventions")
        return 0
    width = max(len(row.cause) for row in rows)
    print(f"{'kind':<8}  {'cause':<{width}}  {'episodes':>8}  {'asked':>5}  {'observed':>8}  {'avoidable':>9}  "
          f"{'recent':>6}  what would remove it")
    for row in rows:
        print(f"{row.level:<8}  {row.cause:<{width}}  {row.episodes:>8}  {row.asked:>5}  {row.observed:>8}  "
              f"{row.avoidable:>9}  {row.recent:>6}  {'; '.join(row.fixes[:2])}")
    return 0


def cmd_todo_list(args) -> int:
    if args.root is None:
        raise UsageError("todo: --root is required")
    backlog = Backlog(args.root)
    items = backlog.items()
    if args.json:
        _print_json([item.to_dict() for item in items])
        return 0
    if not items:
        print("todo list is empty")
        return 0
    marks = {"verified": "[x]", "ineffective": "[!]", "done": "[~]", "dropped": "[-]"}
    for item in items:
        print(f"{marks.get(item.status, '[ ]')} {item.id}  {item.status:<11}  {item.title}  "
              f"({', '.join(item.causes)}; {item.owner})")
    return 0


def _print_item(args, item) -> None:
    if args.json:
        _print_json(item.to_dict())
    else:
        print(f"{item.id} {item.status}: {item.title}")


def cmd_todo_propose(args) -> int:
    from .agent import AgentConfig

    item = Backlog(args.root).propose(title=args.title, causes=tuple(args.cause or ()),
                                      owner=args.owner or AgentConfig().owner, proposed_by=args.by or "cli",
                                      plan=args.plan or "", expected=args.expected or "", now=_now(args))
    _print_item(args, item)
    return 0


TODO_MOVES = {"accept": "accepted", "start": "doing", "drop": "dropped"}


def cmd_todo_move(args) -> int:
    item = Backlog(args.root).move(args.id, TODO_MOVES[args.todo_command], by=args.by or "cli", note=args.note,
                                   now=_now(args))
    _print_item(args, item)
    return 0


def cmd_todo_edit(args) -> int:
    item = Backlog(args.root).edit(args.id, by=args.by or "cli", title=args.title, plan=args.plan,
                                   expected=args.expected, why=args.why, owner=args.owner, now=_now(args))
    _print_item(args, item)
    return 0


def cmd_todo_evidence(args) -> int:
    item = Backlog(args.root).add_evidence(args.id, args.ref, by=args.by or "cli", now=_now(args))
    _print_item(args, item)
    return 0


def cmd_todo_done(args) -> int:
    item = Backlog(args.root).mark_done(args.id, by=args.by or "cli", evidence=tuple(args.evidence or ()),
                                        now=_now(args))
    _print_item(args, item)
    return 0


def _notifier(args):
    from .notify import CommandNotifier

    command = getattr(args, "notify_cmd", None)
    return CommandNotifier(shlex.split(command)) if command else None


def cmd_agent_tick(args) -> int:
    agent = _agent(args, notifier=_notifier(args))
    actions = agent.tick(_now(args))
    if args.json:
        _print_json([action.to_dict() for action in actions])
    else:
        for action in actions:
            print(f"{action.kind} -> {', '.join(action.to)}: {action.message}")
        if not actions:
            print("no actions")
    return 0


def cmd_agent_run(args) -> int:
    agent = _agent(args, notifier=_notifier(args))
    every_s = max(1.0, float(args.every) * 60.0)
    try:
        while True:
            for action in agent.tick(time.time()):
                print(f"{action.kind} -> {', '.join(action.to)}: {action.message}", flush=True)
            time.sleep(every_s)
    except KeyboardInterrupt:
        return 0


def cmd_status(args) -> int:
    from ._files import write_json_atomic

    agent = _agent(args)
    now = _now(args)
    claim = None
    if args.claim is not None:
        claim = {"status": args.claim, "blocked_on": args.blocked_on}
    elif args.blocked_on is not None:
        raise UsageError("--blocked-on goes with --claim")
    card = agent.status_card(now, claim=claim)      # validates the claim before anything is written
    if claim is not None:
        write_json_atomic(agent.state_dir / "claim.json", claim)
    agent.write_status(card)
    if args.json:
        _print_json(card)
    else:
        kpi = card["kpi"]
        value = "—" if kpi["value"] is None else f"{kpi['value']:.1f} h"
        print(f"{card['member']}: {card['status']}" + (f" (on: {card['blocked_on']})" if card["blocked_on"] else ""))
        print(f"{kpi['level']} {kpi['level_name']} · {value} · next {kpi['next_gate'] or '-'} · todo "
              f"{card['todo']['open']} open, {card['todo']['verified']} verified")
        print(f"card: {agent.status_path}")
    return 0


def cmd_report(args) -> int:
    from .views.html import report

    path = report(args.root, config=_config(args), now=_now(args), out_path=args.out)
    print(path)
    return 0


def cmd_wandb_push(args) -> int:
    from .wandb_log import push

    try:
        result = push(args.root, config=_config(args), now=_now(args), project=args.project, entity=args.entity)
    except ImportError as error:
        raise UsageError(f"wandb is not installed (pip install 'robot-autonomy[wandb]'): {error}") from error
    _print_json(result)
    return 0


# ------------------------------------------------------------------------------------------------------------ parser

def _common(parser, *, json_flag=False, config=False, now=True, root_required=True) -> None:
    parser.add_argument("--root", required=root_required,
                        help="the program's autonomy folder (ledger, backlog, agent state)")
    if config:
        parser.add_argument("--config", help=f"agent TOML (default: <root>/{CONFIG_NAME} when it exists)")
    if json_flag:
        parser.add_argument("--json", action="store_true", help="print one JSON document")
    if now:
        parser.add_argument("--now", help="act as of this time (unix seconds or ISO 8601); default: the clock")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="robot-autonomy", description="Robot Autonomy Agent: every reason a "
                                     "person had to step in, from every source, driven to zero.")
    commands = parser.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="write a deterministic simulated program and run the agent over it")
    demo.add_argument("--root", required=True)
    demo.add_argument("--days", type=int, default=56)
    demo.add_argument("--seed", type=int, default=0)
    demo.add_argument("--start", default="2026-09-01")
    demo.set_defaults(handler=cmd_demo)

    for name, handler, help_text in (("ask", cmd_ask, "record an ask from an agent (reading: asked)"),
                                     ("log", cmd_log, "record what a person did (reading: observed)")):
        sub = commands.add_parser(name, help=help_text)
        _common(sub, json_flag=True)
        sub.add_argument("--robot", required=True)
        sub.add_argument("--level", required=True, choices=LEVELS)
        sub.add_argument("--cause", required=True)
        sub.add_argument("--what", required=True)
        sub.add_argument("--by")
        if name == "ask":
            sub.add_argument("--fix")
            sub.add_argument("--avoidable", default="unclear", choices=AVOIDABLE)
            sub.add_argument("--thread")
        else:
            sub.add_argument("--human-min", type=float)
        sub.set_defaults(handler=handler)

    retract = commands.add_parser("retract", help="retract a recorded intervention (it stays in the file)")
    _common(retract)
    retract.add_argument("id")
    retract.add_argument("--why", required=True)
    retract.add_argument("--by")
    retract.set_defaults(handler=cmd_retract)

    block = commands.add_parser("block", help="open or close a block (work waiting on a person)")
    block_commands = block.add_subparsers(dest="block_command", required=True)
    block_open = block_commands.add_parser("open")
    _common(block_open, json_flag=True)
    block_open.add_argument("--robot", required=True)
    block_open.add_argument("--cause", required=True)
    block_open.add_argument("--why", required=True)
    block_open.add_argument("--level", default="remote", choices=LEVELS)
    block_open.add_argument("--item")
    block_open.add_argument("--by")
    block_open.set_defaults(handler=cmd_block_open)
    block_close = block_commands.add_parser("close")
    _common(block_close, json_flag=True)
    block_close.add_argument("id")
    block_close.add_argument("--by")
    block_close.set_defaults(handler=cmd_block_close)

    ingest = commands.add_parser("ingest", help="run every source in the config into the ledger")
    _common(ingest, json_flag=True, now=False)
    ingest.add_argument("--config", required=True)
    ingest.set_defaults(handler=cmd_ingest)

    kpi = commands.add_parser("kpi", help="the KPI over the window ending yesterday")
    _common(kpi, json_flag=True, config=True)
    kpi.set_defaults(handler=cmd_kpi)

    causes = commands.add_parser("causes", help="the causes table (all history)")
    _common(causes, json_flag=True)
    causes.set_defaults(handler=cmd_causes)

    todo = commands.add_parser("todo", help="the todo list (the backlog of improvements)")
    _common(todo, json_flag=True, root_required=False)   # `todo --root DIR` lists; subcommands take their own
    todo.set_defaults(handler=cmd_todo_list)
    todo_commands = todo.add_subparsers(dest="todo_command")
    todo_list = todo_commands.add_parser("list")
    _common(todo_list, json_flag=True)
    todo_list.set_defaults(handler=cmd_todo_list)
    propose = todo_commands.add_parser("propose")
    _common(propose, json_flag=True)
    propose.add_argument("--title", required=True)
    propose.add_argument("--cause", action="append", required=True)
    propose.add_argument("--owner")
    propose.add_argument("--plan")
    propose.add_argument("--expected")
    propose.add_argument("--by")
    propose.set_defaults(handler=cmd_todo_propose)
    for name in TODO_MOVES:
        move = todo_commands.add_parser(name)
        move.add_argument("id")
        _common(move, json_flag=True)
        move.add_argument("--by")
        move.add_argument("--note")
        move.set_defaults(handler=cmd_todo_move)
    edit = todo_commands.add_parser("edit")
    edit.add_argument("id")
    _common(edit, json_flag=True)
    for field_name in ("title", "plan", "expected", "why", "owner", "by"):
        edit.add_argument(f"--{field_name}")
    edit.set_defaults(handler=cmd_todo_edit)
    evidence = todo_commands.add_parser("evidence")
    evidence.add_argument("id")
    evidence.add_argument("ref")
    _common(evidence, json_flag=True)
    evidence.add_argument("--by")
    evidence.set_defaults(handler=cmd_todo_evidence)
    done = todo_commands.add_parser("done")
    done.add_argument("id")
    _common(done, json_flag=True)
    done.add_argument("--evidence", nargs="+", action="extend")
    done.add_argument("--by")
    done.set_defaults(handler=cmd_todo_done)

    agent = commands.add_parser("agent", help="run the agent's loop once (tick) or forever (run)")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    tick = agent_commands.add_parser("tick")
    _common(tick, json_flag=True, config=True)
    tick.add_argument("--notify-cmd", help="a command that gets each action's JSON on stdin")
    tick.set_defaults(handler=cmd_agent_tick)
    run = agent_commands.add_parser("run")
    _common(run, config=True, now=False)
    run.add_argument("--notify-cmd")
    run.add_argument("--every", type=float, default=60.0, help="minutes between ticks (default 60)")
    run.set_defaults(handler=cmd_agent_run)

    status = commands.add_parser("status", help="write and show the AI Team status card")
    _common(status, json_flag=True, config=True)
    status.add_argument("--claim", choices=("working", "waiting_on_zach", "blocked", "idle"))
    status.add_argument("--blocked-on")
    status.set_defaults(handler=cmd_status)

    report = commands.add_parser("report", help="write the self-contained HTML report")
    _common(report, config=True)
    report.add_argument("--out", required=True)
    report.set_defaults(handler=cmd_report)

    wandb = commands.add_parser("wandb", help="copy the KPI to Weights & Biases (extra [wandb])")
    wandb_commands = wandb.add_subparsers(dest="wandb_command", required=True)
    push = wandb_commands.add_parser("push")
    _common(push, config=True)
    push.add_argument("--project", required=True)
    push.add_argument("--entity")
    push.set_defaults(handler=cmd_wandb_push)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_request:     # argparse: usage errors exit 2, --help exits 0
        code = exit_request.code
        return code if isinstance(code, int) else (0 if code is None else 2)
    try:
        return int(args.handler(args) or 0)
    except (UsageError, BacklogError, KeyError, ValueError, OSError) as error:
        message = f"unknown id: {error.args[0]}" if isinstance(error, KeyError) and error.args else str(error)
        print(f"robot-autonomy: error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
