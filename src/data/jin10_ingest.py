"""Append the current Jin10 week and the EURUSD/GBPUSD cross. Never overwrite."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from data.information import feed_events
from data.jin10_client import Jin10Error
from data.jin10_map import calendar_records, cross_observations
from data.store import PitStore
from domain.errors import DataValidationError
from domain.models import ConsensusSnapshot, MacroRelease, MarketObservation
from domain.timeutil import UTC, ensure_utc


class Jin10Source(Protocol):
    def quote(self, code: str) -> dict[str, Any]: ...

    def kline(self, code: str, count: int) -> list[Any]: ...

    def calendar(self) -> list[dict[str, Any]]: ...


def ingest_jin10(
    store: PitStore,
    source: Jin10Source,
    *,
    ingested_at: datetime | None = None,
    kline_count: int = 240,
) -> dict[str, object]:
    moment = ensure_utc(ingested_at or datetime.now(tz=UTC))
    eurusd = source.quote("EURUSD")
    gbpusd = source.quote("GBPUSD")
    markets = cross_observations(eurusd, gbpusd, ingested_at=moment)
    kline_error: str | None = None
    try:
        markets.extend(_kline_markets(source.kline("EURUSD", kline_count), source.kline("GBPUSD", kline_count), moment))
    except (Jin10Error, ValueError, TypeError) as exc:
        kline_error = type(exc).__name__
    calendar = source.calendar()
    macros, consensus, calendar_counts = calendar_records(calendar, ingested_at=moment)
    market_inserted, market_skipped = _append_markets(store, markets)
    macro_inserted, macro_skipped = _append_macros(store, macros)
    consensus_inserted, consensus_skipped = _append_consensus(store, consensus)
    events = feed_events(
        calendar,
        kind="CALENDAR",
        ingested_at=moment,
        source="jin10",
        version="jin10-calendar-v1",
    )
    flash_rows, flash_error = _optional_feed(source, "flash", "FLASH", moment, "jin10-flash-v1")
    news_rows, news_error = _optional_feed(source, "news", "NEWS", moment, "jin10-news-v1")
    events.extend(flash_rows)
    events.extend(news_rows)
    events_inserted, events_skipped = _append_rows(store, "information_events", events)
    return {
        "source": "jin10",
        "direct_eurgbp_quote": False,
        "cross": "EURUSD/GBPUSD",
        "market_inserted": market_inserted,
        "market_skipped": market_skipped,
        "macro_inserted": macro_inserted,
        "macro_skipped": macro_skipped,
        "consensus_inserted": consensus_inserted,
        "consensus_skipped": consensus_skipped,
        "calendar_unmapped": calendar_counts["unmapped"],
        "calendar_unpublished": calendar_counts["unpublished"],
        "calendar_future": calendar_counts["future"],
        "events_inserted": events_inserted,
        "events_skipped": events_skipped,
        "flash_error": flash_error,
        "news_error": news_error,
        "kline_error": kline_error,
        "missing": [
            "EURGBP direct quote",
            "bid",
            "ask",
            "UK2Y",
            "DE2Y",
            "policy path",
            "history before the current week",
        ],
    }


def _append_markets(store: PitStore, rows: list[MarketObservation]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        try:
            store.append_market(row)
            inserted += 1
        except DataValidationError:
            skipped += 1
    return inserted, skipped


def _append_macros(store: PitStore, rows: list[MacroRelease]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        try:
            store.append_macro(row)
            inserted += 1
        except DataValidationError:
            skipped += 1
    return inserted, skipped


def _optional_feed(
    source: Jin10Source,
    method: str,
    kind: str,
    ingested_at: datetime,
    version: str,
) -> tuple[list[dict[str, Any]], str | None]:
    reader = getattr(source, method, None)
    if reader is None:
        return [], None
    try:
        rows = reader()
    except (Jin10Error, ValueError, TypeError) as exc:
        return [], type(exc).__name__
    if not isinstance(rows, list):
        return [], "TypeError"
    payloads = [row for row in rows if isinstance(row, dict)]
    return feed_events(payloads, kind=kind, ingested_at=ingested_at, source="jin10", version=version), None


def _append_rows(store: PitStore, table: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        try:
            store.insert_records(table, [row])
            inserted += 1
        except DataValidationError:
            skipped += 1
    return inserted, skipped


def _append_consensus(store: PitStore, rows: list[ConsensusSnapshot]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        try:
            store.append_consensus(row)
            inserted += 1
        except DataValidationError:
            skipped += 1
    return inserted, skipped


def _kline_markets(
    eurusd: list[Any],
    gbpusd: list[Any],
    ingested_at: datetime,
) -> list[MarketObservation]:
    eur = _closes(eurusd, ingested_at)
    gbp = _closes(gbpusd, ingested_at)
    rows: list[MarketObservation] = []
    for minute, eur_close in eur.items():
        gbp_close = gbp.get(minute)
        if gbp_close is None or gbp_close == 0:
            continue
        rows.append(
            MarketObservation(
                effective_at=minute,
                released_at=minute,
                observed_at=minute,
                ingested_at=ingested_at,
                source="jin10",
                version="cross-eurusd-gbpusd-v1",
                instrument="EURGBP",
                pair="EURGBP",
                bar_size="1m",
                bid=None,
                ask=None,
                mid=eur_close / gbp_close,
                volume=None,
            )
        )
    return rows


def _closes(bars: list[Any], ingested_at: datetime) -> dict[datetime, float]:
    from data.jin10_map import _number, _parse_time

    closes: dict[datetime, float] = {}
    for bar in bars:
        if isinstance(bar, dict):
            price = _number(bar.get("close") or bar.get("c") or bar.get("price"))
            moment = _parse_time(bar.get("time") or bar.get("t") or bar.get("timestamp"))
        elif isinstance(bar, list) and len(bar) >= 5:
            moment = _parse_time(bar[0])
            price = _number(bar[4])
        else:
            continue
        if price is None or moment is None or moment > ingested_at + timedelta(minutes=2):
            continue
        if moment > ingested_at:
            moment = ingested_at
        closes[moment.replace(second=0, microsecond=0)] = price
    return closes
