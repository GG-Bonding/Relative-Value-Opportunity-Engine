"""Turn a raw actual-minus-forecast into that indicator's own z-score.

An event file that already carries a z-score is left alone. A calendar print
without eight earlier raw errors of the same region and indicator stays missing.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from data.store import PitStore
from data.surprise import standardized_surprise
from domain.config import EngineConfig
from domain.timeutil import parse_ts
from forward.events import EventInput


def standardize_events(store: PitStore, events: list[EventInput], config: EngineConfig) -> list[EventInput]:
    minimum = config.features.surprise_min_history
    history = _stored_raw(store)
    standardized: list[EventInput] = []
    for event in events:
        if event.raw_surprise is None:
            standardized.append(event)
            continue
        prior = _prior(history, event.region, event.category, event.observed_at)
        z_score = standardized_surprise(event.raw_surprise, prior, minimum)
        standardized.append(replace(event, surprise=z_score))
        if event.region and event.category and event.observed_at is not None:
            history.setdefault((event.region, event.category), []).append((event.observed_at, event.raw_surprise))
    return standardized


def _stored_raw(store: PitStore) -> dict[tuple[str, str], list[tuple[datetime, float]]]:
    grouped: dict[tuple[str, str], list[tuple[datetime, float]]] = {}
    for row in store.rows("information_events"):
        raw = row.get("raw_surprise")
        region = row.get("region")
        category = row.get("category")
        if raw is None or not isinstance(region, str) or not isinstance(category, str):
            continue
        grouped.setdefault((region, category), []).append((parse_ts(str(row["observed_at"])), float(raw)))
    return grouped


def _prior(
    history: dict[tuple[str, str], list[tuple[datetime, float]]],
    region: str | None,
    category: str | None,
    observed_at: datetime | None,
) -> list[float]:
    if region is None or category is None:
        return []
    rows = history.get((region, category), [])
    if observed_at is None:
        return [value for _stamp, value in rows]
    return [value for stamp, value in rows if stamp < observed_at]
