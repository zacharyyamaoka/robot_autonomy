"""The intervention ledger: append-only JSONL files a human can read.

    <root>/interventions.jsonl   one Intervention.to_dict() per line, or a retraction
                                 {"schema", "retract": <id>, "t", "by", "why"}
    <root>/blocks.jsonl          {"schema", "event": "open", "block": Block.to_dict()}
                                 or {"schema", "event": "close", "id", "until_t", "by"}
    <root>/.lock                 fcntl lock held during every write

Nothing is ever deleted or rewritten: a correction is a retraction plus a new record. The state is in the files, so a
second Ledger on the same root sees every change.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

from ._files import FileLock, append_jsonl, read_jsonl
from .schema import SCHEMA, Block, Intervention


class Ledger:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.interventions_path = self.root / "interventions.jsonl"
        self.blocks_path = self.root / "blocks.jsonl"
        self._lock = FileLock(self.root / ".lock")

    # ------------------------------------------------------------------------------------------------ interventions

    def _scan(self) -> tuple[dict[str, Intervention], set[str]]:
        """(recorded interventions by id, first record wins; retracted ids)."""
        recorded: dict[str, Intervention] = {}
        retracted: set[str] = set()
        for row in read_jsonl(self.interventions_path):
            if "retract" in row:
                retracted.add(str(row["retract"]))
                continue
            try:
                intervention = Intervention.from_dict(row)
            except (TypeError, ValueError, KeyError):
                continue
            recorded.setdefault(intervention.id, intervention)
        return recorded, retracted

    def record(self, iv: Intervention) -> bool:
        """Append one intervention. False and no write when the id is already recorded (retracted ids included)."""
        return self.record_many([iv]) == 1

    def record_many(self, ivs) -> int:
        """Append every intervention whose id is new; returns the number written."""
        with self._lock.held():
            recorded, retracted = self._scan()
            known = set(recorded) | retracted
            new_rows = []
            for intervention in ivs:
                if intervention.id in known:
                    continue
                known.add(intervention.id)
                new_rows.append(intervention.to_dict())
            if new_rows:
                append_jsonl(self.interventions_path, new_rows)
            return len(new_rows)

    def retract(self, iv_id, *, why: str, by: str | None = None, now: float | None = None) -> None:
        """Retract a recorded intervention (it stays in the file; readers skip it). KeyError when never recorded."""
        with self._lock.held():
            recorded, _ = self._scan()
            if iv_id not in recorded:
                raise KeyError(iv_id)
            row = {"schema": SCHEMA, "retract": iv_id, "t": float(time.time() if now is None else now),
                   "by": by, "why": why}
            append_jsonl(self.interventions_path, [row])

    def interventions(self, *, since=None, until=None, robot=None) -> list[Intervention]:
        """Recorded, not retracted, since <= t < until, sorted by (t, id)."""
        recorded, retracted = self._scan()
        out = []
        for intervention in recorded.values():
            if intervention.id in retracted:
                continue
            if since is not None and intervention.t < since:
                continue
            if until is not None and intervention.t >= until:
                continue
            if robot is not None and intervention.robot != robot:
                continue
            out.append(intervention)
        out.sort(key=lambda intervention: (intervention.t, intervention.id))
        return out

    # ------------------------------------------------------------------------------------------------------- blocks

    def _replay_blocks(self) -> dict[str, Block]:
        state: dict[str, Block] = {}
        for row in read_jsonl(self.blocks_path):
            event = row.get("event")
            try:
                if event == "open":
                    block = Block.from_dict(row["block"])
                    state.setdefault(block.id, block)
                elif event == "close":
                    block = state.get(str(row["id"]))
                    if block is not None and block.until_t is None:
                        state[block.id] = dataclasses.replace(block, until_t=float(row["until_t"]))
            except (TypeError, ValueError, KeyError):
                continue
        return state

    def open_block(self, block: Block, *, count: bool = True) -> bool:
        """Open a block. False and no write when the id is already open or closed.

        count=True also records the ask as an Intervention (id "block:<block id>", thread = the block id).
        """
        with self._lock.held():
            if block.id in self._replay_blocks():
                return False
            append_jsonl(self.blocks_path, [{"schema": SCHEMA, "event": "open", "block": block.to_dict()}])
            if count:
                self.record(Intervention(id=f"block:{block.id}", t=block.since_t, robot=block.robot,
                                         level=block.level, cause=block.cause, source=block.source, what=block.why,
                                         thread=block.id, by=block.by))
            return True

    def close_block(self, block_id, *, until_t: float, by: str | None = None) -> Block:
        """Close an open block. KeyError when unknown; closing a closed block returns it unchanged, no write."""
        with self._lock.held():
            state = self._replay_blocks()
            if block_id not in state:
                raise KeyError(block_id)
            block = state[block_id]
            if block.until_t is not None:
                return block
            append_jsonl(self.blocks_path, [{"schema": SCHEMA, "event": "close", "id": block_id,
                                             "until_t": float(until_t), "by": by}])
            return dataclasses.replace(block, until_t=float(until_t))

    def blocks(self, *, open_only: bool = False, robot: str | None = None) -> list[Block]:
        """The current state replayed from blocks.jsonl, sorted by (since_t, id)."""
        out = [block for block in self._replay_blocks().values()
               if (not open_only or block.until_t is None) and (robot is None or block.robot == robot)]
        out.sort(key=lambda block: (block.since_t, block.id))
        return out
