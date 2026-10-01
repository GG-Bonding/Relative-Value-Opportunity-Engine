"""Walk-forward ridge fair value for log(EURGBP).

The fit at date t uses rows strictly before t. Today's factors are applied to
yesterday's coefficients. Residual is therefore not an in-sample fitting error.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from domain.config import EngineConfig
from domain.timeutil import parse_ts
from features.columns import EXPECTED_LOG_PRICE_SIGNS, FAIR_VALUE_FEATURES
from models.linear import fit_linear, predict_row


@dataclass(frozen=True)
class FairValuePath:
    frame: pl.DataFrame
    coefficients: list[dict[str, float]]
    contributions: list[dict[str, float]]

    def row(self, index: int) -> dict[str, object]:
        return self.frame.row(index, named=True)


def attach_fair_value(panel: pl.DataFrame, config: EngineConfig) -> FairValuePath:
    if panel.is_empty():
        return FairValuePath(panel, [], [])
    names = list(FAIR_VALUE_FEATURES)
    target = _as_float(panel["log_price"].to_list())
    features = np.column_stack([_as_float(panel[name].to_list()) for name in names])
    n = len(target)
    fair_log = np.full(n, np.nan)
    residual = np.full(n, np.nan)
    residual_z = np.full(n, np.nan)
    r2 = np.full(n, np.nan)
    n_train = np.zeros(n, dtype=int)
    instability = np.full(n, np.nan)
    mechanism_valid = np.zeros(n, dtype=float)
    train_start_idx = np.full(n, -1, dtype=int)
    train_end_idx = np.full(n, -1, dtype=int)
    coefficients: list[dict[str, float]] = [{} for _ in range(n)]
    contributions: list[dict[str, float]] = [{} for _ in range(n)]
    rate_beta_history: list[float] = []

    window = config.fair_value.window
    minimum = config.fair_value.min_observations
    for t in range(n):
        start = max(0, t - window)
        candidate = np.arange(start, t)
        if len(candidate) == 0 or not np.isfinite(target[t]) or not np.isfinite(features[t]).all():
            continue
        valid = candidate[np.isfinite(target[candidate]) & np.isfinite(features[candidate]).all(axis=1)]
        if len(valid) < minimum:
            continue
        valid = valid[-window:]
        penalty = None if config.fair_value.estimator == "ols" else config.fair_value.ridge_alpha
        fit = fit_linear(features[valid], target[valid], names, penalty)
        if fit is None:
            continue
        prediction, contrib = predict_row(fit, features[t], names)
        fair_log[t] = prediction
        residual[t] = target[t] - prediction
        r2[t] = fit.r2
        n_train[t] = fit.n
        coefficients[t] = fit.coefficients
        contributions[t] = contrib
        train_start_idx[t] = int(valid[0])
        train_end_idx[t] = int(valid[-1])
        rate_beta = fit.coefficients.get("rate_level")
        if rate_beta is not None:
            rate_beta_history.append(rate_beta)
        instability[t] = _instability(rate_beta_history, config.fair_value.stability_window)
        mechanism_valid[t] = 1.0 if _mechanism_ok(fit.coefficients, fit.r2, instability[t], config) else 0.0
        residual_z[t] = _residual_z(
            residual[:t],
            residual[t],
            config.fair_value.min_residual_history,
            config.fair_value.residual_z_lookback,
        )

    market = np.exp(target)
    fair_price = np.exp(fair_log)
    price_gap = market - fair_price
    observed = panel["observed_at"].to_list()
    enriched = panel.with_columns(
        [
            pl.Series("fair_log", fair_log),
            pl.Series("fair_value", fair_price),
            pl.Series("log_residual", residual),
            pl.Series("residual", price_gap),
            pl.Series("residual_z", residual_z),
            pl.Series("fair_r2", r2),
            pl.Series("fair_n_train", n_train),
            pl.Series("coefficient_instability", instability),
            pl.Series("mechanism_valid", mechanism_valid),
            pl.Series("train_start", [_at(observed, i) for i in train_start_idx]),
            pl.Series("train_end", [_at(observed, i) for i in train_end_idx]),
        ]
    )
    return FairValuePath(enriched, coefficients, contributions)


def _instability(history: list[float], window: int) -> float:
    if len(history) < window:
        return float("nan")
    sample = np.asarray(history[-window:], dtype=float)
    center = abs(float(sample.mean()))
    if center < 1e-8:
        return float("nan")
    return float(sample.std(ddof=1) / center)


def _mechanism_ok(
    coefficients: dict[str, float],
    r_squared: float,
    instability: float,
    config: EngineConfig,
) -> bool:
    if not np.isfinite(r_squared) or r_squared < config.fair_value.min_r2:
        return False
    if not np.isfinite(instability) or instability > config.fair_value.max_coef_instability:
        return False
    matches: list[bool] = []
    for name, sign in EXPECTED_LOG_PRICE_SIGNS.items():
        beta = coefficients.get(name)
        if beta is None or abs(beta) < config.fair_value.min_abs_coefficient:
            continue
        matches.append(np.sign(beta) == sign)
    if len(matches) < 3:
        return False
    return sum(matches) / len(matches) >= 0.6


def _residual_z(history: np.ndarray, current: float, minimum: int, lookback: int) -> float:
    usable = history[np.isfinite(history)]
    if len(usable) < minimum:
        return float("nan")
    sample = usable[-lookback:]
    std = float(sample.std(ddof=1))
    if std < 1e-12:
        return float("nan")
    return float((current - float(sample.mean())) / std)


def _as_float(values: list[object]) -> np.ndarray:
    converted: list[float] = []
    for value in values:
        if isinstance(value, int | float):
            converted.append(float(value))
        else:
            converted.append(float("nan"))
    return np.asarray(converted, dtype=float)


def _at(observed: list[str], index: int) -> str | None:
    if index < 0:
        return None
    return parse_ts(observed[index]).strftime("%Y-%m-%dT%H:%M:%S+00:00")
