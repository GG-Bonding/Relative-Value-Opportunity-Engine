"""Event file for a forward cycle. Headlines are not converted into orders."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data.information import event_contribution, event_narrative, information_event
from data.policy_news import policy_context
from domain.errors import DataValidationError
from forward.mt5 import parse_feed_time


@dataclass(frozen=True)
class EventInput:
    event_id: str
    kind: str
    headline: str
    region: str | None
    category: str | None
    surprise: float | None
    observed_at: datetime | None
    raw_surprise: float | None = None


def load_events(path: str | Path) -> list[EventInput]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise DataValidationError("event file must contain a JSON list")
    events: list[EventInput] = []
    for row in payload:
        if not isinstance(row, dict):
            raise DataValidationError("each event must be a JSON object")
        headline = str(row.get("headline") or row.get("title") or "").strip()
        if not headline:
            raise DataValidationError("event is missing a headline")
        event_id = str(row.get("id") or row.get("event_id") or "").strip()
        if not event_id:
            raise DataValidationError("event is missing an id")
        surprise = _surprise(row.get("surprise"))
        events.append(
            EventInput(
                event_id=event_id,
                kind=str(row.get("kind") or "CALENDAR"),
                headline=headline,
                region=_text(row.get("region")),
                category=_text(row.get("category")),
                surprise=surprise,
                observed_at=parse_feed_time(row["time"]) if row.get("time") is not None else None,
            )
        )
    return events


def narratives_for(events: list[EventInput]) -> list[str]:
    lines: list[str] = []
    for event in events:
        line = event_narrative(event.region, event.category, event.surprise, event.headline)
        policy = policy_context(event.headline)
        if line is not None:
            lines.append(line)
        if policy is not None:
            lines.append(policy)
    return lines


def combined_pressure(events: list[EventInput]) -> float | None:
    values = [
        event_contribution(event.region, event.category, event.surprise, event.headline)
        for event in events
    ]
    known = [value for value in values if value is not None]
    if not known:
        return None
    return max(-1.0, min(1.0, sum(known)))


def event_row(
    event: EventInput,
    *,
    ingested_at: datetime,
    observed_at: datetime,
    source: str = "jin10",
    version: str = "event-v1",
) -> dict[str, Any]:
    return information_event(
        event_id=event.event_id,
        kind=event.kind,
        headline=event.headline,
        observed_at=event.observed_at or observed_at,
        ingested_at=ingested_at,
        source=source,
        version=version,
        region=event.region,
        category=event.category,
        surprise=event.surprise,
        raw_surprise=event.raw_surprise,
    )


def _surprise(value: Any) -> float | None:
    if value is None:
        return None
    try:
        surprise = float(value)
    except (TypeError, ValueError) as exc:
        raise DataValidationError("event surprise is not a number") from exc
    return surprise


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
