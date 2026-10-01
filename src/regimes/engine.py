"""First matching rule wins. Thresholds are explicit so each branch can be tested."""

from __future__ import annotations

from domain.enums import RegimeName


def classify_regime(
    *,
    residual_z: float | None,
    spread_z: float | None,
    policy_repricing_5d: float | None,
    rate_change_20d: float | None,
    inflation_diff: float | None,
    growth_diff: float | None,
    risk_change_5d: float | None,
    realized_vol_20d: float | None,
    momentum_20d: float | None,
    momentum_60d: float | None,
    rate_level: float | None,
) -> tuple[RegimeName, list[str]]:
    if spread_z is not None and spread_z > 2.5:
        return RegimeName.LIQUIDITY_STRESS, ["quoted spread is more than 2.5 standard deviations rich"]
    if residual_z is not None and abs(residual_z) >= 2.0:
        return RegimeName.DISLOCATION, ["fair-value residual is at least 2 standard deviations"]
    if residual_z is not None and abs(residual_z) >= 1.5:
        return RegimeName.MEAN_REVERSION, ["residual is extended and the fade is the active regime"]
    if (
        risk_change_5d is not None
        and realized_vol_20d is not None
        and risk_change_5d > 0.10
        and realized_vol_20d > 0.006
    ):
        return RegimeName.RISK_OFF, ["euro risk premium is rising while realized volatility is high"]
    if policy_repricing_5d is not None and abs(policy_repricing_5d) > 0.08:
        return RegimeName.POLICY_DIVERGENCE, ["3m policy differential repriced by more than 8bp in 5 days"]
    if rate_change_20d is not None and abs(rate_change_20d) > 0.15:
        return RegimeName.RATE_DIVERGENCE, ["UK-DE 2Y moved by more than 15bp in 20 days"]
    if inflation_diff is not None and abs(inflation_diff) > 1.0:
        return RegimeName.INFLATION_DIVERGENCE, ["relative inflation surprise index is outside 1"]
    if growth_diff is not None and abs(growth_diff) > 1.0:
        return RegimeName.GROWTH_DIVERGENCE, ["relative growth surprise is outside 1"]
    if (
        momentum_20d is not None
        and momentum_60d is not None
        and abs(momentum_60d) > 0.01
        and np_sign(momentum_20d) == np_sign(momentum_60d)
        and abs(momentum_20d) > 0.004
    ):
        return RegimeName.TREND, ["20d and 60d momentum agree and are large"]
    if rate_level is not None and abs(rate_level) > 0.75 and (realized_vol_20d or 0) < 0.004:
        return RegimeName.CARRY, ["rate differential is wide and realized volatility is low"]
    return RegimeName.NEUTRAL, ["no divergence, stress, trend, or residual rule fired"]


def regime_supports_fade(regime: RegimeName) -> bool:
    """Fading a fair-value gap is not the same trade as following a trend or harvesting carry."""
    return regime in {
        RegimeName.DISLOCATION,
        RegimeName.MEAN_REVERSION,
        RegimeName.NEUTRAL,
        RegimeName.POLICY_DIVERGENCE,
        RegimeName.RATE_DIVERGENCE,
        RegimeName.GROWTH_DIVERGENCE,
        RegimeName.INFLATION_DIVERGENCE,
        RegimeName.RISK_OFF,
    }


def np_sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0
