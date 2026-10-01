"""Map trailing information coefficients and reaction lags into a lifecycle state."""

from __future__ import annotations

import numpy as np

from domain.config import EngineConfig
from domain.enums import AlphaLifecycle


def information_coefficient(signal: np.ndarray, realized: np.ndarray) -> float | None:
    mask = np.isfinite(signal) & np.isfinite(realized)
    if int(mask.sum()) < 20:
        return None
    left = signal[mask]
    right = realized[mask]
    if float(left.std(ddof=1)) < 1e-12 or float(right.std(ddof=1)) < 1e-12:
        return 0.0
    value = float(np.corrcoef(left, right)[0, 1])
    if not np.isfinite(value):
        return 0.0
    return value


def classify_alpha(
    ic_252: float | None,
    ic_63: float | None,
    ic_21: float | None,
    n_252: int,
    lag_long_minutes: float | None,
    lag_mid_minutes: float | None,
    lag_short_minutes: float | None,
    config: EngineConfig,
) -> tuple[AlphaLifecycle, list[str]]:
    """The spec's decay example is a structural mechanism whose tradable lag is gone."""
    reasons: list[str] = []
    if n_252 < 63:
        return AlphaLifecycle.DISCOVERY, ["too few realized outcomes to validate"]
    if n_252 < 252:
        return AlphaLifecycle.VALIDATING, ["trailing sample is shorter than 252 realized outcomes"]

    long_ic = 0.0 if ic_252 is None else ic_252
    mid_ic = 0.0 if ic_63 is None else ic_63
    short_ic = 0.0 if ic_21 is None else ic_21
    if (
        lag_long_minutes is not None
        and lag_short_minutes is not None
        and lag_short_minutes <= 1440
        and lag_long_minutes >= 3.0 * lag_short_minutes
        and n_252 >= 252
    ):
        return AlphaLifecycle.DECAYING, ["reaction lag collapsed from multi-day incorporation to one session"]
    lag_compressed = _lag_compressed(
        lag_long_minutes, lag_mid_minutes, lag_short_minutes, config.alpha.lag_compression_ratio
    )
    ic_collapsed = short_ic <= max(0.02, config.alpha.short_to_long_ratio * long_ic) and mid_ic < (
        config.alpha.mid_to_long_ratio * long_ic
    )
    if long_ic >= config.alpha.decaying_long_ic and ic_collapsed:
        reasons.append(f"IC path {long_ic:.3f} / {mid_ic:.3f} / {short_ic:.3f} shows the short window collapsing")
        if lag_compressed:
            reasons.append("reaction lag is compressing")
        return AlphaLifecycle.DECAYING, reasons
    if lag_compressed and long_ic >= config.alpha.active_ic and short_ic < 0.5 * long_ic:
        reasons.append("mechanism can still be present while the tradable lag has compressed")
        return AlphaLifecycle.DECAYING, reasons
    if long_ic >= config.alpha.active_ic and mid_ic > 0 and short_ic > 0:
        return AlphaLifecycle.ACTIVE, ["trailing IC remains positive at 252d, 63d, and 21d"]
    if long_ic > config.alpha.active_ic and mid_ic < 0 and short_ic < 0:
        return AlphaLifecycle.DORMANT, ["the long-window IC has not yet died, but recent IC is negative"]
    if long_ic <= 0 and mid_ic <= 0:
        return AlphaLifecycle.REJECTED, ["trailing IC is not positive"]
    return AlphaLifecycle.VALIDATING, ["IC is positive in some windows and not in others"]


def health_score(lifecycle: AlphaLifecycle) -> float:
    return {
        AlphaLifecycle.ACTIVE: 0.90,
        AlphaLifecycle.VALIDATING: 0.40,
        AlphaLifecycle.DISCOVERY: 0.10,
        AlphaLifecycle.DECAYING: 0.20,
        AlphaLifecycle.DORMANT: 0.0,
        AlphaLifecycle.RETIRED: 0.0,
        AlphaLifecycle.REJECTED: 0.0,
    }[lifecycle]


def _lag_compressed(
    long_minutes: float | None,
    mid_minutes: float | None,
    short_minutes: float | None,
    ratio: float,
) -> bool:
    if long_minutes is None or short_minutes is None or long_minutes <= 0:
        return False
    if short_minutes > long_minutes / ratio:
        return False
    if mid_minutes is None:
        return True
    return mid_minutes < long_minutes
