from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from alpha.lifecycle import classify_alpha
from domain.config import EngineConfig
from domain.enums import AlphaLifecycle
from evaluation.purged import assert_no_label_overlap, walk_forward_folds
from models.events import HORIZONS, PricePrint, lag_distribution, response_for_event
from models.forward import purged_train_end


def test_spec_decay_path_is_decaying_even_if_the_mechanism_remains() -> None:
    state, reasons = classify_alpha(
        ic_252=0.16,
        ic_63=0.07,
        ic_21=0.01,
        n_252=252,
        lag_long_minutes=180,
        lag_mid_minutes=90,
        lag_short_minutes=12,
        config=EngineConfig(),
    )
    assert state is AlphaLifecycle.DECAYING
    assert reasons


def test_walk_forward_purges_overlapping_labels() -> None:
    folds = walk_forward_folds(n_obs=500, train_size=120, test_size=20, horizon=5, embargo=1)
    assert folds
    assert_no_label_overlap(folds, horizon=5, embargo=1)
    for fold in folds:
        assert fold.train_end == purged_train_end(fold.test_start, 5, 1)
        assert fold.test_start > fold.train_end
        assert fold.train_start < fold.train_end < fold.test_start < fold.test_end


def test_fast_event_is_incorporated_before_the_slow_event() -> None:
    start = datetime(2024, 3, 1, 8, 0, tzinfo=UTC)
    slow = _path(start, complete_after=timedelta(days=5), half_after=timedelta(days=5))
    fast = _path(start, complete_after=timedelta(minutes=15), half_after=timedelta(minutes=15))
    slow_response = response_for_event("slow", "MACRO_RELEASE", start, slow)
    fast_response = response_for_event("fast", "MACRO_RELEASE", start, fast)
    assert slow_response.response_lag == "5d"
    assert fast_response.response_lag == "15m"
    assert fast_response.magnitudes["5m"] is None or abs(fast_response.magnitudes["5m"] or 0) < abs(
        fast_response.magnitudes["15m"] or 0
    )
    yearly = lag_distribution([slow_response, fast_response])
    assert yearly["2024"] == float(np.median([7200.0, 15.0]))
    assert set(HORIZONS) >= {"5m", "15m", "30m", "1h", "4h", "1d", "3d", "5d"}


def _path(start: datetime, complete_after: timedelta, half_after: timedelta) -> list[PricePrint]:
    pre = start - timedelta(minutes=1)
    prints = [PricePrint(pre, 0.8500)]
    stamps = [start + delta for delta in HORIZONS.values()]
    for stamp in stamps:
        if stamp - start >= complete_after:
            price = 0.8600
        elif stamp - start >= half_after:
            price = 0.8550
        else:
            price = 0.8502
        prints.append(PricePrint(stamp, price))
    return prints
