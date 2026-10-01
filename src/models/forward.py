"""Forward-return diagnostics. They do not set the trade direction."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl
from scipy.stats import spearmanr

from domain.config import EngineConfig
from features.columns import FAIR_VALUE_FEATURES
from models.linear import fit_linear, predict_row


@dataclass(frozen=True)
class ForwardReport:
    horizon: int
    ic: float | None
    rank_ic: float | None
    oos_r2: float | None
    hit_rate: float | None
    n: int


def forward_returns(log_price: np.ndarray, horizon: int) -> np.ndarray:
    out = np.full(log_price.shape, np.nan)
    if horizon < 1:
        raise ValueError("horizon must be positive")
    out[:-horizon] = log_price[horizon:] - log_price[:-horizon]
    return out


def purged_train_end(test_start: int, horizon: int, embargo: int) -> int:
    """Last exclusive training index whose label ends before the test block."""
    return test_start - horizon - embargo


def walk_forward_ic(panel: pl.DataFrame, config: EngineConfig, horizon: int = 5) -> ForwardReport:
    names = list(FAIR_VALUE_FEATURES)
    log_price = _column(panel, "log_price")
    features = np.column_stack([_column(panel, name) for name in names])
    future = forward_returns(log_price, horizon)
    n = len(log_price)
    predictions = np.full(n, np.nan)
    fold = config.forward.fold_test_days
    embargo = config.forward.embargo
    minimum = config.forward.min_observations
    test_start = minimum + horizon + embargo
    while test_start < n:
        test_end = min(n, test_start + fold)
        train_end = purged_train_end(test_start, horizon, embargo)
        if train_end > minimum:
            idx = np.arange(0, train_end)
            valid = idx[np.isfinite(future[idx]) & np.isfinite(features[idx]).all(axis=1)]
            valid = valid[-config.fair_value.window :]
            if len(valid) >= minimum:
                fit = fit_linear(features[valid], future[valid], names, config.fair_value.ridge_alpha)
                if fit is not None:
                    for t in range(test_start, test_end):
                        if np.isfinite(features[t]).all():
                            predictions[t], _ = predict_row(fit, features[t], names)
        test_start = test_end
    return _score(predictions, future, horizon)


def _score(predictions: np.ndarray, realized: np.ndarray, horizon: int) -> ForwardReport:
    mask = np.isfinite(predictions) & np.isfinite(realized)
    count = int(mask.sum())
    if count < 20:
        return ForwardReport(horizon, None, None, None, None, count)
    pred = predictions[mask]
    real = realized[mask]
    ic = _corr(pred, real)
    rank = spearmanr(pred, real).statistic
    rank_ic = float(rank) if np.isfinite(rank) else None
    ss_res = float(np.sum((real - pred) ** 2))
    center = real - real.mean()
    ss_tot = float(np.sum(center**2))
    oos_r2 = None if ss_tot < 1e-18 else 1.0 - ss_res / ss_tot
    hit = float(np.mean(np.sign(pred) == np.sign(real)))
    return ForwardReport(horizon, ic, rank_ic, oos_r2, hit, count)


def _corr(left: np.ndarray, right: np.ndarray) -> float | None:
    if left.std(ddof=1) < 1e-12 or right.std(ddof=1) < 1e-12:
        return None
    value = float(np.corrcoef(left, right)[0, 1])
    return value if np.isfinite(value) else None


def _column(panel: pl.DataFrame, name: str) -> np.ndarray:
    return np.asarray(
        [np.nan if value is None else float(value) for value in panel[name].to_list()],
        dtype=float,
    )
