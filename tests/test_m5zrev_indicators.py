"""Deviation z, return z, ATR, and ADX use only closed bars."""

from __future__ import annotations

import math
import statistics
from pathlib import Path

import numpy as np
import pytest

from m5zrev.indicators import deviation_z, ema_alpha, log_ema, return_z, true_range, wilder_adx, wilder_atr
from m5zrev.params import Params, load_params
from m5zrev.trend import align_closed_h1, blocks_long, blocks_short, trend_blocks

ROOT = Path(__file__).parents[1]


def test_config_matches_the_preregistered_simple_book() -> None:
    params = load_params(ROOT / "configs" / "m5zrev.toml")
    assert params == Params()
    assert params.ema_period == 24
    assert params.dev_window == 72
    assert params.use_return_filter is False
    assert params.use_trend_filter is False
    assert params.initial_equity == 1000.0
    assert params.risk_fraction == pytest.approx(0.005)


def test_ema_seeds_on_the_first_log_price_and_warms_for_one_period() -> None:
    close = np.array([0.85, 0.851, 0.849, 0.852, 0.848], dtype=np.float64)
    period = 4
    ema = log_ema(close, period)
    alpha = ema_alpha(period)
    assert alpha == pytest.approx(2.0 / 5.0)
    assert ema[0] == pytest.approx(math.log(close[0]))
    assert ema[1] == pytest.approx(alpha * math.log(close[1]) + (1.0 - alpha) * ema[0])
    _ema, deviation, z = deviation_z(close, period, window=3)
    assert np.all(np.isnan(deviation[:period]))
    assert deviation[period] == pytest.approx(math.log(close[period]) - ema[period - 1])
    assert np.all(np.isnan(z[: period + 3]))


def test_deviation_z_uses_sample_std_and_excludes_the_current_bar() -> None:
    close = np.array([0.85 + 0.0008 * math.sin(index / 3.0) for index in range(40)])
    period = 6
    window = 8
    _ema, deviation, z = deviation_z(close, period, window)
    for index in range(period + window, len(close)):
        sample = [float(item) for item in deviation[index - window : index]]
        assert float(deviation[index]) not in sample
        expected = (float(deviation[index]) - statistics.mean(sample)) / statistics.stdev(sample)
        assert z[index] == pytest.approx(expected)
        assert statistics.stdev(sample) == pytest.approx(float(np.std(sample, ddof=1)))
    changed = close.copy()
    changed[-1] = 0.90
    again = deviation_z(changed, period, window)[2]
    np.testing.assert_allclose(z[:-1], again[:-1], equal_nan=True)
    assert again[-1] != pytest.approx(z[-1])


def test_zero_deviation_is_not_tradable_and_is_not_filled() -> None:
    flat = np.full(30, 0.86)
    _ema, deviation, z = deviation_z(flat, 4, 6)
    assert np.all(deviation[4:] == pytest.approx(0.0))
    assert np.all(np.isnan(z))
    broken = flat.copy()
    broken[10] = 0.0
    ema, _dev, blocked = deviation_z(broken, 4, 6)
    assert np.all(np.isnan(ema[10:]))
    assert np.all(np.isnan(blocked[10:]))
    assert not np.any(np.isfinite(blocked[10:]))


def test_return_z_sign_follows_the_bar_and_excludes_itself() -> None:
    close = np.array([1.0, 1.01, 0.99, 1.02, 1.01, 1.03, 0.98, 1.0, 1.01], dtype=np.float64)
    log_return, z = return_z(close, window=4)
    assert log_return[1] > 0.0
    assert log_return[2] < 0.0
    assert log_return[1] == pytest.approx(math.log(1.01 / 1.0))
    sample = [float(item) for item in log_return[1:5]]
    assert z[5] == pytest.approx((float(log_return[5]) - statistics.mean(sample)) / statistics.stdev(sample))
    assert np.all(np.isnan(z[:5]))
    changed = close.copy()
    changed[-1] = 1.2
    again = return_z(changed, 4)[1]
    np.testing.assert_allclose(z[:-1], again[:-1], equal_nan=True)


def test_wilder_atr_starts_at_the_period_with_the_mean_true_range() -> None:
    high = np.array([10.0, 11.0, 12.0, 11.5, 11.0], dtype=np.float64)
    low = np.array([9.0, 9.5, 10.0, 10.0, 9.5], dtype=np.float64)
    close = np.array([9.5, 10.5, 11.0, 10.2, 10.0], dtype=np.float64)
    period = 3
    tr = true_range(high, low, close)
    assert tr[1] == pytest.approx(max(1.5, abs(11.0 - 9.5), abs(9.5 - 9.5)))
    atr = wilder_atr(high, low, close, period)
    assert np.all(np.isnan(atr[:period]))
    assert atr[period] == pytest.approx(float(np.mean(tr[1 : period + 1])))
    assert atr[period + 1] == pytest.approx((atr[period] * (period - 1) + tr[period + 1]) / period)


def test_wilder_adx_matches_an_independent_smoothing() -> None:
    high = np.array([10, 11, 12, 11, 10, 9, 8, 9, 10, 11], dtype=np.float64)
    low = high - 2.0
    close = high - 1.0
    period = 3
    plus, minus, adx = wilder_adx(high, low, close, period)
    up = np.diff(high, prepend=np.nan)
    down = -np.diff(low, prepend=np.nan)
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(high, low, close)
    smooth_plus = plus_dm[1] + plus_dm[2] + plus_dm[3]
    smooth_minus = minus_dm[1] + minus_dm[2] + minus_dm[3]
    smooth_tr = tr[1] + tr[2] + tr[3]
    assert plus[3] == pytest.approx(100.0 * smooth_plus / smooth_tr)
    assert minus[3] == pytest.approx(100.0 * smooth_minus / smooth_tr)
    assert np.isnan(adx[2 * period - 2])
    assert np.isfinite(adx[2 * period - 1])


def test_h1_filter_ignores_a_bar_that_has_not_closed() -> None:
    from datetime import UTC, datetime

    signal = [
        datetime(2026, 7, 15, 10, 0, tzinfo=UTC),
        datetime(2026, 7, 15, 10, 5, tzinfo=UTC),
        datetime(2026, 7, 15, 11, 0, tzinfo=UTC),
    ]
    h1 = [
        datetime(2026, 7, 15, 10, 0, tzinfo=UTC),
        datetime(2026, 7, 15, 11, 0, tzinfo=UTC),
    ]
    aligned = align_closed_h1(signal, h1)
    assert aligned.tolist() == [0, 0, 1]
    forming_only = align_closed_h1(signal[:2], h1[1:])
    assert forming_only.tolist() == [-1, -1]


def test_downtrend_blocks_new_longs_and_an_uptrend_blocks_new_shorts() -> None:
    down_close = 1.0 - 0.01 * np.arange(80)
    down_long, down_short, down_ready = trend_blocks(
        down_close + 0.002,
        down_close - 0.003,
        down_close,
        ema_period=8,
        adx_period=5,
        adx_level=25.0,
        slope_bars=3,
    )
    assert down_ready[-1]
    assert down_long[-1]
    assert not down_short[-1]
    up_close = 1.0 + 0.01 * np.arange(80)
    up_long, up_short, up_ready = trend_blocks(
        up_close + 0.003,
        up_close - 0.002,
        up_close,
        ema_period=8,
        adx_period=5,
        adx_level=25.0,
        slope_bars=3,
    )
    assert up_ready[-1]
    assert up_short[-1]
    assert not up_long[-1]
    assert not down_ready[0]
    assert not down_long[0]
    assert blocks_long(25.0, 1.0, 1.1, 0.9, 25.0) is False
    assert blocks_long(25.1, 1.0, 1.1, 0.9, 25.0) is True
    assert blocks_long(30.0, 1.0, 1.1, 1.05, 25.0) is False
    assert blocks_short(25.1, 1.2, 1.0, 1.3, 25.0) is True
    assert blocks_short(25.1, 1.2, 1.0, 1.1, 25.0) is False


def test_unknown_config_key_is_rejected(tmp_path: Path) -> None:
    text = (ROOT / "configs" / "m5zrev.toml").read_text(encoding="utf-8") + "\nnot_a_parameter = 1\n"
    target = tmp_path / "bad.toml"
    target.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        load_params(target)
