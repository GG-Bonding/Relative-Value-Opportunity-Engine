"""The only object a model is allowed to consume for a single decision time."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import polars as pl

from data.store import TABLES, PitStore
from domain.config import EngineConfig
from domain.hashing import stable_hash
from domain.models import ExpectationState, FeatureSnapshot
from domain.timeutil import ensure_utc, parse_ts
from features.columns import (
    EXPECTATION_FIELDS,
    GROWTH_FIELDS,
    INFLATION_FIELDS,
    MOMENTUM_FIELDS,
    POLICY_FIELDS,
    POSITIONING_FIELDS,
    RATE_FIELDS,
    RISK_FIELDS,
)
from features.panel import build_panel


def build_snapshot(store: PitStore, as_of: datetime, config: EngineConfig) -> FeatureSnapshot:
    as_of = ensure_utc(as_of)
    panel = build_panel(store, as_of, config)
    if panel.is_empty():
        raise ValueError("no EURGBP daily bar is observable at this as_of")
    row = panel.row(-1, named=True)
    bar_time = parse_ts(str(row["observed_at"]))
    extra_days = max(0.0, (as_of - bar_time).total_seconds() / 86400.0)
    rate = _block(
        row,
        {
            "level": "rate_level",
            "change_1d": "rate_change_1d",
            "change_5d": "rate_change_5d",
            "change_20d": "rate_change_20d",
            "slope_5d": "rate_slope_5d",
            "slope_20d": "rate_slope_20d",
            "zscore_60d": "rate_zscore_60d",
            "zscore_252d": "rate_zscore_252d",
            "percentile_252d": "rate_percentile_252d",
        },
    )
    policy = _block(
        row,
        {
            "policy_diff_1m": "policy_diff_1m",
            "policy_diff_3m": "policy_diff_3m",
            "policy_diff_6m": "policy_diff_6m",
            "policy_diff_12m": "policy_diff_12m",
            "repricing_1d": "policy_repricing_1d",
            "repricing_5d": "policy_repricing_5d",
            "curve_slope_diff": "curve_slope_diff",
        },
    )
    growth = _block(row, {"growth_diff": "growth_diff", "growth_diff_index": "growth_diff_index"})
    inflation = _block(row, {"inflation_diff": "inflation_diff"})
    risk = _block(row, {"risk_premium_diff": "risk_premium_diff"})
    positioning = _block(
        row,
        {
            "cftc_net": "cftc_net",
            "risk_reversal": "risk_reversal",
            "implied_vol": "implied_vol",
            "open_interest": "open_interest",
        },
    )
    momentum = _block(
        row,
        {
            "momentum_5d": "momentum_5d",
            "momentum_20d": "momentum_20d",
            "momentum_60d": "momentum_60d",
            "realized_vol_20d": "realized_vol_20d",
        },
    )
    expectations = _block(
        row,
        {
            "expected_rate_diff_1m": "expected_rate_diff_1m",
            "expected_rate_diff_3m": "expected_rate_diff_3m",
            "expected_policy_diff_3m": "expected_policy_diff_3m",
            "expected_policy_diff_6m": "expected_policy_diff_6m",
            "expected_growth_diff": "expected_growth_diff",
            "expected_inflation_diff": "expected_inflation_diff",
        },
    )
    market = _block(row, {"mid": "mid", "bid": "bid", "ask": "ask", "spread": "spread"})
    missing = _missing(
        {
            "rate": rate,
            "policy": policy,
            "growth": growth,
            "inflation": inflation,
            "risk": risk,
            "positioning": positioning,
            "momentum": momentum,
            "expectations": expectations,
            "market": market,
        }
    )
    stale = _stale(row, extra_days, config)
    versions = _source_versions(store, as_of)
    snapshot_id = stable_hash(
        {
            "as_of": dump_row_time(row),
            "data": store.snapshot_hash(as_of),
            "rate": rate,
            "policy": policy,
            "growth": growth,
            "inflation": inflation,
            "risk": risk,
        }
    )
    _ = (RATE_FIELDS, POLICY_FIELDS, GROWTH_FIELDS, INFLATION_FIELDS, RISK_FIELDS)
    _ = (POSITIONING_FIELDS, MOMENTUM_FIELDS, EXPECTATION_FIELDS)
    return FeatureSnapshot(
        as_of=as_of,
        pair="EURGBP",
        data_snapshot_id=snapshot_id,
        rate=rate,
        policy=policy,
        growth=growth,
        inflation=inflation,
        risk=risk,
        positioning=positioning,
        momentum=momentum,
        expectations=expectations,
        market=market,
        missing_fields=missing,
        stale_fields=stale,
        source_versions=versions,
    )


def expectation_state(snapshot: FeatureSnapshot) -> ExpectationState:
    values = snapshot.expectations
    return ExpectationState(
        as_of=snapshot.as_of,
        pair=snapshot.pair,
        expected_rate_diff_1m=values.get("expected_rate_diff_1m"),
        expected_rate_diff_3m=values.get("expected_rate_diff_3m"),
        expected_policy_diff_3m=values.get("expected_policy_diff_3m"),
        expected_policy_diff_6m=values.get("expected_policy_diff_6m"),
        expected_growth_diff=values.get("expected_growth_diff"),
        expected_inflation_diff=values.get("expected_inflation_diff"),
        sources={key: "feature_snapshot" for key, value in values.items() if value is not None},
    )


def _block(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, float | None]:
    output: dict[str, float | None] = {}
    for name, column in mapping.items():
        value = row.get(column)
        if value is None:
            output[name] = None
        else:
            number = float(value)
            output[name] = None if number != number else number
    return output


def _missing(blocks: dict[str, dict[str, float | None]]) -> list[str]:
    names: list[str] = []
    for block, values in blocks.items():
        for name, value in values.items():
            if value is None:
                names.append(f"{block}.{name}")
    return names


def _stale(row: dict[str, Any], extra_days: float, config: EngineConfig) -> list[str]:
    stale: list[str] = []
    checks = {
        "rate": ("rate_age_days", config.features.rate_stale_days),
        "growth": ("growth_age_days", config.features.macro_stale_days),
        "inflation": ("inflation_age_days", config.features.macro_stale_days),
        "risk": ("risk_age_days", config.features.rate_stale_days),
        "market": (None, config.features.market_stale_days),
    }
    for name, (column, limit) in checks.items():
        age: float | None
        if column is None:
            age = extra_days
        else:
            raw = row.get(column)
            age = None if not isinstance(raw, int | float) else float(raw) + extra_days
        if age is None or age > limit:
            stale.append(name)
    return stale


def _source_versions(store: PitStore, as_of: datetime) -> dict[str, str]:
    versions: dict[str, str] = {}
    for table in TABLES:
        frame = store.history(table, as_of)
        if frame.is_empty() or "version" not in frame.columns:
            continue
        versions[table] = max(str(value) for value in frame["version"].to_list())
    return versions


def dump_row_time(row: dict[str, Any]) -> str:
    return str(row["observed_at"])


def latest_row(panel: pl.DataFrame) -> dict[str, Any]:
    if panel.is_empty():
        raise ValueError("empty panel")
    return panel.row(-1, named=True)
