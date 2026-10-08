"""Jin10 calendar, flash, and news as information. A headline is not an order.

Only a UK or euro-area calendar surprise, actual minus forecast, becomes a relative
factor. Flash and news keep the headline and carry no surprise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from data.information import _CATEGORIES
from data.jin10_client import Jin10Error
from data.jin10_map import _category, _number, _parse_time
from domain.hashing import stable_id
from domain.timeutil import dump_ts, ensure_utc
from forward.events import EventInput


class InformationSource(Protocol):
    def calendar(self) -> list[dict[str, Any]]: ...

    def flash(self) -> list[dict[str, Any]]: ...

    def news(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class InformationPrint:
    kind: str
    event_time: datetime | None
    country: str | None
    indicator: str | None
    actual: float | None
    forecast: float | None
    previous: float | None
    importance: str | None
    headline: str

    @property
    def surprise(self) -> float | None:
        """Raw actual-minus-forecast. Missing numbers stay missing. Headlines stay None."""
        if self.kind != "CALENDAR":
            return None
        if self.country not in {"UK", "EZ"} or self.indicator not in _CATEGORIES:
            return None
        if self.actual is None or self.forecast is None:
            return None
        return self.actual - self.forecast

    @property
    def event_id(self) -> str:
        return stable_id(
            "jin10-print",
            {
                "kind": self.kind,
                "headline": self.headline,
                "event_time": None if self.event_time is None else dump_ts(self.event_time),
                "indicator": self.indicator,
                "actual": self.actual,
                "forecast": self.forecast,
            },
        )


def read_information(source: InformationSource) -> tuple[list[InformationPrint], str | None]:
    """Calendar, flash, and news. This does not ask Jin10 for a quote or a yield."""
    prints: list[InformationPrint] = []
    errors: list[str] = []
    for kind, method in (("CALENDAR", "calendar"), ("FLASH", "flash"), ("NEWS", "news")):
        try:
            rows = getattr(source, method)()
        except (Jin10Error, ValueError, TypeError) as exc:
            errors.append(f"{kind}: {exc}")
            continue
        if not isinstance(rows, list):
            errors.append(f"{kind}: payload is not a list")
            continue
        for row in rows:
            if isinstance(row, dict):
                item = normalize_print(row, kind)
                if item is not None:
                    prints.append(item)
    return prints, "; ".join(errors) or None


def normalize_print(row: dict[str, Any], kind: str) -> InformationPrint | None:
    headline = _headline(row)
    if not headline:
        return None
    calendar = kind == "CALENDAR"
    return InformationPrint(
        kind=kind,
        event_time=_event_time(row),
        country=_country(row, headline) if calendar else None,
        indicator=_indicator(row, headline) if calendar else None,
        actual=_number(row.get("actual")) if calendar else None,
        forecast=_forecast(row) if calendar else None,
        previous=_previous(row) if calendar else None,
        importance=_importance(row),
        headline=headline,
    )


def usable_prints(prints: list[InformationPrint], ingested_at: datetime) -> list[InformationPrint]:
    """Keep what has already happened. A future calendar row is not a surprise."""
    ingested = ensure_utc(ingested_at)
    usable: list[InformationPrint] = []
    for item in prints:
        if item.event_time is not None and item.event_time > ingested:
            continue
        usable.append(item)
    return usable


def as_events(prints: list[InformationPrint]) -> list[EventInput]:
    events: list[EventInput] = []
    for item in prints:
        mapped = item.kind == "CALENDAR" and item.country in {"UK", "EZ"} and item.indicator in _CATEGORIES
        events.append(
            EventInput(
                event_id=item.event_id,
                kind=item.kind,
                headline=item.headline,
                region=item.country if mapped else None,
                category=item.indicator if mapped else None,
                surprise=None,
                observed_at=item.event_time,
                raw_surprise=item.surprise if mapped else None,
            )
        )
    return events


def print_payload(item: InformationPrint) -> dict[str, Any]:
    return {
        "event_time": None if item.event_time is None else dump_ts(item.event_time),
        "country": item.country,
        "indicator": item.indicator,
        "actual": item.actual,
        "forecast": item.forecast,
        "previous": item.previous,
        "importance": item.importance,
        "headline": item.headline,
        "kind": item.kind,
        "surprise": item.surprise,
    }


def _headline(row: dict[str, Any]) -> str:
    for key in ("title", "headline", "content"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _event_time(row: dict[str, Any]) -> datetime | None:
    for key in ("event_time", "pub_time", "time", "datetime"):
        if row.get(key) not in (None, ""):
            return _parse_time(row.get(key))
    return None


def _country(row: dict[str, Any], headline: str) -> str | None:
    raw = row.get("country")
    if raw is None:
        raw = row.get("region")
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        upper = text.upper()
        if upper in {"UK", "GB"} or text == "英国":
            return "UK"
        if upper in {"EZ", "EA"} or "欧元" in text or text.lower() in {"euro area", "eurozone"}:
            return "EZ"
    if "欧元区" in headline:
        return "EZ"
    if headline.startswith("英国"):
        return "UK"
    return None


def _indicator(row: dict[str, Any], headline: str) -> str | None:
    raw = row.get("indicator")
    if raw is None:
        raw = row.get("category")
    if isinstance(raw, str) and raw.strip().upper() in _CATEGORIES:
        return raw.strip().upper()
    return _category(headline)


def _forecast(row: dict[str, Any]) -> float | None:
    if row.get("forecast") not in (None, ""):
        return _number(row.get("forecast"))
    return _number(row.get("consensus"))


def _previous(row: dict[str, Any]) -> float | None:
    revised = _number(row.get("revised"))
    if revised is not None:
        return revised
    return _number(row.get("previous"))


def _importance(row: dict[str, Any]) -> str | None:
    for key in ("importance", "star", "stars"):
        value = row.get(key)
        if value in (None, "") or isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            number = float(value)
            return str(int(number)) if number.is_integer() else str(number)
        text = str(value).strip()
        if text:
            return text
    return None
