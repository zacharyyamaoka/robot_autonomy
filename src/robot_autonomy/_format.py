"""Display formatting: SI and unix seconds inside, human-readable units only here, at the edge."""

from __future__ import annotations

import datetime as dt

from .schema import _zone


def hours(value: float | None, *, none: str = "—") -> str:
    """1.83 -> "1.8 h"; 12.4 -> "12 h"; None -> "—"."""
    if value is None:
        return none
    if value >= 100:
        return f"{value:,.0f} h"
    if value >= 10:
        return f"{value:.0f} h"
    return f"{value:.1f} h"


def gate_hours(value: float | None) -> str:
    return "—" if value is None else f"{value:g} h"


def duration(seconds: float) -> str:
    """Seconds as "45 s", "12 min", "3.5 h" or "2.1 d"."""
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def rate(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def p_value(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2g}" if value >= 0.001 else "< 0.001"


def percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.0f}%"


def when(t: float | None, tz: str | None = None) -> str:
    """Unix seconds as "YYYY-MM-DD HH:MM" in tz (None = system local)."""
    if t is None:
        return "—"
    moment = dt.datetime.fromtimestamp(float(t)) if tz is None else dt.datetime.fromtimestamp(float(t), _zone(tz))
    return moment.strftime("%Y-%m-%d %H:%M")
