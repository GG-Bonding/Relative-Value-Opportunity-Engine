"""How EURGBP responds after a release or shock. Missing horizons stay missing."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

HORIZONS: dict[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
    "5d": timedelta(days=5),
}

HORIZON_MINUTES = {
    "5m": 5.0,
    "15m": 15.0,
    "30m": 30.0,
    "1h": 60.0,
    "4h": 240.0,
    "1d": 1440.0,
    "3d": 4320.0,
    "5d": 7200.0,
}


@dataclass(frozen=True)
class PricePrint:
    observed_at: datetime
    mid: float


@dataclass(frozen=True)
class EventResponse:
    event_id: str
    event_type: str
    observed_at: datetime
    magnitudes: dict[str, float | None]
    response_lag: str | None
    response_lag_minutes: float | None
    half_life: str | None
    half_life_minutes: float | None
    reversal: bool | None


def response_for_event(
    event_id: str,
    event_type: str,
    event_time: datetime,
    prints: list[PricePrint],
) -> EventResponse:
    ordered = sorted(prints, key=lambda item: item.observed_at)
    stamps = [item.observed_at for item in ordered]
    pre_index = bisect_left(stamps, event_time) - 1
    magnitudes: dict[str, float | None] = {}
    if pre_index < 0 or ordered[pre_index].mid <= 0:
        return EventResponse(
            event_id, event_type, event_time, {name: None for name in HORIZONS}, None, None, None, None, None
        )
    baseline = float(np.log(ordered[pre_index].mid))
    for name, delta in HORIZONS.items():
        # Last price known by this horizon. A later daily bar must not be
        # credited to a 5-minute horizon that had no print.
        post_index = bisect_right(stamps, event_time + delta) - 1
        if post_index <= pre_index or ordered[post_index].mid <= 0:
            magnitudes[name] = None
            continue
        magnitudes[name] = float(np.log(ordered[post_index].mid) - baseline)
    lag_name, lag_minutes = _incorporation_lag(magnitudes)
    half_name, half_minutes = _half_life(magnitudes)
    reversal = _reversal(magnitudes)
    return EventResponse(
        event_id=event_id,
        event_type=event_type,
        observed_at=event_time,
        magnitudes=magnitudes,
        response_lag=lag_name,
        response_lag_minutes=lag_minutes,
        half_life=half_name,
        half_life_minutes=half_minutes,
        reversal=reversal,
    )


def lag_distribution(responses: list[EventResponse]) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for response in responses:
        if response.response_lag_minutes is None:
            continue
        year = str(response.observed_at.year)
        buckets.setdefault(year, []).append(response.response_lag_minutes)
    return {year: float(np.median(values)) for year, values in sorted(buckets.items())}


def _incorporation_lag(magnitudes: dict[str, float | None]) -> tuple[str | None, float | None]:
    terminal = magnitudes.get("5d")
    if terminal is None or abs(terminal) < 1e-8:
        return None, None
    for name in HORIZONS:
        value = magnitudes.get(name)
        if value is None:
            continue
        if abs(value) >= 0.5 * abs(terminal) and np.sign(value) == np.sign(terminal):
            return name, HORIZON_MINUTES[name]
    return None, None


def _half_life(magnitudes: dict[str, float | None]) -> tuple[str | None, float | None]:
    available = [(name, magnitudes[name]) for name in HORIZONS if magnitudes.get(name) is not None]
    if len(available) < 2:
        return None, None
    peak_index = int(np.argmax([abs(value) for _name, value in available if value is not None]))
    peak_name, peak_value = available[peak_index]
    if peak_value is None or abs(peak_value) < 1e-8:
        return None, None
    for name, value in available[peak_index + 1 :]:
        if value is not None and abs(value) <= 0.5 * abs(peak_value):
            return name, HORIZON_MINUTES[name]
    _ = peak_name
    return None, None


def _reversal(magnitudes: dict[str, float | None]) -> bool | None:
    first = magnitudes.get("1d")
    last = magnitudes.get("5d")
    if first is None or last is None or abs(first) < 1e-8:
        return None
    return bool(np.sign(last) == -np.sign(first))
