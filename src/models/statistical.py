"""Auxiliary statistical relative value. It does not drive the EURGBP book."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from statsmodels.tsa.stattools import coint


@dataclass(frozen=True)
class StatisticalRV:
    cointegration_pvalue: float | None
    residual_z: float | None
    auxiliary: bool = True


def engle_granger(log_price: np.ndarray, fair_log: np.ndarray) -> StatisticalRV:
    mask = np.isfinite(log_price) & np.isfinite(fair_log)
    if int(mask.sum()) < 80:
        return StatisticalRV(None, None)
    y = log_price[mask]
    x = fair_log[mask]
    _stat, pvalue, _crit = coint(y, x, trend="c", autolag="bic")
    residual = y - x
    std = float(residual.std(ddof=1))
    z = None if std < 1e-12 else float(residual[-1] / std)
    return StatisticalRV(cointegration_pvalue=float(pvalue), residual_z=z)
