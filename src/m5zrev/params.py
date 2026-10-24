"""Pre-registered parameters. The baseline leaves the extra filters off."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Params:
    """Defaults are the simple price-deviation book.

    Return-z and the H1 trend filter stay off until an ablation on the same
    tape shows that they improve the out-of-sample result. Their thresholds
    are still stored here so the ablation can turn them on without inventing
    new numbers.
    """

    ema_period: int = 24
    dev_window: int = 72
    ret_window: int = 48
    z_entry: float = 2.0
    ret_z_threshold: float = 2.0
    use_return_filter: bool = False
    reversal_gap: float = 0.25
    confirm_bars: int = 3
    ret_lookback: int = 2
    atr_period: int = 14
    atr_mult: float = 1.5
    min_rr: float = 1.2
    max_hold_minutes: int = 60
    cooldown_bars: int = 3
    use_trend_filter: bool = False
    h1_ema_period: int = 20
    h1_adx_period: int = 14
    h1_adx_level: float = 25.0
    h1_slope_bars: int = 3
    max_spread_pips: float = 1.5
    risk_fraction: float = 0.005
    daily_loss_fraction: float = 0.02
    max_trades_per_day: int = 3
    slippage_pips: float = 0.0
    pip: float = 0.0001
    allow_long: bool = True
    allow_short: bool = True
    session_start_minute: int = 8 * 60
    session_end_minute: int = 17 * 60
    event_before_minutes: int = 15
    event_after_minutes: int = 15
    event_filter: str = "optional"
    calendar_max_age_days: int = 14
    max_entry_jump_atr: float = 0.5
    initial_equity: float = 1000.0
    min_trades: int = 30
    max_drawdown_fraction: float = 0.10

    def __post_init__(self) -> None:
        if self.ema_period < 2 or self.dev_window < 2 or self.ret_window < 2:
            raise ValueError("indicator windows must be at least 2")
        if self.z_entry <= 0 or self.ret_z_threshold <= 0:
            raise ValueError("z thresholds must be positive")
        if self.reversal_gap <= 0 or self.confirm_bars < 1 or self.ret_lookback < 0:
            raise ValueError("confirmation window is invalid")
        if self.atr_period < 2 or self.atr_mult <= 0 or self.min_rr <= 0:
            raise ValueError("exit parameters are invalid")
        if self.max_hold_minutes <= 0 or self.cooldown_bars < 0:
            raise ValueError("holding and cooldown must be non-negative, hold must be positive")
        if self.h1_ema_period < 2 or self.h1_adx_period < 2 or self.h1_slope_bars < 1:
            raise ValueError("H1 filter lengths are invalid")
        if self.h1_adx_level <= 0 or self.max_spread_pips < 0 or self.pip <= 0:
            raise ValueError("ADX level, spread, or pip is invalid")
        if not 0 < self.risk_fraction < 1 or not 0 < self.daily_loss_fraction < 1:
            raise ValueError("risk fractions must be between 0 and 1")
        if self.max_trades_per_day < 1 or self.slippage_pips < 0:
            raise ValueError("trade count or slippage is invalid")
        if not (0 <= self.session_start_minute < self.session_end_minute <= 24 * 60):
            raise ValueError("London session must be a non-empty minute range")
        if self.event_before_minutes < 0 or self.event_after_minutes < 0:
            raise ValueError("event window must be non-negative")
        if self.event_filter not in {"off", "optional", "required"}:
            raise ValueError("event_filter must be off, optional, or required")
        if self.calendar_max_age_days < 0 or self.max_entry_jump_atr <= 0:
            raise ValueError("calendar age or jump limit is invalid")
        if self.initial_equity <= 0 or self.min_trades < 1:
            raise ValueError("equity and the trade minimum must be positive")
        if not 0 < self.max_drawdown_fraction < 1:
            raise ValueError("drawdown gate must be between 0 and 1")
        if self.allow_long is not True and self.allow_long is not False:
            raise ValueError("allow_long must be a boolean")
        if self.allow_short is not True and self.allow_short is not False:
            raise ValueError("allow_short must be a boolean")


def load_params(path: Path) -> Params:
    """Load `[strategy]` from TOML. Unknown keys fail. Missing keys keep defaults."""
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    block = payload.get("strategy", payload)
    if not isinstance(block, dict):
        raise ValueError(f"{path} has no strategy table")
    known = {item.name for item in fields(Params)}
    unknown = sorted(set(block) - known)
    if unknown:
        raise ValueError(f"unknown strategy keys: {', '.join(unknown)}")
    current = Params()
    for key, value in block.items():
        current = replace(current, **{key: _coerce(key, value)})
    return current


def _coerce(key: str, value: Any) -> Any:
    field_type = next(item.type for item in fields(Params) if item.name == key)
    if field_type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be a boolean")
        return value
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{key} has the wrong type")
    if field_type == "int" and isinstance(value, int):
        return value
    if field_type == "float" and isinstance(value, int | float):
        return float(value)
    if field_type == "str" and isinstance(value, str):
        return value
    raise ValueError(f"{key} has the wrong type")
