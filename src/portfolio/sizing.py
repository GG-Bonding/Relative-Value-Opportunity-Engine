"""Volatility targeting. Kelly is intentionally absent."""

from __future__ import annotations

import math

from domain.config import RiskConfig


def position_size(
    *,
    daily_vol: float | None,
    risk: RiskConfig,
    drawdown: float,
    day_loss: float,
) -> float:
    """Volatility target. Kelly sizing is intentionally absent."""
    if daily_vol is None or not math.isfinite(daily_vol) or daily_vol <= 0:
        return 0.0
    if drawdown >= risk.max_drawdown or day_loss >= risk.max_daily_loss:
        return 0.0
    annual_vol = daily_vol * math.sqrt(252.0)
    if annual_vol <= 0:
        return 0.0
    raw = risk.risk_budget / annual_vol
    return float(min(raw, risk.max_position, risk.max_pair_exposure))
