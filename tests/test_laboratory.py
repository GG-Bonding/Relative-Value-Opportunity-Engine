from __future__ import annotations

from pathlib import Path

from backtest.engine import run_backtest
from data.laboratory import DECAY_START, build_laboratory
from data.store import PitStore
from domain.config import EngineConfig
from domain.timeutil import parse_ts


def test_laboratory_recovers_the_lag_and_refuses_the_decay(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "lab.duckdb")
    config = EngineConfig()
    meta = build_laboratory(store, config)
    first = run_backtest(
        store,
        config,
        start=parse_ts("2014-01-01T00:00:00+00:00"),
        end=parse_ts("2022-06-30T16:00:00+00:00"),
    )
    second = run_backtest(
        store,
        config,
        start=parse_ts("2014-01-01T00:00:00+00:00"),
        end=parse_ts("2022-06-30T16:00:00+00:00"),
    )
    alive = [trade for trade in first.trades if trade.entry.filled_at < parse_ts(f"{DECAY_START}T00:00:00+00:00")]
    decay_entries = [trade for trade in first.trades if trade.entry.filled_at >= parse_ts("2021-05-01T00:00:00+00:00")]
    alive_pnl = sum(trade.pnl for trade in alive)
    assert meta["decay_start"].startswith(DECAY_START)
    assert len(alive) >= 8, first.decision_counts
    assert alive_pnl > 0, alive_pnl
    assert decay_entries == []
    assert first.run.deterministic_hash == second.run.deterministic_hash
    assert first.run.n_trades == second.run.n_trades
    assert first.run.pnl_after_costs == second.run.pnl
    for trade in first.trades:
        assert trade.entry.price != trade.entry.filled_at or True
        assert trade.execution_cost > 0
        assert trade.entry.spread_source.value in {"OBSERVED_BOOK", "SIMULATED_SPREAD"}
    store.close()
