"""Run every source adapter and write what it saw to the ledger (idempotent: ids already recorded are skipped)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

SOURCE_KINDS = ("labelled", "robot_activity", "diagnostics", "field_service")


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

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise ValueError(f"SourceConfig.kind must be one of {SOURCE_KINDS}, got {self.kind!r}")


def _with_reading(interventions, reading):
    if reading is None:
        return list(interventions)
    return [dataclasses.replace(intervention, reading=reading) for intervention in interventions]


def ingest(ledger, sources, *, tz=None) -> dict:
    """Run every adapter; record interventions (idempotent), open blocks (count=True) and close the ones the source
    shows closed. Returns {"interventions", "blocks_opened", "blocks_closed", "self_recoveries"}."""
    counts = {"interventions": 0, "blocks_opened": 0, "blocks_closed": 0, "self_recoveries": 0}
    for config in sources:
        if config.kind == "robot_activity" and not config.root:
            raise ValueError("a robot_activity source needs root")
        if config.kind != "robot_activity" and not config.path:
            raise ValueError(f"a {config.kind} source needs path")
        if config.kind == "labelled":
            from .sources.labelled import read_labelled

            found = read_labelled(config.path, source=config.source or "chat", reading=config.reading,
                                  robot=config.robot, tz=tz)
            counts["interventions"] += ledger.record_many(found)
        elif config.kind == "robot_activity":
            from .sources.robot_activity import read_blocks, read_faults

            open_now = {block.id for block in ledger.blocks(open_only=True)}
            for block in read_blocks(config.root):
                if ledger.open_block(dataclasses.replace(block, until_t=None), count=True):
                    counts["blocks_opened"] += 1
                    # SPEC: "interventions" counts every new ledger intervention, the block's own ask included.
                    counts["interventions"] += 1
                    open_now.add(block.id)
                if block.until_t is not None and block.id in open_now:
                    ledger.close_block(block.id, until_t=block.until_t)
                    open_now.discard(block.id)
                    counts["blocks_closed"] += 1
            if config.faults_need_human_after_s is not None:
                faults, recoveries = read_faults(config.root, needs_human_after_s=config.faults_need_human_after_s,
                                                 tz=tz)
                counts["interventions"] += ledger.record_many(_with_reading(faults, config.reading))
                counts["self_recoveries"] += len(recoveries)
        elif config.kind == "diagnostics":
            from .sources.diagnostics import read_diagnostics

            found, recoveries = read_diagnostics(config.path, needs_human_after_s=config.needs_human_after_s)
            counts["interventions"] += ledger.record_many(_with_reading(found, config.reading))
            counts["self_recoveries"] += len(recoveries)
        elif config.kind == "field_service":
            from .sources.field_service import read_tickets

            found = read_tickets(config.path, tz=tz)
            counts["interventions"] += ledger.record_many(_with_reading(found, config.reading))
        else:   # unreachable: SourceConfig validates kind
            raise ValueError(f"unknown source kind {config.kind!r}")
    return counts
