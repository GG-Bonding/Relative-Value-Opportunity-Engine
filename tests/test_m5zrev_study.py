"""A short tape can be reported without becoming a market conclusion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from m5zrev.backtest import Trade
from m5zrev.experiments import Window, scale_extra_cost
from m5zrev.histdata import research_spec
from m5zrev.params import Params
from m5zrev.study import _direction_block, _Run, run_study
from tests.test_m5zrev_backtest import _bars


def test_short_tape_is_not_a_pass(tmp_path: Path) -> None:
    output = tmp_path
    bars = _bars(datetime(2024, 1, 8, 8, 0, tzinfo=UTC), 40)
    label = run_study(
        bars,
        None,
        research_spec(),
        (),
        Params(),
        output,
        study_spread_pips=1.0,
        notes="unit probe",
    )
    verdict = (output / "verdict.md").read_text(encoding="utf-8")
    assert label == "INCONCLUSIVE"
    assert "结论：**INCONCLUSIVE**" in verdict
    assert "结论：**PASS**" not in verdict
    assert "未运行" in (output / "sensitivity.svg").read_text(encoding="utf-8")
    assert (output / "trades.csv").read_text(encoding="utf-8").startswith("direction,")


def _trade(when: datetime) -> Trade:
    return Trade(
        "LONG",
        when,
        when,
        when + timedelta(minutes=20),
        0.85,
        0.851,
        0.849,
        0.852,
        0.01,
        1.0,
        1.0,
        0.2,
        20.0,
        "TIME",
        False,
        -1.0,
        1.0,
        1.0,
        -2.0,
        -2.0,
        0.0,
        "bid_ohlc",
    )


def test_direction_block_ignores_trades_outside_the_window() -> None:
    early = datetime(2024, 1, 2, 10, tzinfo=UTC)
    late = datetime(2024, 6, 3, 10, tzinfo=UTC)

    class _Cache:
        def run(self, params: Params, spread: float) -> _Run:
            return _Run((_trade(early), _trade(late)), (), "bid_ohlc", ())

    window = Window("holdout", datetime(2024, 6, 1, tzinfo=UTC), datetime(2024, 7, 1, tzinfo=UTC))
    text = _direction_block(_Cache(), Params(), 1.0, window)  # type: ignore[arg-type]
    assert text.count("成交 1") == 3
    assert "成交 2" not in text


def test_cost_factor_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="cost factor"):
        scale_extra_cost(Params(), research_spec(), 0.5)
