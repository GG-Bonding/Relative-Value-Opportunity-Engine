"""Controlled EURGBP laboratory.

This is not a market history. The price is a known function of relative factors
plus a five-day incorporation lag that ends on 2021-01-01. The engine is
supposed to harvest that lag and to refuse the permanent jumps that follow.
A research conclusion about live EURGBP is still impossible from this tape.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from data.store import PitStore
from domain.config import EngineConfig
from domain.timeutil import parse_ts
from features.panel import build_panel

DECAY_START = "2021-01-01"
PERMANENT_SHOCKS = ("2021-06-15", "2021-11-15", "2022-05-16")
TRUE_BETA = {
    "rate_level": -0.035,
    "policy_diff_3m": -0.025,
    "growth_diff_index": -0.004,
    "inflation_diff": -0.004,
    "risk_premium_diff": -0.020,
    "expected_rate_diff_3m": -0.010,
    "expected_growth_diff": -0.010,
    "expected_inflation_diff": -0.010,
}
INTERCEPT = -0.16


def build_laboratory(store: PitStore, config: EngineConfig | None = None) -> dict[str, str]:
    config = config or EngineConfig()
    rng = np.random.default_rng(config.seed)
    days = _business_days("2010-01-01", "2022-12-31")
    stamps = [_close_stamp(day) for day in days]
    _write_curves(store, stamps, rng)
    _write_policy(store, stamps, rng)
    _write_risk(store, stamps, rng)
    _write_macro(store, rng)
    _write_consensus(store, stamps, rng)
    panel = build_panel(store, parse_ts(stamps[-1]), config, calendar=stamps)
    log_fv = _structural_log_fair(panel)
    log_px = _price_path(np.array(days), log_fv, rng)
    _write_markets(store, stamps, log_px)
    return {"start": stamps[0], "end": stamps[-1], "decay_start": f"{DECAY_START}T16:00:00+00:00"}


def laboratory_store(path: str | Path, config: EngineConfig | None = None) -> PitStore:
    store = PitStore(path)
    build_laboratory(store, config)
    return store


def _business_days(start: str, end: str) -> np.ndarray:
    grid = np.arange(np.datetime64(start), np.datetime64(end), dtype="datetime64[D]")
    return grid[np.is_busday(grid)]


def _close_stamp(day: np.datetime64) -> str:
    return f"{day}T16:00:00+00:00"


def _write_curves(store: PitStore, stamps: list[str], rng: np.random.Generator) -> None:
    n = len(stamps)
    rate = np.cumsum(rng.normal(0.0, 0.02, n))
    de = 2.0 + np.cumsum(rng.normal(0.0, 0.004, n))
    uk = de + rate
    uk_fwd_1m = uk + rng.normal(0.0, 0.01, n)
    de_fwd_1m = de + rng.normal(0.0, 0.01, n)
    uk_fwd_3m = uk + 0.05 + np.cumsum(rng.normal(0.0, 0.005, n))
    de_fwd_3m = de + np.cumsum(rng.normal(0.0, 0.005, n))
    rows: list[dict[str, object]] = []
    series = {
        "UK2Y": uk,
        "DE2Y": de,
        "UK_FWD_1M": uk_fwd_1m,
        "DE_FWD_1M": de_fwd_1m,
        "UK_FWD_3M": uk_fwd_3m,
        "DE_FWD_3M": de_fwd_3m,
    }
    for curve_id, values in series.items():
        for stamp, value in zip(stamps, values, strict=True):
            rows.append(_point(curve_id=curve_id, rate=float(value), stamp=stamp))
    store.insert_records("rate_observations", rows)


def _write_policy(store: PitStore, stamps: list[str], rng: np.random.Generator) -> None:
    n = len(stamps)
    # Rebuild the rate differential from the stored curves so policy is tied to the same seed stream
    # only through an independent shock. The level is generated here, not read back.
    gap = np.cumsum(rng.normal(0.0, 0.015, n))
    ecb_3m = 2.0 + np.cumsum(rng.normal(0.0, 0.004, n))
    boe_3m = ecb_3m + gap
    tenors = {
        "1M": (-0.04, -0.03),
        "3M": (0.0, 0.0),
        "6M": (0.06, 0.04),
        "12M": (0.14, 0.08),
    }
    rows: list[dict[str, object]] = []
    for horizon, (boe_add, ecb_add) in tenors.items():
        for stamp, boe, ecb in zip(stamps, boe_3m + boe_add, ecb_3m + ecb_add, strict=True):
            rows.append(_policy_row("BOE", horizon, float(boe), stamp))
            rows.append(_policy_row("ECB", horizon, float(ecb), stamp))
    store.insert_records("policy_expectations", rows)


def _write_risk(store: PitStore, stamps: list[str], rng: np.random.Generator) -> None:
    n = len(stamps)
    eur = np.cumsum(rng.normal(0.0, 0.01, n))
    gbp = np.cumsum(rng.normal(0.0, 0.01, n))
    rows: list[dict[str, object]] = []
    for metric, weight in (("OAT_BUND", 1.0), ("BTP_BUND", 1.3), ("EU_CREDIT", 0.7)):
        noise = rng.normal(0.0, 0.002, n)
        for stamp, value in zip(stamps, eur * weight + noise, strict=True):
            rows.append(_risk_row("EUR", metric, float(value), stamp))
    for metric, weight in (("GILT_RISK", 1.0), ("FISCAL_STRESS", 0.8), ("UK_CREDIT", 0.9)):
        noise = rng.normal(0.0, 0.002, n)
        for stamp, value in zip(stamps, gbp * weight + noise, strict=True):
            rows.append(_risk_row("GBP", metric, float(value), stamp))
    store.insert_records("risk_observations", rows)


def _write_macro(store: PitStore, rng: np.random.Generator) -> None:
    rows: list[dict[str, object]] = []
    categories = {
        "GDP": "growth",
        "PMI": "growth",
        "RETAIL_SALES": "growth",
        "EMPLOYMENT": "growth",
        "INDUSTRIAL_PRODUCTION": "growth",
        "CPI": "inflation",
        "CORE_CPI": "inflation",
        "SERVICES_INFLATION": "inflation",
        "WAGES": "inflation",
    }
    months = _month_starts("2008-01-01", "2022-12-01")
    for region, series_prefix in (("UK", "UK"), ("EZ", "EZ")):
        for category in categories:
            history: list[float] = []
            for month in months:
                actual = float(rng.normal(0.2, 1.0))
                consensus = float(actual - rng.normal(0.0, 0.8))
                previous = history[-1] if history else None
                release_day = _release_day(month)
                stamp = f"{release_day}T08:00:00+00:00"
                period = str(month)[:7]
                rows.append(
                    _macro_row(
                        series_id=f"{series_prefix}_{category}",
                        region=region,
                        category=category,
                        period=period,
                        actual=actual,
                        consensus=consensus,
                        previous=previous,
                        revision=0,
                        effective=f"{month}T00:00:00+00:00",
                        stamp=stamp,
                    )
                )
                history.append(actual)
                if category == "GDP" and str(month)[5:7] == "06":
                    revised_day = np.busday_offset(release_day, 20)
                    rows.append(
                        _macro_row(
                            series_id=f"{series_prefix}_{category}",
                            region=region,
                            category=category,
                            period=period,
                            actual=actual + 0.1,
                            consensus=actual,
                            previous=actual,
                            revision=1,
                            effective=f"{month}T00:00:00+00:00",
                            stamp=f"{revised_day}T08:00:00+00:00",
                        )
                    )
    store.insert_records("macro_releases", rows)


def _write_consensus(store: PitStore, stamps: list[str], rng: np.random.Generator) -> None:
    # Monthly survey levels, distinct from the surprise index.
    months = _month_starts("2009-01-01", "2022-12-01")
    rows: list[dict[str, object]] = []
    categories = (
        "GDP",
        "PMI",
        "RETAIL_SALES",
        "EMPLOYMENT",
        "INDUSTRIAL_PRODUCTION",
        "CPI",
        "CORE_CPI",
        "SERVICES_INFLATION",
        "WAGES",
    )
    for region in ("UK", "EZ"):
        level = 1.0
        for category in categories:
            level = 1.0
            for month in months:
                level += float(rng.normal(0.0, 0.15))
                day = _release_day(month)
                rows.append(
                    {
                        "series_id": f"{region}_{category}_CONSENSUS",
                        "region": region,
                        "category": category,
                        "horizon": "3M",
                        "expected_value": level,
                        "effective_at": f"{day}T08:00:00+00:00",
                        "released_at": f"{day}T08:00:00+00:00",
                        "observed_at": f"{day}T08:00:00+00:00",
                        "ingested_at": f"{day}T08:00:00+00:00",
                        "source": "laboratory",
                        "version": "1",
                    }
                )
    store.insert_records("consensus_snapshots", rows)


def _write_markets(store: PitStore, stamps: list[str], log_px: np.ndarray) -> None:
    rows: list[dict[str, object]] = []
    half = 0.6 * 0.0001
    for stamp, log_price in zip(stamps, log_px, strict=True):
        if not np.isfinite(log_price):
            continue
        mid = float(np.exp(log_price))
        rows.append(
            {
                "instrument": "EURGBP",
                "pair": "EURGBP",
                "bar_size": "1d",
                "bid": mid - half,
                "ask": mid + half,
                "mid": mid,
                "volume": 1.0,
                "effective_at": stamp,
                "released_at": stamp,
                "observed_at": stamp,
                "ingested_at": stamp,
                "source": "laboratory",
                "version": "1",
            }
        )
    store.insert_records("market_observations", rows)


def _structural_log_fair(panel: object) -> np.ndarray:
    import polars as pl

    frame = panel
    assert isinstance(frame, pl.DataFrame)
    n = frame.height
    out = np.full(n, INTERCEPT)
    for name, beta in TRUE_BETA.items():
        values = np.asarray(
            [np.nan if value is None else float(value) for value in frame[name].to_list()],
            dtype=float,
        )
        missing = ~np.isfinite(values)
        out = out + beta * np.where(missing, np.nan, values)
    return out


def _price_path(days: np.ndarray, log_fv: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    n = len(log_fv)
    noise = rng.normal(0.0, 0.00015, n)
    tiny = rng.normal(0.0, 0.00003, n)
    decay = np.datetime64(DECAY_START)
    out = np.full(n, np.nan)
    for index in range(n):
        if not np.isfinite(log_fv[index]):
            continue
        if days[index] < decay:
            source = index - 5
            base = log_fv[source] if source >= 0 and np.isfinite(log_fv[source]) else log_fv[index]
            out[index] = base + noise[index]
        else:
            out[index] = log_fv[index] + tiny[index]
    for shock in PERMANENT_SHOCKS:
        day = np.datetime64(shock)
        position = int(np.searchsorted(days, day))
        if position < n:
            out[position:] = out[position:] + 0.008
    return out


def _point(curve_id: str, rate: float, stamp: str) -> dict[str, object]:
    return {
        "curve_id": curve_id,
        "rate": rate,
        "effective_at": stamp,
        "released_at": stamp,
        "observed_at": stamp,
        "ingested_at": stamp,
        "source": "laboratory",
        "version": "1",
    }


def _policy_row(bank: str, horizon: str, expected: float, stamp: str) -> dict[str, object]:
    return {
        "central_bank": bank,
        "horizon": horizon,
        "expected_rate": expected,
        "effective_at": stamp,
        "released_at": stamp,
        "observed_at": stamp,
        "ingested_at": stamp,
        "source": "laboratory",
        "version": "1",
    }


def _risk_row(leg: str, metric: str, spread: float, stamp: str) -> dict[str, object]:
    return {
        "leg": leg,
        "metric": metric,
        "spread": spread,
        "effective_at": stamp,
        "released_at": stamp,
        "observed_at": stamp,
        "ingested_at": stamp,
        "source": "laboratory",
        "version": "1",
    }


def _macro_row(
    *,
    series_id: str,
    region: str,
    category: str,
    period: str,
    actual: float,
    consensus: float,
    previous: float | None,
    revision: int,
    effective: str,
    stamp: str,
) -> dict[str, object]:
    return {
        "series_id": series_id,
        "region": region,
        "category": category,
        "period": period,
        "actual": actual,
        "consensus": consensus,
        "previous_as_known": previous,
        "revision_number": revision,
        "effective_at": effective,
        "released_at": stamp,
        "observed_at": stamp,
        "ingested_at": stamp,
        "source": "laboratory",
        "version": "1",
    }


def _month_starts(start: str, end: str) -> np.ndarray:
    return np.arange(np.datetime64(start), np.datetime64(end), dtype="datetime64[M]").astype("datetime64[D]")


def _release_day(month: np.datetime64) -> np.datetime64:
    candidate = month + np.timedelta64(14, "D")
    if np.is_busday(candidate):
        return candidate
    return np.busday_offset(candidate, 0, roll="forward")
