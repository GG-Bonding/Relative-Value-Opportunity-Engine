from __future__ import annotations

import math
from pathlib import Path

import pytest

from data.store import PitStore
from domain.config import EngineConfig
from domain.models import MarketObservation, RateObservation
from features.panel import build_panel
from features.snapshot import build_snapshot
from tests.conftest import ts


def _stamp(day: int, hour: int = 16) -> str:
    return f"2024-01-{day:02d}T{hour:02d}:00:00+00:00"


def _prov(when: str) -> dict[str, object]:
    moment = ts(when)
    return {
        "effective_at": moment,
        "released_at": moment,
        "observed_at": moment,
        "ingested_at": moment,
        "source": "fixture",
        "version": "1",
    }


def test_rate_factor_is_the_relative_change_and_missing_positioning_is_not_zero(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "factors.duckdb")
    for day, uk, de, price in (
        (2, 4.00, 3.00, 0.8600),
        (3, 4.10, 3.00, 0.8610),
        (4, 4.30, 3.00, 0.8590),
    ):
        stamp = _stamp(day)
        store.append_rate(RateObservation(curve_id="UK2Y", rate=uk, **_prov(stamp)))
        store.append_rate(RateObservation(curve_id="DE2Y", rate=de, **_prov(stamp)))
        store.append_market(
            MarketObservation(
                instrument="EURGBP",
                pair="EURGBP",
                bar_size="1d",
                bid=price - 0.00006,
                ask=price + 0.00006,
                mid=price,
                **_prov(stamp),
            )
        )
    config = EngineConfig()
    panel = build_panel(store, ts("2024-01-04T16:00:00+00:00"), config)
    last = panel.row(-1, named=True)
    previous = panel.row(-2, named=True)
    assert last["rate_level"] == pytest.approx(1.30)
    assert previous["rate_level"] == pytest.approx(1.10)
    assert last["rate_change_1d"] == pytest.approx(0.20)
    zscore = last["rate_zscore_252d"]
    assert zscore is None or (isinstance(zscore, float) and math.isnan(zscore))
    snapshot = build_snapshot(store, ts("2024-01-04T16:00:00+00:00"), config)
    assert snapshot.rate["zscore_252d"] is None
    assert snapshot.positioning["cftc_net"] is None
    assert "positioning.cftc_net" in snapshot.missing_fields
    assert 0.0 not in snapshot.positioning.values()
    # A future rate print must not change the 4 Jan factor.
    before = panel["rate_level"].to_list()
    store.append_rate(RateObservation(curve_id="UK2Y", rate=9.0, **_prov(_stamp(5))))
    again = build_panel(store, ts("2024-01-04T16:00:00+00:00"), config)
    assert again["rate_level"].to_list() == before
    store.close()
