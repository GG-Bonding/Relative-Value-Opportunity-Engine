"""Close-to-close baselines on the same calendar as the fair-value book."""

from __future__ import annotations

import numpy as np


def strategy_pnl(position: np.ndarray, forward_return: np.ndarray, cost: float) -> float:
    """position is known before the forward return. Turnover pays `cost` in return units."""
    if len(position) != len(forward_return):
        raise ValueError("position and return must align")
    previous = np.concatenate([[0.0], position[:-1]])
    turnover = np.abs(position - previous)
    pnl = position * forward_return - cost * turnover
    finite = np.isfinite(pnl)
    return float(pnl[finite].sum())


def constant_book(direction: float, n: int) -> np.ndarray:
    return np.full(n, direction)


def sign_book(signal: np.ndarray, threshold: float = 0.0) -> np.ndarray:
    out = np.zeros(len(signal))
    valid = np.isfinite(signal)
    out[valid & (signal > threshold)] = 1.0
    out[valid & (signal < -threshold)] = -1.0
    return out
