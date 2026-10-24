"""London session, MT5 server clock, and the economic-release blackout.

London local time uses `Europe/London`, so British Summer Time is the zone
database's transition rather than a fixed offset. MT5 server time is a naive
clock plus an explicit UTC offset supplied by the terminal or the user. This
module does not assume an Anzo Capital offset.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

LONDON = ZoneInfo("Europe/London")


@dataclass(frozen=True, slots=True)
class Clock:
    server_utc_offset_hours: float | None
    source: str
    note: str

    @property
    def verified(self) -> bool:
        return self.source == "mt5_terminal" and self.server_utc_offset_hours is not None


def london_local(stamp: datetime) -> datetime:
    return require_utc(stamp).astimezone(LONDON)


def in_london_session(stamp: datetime, start_minute: int, end_minute: int) -> bool:
    """Weekday London clock in `[start, end)`. Weekend bars are outside the session."""
    if not 0 <= start_minute < end_minute <= 24 * 60:
        raise ValueError("session must be a non-empty minute range")
    local = london_local(stamp)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    return start_minute <= minute < end_minute


def session_mask(stamps: list[datetime], start_minute: int, end_minute: int) -> np.ndarray:
    return np.array([in_london_session(stamp, start_minute, end_minute) for stamp in stamps], dtype=bool)


def server_to_utc(server_clock: datetime, offset_hours: float) -> datetime:
    """Convert a naive MT5 server timestamp to UTC.

    `offset_hours` is server minus UTC. A server clock of 11:00 with offset 3
    is 08:00 UTC. Aware inputs are rejected so a UTC value cannot be shifted twice.
    """
    if server_clock.tzinfo is not None and server_clock.utcoffset() is not None:
        raise ValueError("server time must be a naive clock")
    if not math.isfinite(offset_hours):
        raise ValueError("server offset must be finite")
    shifted = server_clock - timedelta(hours=offset_hours)
    return shifted.replace(tzinfo=UTC)


def require_utc(stamp: datetime) -> datetime:
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return stamp.astimezone(UTC)


def assess_calendar(events: tuple[datetime, ...], sample_end: datetime, max_age: timedelta) -> str:
    """`missing`, `stale`, or `current` relative to the sample end, not to a hidden clock."""
    if max_age < timedelta(0):
        raise ValueError("calendar age must be non-negative")
    if not events:
        return "missing"
    latest = max(require_utc(item) for item in events)
    if latest < require_utc(sample_end) - max_age:
        return "stale"
    return "current"


def event_block_mask(
    stamps: list[datetime],
    events: tuple[datetime, ...],
    *,
    before: timedelta,
    after: timedelta,
    mode: str,
    status: str,
) -> np.ndarray:
    """Block new entries. `optional` with no calendar blocks nothing and stays unverified.

    `required` blocks every bar when the calendar is missing, and blocks the
    unverified tail when it is stale. Historical windows still apply.
    """
    if mode not in {"off", "optional", "required"}:
        raise ValueError("event filter mode must be off, optional, or required")
    mask = np.zeros(len(stamps), dtype=bool)
    if mode == "off" or not events:
        if mode == "required" and status != "current":
            mask[:] = True
        return mask
    if status == "missing":
        if mode == "required":
            mask[:] = True
        return mask
    windows = tuple(
        (require_utc(event) - before, require_utc(event) + after) for event in events if before >= timedelta(0)
    )
    last = max(require_utc(event) for event in events) + after if events else None
    for index, stamp in enumerate(stamps):
        utc = require_utc(stamp)
        if any(start <= utc < end for start, end in windows):
            mask[index] = True
        elif mode == "required" and status == "stale" and last is not None and utc >= last:
            mask[index] = True
    return mask


def live_entries_allowed(mode: str, status: str) -> bool:
    """Real-money automation stops when a required calendar is missing or stale."""
    if mode == "required" and status != "current":
        return False
    return True
