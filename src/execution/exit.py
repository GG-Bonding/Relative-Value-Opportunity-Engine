"""Exits follow the live fair value and the thesis. The original target is not a promise."""

from __future__ import annotations

from domain.config import EngineConfig
from domain.enums import AlphaLifecycle, Direction, ExitReason, RegimeName
from regimes.engine import regime_supports_fade


def invalidation_reasons(
    *,
    direction: Direction,
    rate_change: float | None,
    policy_change: float | None,
    growth_change: float | None,
    inflation_change: float | None,
    risk_change: float | None,
    momentum_change: float | None,
    regime: RegimeName,
    config: EngineConfig,
) -> list[str]:
    """For a short EURGBP book, GBP-supportive spreads narrowing invalidate the thesis."""
    sign = 1.0 if direction is Direction.SHORT else -1.0
    reasons: list[str] = []
    checks = [
        ("UK-DE 2Y narrowed against the thesis", rate_change, config.exit.rate_invalidation),
        ("BoE-ECB path repriced against the thesis", policy_change, config.exit.policy_invalidation),
        ("growth differential moved against the thesis", growth_change, config.exit.growth_invalidation),
        ("inflation differential moved against the thesis", inflation_change, config.exit.inflation_invalidation),
        ("relative risk premium moved against the thesis", risk_change, config.exit.risk_invalidation),
        ("momentum reversed against the thesis", momentum_change, config.exit.momentum_invalidation),
    ]
    for label, change, threshold in checks:
        if change is None:
            continue
        if sign * change < -threshold:
            reasons.append(label)
    if not regime_supports_fade(regime):
        reasons.append(f"regime changed to {regime.value}")
    return reasons


def exit_reasons(
    *,
    remaining_pips: float | None,
    direction: Direction,
    entry_price: float,
    market_price: float,
    fair_value: float,
    holding_days: float,
    alpha: AlphaLifecycle,
    thesis_reasons: list[str],
    spread_z: float | None,
    config: EngineConfig,
) -> list[ExitReason]:
    reasons: list[ExitReason] = []
    if remaining_pips is not None and abs(remaining_pips) <= config.exit.close_pips:
        reasons.append(ExitReason.MISPRICING_CLOSED)
    if thesis_reasons:
        if any(reason.startswith("regime changed") for reason in thesis_reasons):
            reasons.append(ExitReason.REGIME_CHANGE)
        if any(not reason.startswith("regime changed") for reason in thesis_reasons):
            reasons.append(ExitReason.THESIS_INVALIDATED)
    if alpha in {AlphaLifecycle.DECAYING, AlphaLifecycle.RETIRED, AlphaLifecycle.REJECTED}:
        reasons.append(ExitReason.ALPHA_DECAYED)
    if holding_days > config.exit.expected_hold_days * config.exit.max_hold_multiple:
        reasons.append(ExitReason.TIME_EXPIRED)
    adverse_pips = _adverse_pips(direction, entry_price, market_price, config.pip)
    if adverse_pips >= config.exit.stop_pips:
        reasons.append(ExitReason.STOP_LOSS)
    target_gap = abs(market_price - fair_value) / config.pip
    if target_gap <= config.exit.close_pips:
        reasons.append(ExitReason.TARGET_REACHED)
    if spread_z is not None and spread_z > 2.5:
        reasons.append(ExitReason.LIQUIDITY_EVENT)
    return _unique(reasons)


def _adverse_pips(direction: Direction, entry: float, market: float, pip: float) -> float:
    if direction is Direction.LONG:
        return (entry - market) / pip
    return (market - entry) / pip


def _unique(reasons: list[ExitReason]) -> list[ExitReason]:
    seen: list[ExitReason] = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    return seen
