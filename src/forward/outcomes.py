"""Score a frozen opportunity after the fact, then update alpha health.

Each opening row is marked at the first quote that is already at least one
hour, one day, three days, and five days later. The opening price and fair
value are not rewritten. A short history stays DISCOVERY.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from alpha.lifecycle import classify_alpha, information_coefficient
from data.store import PitStore
from domain.config import EngineConfig
from domain.enums import AlphaLifecycle
from domain.errors import DataValidationError
from domain.timeutil import dump_ts, ensure_utc, parse_ts
from forward.mt5 import Mt5Tick

HORIZONS: tuple[tuple[str, timedelta], ...] = (
    ("1h", timedelta(hours=1)),
    ("1d", timedelta(days=1)),
    ("3d", timedelta(days=3)),
    ("5d", timedelta(days=5)),
)
_HORIZON_MINUTES = {"1h": 60.0, "1d": 1440.0, "3d": 4320.0, "5d": 7200.0}


def score_due(
    store: PitStore,
    tick: Mt5Tick | None,
    ingested_at: datetime,
    config: EngineConfig,
) -> dict[str, Any]:
    """Append marks that are due. Return the alpha snapshot from the one-day sample."""
    ingested = ensure_utc(ingested_at)
    if tick is not None:
        _append_due(store, tick, ingested, config)
    snapshot = alpha_snapshot(store, config)
    _store_alpha(store, snapshot, ingested)
    return snapshot


def current_lifecycle(store: PitStore, config: EngineConfig) -> AlphaLifecycle:
    snapshot = alpha_snapshot(store, config)
    return AlphaLifecycle(str(snapshot["lifecycle"]))


def alpha_snapshot(store: PitStore, config: EngineConfig) -> dict[str, Any]:
    """One-day marks are the sample. Fewer than 63 stays DISCOVERY; fewer than 252 stays VALIDATING."""
    marks = [row for row in store.rows("forward_outcomes") if row["horizon"] == "1d"]
    marks.sort(key=lambda row: (str(row["entry_observed_at"]), str(row["opportunity_id"])))
    n_samples = len(marks)
    hit_rate = _rate(marks, "hit")
    convergence_rate = _rate(marks, "converged")
    after_cost = None if not marks else float(sum(float(row["after_cost_pips"]) for row in marks))
    ic_21 = _window_ic(marks, 21)
    ic_63 = _window_ic(marks, 63)
    ic_252 = _window_ic(marks, 252)
    lag_21 = _window_lag(store, marks[-21:])
    lag_63 = _window_lag(store, marks[-63:])
    lag_252 = _window_lag(store, marks)
    lifecycle, reasons = classify_alpha(
        ic_252,
        ic_63,
        ic_21,
        n_samples,
        lag_252,
        lag_63,
        lag_21,
        config,
    )
    return {
        "lifecycle": lifecycle.value,
        "n_samples": n_samples,
        "hit_rate": hit_rate,
        "convergence_rate": convergence_rate,
        "after_cost_pnl": after_cost,
        "ic_21": ic_21,
        "ic_63": ic_63,
        "ic_252": ic_252,
        "reasons": reasons,
    }


def _store_alpha(store: PitStore, snapshot: dict[str, Any], as_of: datetime) -> None:
    rows = store.rows("forward_alpha")
    if rows:
        last = rows[-1]
        if int(last["n_samples"]) == int(snapshot["n_samples"]) and last["lifecycle"] == snapshot["lifecycle"]:
            return
    try:
        store.insert_records(
            "forward_alpha",
            [
                {
                    "observed_at": dump_ts(as_of),
                    "lifecycle": snapshot["lifecycle"],
                    "n_samples": snapshot["n_samples"],
                    "hit_rate": snapshot["hit_rate"],
                    "convergence_rate": snapshot["convergence_rate"],
                    "after_cost_pnl": snapshot["after_cost_pnl"],
                    "ic_21": snapshot["ic_21"],
                    "ic_63": snapshot["ic_63"],
                    "ic_252": snapshot["ic_252"],
                    "reasons": json.dumps(snapshot["reasons"], ensure_ascii=False),
                }
            ],
        )
    except DataValidationError:
        return


def write_alpha(path: str, snapshot: dict[str, Any], as_of: datetime) -> None:
    """Append the snapshot when the evidence changed. The previous line stays."""
    from pathlib import Path

    target = Path(path).with_name("alpha.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    stamped = {**snapshot, "as_of": dump_ts(ensure_utc(as_of))}
    if target.exists():
        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            try:
                previous = json.loads(lines[-1])
            except json.JSONDecodeError:
                previous = None
            if isinstance(previous, dict) and _same_evidence(previous, stamped):
                return
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stamped, ensure_ascii=False, sort_keys=True) + "\n")


def _append_due(store: PitStore, tick: Mt5Tick, ingested: datetime, config: EngineConfig) -> None:
    seen = {(str(row["opportunity_id"]), str(row["horizon"])) for row in store.rows("forward_outcomes")}
    pip = config.pip
    for opening in _openings(store):
        entry_at = parse_ts(str(opening["observed_at"]))
        for name, delay in HORIZONS:
            identity = (str(opening["opportunity_id"]), name)
            if identity in seen or tick.time < entry_at + delay:
                continue
            row = _mark(opening, name, tick, ingested, pip)
            store.insert_records("forward_outcomes", [row])
            seen.add(identity)


def _openings(store: PitStore) -> list[dict[str, Any]]:
    openings: list[dict[str, Any]] = []
    for row in store.rows("opportunity_journal"):
        if int(row["sequence"]) != 0:
            continue
        if row.get("direction") not in {"LONG", "SHORT"}:
            continue
        if row.get("mid") is None or row.get("fair_value") is None:
            continue
        if row.get("bid") is None or row.get("ask") is None:
            continue
        openings.append(row)
    return openings


def _mark(
    opening: dict[str, Any],
    horizon: str,
    tick: Mt5Tick,
    ingested: datetime,
    pip: float,
) -> dict[str, Any]:
    direction = str(opening["direction"])
    entry_mid = float(opening["mid"])
    entry_bid = float(opening["bid"])
    entry_ask = float(opening["ask"])
    fair_value = float(opening["fair_value"])
    gap_entry = (entry_mid - fair_value) / pip
    gap_now = (tick.mid - fair_value) / pip
    if direction == "LONG":
        gross = (tick.mid - entry_mid) / pip
        after_cost = (tick.bid - entry_ask) / pip
    else:
        gross = (entry_mid - tick.mid) / pip
        after_cost = (entry_bid - tick.ask) / pip
    return {
        "opportunity_id": str(opening["opportunity_id"]),
        "horizon": horizon,
        "observed_at": dump_ts(tick.time),
        "ingested_at": dump_ts(ingested),
        "direction": direction,
        "gross_pips": gross,
        "after_cost_pips": after_cost,
        "gap_entry_pips": gap_entry,
        "gap_now_pips": gap_now,
        "converged": abs(gap_now) < abs(gap_entry),
        "hit": gross > 0,
        "entry_observed_at": str(opening["observed_at"]),
    }


def _window_ic(marks: list[dict[str, Any]], window: int) -> float | None:
    sample = marks[-window:]
    if len(sample) < 20:
        return None
    signal = np.asarray([1.0 if row["direction"] == "LONG" else -1.0 for row in sample], dtype=float)
    realized = np.asarray([float(row["after_cost_pips"]) for row in sample], dtype=float)
    return information_coefficient(signal, realized)


def _window_lag(store: PitStore, marks: list[dict[str, Any]]) -> float | None:
    if not marks:
        return None
    by_opportunity: dict[str, list[dict[str, Any]]] = {}
    for row in store.rows("forward_outcomes"):
        by_opportunity.setdefault(str(row["opportunity_id"]), []).append(row)
    lags: list[float] = []
    for mark in marks:
        rows = by_opportunity.get(str(mark["opportunity_id"]), [])
        due = [row for row in rows if row.get("converged")]
        if not due:
            continue
        earliest = min(due, key=lambda row: _HORIZON_MINUTES.get(str(row["horizon"]), 10**9))
        minutes = _HORIZON_MINUTES.get(str(earliest["horizon"]))
        if minutes is not None:
            lags.append(minutes)
    if not lags:
        return None
    return float(np.median(np.asarray(lags, dtype=float)))


def _rate(marks: list[dict[str, Any]], key: str) -> float | None:
    if not marks:
        return None
    return float(sum(1.0 if row[key] else 0.0 for row in marks) / len(marks))


def _same_evidence(previous: dict[str, Any], stamped: dict[str, Any]) -> bool:
    keys = (
        "lifecycle",
        "n_samples",
        "hit_rate",
        "convergence_rate",
        "after_cost_pnl",
        "ic_21",
        "ic_63",
        "ic_252",
    )
    return all(previous.get(key) == stamped.get(key) for key in keys)
