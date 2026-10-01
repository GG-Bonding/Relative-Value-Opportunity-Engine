"""UTC timestamps stored in a lexicographically sortable form."""

from __future__ import annotations

from datetime import UTC, datetime

from domain.errors import PointInTimeError

UTC = UTC


def ensure_utc(value: datetime) -> datetime:
    """Reject naive timestamps. Point-in-time ordering is undefined without a zone."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise PointInTimeError("naive datetime rejected; use timezone-aware UTC")
    return value.astimezone(UTC)


def dump_ts(value: datetime) -> str:
    """Format as YYYY-MM-DDTHH:MM:SS+00:00 so string order matches time order."""
    stamped = ensure_utc(value).replace(microsecond=0)
    return stamped.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return ensure_utc(parsed)
