"""Frozen opportunities are scored after the horizon, without editing the opening row."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from data.information import information_event
from data.store import PitStore
from data.surprise import standardized_surprise
from domain.config import EngineConfig
from domain.enums import ExecutionMode
from domain.timeutil import UTC
from forward.cycle import ForwardBook, run_forward
from forward.events import EventInput
from forward.mt5 import Mt5Tick
from forward.outcomes import score_due
from forward.rates import RateSnapshot

ENTRY = datetime(2026, 10, 1, 10, tzinfo=UTC)
FAIR = 0.8515
CONFIG = EngineConfig()


def test_a_short_history_has_no_z_score_and_eight_priors_do() -> None:
    prior = [0.10, -0.20, 0.05, 0.15, -0.10, 0.20, -0.05, 0.12]
    assert standardized_surprise(0.20, prior[:7]) is None
    score = standardized_surprise(0.20, prior)
    assert score is not None
    assert score == standardized_surprise(0.20, prior)


def test_calendar_history_becomes_the_factor(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "z.duckdb")
    prior = [0.10, -0.20, 0.05, 0.15, -0.10, 0.20, -0.05, 0.12]
    for index, raw in enumerate(prior):
        stamp = ENTRY - timedelta(days=index + 1)
        store.insert_records(
            "information_events",
            [
                information_event(
                    event_id=f"prior-{index}",
                    kind="CALENDAR",
                    headline="UK CPI",
                    observed_at=stamp,
                    ingested_at=stamp,
                    source="jin10",
                    version=f"prior-{index}",
                    region="UK",
                    category="CPI",
                    raw_surprise=raw,
                )
            ],
        )
    book = ForwardBook(
        Mt5Tick("EURGBP", ENTRY, 0.8557, 0.8563),
        RateSnapshot(4.2, 2.55, ENTRY, ENTRY),
        [EventInput("uk-cpi", "CALENDAR", "UK CPI", "UK", "CPI", None, ENTRY, raw_surprise=0.02)],
    )
    result = run_forward(store, CONFIG, ExecutionMode.SHADOW, book, ingested_at=ENTRY, fair_value=None)
    expected = standardized_surprise(0.02, prior)
    assert expected is not None
    assert result["event_pressure"] == pytest.approx(-expected)
    store.close()


def test_one_day_mark_scores_hit_convergence_and_stays_discovery(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "out.duckdb")
    opened = run_forward(
        store,
        CONFIG,
        ExecutionMode.PAPER,
        _book(ENTRY, 0.8557, 0.8563),
        ingested_at=ENTRY,
        fair_value=FAIR,
    )
    opening = [
        row
        for row in store.rows("opportunity_journal")
        if row["opportunity_id"] == opened["opportunity_id"] and row["sequence"] == 0
    ][0]
    early = score_due(store, Mt5Tick("EURGBP", ENTRY + timedelta(hours=1), 0.8540, 0.8542), ENTRY, CONFIG)
    assert early["n_samples"] == 0
    assert early["lifecycle"] == "DISCOVERY"
    later = ENTRY + timedelta(days=1, hours=1)
    snapshot = score_due(store, Mt5Tick("EURGBP", later, 0.8520, 0.8522), later, CONFIG)
    marks = store.rows("forward_outcomes")
    day = next(row for row in marks if row["horizon"] == "1d")
    assert day["hit"] is True
    assert day["converged"] is True
    assert day["after_cost_pips"] > 0
    assert snapshot["n_samples"] == 1
    assert snapshot["hit_rate"] == 1.0
    assert snapshot["convergence_rate"] == 1.0
    assert snapshot["lifecycle"] == "DISCOVERY"
    assert snapshot["after_cost_pnl"] == day["after_cost_pips"]
    still = [
        row
        for row in store.rows("opportunity_journal")
        if row["opportunity_id"] == opened["opportunity_id"] and row["sequence"] == 0
    ][0]
    assert still["mid"] == opening["mid"]
    assert still["fair_value"] == opening["fair_value"]
    store.close()


def _book(moment: datetime, bid: float, ask: float) -> ForwardBook:
    return ForwardBook(
        Mt5Tick("EURGBP", moment, bid, ask),
        RateSnapshot(4.2, 2.55, moment, moment),
        [EventInput("uk-cpi", "CALENDAR", "UK CPI", "UK", "CPI", 0.7, moment)],
    )
