"""The agent's todo list: one Improvement per fix, with a FRACAS lifecycle and the evidence of the work.

    <root>/backlog/items/<id>.json   one Improvement each (current state)
    <root>/backlog/events.jsonl      append-only {"schema": "robot-autonomy-backlog-event/1", "t", "kind", "id",
                                     "by", ...}
    <root>/backlog/.lock             fcntl lock held during every write

One writer API, many readers. WHY the causes list is the backlog: progress shows as the todo list crossed off with
evidence and a measured effect; only verify.assess (through record_effect) marks a fix verified, never an assertion.
"""

from __future__ import annotations

import dataclasses
import hashlib
import time
from dataclasses import dataclass, fields
from pathlib import Path

from ._files import FileLock, append_jsonl, read_json, read_jsonl, write_json_atomic

EVENT_SCHEMA = "robot-autonomy-backlog-event/1"
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
    id: str
    title: str
    causes: tuple[str, ...]          # non-empty
    owner: str                       # who does the work
    proposed_by: str
    robot: str = "any"
    status: str = "proposed"
    why: str = ""                    # the numbers that motivated it
    plan: str = ""                   # what will be done
    expected: str = ""               # what should change, measurably
    evidence: tuple[str, ...] = ()   # commits, PRs, reports, links: the proof of the work
    proposed_t: float
    updated_t: float
    done_t: float | None = None
    verified_t: float | None = None  # set when it becomes verified or ineffective
    effect: dict | None = None       # Effect.to_dict() at verification
    note: str | None = None

    def __post_init__(self) -> None:
        causes = (self.causes,) if isinstance(self.causes, str) else tuple(self.causes)
        if not causes or not all(isinstance(cause, str) and cause for cause in causes):
            raise ValueError("Improvement.causes must be a non-empty tuple of non-empty strings")
        object.__setattr__(self, "causes", causes)
        evidence = (self.evidence,) if isinstance(self.evidence, str) else tuple(self.evidence)
        object.__setattr__(self, "evidence", evidence)
        if self.status not in STATUSES:
            raise ValueError(f"Improvement.status must be one of {STATUSES}, got {self.status!r}")
        object.__setattr__(self, "proposed_t", float(self.proposed_t))
        object.__setattr__(self, "updated_t", float(self.updated_t))
        for name in ("done_t", "verified_t"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, float(value))

    def to_dict(self) -> dict:
        out = {}
        for spec in fields(self):
            value = getattr(self, spec.name)
            out[spec.name] = list(value) if isinstance(value, tuple) else value
        return out

    @classmethod
    def from_dict(cls, d) -> "Improvement":
        names = {spec.name for spec in fields(cls)}
        return cls(**{key: value for key, value in dict(d).items() if key in names})


class BacklogError(Exception):
    """A backlog rule was broken (a transition not allowed, done without evidence, a duplicate id, ...)."""


class Backlog:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.dir = self.root / "backlog"
        self.items_dir = self.dir / "items"
        self.events_path = self.dir / "events.jsonl"
        self._lock = FileLock(self.dir / ".lock")

    # -------------------------------------------------------------------------------------------------------- files

    def _path(self, item_id: str) -> Path:
        item_id = str(item_id)
        if not item_id or "/" in item_id or item_id.startswith("."):
            raise KeyError(item_id)
        return self.items_dir / f"{item_id}.json"

    def _save(self, item: Improvement) -> None:
        write_json_atomic(self._path(item.id), item.to_dict())

    def _event(self, now: float, kind: str, item_id: str, by, **extra) -> None:
        row = {"schema": EVENT_SCHEMA, "t": now, "kind": kind, "id": item_id, "by": by}
        row.update(extra)
        append_jsonl(self.events_path, [row])

    @staticmethod
    def _now(now) -> float:
        return float(time.time() if now is None else now)

    # -------------------------------------------------------------------------------------------------------- reads

    def get(self, item_id) -> Improvement:
        """The item's current state; KeyError when unknown."""
        data = read_json(self._path(item_id))
        if not isinstance(data, dict):
            raise KeyError(item_id)
        return Improvement.from_dict(data)

    def items(self, *, status=None, cause=None) -> list[Improvement]:
        """status: a str or a tuple of str; cause: items whose causes include it; sorted by (proposed_t, id)."""
        statuses = None if status is None else ({status} if isinstance(status, str) else set(status))
        out = []
        if self.items_dir.is_dir():
            for path in self.items_dir.glob("*.json"):
                data = read_json(path)
                if not isinstance(data, dict):
                    continue
                try:
                    item = Improvement.from_dict(data)
                except (TypeError, ValueError):
                    continue
                if statuses is not None and item.status not in statuses:
                    continue
                if cause is not None and cause not in item.causes:
                    continue
                out.append(item)
        out.sort(key=lambda item: (item.proposed_t, item.id))
        return out

    def live(self, cause) -> list[Improvement]:
        """Items in OPEN or "done" whose causes include `cause`."""
        return self.items(status=OPEN + ("done",), cause=cause)

    def events(self) -> list[dict]:
        return read_jsonl(self.events_path)

    # ---------------------------------------------------------------------------------------------------- mutations

    def _default_id(self, title, causes, proposed_by, now) -> str:
        # WHY derived, not random: the same inputs give the same id, so a seeded run writes byte-identical files.
        attempt = 0
        while True:
            digest = hashlib.sha1(f"{title}|{'|'.join(causes)}|{proposed_by}|{now!r}|{attempt}".encode()).hexdigest()
            item_id = "imp-" + digest[:8]
            if not self._path(item_id).exists():
                return item_id
            attempt += 1

    def propose(self, *, title, causes, owner, proposed_by, robot="any", why="", plan="", expected="", note=None,
                now=None, id=None) -> Improvement:
        """Add an item in "proposed". BacklogError if causes is empty or the id exists."""
        now = self._now(now)
        causes = (causes,) if isinstance(causes, str) else tuple(causes)
        if not causes:
            raise BacklogError("an improvement needs at least one cause")
        with self._lock.held():
            item_id = self._default_id(title, causes, proposed_by, now) if id is None else str(id)
            if self._path(item_id).exists():
                raise BacklogError(f"improvement {item_id} already exists")
            try:
                item = Improvement(id=item_id, title=title, causes=causes, owner=owner, proposed_by=proposed_by,
                                   robot=robot, why=why, plan=plan, expected=expected, note=note, proposed_t=now,
                                   updated_t=now)
            except ValueError as error:
                raise BacklogError(str(error)) from error
            self._save(item)
            self._event(now, "propose", item.id, proposed_by, item=item.to_dict())
            return item

    def move(self, item_id, status, *, by, note=None, now=None) -> Improvement:
        """Move to `status` along TRANSITIONS (BacklogError names the allowed moves). "done" goes through mark_done."""
        if status == "done":
            return self.mark_done(item_id, by=by, now=now)     # SPEC clarification 9: exactly like mark_done
        now = self._now(now)
        with self._lock.held():
            item = self.get(item_id)
            allowed = TRANSITIONS.get(item.status, ())
            if status not in allowed:
                raise BacklogError(f"{item.id}: cannot move {item.status} -> {status}; allowed from {item.status}: "
                                   f"{', '.join(allowed) if allowed else 'none'}")
            changes = {"status": status, "updated_t": now}
            if note is not None:
                changes["note"] = note
            # SPEC clarification 9: verified_t is set only by record_effect (a measurement), never by a move.
            moved = dataclasses.replace(item, **changes)
            self._save(moved)
            self._event(now, "move", item.id, by, **{"from": item.status, "to": status, "note": note})
            return moved

    def edit(self, item_id, *, by, title=None, plan=None, expected=None, why=None, owner=None,
             now=None) -> Improvement:
        now = self._now(now)
        with self._lock.held():
            item = self.get(item_id)
            given = {"title": title, "plan": plan, "expected": expected, "why": why, "owner": owner}
            changed = {name: value for name, value in given.items()
                       if value is not None and value != getattr(item, name)}
            edited = dataclasses.replace(item, updated_t=now, **changed)
            self._save(edited)
            self._event(now, "edit", item.id, by, **changed)
            return edited

    def add_evidence(self, item_id, ref: str, *, by, now=None) -> Improvement:
        """Append one evidence ref (once: a ref already there is not added again, and nothing is written)."""
        now = self._now(now)
        with self._lock.held():
            item = self.get(item_id)
            if ref in item.evidence:
                return item
            updated = dataclasses.replace(item, evidence=item.evidence + (ref,), updated_t=now)
            self._save(updated)
            self._event(now, "evidence", item.id, by, ref=ref)
            return updated

    def mark_done(self, item_id, *, by, evidence=(), now=None) -> Improvement:
        """From "doing" only; BacklogError "done needs evidence" when the item has no evidence after adding
        `evidence`. Sets done_t."""
        now = self._now(now)
        refs = (evidence,) if isinstance(evidence, str) else tuple(evidence)
        with self._lock.held():
            item = self.get(item_id)
            if item.status != "doing":
                allowed = TRANSITIONS.get(item.status, ())
                raise BacklogError(f"{item.id}: done only from doing (it is {item.status}); allowed from "
                                   f"{item.status}: {', '.join(allowed) if allowed else 'none'}")
            all_evidence = list(item.evidence)
            for ref in refs:
                if ref not in all_evidence:
                    all_evidence.append(ref)
            if not all_evidence:
                raise BacklogError("done needs evidence")
            done = dataclasses.replace(item, status="done", evidence=tuple(all_evidence), done_t=now, updated_t=now)
            self._save(done)
            self._event(now, "done", item.id, by, evidence=list(done.evidence))
            return done

    def record_effect(self, item_id, effect: dict, verdict: str, *, by, now=None) -> Improvement:
        """From "done" only; verdict "verified" | "ineffective"; sets effect and verified_t."""
        now = self._now(now)
        if verdict not in ("verified", "ineffective"):
            raise BacklogError(f"verdict must be verified or ineffective, got {verdict!r}")
        with self._lock.held():
            item = self.get(item_id)
            if item.status != "done":
                raise BacklogError(f"{item.id}: an effect is recorded only on a done item (it is {item.status})")
            recorded = dataclasses.replace(item, status=verdict, effect=dict(effect), verified_t=now, updated_t=now)
            self._save(recorded)
            self._event(now, "effect", item.id, by, verdict=verdict, effect=dict(effect))
            return recorded
