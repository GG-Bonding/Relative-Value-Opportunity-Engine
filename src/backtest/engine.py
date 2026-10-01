"""One EURGBP book, walked in observed_at order, with bid/ask fills."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from alpha.lifecycle import classify_alpha, information_coefficient
from data.store import PitStore
from domain.config import MODEL_VERSION, EngineConfig
from domain.enums import (
    AlphaLifecycle,
    DecisionState,
    Direction,
    ExitReason,
    FillSide,
    OpportunityStatus,
    SpreadSource,
    TradeReviewLabel,
)
from domain.hashing import stable_hash, stable_id
from domain.models import BacktestRun, Fill, Trade
from domain.timeutil import parse_ts
from execution.costs import execution_price, expected_net_edge_pips
from execution.decision import DecisionInput, chase_already_closed, decide
from execution.exit import exit_reasons, invalidation_reasons
from features.columns import FEATURE_GROUPS
from features.panel import build_panel
from features.series import rolling_change, rolling_zscore
from mispricing.engine import classify_mispricing
from models.events import PricePrint, response_for_event
from models.fair_value import FairValuePath, attach_fair_value
from models.forward import forward_returns
from opportunities.score import fundamental_score
from portfolio.sizing import position_size
from regimes.engine import classify_regime

_TERMINAL = {
    OpportunityStatus.EXITED,
    OpportunityStatus.INVALIDATED,
    OpportunityStatus.EXPIRED,
    OpportunityStatus.REJECTED,
}


@dataclass
class _Open:
    direction: Direction
    quantity: float
    entry_price: float
    entry_mid: float
    opened_at: datetime
    entry_index: int
    opportunity_id: str
    entry_fair: float
    entry_pips: float
    factors: dict[str, float | None]
    contributions: dict[str, float]
    expected_pnl: float
    spread_source: SpreadSource
    spread_pips: float
    entry_regime: str
    status: OpportunityStatus = OpportunityStatus.ENTERED


@dataclass
class BacktestResult:
    run: BacktestRun
    trades: list[Trade] = field(default_factory=list)
    decision_counts: dict[str, int] = field(default_factory=dict)
    reason_counts: dict[str, int] = field(default_factory=dict)
    daily_pnl: list[float] = field(default_factory=list)
    daily_times: list[str] = field(default_factory=list)
    alphas: dict[str, str] = field(default_factory=dict)
    entry_times: list[str] = field(default_factory=list)


def run_backtest(
    store: PitStore,
    config: EngineConfig,
    start: datetime,
    end: datetime,
) -> BacktestResult:
    panel = build_panel(store, end, config)
    path = attach_fair_value(panel, config)
    frame = path.frame
    if frame.is_empty():
        raise ValueError("nothing to replay")
    times = [parse_ts(value) for value in frame["observed_at"].to_list()]
    start_index = next((i for i, moment in enumerate(times) if moment >= start), len(times))
    needed = [
        "log_price",
        "mid",
        "bid",
        "ask",
        "spread",
        "residual",
        "residual_z",
        "fair_value",
        "fair_r2",
        "mechanism_valid",
        "coefficient_instability",
        "rate_level",
        "rate_change_1d",
        "rate_change_20d",
        "rate_zscore_252d",
        "rate_age_days",
        "policy_diff_3m",
        "policy_repricing_1d",
        "policy_repricing_5d",
        "growth_diff",
        "growth_diff_index",
        "inflation_diff",
        "risk_premium_diff",
        "momentum_20d",
        "momentum_60d",
        "realized_vol_20d",
        "return_1d",
        "event_age_days",
        "last_event_abs_surprise",
        "cftc_net",
    ]
    arrays = {name: _col(frame, name) for name in needed}
    log_price = arrays["log_price"]
    residual = arrays["residual"]
    future = forward_returns(log_price, config.alpha.ic_horizon)
    signal = -residual
    lag_table = _lag_table(store, end, times)
    risk_change = rolling_change(arrays["risk_premium_diff"], 5)
    spread_z = rolling_zscore(arrays["spread"], 60)
    trades: list[Trade] = []
    daily = np.zeros(len(times))
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    alphas: dict[str, str] = {}
    position: _Open | None = None
    wealth = 1.0
    peak_wealth = 1.0
    day_pnl = 0.0
    known_hits: list[bool] = []

    for index in range(start_index, len(times)):
        info = _info_index(times, index, config.delays.data_delay_seconds)
        if info is None:
            counts["DATA_DEGRADED"] = counts.get("DATA_DEGRADED", 0) + 1
            continue
        ic_252, ic_63, ic_21, n_known = _ics(signal, future, info, config.alpha.ic_horizon)
        lag_long, lag_mid, lag_short = lag_table[info]
        lifecycle, _alpha_reasons = classify_alpha(ic_252, ic_63, ic_21, n_known, lag_long, lag_mid, lag_short, config)
        alphas[times[info].strftime("%Y-%m-%d")] = lifecycle.value
        if position is not None:
            closed = _maybe_exit(
                position, info, index, times, arrays, path, risk_change, spread_z, lifecycle, config, trades, daily
            )
            if closed:
                wealth += trades[-1].pnl
                peak_wealth = max(peak_wealth, wealth)
                day_pnl = trades[-1].pnl
                position = None
            continue
        decision_name, opened, reason = _maybe_enter(
            info=info,
            index=index,
            times=times,
            arrays=arrays,
            path=path,
            risk_change=risk_change,
            spread_z=spread_z,
            lifecycle=lifecycle,
            config=config,
            signal=signal,
            future=future,
            known_hits=known_hits,
            equity=wealth,
            peak=peak_wealth,
            day_pnl=max(0.0, -day_pnl),
        )
        counts[decision_name] = counts.get(decision_name, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1
        position = opened
        day_pnl = 0.0

    pnl = float(sum(trade.pnl for trade in trades))
    equity_path = np.cumsum(daily[start_index:])
    sharpe = _sharpe(daily[start_index:])
    drawdown = _max_drawdown(equity_path)
    payload = [
        (
            trade.entry.filled_at.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            trade.exit.filled_at.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            trade.direction.value,
            round(trade.pnl, 8),
        )
        for trade in trades
    ]
    run = BacktestRun(
        run_id=stable_id("backtest", config.hash(), store.snapshot_hash(end), start, end),
        pair="EURGBP",
        git_commit=_git_commit(),
        config_hash=config.hash(),
        data_snapshot_hash=store.snapshot_hash(end),
        model_version=MODEL_VERSION,
        random_seed=config.seed,
        start=start,
        end=end,
        spread_source=(
            SpreadSource.OBSERVED_BOOK
            if trades and trades[0].entry.spread_source is SpreadSource.OBSERVED_BOOK
            else SpreadSource.SIMULATED_SPREAD
        ),
        n_trades=len(trades),
        pnl=pnl,
        pnl_after_costs=pnl,
        sharpe=sharpe,
        max_drawdown=drawdown,
        deterministic_hash=stable_hash(payload),
    )
    return BacktestResult(
        run=run,
        trades=trades,
        decision_counts=counts,
        reason_counts=reasons,
        daily_pnl=daily[start_index:].tolist(),
        daily_times=[moment.strftime("%Y-%m-%dT%H:%M:%S+00:00") for moment in times[start_index:]],
        alphas=alphas,
        entry_times=[trade.entry.filled_at.strftime("%Y-%m-%dT%H:%M:%S+00:00") for trade in trades],
    )


def _maybe_enter(
    *,
    info: int,
    index: int,
    times: list[datetime],
    arrays: dict[str, np.ndarray],
    path: FairValuePath,
    risk_change: np.ndarray,
    spread_z: np.ndarray,
    lifecycle: AlphaLifecycle,
    config: EngineConfig,
    signal: np.ndarray,
    future: np.ndarray,
    known_hits: list[bool],
    equity: float,
    peak: float,
    day_pnl: float,
) -> tuple[str, _Open | None, str]:
    pips = _pips(arrays["residual"][info], config.pip)
    fair = _optional(arrays["fair_value"][info])
    mid = _optional(arrays["mid"][info])
    missing_critical = mid is None or fair is None or not np.isfinite(arrays["rate_level"][info])
    stale = _stale(arrays, info, config)
    classification, _reasons, _tradable = classify_mispricing(
        residual_z=_optional(arrays["residual_z"][info]),
        r_squared=_optional(arrays["fair_r2"][info]),
        mechanism_valid=bool(arrays["mechanism_valid"][info]) if np.isfinite(arrays["fair_r2"][info]) else False,
        coefficient_instability=_optional(arrays["coefficient_instability"][info]),
        event_age_days=_optional(arrays["event_age_days"][info]),
        event_abs_surprise=_optional(arrays["last_event_abs_surprise"][info]),
        factor_move=_factor_move(arrays, info),
        price_jump=_optional(arrays["return_1d"][info]),
        spread_z=_optional(spread_z[info]),
        config=config,
    )
    regime, _regime_reasons = classify_regime(
        residual_z=_optional(arrays["residual_z"][info]),
        spread_z=_optional(spread_z[info]),
        policy_repricing_5d=_optional(arrays["policy_repricing_5d"][info]),
        rate_change_20d=_optional(arrays["rate_change_20d"][info]),
        inflation_diff=_optional(arrays["inflation_diff"][info]),
        growth_diff=_optional(arrays["growth_diff"][info]),
        risk_change_5d=_optional(risk_change[info]),
        realized_vol_20d=_optional(arrays["realized_vol_20d"][info]),
        momentum_20d=_optional(arrays["momentum_20d"][info]),
        momentum_60d=_optional(arrays["momentum_60d"][info]),
        rate_level=_optional(arrays["rate_level"][info]),
    )
    score, _parts = fundamental_score(
        rate_z=_optional(arrays["rate_zscore_252d"][info]),
        policy_repricing=_optional(arrays["policy_repricing_5d"][info]),
        growth_diff=_optional(arrays["growth_diff"][info]),
        inflation_diff=_optional(arrays["inflation_diff"][info]),
        risk_diff_change=_optional(risk_change[info]),
        weights=config.fundamental,
    )
    probability = _probability(signal, future, arrays["residual"], info, config, known_hits)
    _edge, _cost, net = expected_net_edge_pips(
        pips,
        probability,
        config.costs,
        _spread_pips(arrays, info, config),
    )
    gaps = _recent_gaps(arrays["residual"], info, config.entry.recent_lookback)
    from opportunities.score import direction_from_gap

    direction = direction_from_gap(pips)
    chase = False
    if direction is not None:
        chase = chase_already_closed(gaps=gaps, direction=direction, chase_fraction=config.opportunity.chase_fraction)
    decision = decide(
        DecisionInput(
            mispricing_pips=pips,
            fundamental=score,
            classification=classification,
            regime=regime,
            alpha=lifecycle,
            crowding=_crowding(arrays, info),
            net_edge_pips=net,
            missing_critical=missing_critical,
            stale_critical=stale,
            chase=chase,
            breakout_pending=False,
            reaction_lag_blocked=False,
        ),
        config,
    )
    reason = decision.reasons[0] if decision.reasons else decision.state.value
    tradable = decision.state in {DecisionState.LONG, DecisionState.SHORT}
    if not tradable or decision.direction is None or mid is None or fair is None or net is None:
        return decision.state.value, None, reason
    drawdown = 0.0 if peak <= 0 else max(0.0, (peak - equity) / peak)
    quantity = position_size(
        daily_vol=_optional(arrays["realized_vol_20d"][info]),
        risk=config.risk,
        drawdown=drawdown,
        day_loss=day_pnl,
    )
    if quantity <= 0:
        return "NO_TRADE", None, "volatility target is zero"
    fill_index = _fill_index(times, index, config)
    if fill_index is None:
        return "NO_TRADE", None, "no bar is available after the execution delay"
    side = FillSide.BUY if decision.direction is Direction.LONG else FillSide.SELL
    price, source, spread = execution_price(
        side=side,
        mid=float(arrays["mid"][fill_index]),
        bid=_optional(arrays["bid"][fill_index]),
        ask=_optional(arrays["ask"][fill_index]),
        pip=config.pip,
        costs=config.costs,
    )
    expected = net * config.pip * quantity / mid
    return (
        decision.state.value,
        _Open(
            direction=decision.direction,
            quantity=quantity,
            entry_price=price,
            entry_mid=float(arrays["mid"][fill_index]),
            opened_at=times[fill_index],
            entry_index=fill_index,
            opportunity_id=stable_id("opportunity", times[info], decision.direction.value),
            entry_fair=fair,
            entry_pips=pips if pips is not None else 0.0,
            factors={
                "rate_level": _optional(arrays["rate_level"][info]),
                "policy_diff_3m": _optional(arrays["policy_diff_3m"][info]),
                "growth_diff_index": _optional(arrays["growth_diff_index"][info]),
                "inflation_diff": _optional(arrays["inflation_diff"][info]),
                "risk_premium_diff": _optional(arrays["risk_premium_diff"][info]),
                "momentum_20d": _optional(arrays["momentum_20d"][info]),
            },
            contributions=dict(path.contributions[info]),
            expected_pnl=expected,
            spread_source=source,
            spread_pips=spread,
            entry_regime=regime.value,
        ),
        reason,
    )


def _maybe_exit(
    position: _Open,
    info: int,
    index: int,
    times: list[datetime],
    arrays: dict[str, np.ndarray],
    path: FairValuePath,
    risk_change: np.ndarray,
    spread_z: np.ndarray,
    lifecycle: AlphaLifecycle,
    config: EngineConfig,
    trades: list[Trade],
    daily: np.ndarray,
) -> bool:
    fair = _optional(arrays["fair_value"][info])
    mid = _optional(arrays["mid"][info])
    if fair is None or mid is None:
        return False
    remaining = (mid - fair) / config.pip
    regime, _reasons = classify_regime(
        residual_z=_optional(arrays["residual_z"][info]),
        spread_z=_optional(spread_z[info]),
        policy_repricing_5d=_optional(arrays["policy_repricing_5d"][info]),
        rate_change_20d=_optional(arrays["rate_change_20d"][info]),
        inflation_diff=_optional(arrays["inflation_diff"][info]),
        growth_diff=_optional(arrays["growth_diff"][info]),
        risk_change_5d=_optional(risk_change[info]),
        realized_vol_20d=_optional(arrays["realized_vol_20d"][info]),
        momentum_20d=_optional(arrays["momentum_20d"][info]),
        momentum_60d=_optional(arrays["momentum_60d"][info]),
        rate_level=_optional(arrays["rate_level"][info]),
    )
    thesis = invalidation_reasons(
        direction=position.direction,
        rate_change=_delta(arrays["rate_level"][info], position.factors.get("rate_level")),
        policy_change=_delta(arrays["policy_diff_3m"][info], position.factors.get("policy_diff_3m")),
        growth_change=_delta(arrays["growth_diff_index"][info], position.factors.get("growth_diff_index")),
        inflation_change=_delta(arrays["inflation_diff"][info], position.factors.get("inflation_diff")),
        risk_change=_delta(arrays["risk_premium_diff"][info], position.factors.get("risk_premium_diff")),
        momentum_change=_delta(arrays["momentum_20d"][info], position.factors.get("momentum_20d")),
        regime=regime,
        config=config,
    )
    holding_days = (times[info] - position.opened_at).total_seconds() / 86400.0
    reasons = exit_reasons(
        remaining_pips=remaining,
        direction=position.direction,
        entry_price=position.entry_price,
        market_price=mid,
        fair_value=fair,
        holding_days=holding_days,
        alpha=lifecycle,
        thesis_reasons=thesis,
        spread_z=_optional(spread_z[info]),
        config=config,
    )
    if not reasons:
        if abs(remaining) < abs(position.entry_pips) * (1.0 - config.exit.convergence_fraction):
            position.status = OpportunityStatus.CONVERGING
        return False
    fill_index = _fill_index(times, index, config)
    if fill_index is None:
        return False
    side = FillSide.SELL if position.direction is Direction.LONG else FillSide.BUY
    exit_price, source, spread = execution_price(
        side=side,
        mid=float(arrays["mid"][fill_index]),
        bid=_optional(arrays["bid"][fill_index]),
        ask=_optional(arrays["ask"][fill_index]),
        pip=config.pip,
        costs=config.costs,
    )
    exit_mid = float(arrays["mid"][fill_index])
    sign = 1.0 if position.direction is Direction.LONG else -1.0
    unit = position.quantity / position.entry_mid
    mid_pnl = sign * unit * (exit_mid - position.entry_mid)
    fill_pnl = sign * unit * (exit_price - position.entry_price)
    fees = (2.0 * config.costs.fee_pips + config.costs.latency_cost_pips) * config.pip * unit
    pnl = fill_pnl - fees
    attribution, unexplained = _attribute(
        position, path.contributions[fill_index], mid_pnl, position.entry_mid, exit_mid
    )
    review = _review(position.direction, position.entry_mid, exit_mid, pnl, reasons, lifecycle)
    entry_fill = _fill(
        position.opportunity_id + ":entry",
        position.opportunity_id,
        FillSide.BUY if position.direction is Direction.LONG else FillSide.SELL,
        position.quantity,
        position.entry_price,
        position.opened_at,
        position.spread_source,
        position.spread_pips,
        config,
    )
    exit_fill = _fill(
        position.opportunity_id + ":exit",
        position.opportunity_id,
        side,
        position.quantity,
        exit_price,
        times[fill_index],
        source,
        spread,
        config,
    )
    trade = Trade(
        trade_id=position.opportunity_id + ":trade",
        pair="EURGBP",
        direction=position.direction,
        entry=entry_fill,
        exit=exit_fill,
        pnl=float(pnl),
        pnl_pips=float(sign * (exit_mid - position.entry_mid) / config.pip),
        expected_pnl=position.expected_pnl,
        captured_edge=float(pnl),
        holding_period_days=float((times[fill_index] - position.opened_at).total_seconds() / 86400.0),
        execution_cost=float(mid_pnl - pnl),
        attribution=attribution,
        unexplained_pnl=float(unexplained),
        review=review,
        exit_reasons=reasons,
        opportunity_id=position.opportunity_id,
        entry_regime=position.entry_regime,
    )
    trades.append(trade)
    daily[fill_index] += pnl
    return True


def _attribute(
    position: _Open,
    exit_contrib: dict[str, float],
    mid_pnl: float,
    entry_mid: float,
    exit_mid: float,
) -> tuple[dict[str, float], float]:
    sign = 1.0 if position.direction is Direction.LONG else -1.0
    grouped = {name: 0.0 for name in FEATURE_GROUPS}
    for group, names in FEATURE_GROUPS.items():
        for name in names:
            if name not in position.contributions or name not in exit_contrib:
                continue
            delta = exit_contrib[name] - position.contributions[name]
            grouped[group] += sign * position.quantity * delta
    explained = float(sum(grouped.values()))
    unexplained = float(mid_pnl - explained)
    _ = exit_mid
    return grouped, unexplained


def _review(
    direction: Direction,
    entry_mid: float,
    exit_mid: float,
    pnl: float,
    reasons: list[ExitReason],
    lifecycle: AlphaLifecycle,
) -> TradeReviewLabel:
    if lifecycle in {AlphaLifecycle.DECAYING, AlphaLifecycle.REJECTED} and ExitReason.ALPHA_DECAYED in reasons:
        return TradeReviewLabel.ALPHA_DECAY
    if ExitReason.REGIME_CHANGE in reasons:
        return TradeReviewLabel.REGIME_CHANGE
    moved_with_fade = (direction is Direction.SHORT and exit_mid < entry_mid) or (
        direction is Direction.LONG and exit_mid > entry_mid
    )
    if ExitReason.THESIS_INVALIDATED in reasons and not moved_with_fade:
        return TradeReviewLabel.THESIS_WRONG
    if moved_with_fade and pnl > 0:
        return TradeReviewLabel.THESIS_CORRECT_EXECUTION_GOOD
    if moved_with_fade and pnl <= 0:
        return TradeReviewLabel.THESIS_CORRECT_EXECUTION_BAD
    if not moved_with_fade:
        return TradeReviewLabel.THESIS_WRONG
    return TradeReviewLabel.RANDOM_OUTCOME


def _fill(
    fill_id: str,
    opportunity_id: str,
    side: FillSide,
    quantity: float,
    price: float,
    when: datetime,
    source: SpreadSource,
    spread_pips: float,
    config: EngineConfig,
) -> Fill:
    return Fill(
        fill_id=fill_id,
        order_id=opportunity_id,
        pair="EURGBP",
        side=side,
        quantity=quantity,
        price=price,
        filled_at=when,
        spread_source=source,
        spread_pips=spread_pips,
        slippage_pips=config.costs.slippage_pips,
        fee_pips=config.costs.fee_pips,
    )


def _lag_table(
    store: PitStore, end: datetime, times: list[datetime]
) -> list[tuple[float | None, float | None, float | None]]:
    """Trailing reaction lags whose 5-day outcome is already known on that bar."""
    releases = store.history("macro_releases", end)
    market = store.history("market_observations", end).filter(pl_pair())
    if releases.is_empty() or market.is_empty():
        return [(None, None, None)] * len(times)
    prints = [
        PricePrint(parse_ts(str(observed)), float(mid))
        for observed, mid in zip(market["observed_at"].to_list(), market["mid"].to_list(), strict=True)
        if mid is not None
    ]
    completed: list[tuple[datetime, datetime, float]] = []
    for observed in releases["observed_at"].to_list():
        event_time = parse_ts(str(observed))
        response = response_for_event("macro", "MACRO_RELEASE", event_time, prints)
        if response.response_lag_minutes is None:
            continue
        completed.append((event_time + timedelta(days=5), event_time, response.response_lag_minutes))
    completed.sort()
    table: list[tuple[float | None, float | None, float | None]] = []
    for moment in times:
        known = [item for item in completed if item[0] <= moment]
        table.append(
            (
                _median_lag(known, moment, 756),
                _median_lag(known, moment, 252),
                _median_lag(known, moment, 63),
            )
        )
    return table


def _median_lag(known: list[tuple[datetime, datetime, float]], moment: datetime, window_days: int) -> float | None:
    cutoff = moment - timedelta(days=window_days)
    sample = [minutes for _available, event_time, minutes in known if event_time >= cutoff]
    if len(sample) < 4:
        return None
    return float(np.median(sample))


def pl_pair() -> Any:
    import polars as pl

    return (pl.col("pair") == "EURGBP") & (pl.col("mid").is_not_null())


def _ics(
    signal: np.ndarray, future: np.ndarray, index: int, horizon: int
) -> tuple[float | None, float | None, float | None, int]:
    known = np.flatnonzero(np.arange(len(signal)) + horizon <= index)
    if len(known) == 0:
        return None, None, None, 0
    usable = known[np.isfinite(signal[known]) & np.isfinite(future[known])]

    def _window(size: int) -> float | None:
        if len(usable) < 20:
            return None
        sample = usable[-size:]
        return information_coefficient(signal[sample], future[sample])

    return _window(252), _window(63), _window(21), int(min(len(usable), 252))


def _probability(
    signal: np.ndarray,
    future: np.ndarray,
    residual: np.ndarray,
    index: int,
    config: EngineConfig,
    known_hits: list[bool],
) -> float:
    horizon = config.alpha.ic_horizon
    last = index - horizon
    if last >= 0 and np.isfinite(residual[last]) and np.isfinite(future[last]):
        if abs(residual[last]) / config.pip >= config.opportunity.min_mispricing_pips:
            known_hits.append(bool(np.sign(signal[last]) == np.sign(future[last]) and np.sign(signal[last]) != 0))
    if len(known_hits) < config.opportunity.min_hit_rate_sample:
        return config.opportunity.prior_convergence_probability
    recent = known_hits[-100:]
    return float(sum(recent) / len(recent))


def _info_index(times: list[datetime], index: int, delay_seconds: int) -> int | None:
    cutoff = times[index] - timedelta(seconds=delay_seconds)
    chosen: int | None = None
    for cursor in range(index + 1):
        if times[cursor] <= cutoff:
            chosen = cursor
        else:
            break
    return chosen


def _fill_index(times: list[datetime], index: int, config: EngineConfig) -> int | None:
    delay = (
        config.delays.processing_delay_seconds
        + config.delays.signal_delay_seconds
        + config.delays.execution_delay_seconds
    )
    target = times[index] + timedelta(seconds=delay)
    for cursor in range(index, len(times)):
        if times[cursor] >= target:
            return cursor
    return None


def _recent_gaps(residual: np.ndarray, index: int, lookback: int) -> list[float]:
    start = max(0, index - lookback + 1)
    return [float(value) for value in residual[start : index + 1] if np.isfinite(value)]


def _crowding(arrays: dict[str, np.ndarray], index: int) -> float | None:
    cftc = _optional(arrays["cftc_net"][index])
    if cftc is not None:
        return max(0.0, min(1.0, abs(cftc)))
    momentum = _optional(arrays["momentum_20d"][index])
    vol = _optional(arrays["realized_vol_20d"][index])
    if momentum is None or vol is None or vol <= 0:
        return None
    return max(0.0, min(1.0, abs(momentum) / (vol * np.sqrt(20.0)) / 3.0))


def _factor_move(arrays: dict[str, np.ndarray], index: int) -> float | None:
    parts = [
        _optional(arrays["rate_change_1d"][index]),
        _optional(arrays["policy_repricing_1d"][index]),
    ]
    usable = [abs(part) for part in parts if part is not None]
    if not usable:
        return None
    return float(max(usable))


def _spread_pips(arrays: dict[str, np.ndarray], index: int, config: EngineConfig) -> float:
    spread = _optional(arrays["spread"][index])
    if spread is None:
        return config.costs.simulated_spread_pips
    return spread / config.pip


def _stale(arrays: dict[str, np.ndarray], index: int, config: EngineConfig) -> bool:
    rate_age = _optional(arrays["rate_age_days"][index])
    return rate_age is None or rate_age > config.features.rate_stale_days


def _pips(residual: float, pip: float) -> float | None:
    if not np.isfinite(residual):
        return None
    return float(residual / pip)


def _delta(current: float, previous: float | None) -> float | None:
    if previous is None or not np.isfinite(current):
        return None
    return float(current - previous)


def _optional(value: float) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def _col(frame: Any, name: str) -> np.ndarray:
    return np.asarray(
        [np.nan if value is None else float(value) for value in frame[name].to_list()],
        dtype=float,
    )


def _sharpe(pnl: np.ndarray) -> float | None:
    if len(pnl) < 5:
        return None
    std = float(pnl.std(ddof=1))
    if std < 1e-12:
        return 0.0
    return float(pnl.mean() / std * np.sqrt(252.0))


def _max_drawdown(equity: np.ndarray) -> float | None:
    if len(equity) == 0:
        return None
    peak = np.maximum.accumulate(equity)
    drawdown = peak - equity
    return float(drawdown.max()) if len(drawdown) else None


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"
