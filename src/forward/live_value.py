"""Live fair value from the rate spread and EURGBP closes.

The fit uses UK2Y minus DE2Y and its 20-session change. Policy, growth,
inflation, risk, and expectations are absent from this history, so they are
left out. Own-price momentum stays out. A missing day is skipped, never filled
with zero. The last session is predicted from earlier sessions only.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx
import numpy as np

from domain.config import EngineConfig
from domain.errors import DataValidationError
from domain.timeutil import ensure_utc
from forward.official_rates import _known_at, download_de2y, download_uk2y_history
from forward.terminal import read_eurgbp_closes
from models.linear import fit_linear, predict_row

VALUE_REFRESH = timedelta(minutes=30)
HISTORY_REFRESH = timedelta(hours=6)
FEATURES = ("rate_level", "rate_change_20d")
CHANGE_LAG = 20


@dataclass(frozen=True)
class RateFairValue:
    price: float
    day: date
    sessions: int
    r2: float


def rate_fair_value(
    closes: dict[date, float],
    uk: dict[date, float],
    de: dict[date, float],
    as_of: datetime,
    config: EngineConfig,
) -> RateFairValue | None:
    """Price implied by the latest known rate state. None until the window is full."""
    limit = ensure_utc(as_of)
    days = [
        day
        for day in sorted(closes)
        if day in uk and day in de and closes[day] > 0 and _known_at(day) <= limit
    ]
    if len(days) <= CHANGE_LAG:
        return None
    level = [uk[day] - de[day] for day in days]
    usable = list(range(CHANGE_LAG, len(days)))
    predict_at = usable[-1]
    train = [index for index in usable if index < predict_at]
    train = train[-config.fair_value.window :]
    if len(train) < config.fair_value.min_observations:
        return None
    names = list(FEATURES)
    features = np.array([[level[index], level[index] - level[index - CHANGE_LAG]] for index in train])
    target = np.log(np.array([closes[days[index]] for index in train], dtype=float))
    penalty = None if config.fair_value.estimator == "ols" else config.fair_value.ridge_alpha
    fit = fit_linear(features, target, names, penalty)
    if fit is None:
        return None
    current = np.array([level[predict_at], level[predict_at] - level[predict_at - CHANGE_LAG]])
    log_price, _contributions = predict_row(fit, current, names)
    if not math.isfinite(log_price):
        return None
    return RateFairValue(price=float(math.exp(log_price)), day=days[predict_at], sessions=fit.n, r2=fit.r2)


def caching_rate_value(terminal: str | None, config: EngineConfig) -> Callable[[datetime], float | None]:
    """Refit on a timer. A failed download keeps the previous history."""
    state: dict[str, object] = {"value_at": None, "value": None, "history_at": None, "uk": {}, "de": {}}

    def quote(moment: datetime) -> float | None:
        now = ensure_utc(moment)
        value_at = state["value_at"]
        if isinstance(value_at, datetime) and now - value_at < VALUE_REFRESH:
            cached = state["value"]
            return cached if isinstance(cached, float) else None
        history_at = state["history_at"]
        if not isinstance(history_at, datetime) or now - history_at >= HISTORY_REFRESH:
            uk, de = _curves()
            if uk and de:
                state["uk"] = uk
                state["de"] = de
                state["history_at"] = now
        uk = state["uk"] if isinstance(state["uk"], dict) else {}
        de = state["de"] if isinstance(state["de"], dict) else {}
        price: float | None = None
        try:
            fitted = rate_fair_value(read_eurgbp_closes(terminal), uk, de, now, config)
        except (DataValidationError, OSError, ValueError, TypeError):
            fitted = None
        if fitted is not None:
            price = fitted.price
        state["value"] = price
        state["value_at"] = now
        return price

    return quote


def _curves() -> tuple[dict[date, float], dict[date, float]]:
    try:
        with httpx.Client(
            timeout=120.0,
            follow_redirects=True,
            headers={"User-Agent": "relative-value-engine"},
        ) as client:
            uk = download_uk2y_history(client)
            de = download_de2y(client)
    except (DataValidationError, httpx.HTTPError, OSError, ValueError):
        return {}, {}
    return uk, de
