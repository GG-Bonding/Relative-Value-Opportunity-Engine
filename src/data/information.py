"""Received headlines and releases. A surprise is a factor, not an order."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from domain.errors import PointInTimeError
from domain.hashing import stable_id
from domain.timeutil import dump_ts, ensure_utc

_CATEGORIES = frozenset(
    {
        "CPI",
        "CORE_CPI",
        "GDP",
        "PMI",
        "WAGES",
        "RETAIL_SALES",
        "INDUSTRIAL_PRODUCTION",
        "EMPLOYMENT",
    }
)
_EURO = frozenset({"EZ", "DE"})
_STERLING = frozenset({"UK"})


def relative_pressure(
    region: str | None,
    category: str | None,
    surprise: float | None,
    headline: str = "",
) -> float | None:
    """Positive pressure means EUR is relatively stronger. Missing surprise stays missing."""
    if region is None or category is None or surprise is None:
        return None
    if not math.isfinite(surprise) or surprise == 0:
        return None
    code = region.upper()
    if code in _EURO:
        leg = 1.0
    elif code in _STERLING:
        leg = -1.0
    else:
        return None
    if category not in _CATEGORIES:
        return None
    if category == "EMPLOYMENT" and "失业" in headline:
        leg *= -1.0
    scaled = max(-1.0, min(1.0, surprise / 3.0))
    return leg * scaled


def event_narrative(region: str | None, category: str | None, surprise: float | None, headline: str = "") -> str | None:
    pressure = relative_pressure(region, category, surprise, headline)
    if pressure is None or region is None or category is None or surprise is None:
        return None
    side = "above" if surprise > 0 else "below"
    if pressure < 0:
        effect = "GBP relative support → EURGBP bearish factor"
    else:
        effect = "EUR relative support → EURGBP bullish factor"
    return f"{region} {category} {side} expectation → {effect}"


def information_event(
    *,
    event_id: str,
    kind: str,
    headline: str,
    observed_at: datetime,
    ingested_at: datetime,
    source: str,
    version: str,
    region: str | None = None,
    category: str | None = None,
    surprise: float | None = None,
) -> dict[str, Any]:
    observed = ensure_utc(observed_at)
    ingested = ensure_utc(ingested_at)
    if ingested < observed:
        raise PointInTimeError("information event is after the ingest time")
    return {
        "event_id": event_id,
        "kind": kind,
        "headline": headline,
        "region": region,
        "category": category,
        "surprise": surprise,
        "pressure": relative_pressure(region, category, surprise, headline),
        "observed_at": dump_ts(observed),
        "ingested_at": dump_ts(ingested),
        "source": source,
        "version": version,
    }


def feed_events(
    rows: list[dict[str, Any]],
    *,
    kind: str,
    ingested_at: datetime,
    source: str,
    version: str,
) -> list[dict[str, Any]]:
    """Store what arrived. No surprise is invented from a headline."""
    events: list[dict[str, Any]] = []
    for row in rows:
        headline = _headline(row)
        if not headline:
            continue
        raw_id = row.get("id")
        if raw_id is None:
            raw_id = row.get("news_id")
        if raw_id is not None and str(raw_id).strip():
            event_id = f"{kind}:{raw_id}"
        else:
            event_id = stable_id("information", {"kind": kind, "headline": headline})
        events.append(
            information_event(
                event_id=event_id,
                kind=kind,
                headline=headline,
                observed_at=ingested_at,
                ingested_at=ingested_at,
                source=source,
                version=version,
                region=_region_hint(headline),
            )
        )
    return events


def _headline(row: dict[str, Any]) -> str:
    for key in ("title", "headline", "content", "data"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _region_hint(headline: str) -> str | None:
    if "欧元区" in headline:
        return "EZ"
    if headline.startswith("英国"):
        return "UK"
    if headline.startswith("德国"):
        return "DE"
    return None
