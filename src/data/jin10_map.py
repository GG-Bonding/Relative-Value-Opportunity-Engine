"""Map Jin10 calendar rows and EURUSD/GBPUSD quotes onto EURGBP records.

Jin10 does not list EURGBP. The mid is the contemporaneous cross EURUSD/GBPUSD.
Bid and ask stay missing. The calendar tool returns the current week only.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from domain.models import ConsensusSnapshot, MacroRelease, MarketObservation
from domain.timeutil import UTC, ensure_utc

SHANGHAI = ZoneInfo("Asia/Shanghai")
SOURCE = "jin10"
CROSS_VERSION = "cross-eurusd-gbpusd-v1"
MACRO_VERSION = "calendar-week-v1"

_QUARTER = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def cross_observations(
    eurusd: dict[str, Any],
    gbpusd: dict[str, Any],
    *,
    ingested_at: datetime,
) -> list[MarketObservation]:
    eur = _price(eurusd)
    gbp = _price(gbpusd)
    if eur is None or gbp is None or gbp == 0:
        raise ValueError("EURUSD and GBPUSD closes are required to cross EURGBP")
    eur_time = _quote_time(eurusd)
    gbp_time = _quote_time(gbpusd)
    observed = min(eur_time, gbp_time)
    ingested = ensure_utc(ingested_at)
    if ingested < observed:
        observed = ingested
    mid = eur / gbp
    minute = observed.replace(second=0, microsecond=0)
    rows = [_market(minute, mid, ingested, "1m")]
    if observed.hour >= 16:
        daily_at = observed.replace(hour=16, minute=0, second=0, microsecond=0)
        if daily_at <= observed:
            rows.append(_market(daily_at, mid, ingested, "1d"))
    return rows


def calendar_records(
    rows: list[dict[str, Any]],
    *,
    ingested_at: datetime,
) -> tuple[list[MacroRelease], list[ConsensusSnapshot], dict[str, int]]:
    ingested = ensure_utc(ingested_at)
    macros: list[MacroRelease] = []
    consensus: list[ConsensusSnapshot] = []
    counts = {"unmapped": 0, "unpublished": 0, "future": 0}
    for row in rows:
        mapped = map_calendar_row(row, ingested_at=ingested)
        if mapped is None:
            counts["unmapped"] += 1
            continue
        macro, snap, flag = mapped
        if flag == "future":
            counts["future"] += 1
        elif macro is None:
            counts["unpublished"] += 1
        if macro is not None:
            macros.append(macro)
        if snap is not None:
            consensus.append(snap)
    return macros, consensus, counts


def map_calendar_row(
    row: dict[str, Any],
    *,
    ingested_at: datetime,
) -> tuple[MacroRelease | None, ConsensusSnapshot | None, str] | None:
    title = str(row.get("title") or "").strip()
    published_hint = _shanghai_time(row.get("pub_time"))
    if published_hint is None:
        return None
    identity = series_identity(title, published_hint)
    if identity is None:
        return None
    published = published_hint
    ingested = ensure_utc(ingested_at)
    region, category, series_id, period = identity
    actual = _number(row.get("actual"))
    forecast = _number(row.get("consensus"))
    previous = _number(row.get("revised"))
    if previous is None:
        previous = _number(row.get("previous"))
    if published > ingested:
        snap = _consensus(series_id, region, category, period, forecast, ingested, ingested)
        return None, snap, "future"
    if actual is None:
        snap = _consensus(series_id, region, category, period, forecast, published, ingested)
        return None, snap, "unpublished"
    macro = MacroRelease(
        effective_at=published,
        released_at=published,
        observed_at=published,
        ingested_at=ingested,
        source=SOURCE,
        version=MACRO_VERSION,
        series_id=series_id,
        region=region,
        category=category,
        period=period,
        actual=actual,
        consensus=forecast,
        previous_as_known=previous,
        revision_number=_revision(title),
    )
    snap = _consensus(series_id, region, category, period, forecast, published, ingested)
    return macro, snap, "published"


def series_identity(title: str, published: datetime) -> tuple[str, str, str, str] | None:
    if "欧元区" in title:
        region = "EZ"
    elif title.startswith("英国"):
        region = "UK"
    else:
        return None
    category = _category(title)
    if category is None:
        return None
    series_id = f"{region}.{category}.{_detail(title, category)}.{_frequency(title)}"
    period = _period(title, published)
    if period is None:
        return None
    return region, category, series_id, period


def _category(title: str) -> str | None:
    if "核心CPI" in title or "核心消费者物价" in title:
        return "CORE_CPI"
    if "CPI" in title or "消费者物价" in title or "调和CPI" in title:
        return "CPI"
    if any(word in title for word in ("工资", "平均周薪", "平均收入")):
        return "WAGES"
    if "GDP" in title:
        return "GDP"
    if "PMI" in title:
        return "PMI"
    if "零售" in title:
        return "RETAIL_SALES"
    if any(word in title for word in ("失业率", "就业人数", "失业金")):
        return "EMPLOYMENT"
    if "工业产出" in title or "工业生产" in title:
        return "INDUSTRIAL_PRODUCTION"
    return None


def _detail(title: str, category: str) -> str:
    if category == "PMI":
        if "制造业" in title:
            return "MANUFACTURING"
        if "服务业" in title:
            return "SERVICES"
        if "综合" in title:
            return "COMPOSITE"
        return "HEADLINE"
    if category == "EMPLOYMENT" and "失业率" in title:
        return "UNEMPLOYMENT"
    return "HEADLINE"


def _frequency(title: str) -> str:
    if "年率" in title:
        return "YOY"
    if "季率" in title:
        return "QOQ"
    if "月率" in title:
        return "MOM"
    return "LEVEL"


def _revision(title: str) -> int:
    if "终值" in title:
        return 2
    if "修正" in title:
        return 1
    return 0


def _period(title: str, published: datetime) -> str | None:
    year_match = re.search(r"(20\d{2})", title)
    explicit_year = int(year_match.group(1)) if year_match else None
    local = published.astimezone(SHANGHAI)
    quarter = re.search(r"第([一二三四1-4])季度", title)
    if quarter:
        year = explicit_year if explicit_year is not None else local.year
        quarter_number = _QUARTER[quarter.group(1)]
        if explicit_year is None and quarter_number > ((local.month - 1) // 3 + 1):
            year -= 1
        return f"{year}Q{quarter_number}"
    month = re.search(r"(\d{1,2})月", title)
    if month:
        year = explicit_year if explicit_year is not None else local.year
        month_number = int(month.group(1))
        if explicit_year is None and month_number > local.month:
            year -= 1
        return f"{year}-{month_number:02d}"
    return None


def _market(observed: datetime, mid: float, ingested: datetime, bar_size: str) -> MarketObservation:
    return MarketObservation(
        effective_at=observed,
        released_at=observed,
        observed_at=observed,
        ingested_at=ingested,
        source=SOURCE,
        version=CROSS_VERSION,
        instrument="EURGBP",
        pair="EURGBP",
        bar_size=bar_size,
        bid=None,
        ask=None,
        mid=mid,
        volume=None,
    )


def _consensus(
    series_id: str,
    region: str,
    category: str,
    period: str,
    forecast: float | None,
    observed: datetime,
    ingested: datetime,
) -> ConsensusSnapshot | None:
    if forecast is None:
        return None
    return ConsensusSnapshot(
        effective_at=observed,
        released_at=observed,
        observed_at=observed,
        ingested_at=ingested,
        source=SOURCE,
        version=MACRO_VERSION,
        series_id=series_id,
        region=region,
        category=category,
        horizon=period,
        expected_value=forecast,
    )


def _price(payload: dict[str, Any]) -> float | None:
    return _number(payload.get("close"))


def _quote_time(payload: dict[str, Any]) -> datetime:
    stamped = _parse_time(payload.get("time"))
    if stamped is None:
        raise ValueError("Jin10 quote is missing a time")
    return stamped


def _shanghai_time(value: object) -> datetime | None:
    return _parse_time(value)


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, tz=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.isdigit():
        seconds = int(raw)
        if seconds > 10_000_000_000:
            seconds //= 1000
        return datetime.fromtimestamp(seconds, tz=UTC)
    iso = raw.replace("Z", "+00:00")
    parsed = _from_iso(iso, raw)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(UTC)


def _from_iso(iso: str, raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return _parse_naive(raw)


def _parse_naive(raw: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("%", "").strip()
        if not cleaned or cleaned.lower() == "null":
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None
