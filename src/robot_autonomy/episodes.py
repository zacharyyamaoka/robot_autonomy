"""Merge repeated mentions into episodes.

An agent that writes "still needs you: replug" in nine wrap-ups made one ask.
"""

from __future__ import annotations

from dataclasses import dataclass

EPISODE_GAP_S = 12 * 3600   # WHY 12 h: hourly "still needs you" wrap-ups ran ~10 h on 2026-11-14 (BAM)


@dataclass(frozen=True)
class Episode:
    key: tuple                  # (thread or id, level, cause, reading)
    robot: str
    level: str
    cause: str
    reading: str
    source: str
    first_t: float
    last_t: float
    mentions: int
    avoidable: str              # the first mention's
    what: str                   # the first mention's
    fixes: tuple[str, ...]      # distinct non-empty fix strings over all mentions, first-seen order
    human_s: float | None       # sum of the mentions' known human_s; None when none known
    commissioning: bool         # the first mention's
    ids: tuple[str, ...]        # every mention's id, in time order


class _Open:
    """A mutable episode while the walk is still adding mentions to it."""

    def __init__(self, key: tuple, first):
        self.key = key
        self.first = first
        self.last_t = first.t
        self.mentions = [first]

    def add(self, mention) -> None:
        self.mentions.append(mention)
        self.last_t = mention.t

    def freeze(self) -> Episode:
        fixes: list[str] = []
        for mention in self.mentions:
            if mention.fix and mention.fix not in fixes:
                fixes.append(mention.fix)
        known_human_s = [mention.human_s for mention in self.mentions if mention.human_s is not None]
        first = self.first
        return Episode(key=self.key, robot=first.robot, level=first.level, cause=first.cause, reading=first.reading,
                       source=first.source, first_t=first.t, last_t=self.last_t, mentions=len(self.mentions),
                       avoidable=first.avoidable, what=first.what, fixes=tuple(fixes),
                       human_s=sum(known_human_s) if known_human_s else None,
                       commissioning=bool(first.commissioning),
                       ids=tuple(mention.id for mention in self.mentions))


def episodes(ivs, gap_s: float = EPISODE_GAP_S) -> list[Episode]:
    """Walk the interventions by (t, id); a mention joins the open episode with its key when it is within gap_s of
    that episode's last mention, else it opens a new one. No thread: keyed on its own id, so always its own episode.
    Output sorted by (first_t, key)."""
    open_by_key: dict[tuple, _Open] = {}
    all_open: list[_Open] = []
    for intervention in sorted(ivs, key=lambda iv: (iv.t, iv.id)):
        key = (intervention.thread or intervention.id, intervention.level, intervention.cause, intervention.reading)
        current = open_by_key.get(key)
        if current is not None and intervention.t - current.last_t <= gap_s:
            current.add(intervention)
            continue
        current = _Open(key, intervention)
        open_by_key[key] = current
        all_open.append(current)
    out = [episode.freeze() for episode in all_open]
    out.sort(key=lambda episode: (episode.first_t, episode.key))
    return out
