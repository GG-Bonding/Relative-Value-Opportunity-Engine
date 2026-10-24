"""HistData M1 rows become complete UTC bars. No ask is invented."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from m5zrev.histdata import _drop_duplicate_minutes, aggregate_complete, discover_archives, parse_m1_text, research_spec


def test_est_open_becomes_a_utc_close_and_a_partial_bucket_is_dropped() -> None:
    text = "\n".join(
        [
            "20240101 170000;0.85000;0.85020;0.84980;0.85010;0",
            "20240101 170100;0.85010;0.85030;0.85000;0.85020;0",
            "20240101 170200;0.85020;0.85040;0.85010;0.85030;0",
            "20240101 170300;0.85030;0.85050;0.85020;0.85040;0",
            "20240101 170400;0.85040;0.85060;0.85030;0.85050;0",
            "20240101 170500;0.85050;0.85070;0.85040;0.85060;0",
        ]
    )
    unix, open_, high, low, close = parse_m1_text(text)
    expected = datetime(2024, 1, 1, 22, 4, tzinfo=UTC)
    assert int(unix[4]) == int(expected.timestamp())
    bars = aggregate_complete(unix, open_, high, low, close, 5)
    assert len(bars.close) == 1
    assert bars.close_time[0] == datetime(2024, 1, 1, 22, 5, tzinfo=UTC)
    assert bars.open[0] == pytest.approx(0.85)
    assert bars.high[0] == pytest.approx(0.8506)
    assert bars.low[0] == pytest.approx(0.8498)
    assert bars.close[0] == pytest.approx(0.8505)
    assert np.isnan(bars.spread_pips[0])


def test_identical_minutes_collapse_and_a_conflict_is_an_error() -> None:
    text = "20240101 170000;0.85000;0.85020;0.84980;0.85010;0\n" * 2
    unix, *_rest = parse_m1_text(text)
    assert len(unix) == 2
    open_ = np.array([0.85, 0.85])
    high = np.array([0.8502, 0.8502])
    low = np.array([0.8498, 0.8498])
    close = np.array([0.8501, 0.8501])
    kept, *_prices, conflicts = _drop_duplicate_minutes(unix, open_, high, low, close)
    assert len(kept) == 1
    assert conflicts == 0
    close[-1] = 0.851
    kept, *_prices, conflicts = _drop_duplicate_minutes(unix, open_, high, low, close)
    assert len(kept) == 0
    assert conflicts == 1


def test_a_yearly_zip_hides_the_same_year_of_monthly_files(tmp_path: Path) -> None:
    raw = tmp_path
    (raw / "EURGBP_M1_2024.zip").write_bytes(b"")
    (raw / "EURGBP_M1_2024_01.zip").write_bytes(b"")
    (raw / "EURGBP_M1_2026_01.zip").write_bytes(b"")
    names = [path.name for path in discover_archives(raw)]
    assert names == ["EURGBP_M1_2024.zip", "EURGBP_M1_2026_01.zip"]


def test_research_spec_is_not_a_terminal_reading() -> None:
    spec = research_spec()
    assert spec.verified_from_terminal is False
    assert spec.symbol == "EURGBP"
    assert spec.tick_value_account == pytest.approx(1.25)
