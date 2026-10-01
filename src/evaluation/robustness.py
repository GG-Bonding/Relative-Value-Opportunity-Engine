"""Robustness statistics that do not refit the book."""

from __future__ import annotations

import numpy as np


def max_drawdown(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float((peak - equity).max())


def bootstrap_mean(pnls: np.ndarray, draws: int, seed: int) -> dict[str, float]:
    if len(pnls) == 0:
        return {"p05": 0.0, "p50": 0.0, "p95": 0.0}
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for index in range(draws):
        sample = rng.choice(pnls, size=len(pnls), replace=True)
        means[index] = sample.mean()
    quantiles = np.quantile(means, [0.05, 0.5, 0.95])
    return {"p05": float(quantiles[0]), "p50": float(quantiles[1]), "p95": float(quantiles[2])}


def monte_carlo_drawdowns(pnls: np.ndarray, draws: int, seed: int) -> dict[str, float]:
    """Reordering trades does not change total pnl. It does change the path drawdown."""
    if len(pnls) == 0:
        return {"total": 0.0, "drawdown_p50": 0.0, "drawdown_p95": 0.0}
    rng = np.random.default_rng(seed)
    drawdowns = np.empty(draws)
    for index in range(draws):
        order = rng.permutation(pnls)
        drawdowns[index] = max_drawdown(np.cumsum(order))
    return {
        "total": float(pnls.sum()),
        "drawdown_p50": float(np.quantile(drawdowns, 0.5)),
        "drawdown_p95": float(np.quantile(drawdowns, 0.95)),
    }


def cost_stress(pnls: np.ndarray, cost_per_trade: float, multiplier: float) -> float:
    """Increase a per-trade cost that was already removed, and report the stressed sum."""
    if len(pnls) == 0:
        return 0.0
    extra = cost_per_trade * (multiplier - 1.0)
    return float(pnls.sum() - extra * len(pnls))
