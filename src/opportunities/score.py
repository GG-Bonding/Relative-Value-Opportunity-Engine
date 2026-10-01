"""Explainable scores. Missing inputs are omitted, never replaced with zero."""

from __future__ import annotations

import math

from domain.config import FundamentalWeights, ScoreWeights
from domain.enums import Direction


def _unit(value: float | None, scale: float) -> float | None:
    if value is None or not math.isfinite(value) or scale <= 0:
        return None
    return max(-1.0, min(1.0, value / scale))


def fundamental_score(
    *,
    rate_z: float | None,
    policy_repricing: float | None,
    growth_diff: float | None,
    inflation_diff: float | None,
    risk_diff_change: float | None,
    weights: FundamentalWeights,
) -> tuple[float | None, dict[str, float | None]]:
    """+1 means EUR is relatively strong. Signs follow the registered mechanisms."""
    parts = {
        "rate": _negate(_unit(rate_z, 2.0)),
        "policy": _negate(_unit(policy_repricing, 0.10)),
        "growth": _negate(_unit(growth_diff, 1.0)),
        "inflation": _negate(_unit(inflation_diff, 1.0)),
        "risk": _negate(_unit(risk_diff_change, 0.10)),
    }
    weight_map = {
        "rate": weights.rate,
        "policy": weights.policy,
        "growth": weights.growth,
        "inflation": weights.inflation,
        "risk": weights.risk,
    }
    available = {name: value for name, value in parts.items() if value is not None}
    if len(available) < 3:
        return None, parts
    total = sum(weight_map[name] for name in available)
    if total <= 0:
        return None, parts
    score = sum(weight_map[name] / total * available[name] for name in available)
    return max(-1.0, min(1.0, score)), parts


def opportunity_score(
    components: dict[str, float | None],
    weights: ScoreWeights,
) -> tuple[float | None, dict[str, float | None]]:
    weight_map = weights.model_dump()
    available = {name: value for name, value in components.items() if value is not None and name in weight_map}
    if not available:
        return None, components
    total = sum(weight_map[name] for name in available)
    if total <= 0:
        return None, components
    score = sum(weight_map[name] / total * available[name] for name in available)
    return max(0.0, min(1.0, score)), components


def mispricing_component(pips: float | None, scale: float = 80.0) -> float | None:
    if pips is None or not math.isfinite(pips):
        return None
    return max(0.0, min(1.0, abs(pips) / scale))


def direction_from_gap(pips: float | None) -> Direction | None:
    """Positive pips means the market is above fair value: EUR is rich, so the fade is short."""
    if pips is None or not math.isfinite(pips) or pips == 0:
        return None
    return Direction.SHORT if pips > 0 else Direction.LONG


def fundamental_agrees(score: float | None, direction: Direction | None, conflict: float) -> bool | None:
    if score is None or direction is None:
        return None
    if abs(score) < conflict:
        return True
    eur_sign = 1.0 if direction is Direction.LONG else -1.0
    return math.copysign(1.0, score) == eur_sign


def _negate(value: float | None) -> float | None:
    if value is None:
        return None
    return -value
