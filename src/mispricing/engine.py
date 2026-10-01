"""Classify the fair-value residual before anyone is allowed to trade it."""

from __future__ import annotations

from domain.config import EngineConfig
from domain.enums import MispricingClass


def classify_mispricing(
    *,
    residual_z: float | None,
    r_squared: float | None,
    mechanism_valid: bool,
    coefficient_instability: float | None,
    event_age_days: float | None,
    event_abs_surprise: float | None,
    factor_move: float | None,
    price_jump: float | None,
    spread_z: float | None,
    config: EngineConfig,
) -> tuple[MispricingClass, list[str], bool]:
    reasons: list[str] = []
    if r_squared is not None and r_squared < config.fair_value.min_r2:
        return MispricingClass.MODEL_ERROR, ["training R² is below the fair-value floor"], False
    if coefficient_instability is not None and coefficient_instability > config.fair_value.max_coef_instability:
        return (
            MispricingClass.MODEL_ERROR,
            ["rate-factor coefficient is unstable relative to its mean"],
            False,
        )
    if not mechanism_valid and residual_z is not None and abs(residual_z) >= config.opportunity.residual_z_entry:
        return (
            MispricingClass.MODEL_ERROR,
            ["residual is large but the registered factor signs are not in the fit"],
            False,
        )
    recent_event = (
        event_age_days is not None
        and event_age_days <= config.opportunity.event_confirmation_days
        and event_abs_surprise is not None
        and event_abs_surprise >= 1.0
    )
    price_moved = price_jump is not None and abs(price_jump) >= 0.002
    factors_moved = factor_move is not None and abs(factor_move) >= 0.05
    if recent_event and price_moved and not factors_moved:
        return (
            MispricingClass.EVENT_DISLOCATION,
            ["price jumped on an event that the relative-factor set did not absorb"],
            False,
        )
    if spread_z is not None and spread_z > 2.5:
        return MispricingClass.LIQUIDITY_DISTORTION, ["spread is stressed"], False
    if (
        residual_z is not None
        and abs(residual_z) >= 2.5
        and factor_move is not None
        and abs(factor_move) < 0.02
        and price_moved
    ):
        return (
            MispricingClass.REGIME_BREAK,
            ["a large residual is not explained by the registered factor moves"],
            False,
        )
    if residual_z is not None and abs(residual_z) >= config.opportunity.residual_z_entry and mechanism_valid:
        reasons.append("mechanism signs agree, the fit is stable, and the residual is extended")
        return MispricingClass.POSSIBLE_MISPRICING, reasons, True
    return MispricingClass.UNKNOWN, ["residual is inside the band or the model is not yet stable"], False
