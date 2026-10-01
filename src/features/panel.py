"""Build a daily EURGBP panel using only observations at or before as_of.

Every rolling statistic looks backward. A null input stays null.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import polars as pl

from data.store import PitStore
from data.surprise import with_surprise
from domain.config import EngineConfig
from domain.timeutil import dump_ts, ensure_utc
from features.columns import (
    GROWTH_CATEGORIES,
    INFLATION_CATEGORIES,
    RISK_EUR,
    RISK_GBP,
)
from features.series import (
    age_days,
    information_ema,
    realized_vol,
    rolling_change,
    rolling_log_return,
    rolling_percentile,
    rolling_slope,
    rolling_zscore,
)

_EMPTY = pl.DataFrame({"observed_at": [], "value": []})


def build_panel(
    store: PitStore,
    as_of: datetime,
    config: EngineConfig,
    calendar: list[str] | None = None,
) -> pl.DataFrame:
    """Daily bars and relative factors visible at `as_of`.

    `calendar` lets the laboratory price a path before any market rows exist.
    An empty calendar and an empty market both return an empty frame.
    """
    as_of = ensure_utc(as_of)
    if calendar is None:
        market = store.history("market_observations", as_of).filter(
            (pl.col("pair") == "EURGBP") & (pl.col("bar_size") == "1d")
        )
        if market.is_empty():
            return pl.DataFrame()
        market = (
            market.sort(["observed_at", "record_id"])
            .unique(subset=["observed_at"], keep="last", maintain_order=True)
            .sort("observed_at")
        )
        frame = market.select(["observed_at", "mid", "bid", "ask", "version"])
    else:
        if not calendar:
            return pl.DataFrame()
        frame = pl.DataFrame(
            {
                "observed_at": calendar,
                "mid": [None] * len(calendar),
                "bid": [None] * len(calendar),
                "ask": [None] * len(calendar),
                "version": ["calendar"] * len(calendar),
            }
        )
    rates = store.history("rate_observations", as_of)
    policy = store.history("policy_expectations", as_of)
    risk = store.history("risk_observations", as_of)
    positioning = store.history("positioning_observations", as_of)
    options = store.history("option_observations", as_of)
    releases = with_surprise(
        store.history("macro_releases", as_of),
        min_history=config.features.surprise_min_history,
    )
    consensus = store.history("consensus_snapshots", as_of)

    frame = _join_curve(frame, rates, "UK2Y", "uk2y")
    frame = _join_curve(frame, rates, "DE2Y", "de2y")
    frame = _join_curve(frame, rates, "UK_FWD_1M", "uk_fwd_1m")
    frame = _join_curve(frame, rates, "DE_FWD_1M", "de_fwd_1m")
    frame = _join_curve(frame, rates, "UK_FWD_3M", "uk_fwd_3m")
    frame = _join_curve(frame, rates, "DE_FWD_3M", "de_fwd_3m")
    for bank, horizon, alias in _policy_aliases():
        frame = _join_policy(frame, policy, bank, horizon, alias)
    for leg, metric, alias in _risk_aliases():
        frame = _join_risk(frame, risk, leg, metric, alias)
    frame = _join_metric(frame, positioning, "CFTC_NET", "cftc_net")
    frame = _join_option(frame, options, "risk_reversal")
    frame = _join_option(frame, options, "implied_vol")
    frame = _join_option(frame, options, "open_interest")

    day_index = _ordinals(frame["observed_at"].to_list())
    growth_index, growth_diff, growth_age = _macro_state(
        day_index, releases, GROWTH_CATEGORIES, config.features.info_half_life_days
    )
    inflation_index, inflation_diff, inflation_age = _macro_state(
        day_index, releases, INFLATION_CATEGORIES, config.features.info_half_life_days
    )
    expected_growth = _consensus_gap(day_index, consensus, GROWTH_CATEGORIES)
    expected_inflation = _consensus_gap(day_index, consensus, INFLATION_CATEGORIES)
    event_age, event_surprise = _event_state(day_index, releases)
    rate_age = _series_age(day_index, rates.filter(pl.col("curve_id") == "UK2Y"))
    risk_age = _series_age(day_index, risk)

    arrays = frame.to_dict(as_series=False)
    uk2y = _num(arrays["uk2y"])
    de2y = _num(arrays["de2y"])
    rate_level = _subtract(uk2y, de2y)
    boe_1m = _num(arrays["boe_1m"])
    ecb_1m = _num(arrays["ecb_1m"])
    boe_3m = _num(arrays["boe_3m"])
    ecb_3m = _num(arrays["ecb_3m"])
    boe_6m = _num(arrays["boe_6m"])
    ecb_6m = _num(arrays["ecb_6m"])
    boe_12m = _num(arrays["boe_12m"])
    ecb_12m = _num(arrays["ecb_12m"])
    policy_1m = _subtract(boe_1m, ecb_1m)
    policy_3m = _subtract(boe_3m, ecb_3m)
    policy_6m = _subtract(boe_6m, ecb_6m)
    policy_12m = _subtract(boe_12m, ecb_12m)
    curve_slope = _subtract(_subtract(boe_12m, boe_1m), _subtract(ecb_12m, ecb_1m))
    mid = _num(arrays["mid"])
    bid = _num(arrays["bid"])
    ask = _num(arrays["ask"])
    eur_risk = _row_mean([_num(arrays[alias]) for _leg, metric, alias in _risk_aliases() if _leg == "EUR"])
    gbp_risk = _row_mean([_num(arrays[alias]) for _leg, metric, alias in _risk_aliases() if _leg == "GBP"])
    risk_diff = _subtract(eur_risk, gbp_risk)

    built = pl.DataFrame(
        {
            "observed_at": arrays["observed_at"],
            "mid": mid,
            "bid": bid,
            "ask": ask,
            "log_price": np.where(np.isfinite(mid) & (mid > 0), np.log(mid), np.nan),
            "spread": _subtract(ask, bid),
            "rate_level": rate_level,
            "rate_change_1d": rolling_change(rate_level, 1),
            "rate_change_5d": rolling_change(rate_level, 5),
            "rate_change_20d": rolling_change(rate_level, 20),
            "rate_slope_5d": rolling_slope(rate_level, 5),
            "rate_slope_20d": rolling_slope(rate_level, 20),
            "rate_zscore_60d": rolling_zscore(rate_level, 60),
            "rate_zscore_252d": rolling_zscore(rate_level, 252),
            "rate_percentile_252d": rolling_percentile(rate_level, 252),
            "policy_diff_1m": policy_1m,
            "policy_diff_3m": policy_3m,
            "policy_diff_6m": policy_6m,
            "policy_diff_12m": policy_12m,
            "policy_repricing_1d": rolling_change(policy_3m, 1),
            "policy_repricing_5d": rolling_change(policy_3m, 5),
            "curve_slope_diff": curve_slope,
            "growth_diff": growth_diff,
            "growth_diff_index": growth_index,
            "growth_age_days": growth_age,
            "inflation_diff": inflation_diff,
            "inflation_age_days": inflation_age,
            "risk_premium_diff": risk_diff,
            "risk_age_days": risk_age,
            "rate_age_days": rate_age,
            "cftc_net": _num(arrays["cftc_net"]),
            "risk_reversal": _num(arrays["risk_reversal"]),
            "implied_vol": _num(arrays["implied_vol"]),
            "open_interest": _num(arrays["open_interest"]),
            "momentum_5d": rolling_log_return(mid, 5),
            "momentum_20d": rolling_log_return(mid, 20),
            "momentum_60d": rolling_log_return(mid, 60),
            "realized_vol_20d": realized_vol(mid, 20),
            "return_1d": rolling_log_return(mid, 1),
            "expected_rate_diff_1m": _subtract(_num(arrays["uk_fwd_1m"]), _num(arrays["de_fwd_1m"])),
            "expected_rate_diff_3m": _subtract(_num(arrays["uk_fwd_3m"]), _num(arrays["de_fwd_3m"])),
            "expected_policy_diff_3m": policy_3m,
            "expected_policy_diff_6m": policy_6m,
            "expected_growth_diff": expected_growth,
            "expected_inflation_diff": expected_inflation,
            "event_age_days": event_age,
            "last_event_abs_surprise": event_surprise,
        }
    )
    return built


def _policy_aliases() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for bank, prefix in (("BOE", "boe"), ("ECB", "ecb")):
        for horizon in ("1M", "3M", "6M", "12M"):
            rows.append((bank, horizon, f"{prefix}_{horizon.lower()}"))
    return rows


def _risk_aliases() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for metric in RISK_EUR:
        rows.append(("EUR", metric, f"eur_{metric.lower()}"))
    for metric in RISK_GBP:
        rows.append(("GBP", metric, f"gbp_{metric.lower()}"))
    return rows


def _join_curve(frame: pl.DataFrame, rates: pl.DataFrame, curve_id: str, alias: str) -> pl.DataFrame:
    if rates.is_empty():
        return frame.with_columns(pl.lit(None, dtype=pl.Float64).alias(alias))
    subset = rates.filter(pl.col("curve_id") == curve_id).select(
        ["observed_at", "record_id", pl.col("rate").alias(alias)]
    )
    return _asof(frame, subset, alias)


def _join_policy(frame: pl.DataFrame, policy: pl.DataFrame, bank: str, horizon: str, alias: str) -> pl.DataFrame:
    if policy.is_empty():
        return frame.with_columns(pl.lit(None, dtype=pl.Float64).alias(alias))
    subset = policy.filter((pl.col("central_bank") == bank) & (pl.col("horizon") == horizon)).select(
        ["observed_at", "record_id", pl.col("expected_rate").alias(alias)]
    )
    return _asof(frame, subset, alias)


def _join_risk(frame: pl.DataFrame, risk: pl.DataFrame, leg: str, metric: str, alias: str) -> pl.DataFrame:
    if risk.is_empty():
        return frame.with_columns(pl.lit(None, dtype=pl.Float64).alias(alias))
    subset = risk.filter((pl.col("leg") == leg) & (pl.col("metric") == metric)).select(
        ["observed_at", "record_id", pl.col("spread").alias(alias)]
    )
    return _asof(frame, subset, alias)


def _join_metric(frame: pl.DataFrame, table: pl.DataFrame, metric: str, alias: str) -> pl.DataFrame:
    if table.is_empty():
        return frame.with_columns(pl.lit(None, dtype=pl.Float64).alias(alias))
    subset = table.filter(pl.col("metric") == metric).select(["observed_at", "record_id", pl.col("value").alias(alias)])
    return _asof(frame, subset, alias)


def _join_option(frame: pl.DataFrame, options: pl.DataFrame, column: str) -> pl.DataFrame:
    if options.is_empty() or column not in options.columns:
        return frame.with_columns(pl.lit(None, dtype=pl.Float64).alias(column))
    subset = options.select(["observed_at", "record_id", column])
    return _asof(frame, subset, column)


def _asof(frame: pl.DataFrame, subset: pl.DataFrame, alias: str) -> pl.DataFrame:
    if subset.is_empty():
        return frame.with_columns(pl.lit(None, dtype=pl.Float64).alias(alias))
    sort_on = ["observed_at"] + (["record_id"] if "record_id" in subset.columns else [])
    piece = (
        subset.sort(sort_on)
        .unique(subset=["observed_at"], keep="last", maintain_order=True)
        .select(["observed_at", alias])
    )
    return frame.join_asof(piece, on="observed_at", strategy="backward")


def _ordinals(values: list[str]) -> np.ndarray:
    days = np.array([value[:10] for value in values], dtype="datetime64[D]").astype(np.int64)
    return days


def _macro_state(
    calendar: np.ndarray,
    releases: pl.DataFrame,
    categories: tuple[str, ...],
    half_life: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    index_parts: list[np.ndarray] = []
    diff_parts: list[np.ndarray] = []
    age_parts: list[np.ndarray] = []
    for category in categories:
        uk = _events(releases, "UK", category)
        ez = _events(releases, "EZ", category)
        uk_ema = information_ema(calendar, uk[0], uk[1], half_life)
        ez_ema = information_ema(calendar, ez[0], ez[1], half_life)
        uk_last = _carry(calendar, uk[0], uk[1])
        ez_last = _carry(calendar, ez[0], ez[1])
        index_parts.append(_subtract(uk_ema, ez_ema))
        diff_parts.append(_subtract(uk_last, ez_last))
        age_parts.append(np.fmin(age_days(calendar, uk[0]), age_days(calendar, ez[0])))
    return _nanmean(index_parts), _nanmean(diff_parts), _nanmin(age_parts)


def _consensus_gap(calendar: np.ndarray, consensus: pl.DataFrame, categories: tuple[str, ...]) -> np.ndarray:
    if consensus.is_empty():
        return np.full(calendar.shape, np.nan)
    parts: list[np.ndarray] = []
    for category in categories:
        uk = _consensus_events(consensus, "UK", category)
        ez = _consensus_events(consensus, "EZ", category)
        parts.append(_subtract(_carry(calendar, uk[0], uk[1]), _carry(calendar, ez[0], ez[1])))
    return _nanmean(parts)


def _event_state(calendar: np.ndarray, releases: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    if releases.is_empty() or "surprise" not in releases.columns:
        return np.full(calendar.shape, np.nan), np.full(calendar.shape, np.nan)
    usable = releases.filter(pl.col("surprise").is_not_null())
    if usable.is_empty():
        return np.full(calendar.shape, np.nan), np.full(calendar.shape, np.nan)
    ordinals = _ordinals(usable["observed_at"].to_list())
    surprises = np.abs(np.asarray(usable["surprise"].to_list(), dtype=float))
    return age_days(calendar, ordinals), _carry(calendar, ordinals, surprises)


def _events(releases: pl.DataFrame, region: str, category: str) -> tuple[np.ndarray, np.ndarray]:
    if releases.is_empty() or "surprise" not in releases.columns:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    subset = releases.filter(
        (pl.col("region") == region) & (pl.col("category") == category) & pl.col("surprise").is_not_null()
    )
    if subset.is_empty():
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    return _ordinals(subset["observed_at"].to_list()), np.asarray(subset["surprise"].to_list(), dtype=float)


def _consensus_events(consensus: pl.DataFrame, region: str, category: str) -> tuple[np.ndarray, np.ndarray]:
    subset = consensus.filter((pl.col("region") == region) & (pl.col("category") == category))
    if subset.is_empty():
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    return (
        _ordinals(subset["observed_at"].to_list()),
        np.asarray(subset["expected_value"].to_list(), dtype=float),
    )


def _series_age(calendar: np.ndarray, frame: pl.DataFrame) -> np.ndarray:
    if frame.is_empty():
        return np.full(calendar.shape, np.nan)
    return age_days(calendar, _ordinals(frame["observed_at"].to_list()))


def _carry(calendar: np.ndarray, event_ord: np.ndarray, values: np.ndarray) -> np.ndarray:
    out = np.full(calendar.shape, np.nan)
    if len(event_ord) == 0:
        return out
    order = np.argsort(event_ord, kind="mergesort")
    event_ord = event_ord[order]
    values = values[order]
    positions = np.searchsorted(event_ord, calendar, side="right") - 1
    valid = positions >= 0
    picked = values[np.clip(positions, 0, len(values) - 1)]
    finite = valid & np.isfinite(picked)
    out[finite] = picked[finite]
    return out


def _num(values: list[object]) -> np.ndarray:
    converted: list[float] = []
    for value in values:
        if isinstance(value, int | float):
            converted.append(float(value))
        else:
            converted.append(float("nan"))
    return np.asarray(converted, dtype=float)


def _subtract(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    out = np.full(left.shape, np.nan)
    good = np.isfinite(left) & np.isfinite(right)
    out[good] = left[good] - right[good]
    return out


def _row_mean(columns: list[np.ndarray]) -> np.ndarray:
    if not columns:
        raise ValueError("no columns")
    stack = np.vstack(columns)
    count = np.isfinite(stack).sum(axis=0)
    total = np.nansum(stack, axis=0)
    out = np.full(stack.shape[1], np.nan)
    good = count == stack.shape[0]
    out[good] = total[good] / count[good]
    return out


def _nanmean(parts: list[np.ndarray]) -> np.ndarray:
    stack = np.vstack(parts)
    count = np.isfinite(stack).sum(axis=0)
    total = np.nansum(stack, axis=0)
    out = np.full(stack.shape[1], np.nan)
    good = count > 0
    out[good] = total[good] / count[good]
    return out


def _nanmin(parts: list[np.ndarray]) -> np.ndarray:
    stack = np.vstack(parts)
    masked = np.where(np.isfinite(stack), stack, np.inf)
    lowest = masked.min(axis=0)
    out = np.full(lowest.shape, np.nan)
    good = np.isfinite(lowest) & (lowest < np.inf)
    out[good] = lowest[good]
    return out


def panel_as_of_text(as_of: datetime) -> str:
    return dump_ts(ensure_utc(as_of))
