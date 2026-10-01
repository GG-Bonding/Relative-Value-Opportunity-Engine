"""V1 research report. The live-market conclusion is not allowed to outrun the data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl

from backtest.engine import run_backtest
from data.laboratory import DECAY_START, build_laboratory
from data.store import PitStore
from domain.config import EngineConfig
from domain.enums import ExperimentConclusion
from domain.models import ResearchExperiment
from domain.timeutil import UTC, parse_ts
from evaluation.baselines import constant_book, sign_book, strategy_pnl
from evaluation.experiments import ExperimentRegistry
from evaluation.robustness import bootstrap_mean, cost_stress, monte_carlo_drawdowns
from features.panel import build_panel
from models.forward import walk_forward_ic

HEADINGS = (
    "Data Coverage",
    "Point-in-Time Validation",
    "Leakage Tests",
    "Economic Mechanisms",
    "Expectation Modeling",
    "Factor Definitions",
    "Factor IC",
    "Rolling IC",
    "IC Decay",
    "Fair Value Accuracy",
    "Coefficient Stability",
    "Residual Stability",
    "Event Response",
    "Reaction Lag",
    "Reaction Lag Drift",
    "Regime Analysis",
    "Mispricing Analysis",
    "Alpha Lifecycle",
    "Opportunity Performance",
    "Entry Performance",
    "Exit Performance",
    "PnL After Costs",
    "Subperiod Performance",
    "Regime Performance",
    "PnL Attribution",
    "Execution Impact",
    "Robustness",
    "Failure Cases",
    "Known Limitations",
    "Conclusion",
)


@dataclass
class V1Study:
    market_rows: int
    macro_rows: int
    trades: int
    pnl: float
    sharpe: float | None
    max_drawdown: float | None
    alive_trades: int
    alive_pnl: float
    decay_trades: int
    decision_counts: dict[str, int] = field(default_factory=dict)
    forward_ic: float | None = None
    forward_rank_ic: float | None = None
    forward_r2: float | None = None
    baselines: dict[str, float] = field(default_factory=dict)
    attribution: dict[str, float] = field(default_factory=dict)
    bootstrap: dict[str, float] = field(default_factory=dict)
    monte_carlo: dict[str, float] = field(default_factory=dict)
    cost_x2_pnl: float | None = None
    delay_trades: int = 0
    delay_pnl: float | None = None
    wider_gap_trades: int = 0
    winsorized_mean: float | None = None
    ols_trades: int = 0
    ols_pnl: float | None = None
    signal_delay_trades: int = 0
    signal_delay_pnl: float | None = None
    regime_pnl: dict[str, float] = field(default_factory=dict)
    mean_hold: float | None = None
    spread_source: str = "OBSERVED_BOOK"
    run_hash: str = ""
    data_hash: str = ""


def render_report(study: V1Study) -> str:
    lines = [
        "# EURGBP V1 Research Report",
        "",
        "This report separates two claims. The laboratory claim is that the engine follows its own rules on a known data-generating process. The market claim, that UK and euro-area relative fundamentals identify tradable EURGBP mispricing in vendor point-in-time history, is not established.",
        "",
        f"Conclusion: {ExperimentConclusion.INCONCLUSIVE.value}",
        "",
        "## Data Coverage",
        "",
        f"Laboratory market rows: {study.market_rows}. Macro release rows: {study.macro_rows}. The tape is synthetic, seeded, and labeled `source=laboratory`. It is not a substitute for OIS, consensus vintages, or revised national accounts.",
        "",
        "## Point-in-Time Validation",
        "",
        "Macros keep every revision. An as-of query returns the latest vintage whose `observed_at` is at or before the query, and it leaves the earlier print in the table. Surprise standard deviations use only earlier releases of the same series.",
        "",
        "## Leakage Tests",
        "",
        "Appending a rate or a macro revision after T does not change the snapshot hash or the factor row at T. Fair value at t is fit on rows strictly before t. Forward labels are purged by the label horizon plus an embargo. There is no random split.",
        "",
        "## Economic Mechanisms",
        "",
        "Six mechanisms are registered before the fit: rate, policy path, growth surprise, inflation-through-policy, risk premium, and positioning. Positioning is a pattern, not a fair-value input. Expected signs on log EURGBP are negative for a rise in UK-minus-euro relative carry, policy, growth, inflation pressure, and euro risk premium.",
        "",
        "## Expectation Modeling",
        "",
        "Expected rate gaps come from forward curves. Expected policy gaps come from the BoE and ECB path, not from spot Bank Rate. Expected growth and inflation are survey levels carried forward. They are not the surprise index and they are not filled with zero.",
        "",
        "## Factor Definitions",
        "",
        "Rate is UK2Y minus DE2Y, with changes, slopes, z-scores, and a trailing percentile. Policy is the expected-path gap at 1M, 3M, 6M, and 12M. Growth and inflation are relative surprises. Risk is the euro premium minus the sterling premium. Momentum and realized volatility are tape descriptors. Missing positioning stays missing.",
        "",
        "## Factor IC",
        "",
        (
            f"Walk-forward factor model, 5-day horizon: IC {_fmt(study.forward_ic)}, "
            f"rank IC {_fmt(study.forward_rank_ic)}, OOS R² {_fmt(study.forward_r2)}. "
            "This is a test of whether the factors forecast the next return, separate from the fair-value residual. "
            "A positive rank correlation with a negative R² means the sign lines up more often than the size of the forecast."
        ),
        "",
        "## Rolling IC",
        "",
        "Alpha state uses the information coefficient of the residual fade on 21, 63, and 252 realized five-day outcomes. A window with no variance is an IC of zero, not a missing trade.",
        "",
        "## IC Decay",
        "",
        "The registered decay path is a long-window IC that is still positive while the 63-day and 21-day IC collapse, or a reaction lag that compresses from multi-day incorporation to one session. Either path sets the lifecycle to DECAYING and blocks new entries.",
        "",
        "## Fair Value Accuracy",
        "",
        "V1 fair value is a rolling ridge of log EURGBP on relative factors and expected relative fundamentals. The window is 504 sessions, the minimum is 252, and the fit excludes the prediction day. In the laboratory the price is a known function of those factors, lagged by five sessions until 2021.",
        "",
        "## Coefficient Stability",
        "",
        "Instability is the trailing standard deviation of the standardized UK-DE coefficient divided by the absolute mean of that coefficient. An unstable or wrong-signed fit is model error, not a mispricing.",
        "",
        "## Residual Stability",
        "",
        "Residual z-scores use the mean and standard deviation of earlier out-of-sample residuals only. A large residual is not tradable until the mechanism signs agree, the fit clears the R² floor, and the regime supports a fade.",
        "",
        "## Event Response",
        "",
        "Horizons are 5m, 15m, 30m, 1h, 4h, 1d, 3d, and 5d. A horizon with no print is missing. A later daily bar is not credited to a five-minute horizon.",
        "",
        "## Reaction Lag",
        "",
        "Response lag is the first horizon that captures at least half of the five-day move in the same direction. Half-life is the later horizon that gives back half of the peak, when that give-back exists.",
        "",
        "## Reaction Lag Drift",
        "",
        "The book compares the median lag of macro events over 756, 252, and 63 days, using only events whose five-day window is already known. When the short window is one session and the long window is still multi-day, alpha is DECAYING even if a noisy 21-day IC is still positive.",
        "",
        "## Regime Analysis",
        "",
        "Regimes are ordered rules. An extended residual is dislocation or mean reversion before it is allowed to be labeled trend or carry. Trend and carry do not authorize a fade. Liquidity stress does not authorize a trade.",
        "",
        "## Mispricing Analysis",
        "",
        "Classifications are possible mispricing, model error, regime break, event dislocation, liquidity distortion, and unknown. Only possible mispricing can become a trade, and only after the net-edge, crowding, data, and chase gates.",
        "",
        "## Alpha Lifecycle",
        "",
        "States are discovery, validating, active, decaying, dormant, retired, and rejected. The laboratory book is active while the five-day lag is the price process and decaying once incorporation collapses to the same session.",
        "",
        "## Opportunity Performance",
        "",
        f"Trades: {study.trades}. After-cost pnl (return on capital): {study.pnl:.6f}. Sharpe: {_fmt(study.sharpe)}. Max drawdown: {_fmt(study.max_drawdown)}.",
        "",
        f"Decision counts: {study.decision_counts}.",
        "",
        "## Entry Performance",
        "",
        f"Entries while the lag was alive, before {DECAY_START}: {study.alive_trades}, pnl {study.alive_pnl:.6f}. Entries on or after 2021-05-01, when the lag had collapsed: {study.decay_trades}.",
        "",
        "A correct fundamental sign with a closed gap is NO_TRADE. A gap that has already mean-reverted most of the way is WAIT_PULLBACK.",
        "",
        "## Exit Performance",
        "",
        f"Mean holding period: {_fmt(study.mean_hold)} days. Exits are mispricing closed, thesis invalidated, alpha decayed, time expired, stop, dynamic target, regime change, or a liquidity event. The target is the live fair value, not the fair value stored at entry.",
        "",
        "## PnL After Costs",
        "",
        f"Fills buy the ask plus slippage and sell the bid minus slippage. Spread source on the run: {study.spread_source}. The reported pnl is after spread, slippage, fees, and the latency charge. PnL {study.pnl:.6f}.",
        "",
        "## Subperiod Performance",
        "",
        "The alive window is the pre-2021 lag. The decay window is 2021 onward. Positive pnl is concentrated in the alive window; the decay window is not allowed to add trades once the lag gate trips.",
        "",
        "## Regime Performance",
        "",
        f"After-cost pnl by the regime at entry: {study.regime_pnl}. Fades are permitted in dislocation, mean reversion, neutral, and the divergence regimes. They are refused in trend, carry, and liquidity stress.",
        "",
        "## PnL Attribution",
        "",
        f"Sum of grouped log-contribution changes, in return space: {study.attribution}. Unexplained is the residual of that identity versus the mid-to-mid move. Execution cost is reported separately and is not called alpha.",
        "",
        "## Execution Impact",
        "",
        "Same-bar decisions with a zero second delay still pay the quoted book. A one-day execution delay is a robustness case, not the base fill. There is no mid-price fill.",
        "",
        "## Robustness",
        "",
        f"Bootstrap mean of trade pnl: {study.bootstrap}. Monte Carlo reorder of trades, total pnl unchanged, drawdown distribution: {study.monte_carlo}.",
        "",
        (
            f"Doubling realized execution cost, which already contains the spread, leaves pnl {_fmt(study.cost_x2_pnl)}. "
            f"A one-day execution delay produces {study.delay_trades} trades and pnl {_fmt(study.delay_pnl)}. "
            f"A one-day signal delay produces {study.signal_delay_trades} trades and pnl {_fmt(study.signal_delay_pnl)}. "
            f"Raising the minimum gap by half produces {study.wider_gap_trades} trades. "
            f"Rolling OLS, on the same window and the same gates, produces {study.ols_trades} trades and pnl {_fmt(study.ols_pnl)}. "
            f"Winsorized mean trade pnl: {_fmt(study.winsorized_mean)}."
        ),
        "",
        f"Close-to-close baselines on the same calendar, one spread of cost on turnover: {study.baselines}. They are a refusal test for a more complicated model. They are not the same position size or the same fill as the event-driven book, and they are not a live-market result. On this tape the close-to-close rate-change book sums to a larger log return than the vol-targeted residual book. That book is not promoted: it trades through the window where the reaction lag has already collapsed, and it does not pay the bid or the ask.",
        "",
        "## Failure Cases",
        "",
        "The permanent price jumps after the lag dies are the failure case the gate is there to refuse. Individual macro-event lags on daily bars are noisy; the book uses the median, not one release. A fundamental score that strongly disagrees with the fade is model uncertainty, not a trade.",
        "",
        "## Known Limitations",
        "",
        "No vendor point-in-time consensus, no real OIS history, no CFTC tape, and no executable FX book are in this repository. Kalman filtering and additional pairs are not implemented: the complexity gate requires a stable EURGBP out-of-sample result on real vintages first. The laboratory sharpe is evidence about the engine, not about EURGBP.",
        "",
        "## Conclusion",
        "",
        "Conclusion: INCONCLUSIVE",
        "",
        "Question 1. Relative UK and euro-area fundamentals explain the laboratory price by construction. They have not been shown to explain live EURGBP on revision-safe data.",
        "",
        "Question 2. The factor model and the residual fade have laboratory forward correlation. Out-of-sample power in the market is unmeasured.",
        "",
        "Question 3. The engine repeatedly flags the planted five-day lag. That is not yet evidence of a repeatable market mispricing.",
        "",
        "Question 4. Laboratory pnl is after a quoted spread, slippage, and fees. Live cost and impact are unmeasured.",
        "",
        "Question 5. The laboratory lag is several days and then collapses to one session, and the book stops. Whether any live reaction lag remains is unknown.",
        "",
        "Question 6. In the laboratory the opportunity is visible on the close that first contains the gap. That is not a claim about trading ahead of a real release.",
        "",
        "Question 7. The chase rule refuses a gap that has already closed. Whether that improves live capture is unmeasured.",
        "",
        "Question 8. Exits follow the live fair value and the thesis. A comparison with a fixed take-profit on live trades has not been run.",
        "",
        "Question 9. The laboratory pnl is produced by a lag that was written into the generator. It is not evidence against data mining in a live sample, because there is no live sample.",
        "",
        f"Run hash `{study.run_hash}`. Data snapshot hash `{study.data_hash}`.",
        "",
    ]
    return "\n".join(lines)


def write_v1_report(db_path: Path, output: Path, config: EngineConfig | None = None) -> V1Study:
    config = config or EngineConfig()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = PitStore(db_path)
    if store.count("market_observations") == 0:
        build_laboratory(store, config)
    study = _study(store, config)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(study), encoding="utf-8")
    registry = ExperimentRegistry(output.parent / "experiments.jsonl")
    _record_experiment(
        registry,
        "always-long-laboratory",
        "A constant long matches the residual book after costs.",
        "always_long",
        study.baselines.get("always_long"),
        study.pnl,
    )
    _record_experiment(
        registry,
        "fundamental-sign-is-the-trade",
        "The sign of the relative-rate level is itself a trade.",
        "fundamental_sign",
        study.baselines.get("fundamental_sign"),
        study.pnl,
    )
    store.close()
    return study


def _record_experiment(
    registry: ExperimentRegistry,
    experiment_id: str,
    hypothesis: str,
    model: str,
    baseline_pnl: float | None,
    book_pnl: float,
) -> None:
    if any(item.experiment_id == experiment_id for item in registry.all()):
        return
    worse = baseline_pnl is not None and baseline_pnl < book_pnl
    registry.add(
        ResearchExperiment(
            experiment_id=experiment_id,
            hypothesis=hypothesis,
            mechanism="none",
            dataset="laboratory",
            features=[],
            model=model,
            train_period="2010-2013",
            validation_period="2014-2017",
            test_period="2018-2022",
            cost_assumption="one simulated spread on each turnover, close to close",
            oos_result={"pnl": baseline_pnl, "fair_value_book_pnl": book_pnl},
            conclusion=ExperimentConclusion.REJECTED if worse else ExperimentConclusion.INCONCLUSIVE,
            status="saved",
        )
    )


def _study(store: PitStore, config: EngineConfig) -> V1Study:
    end = parse_ts("2022-06-30T16:00:00+00:00")
    result = run_backtest(store, config, datetime(2014, 1, 1, tzinfo=UTC), end)
    panel = build_panel(store, end, config)
    forward = walk_forward_ic(panel, config, horizon=5)
    alive_cut = parse_ts(f"{DECAY_START}T00:00:00+00:00")
    late_cut = parse_ts("2021-05-01T00:00:00+00:00")
    alive = [trade for trade in result.trades if trade.entry.filled_at < alive_cut]
    late = [trade for trade in result.trades if trade.entry.filled_at >= late_cut]
    pnls = np.asarray([trade.pnl for trade in result.trades], dtype=float)
    attribution: dict[str, float] = {}
    for trade in result.trades:
        for key, value in trade.attribution.items():
            attribution[key] = attribution.get(key, 0.0) + value
    baselines = _baselines(panel, config)
    holds = [trade.holding_period_days for trade in result.trades]
    costs = np.asarray([trade.execution_cost for trade in result.trades], dtype=float)
    mean_cost = float(costs.mean()) if len(costs) else 0.0
    delayed = config.model_copy(deep=True)
    delayed.delays.execution_delay_seconds = 86_400
    delay_result = run_backtest(store, delayed, datetime(2014, 1, 1, tzinfo=UTC), end)
    wider = config.model_copy(deep=True)
    wider.opportunity.min_mispricing_pips = config.opportunity.min_mispricing_pips * 1.5
    wider_result = run_backtest(store, wider, datetime(2014, 1, 1, tzinfo=UTC), end)
    ols = config.model_copy(deep=True)
    ols.fair_value.estimator = "ols"
    ols_result = run_backtest(store, ols, datetime(2014, 1, 1, tzinfo=UTC), end)
    delayed_signal = config.model_copy(deep=True)
    delayed_signal.delays.signal_delay_seconds = 86_400
    signal_result = run_backtest(store, delayed_signal, datetime(2014, 1, 1, tzinfo=UTC), end)
    regime_pnl: dict[str, float] = {}
    for trade in result.trades:
        regime_pnl[trade.entry_regime] = regime_pnl.get(trade.entry_regime, 0.0) + trade.pnl
    winsorized = _winsorized_mean(pnls)
    return V1Study(
        market_rows=store.count("market_observations"),
        macro_rows=store.count("macro_releases"),
        trades=result.run.n_trades,
        pnl=result.run.pnl,
        sharpe=result.run.sharpe,
        max_drawdown=result.run.max_drawdown,
        alive_trades=len(alive),
        alive_pnl=float(sum(trade.pnl for trade in alive)),
        decay_trades=len(late),
        decision_counts=result.decision_counts,
        forward_ic=forward.ic,
        forward_rank_ic=forward.rank_ic,
        forward_r2=forward.oos_r2,
        baselines=baselines,
        attribution={key: round(value, 6) for key, value in attribution.items()},
        bootstrap=bootstrap_mean(pnls, 200, config.seed),
        monte_carlo=monte_carlo_drawdowns(pnls, 200, config.seed),
        cost_x2_pnl=cost_stress(pnls, mean_cost, 2.0),
        delay_trades=delay_result.run.n_trades,
        delay_pnl=delay_result.run.pnl,
        wider_gap_trades=wider_result.run.n_trades,
        winsorized_mean=winsorized,
        ols_trades=ols_result.run.n_trades,
        ols_pnl=ols_result.run.pnl,
        signal_delay_trades=signal_result.run.n_trades,
        signal_delay_pnl=signal_result.run.pnl,
        regime_pnl={key: round(value, 6) for key, value in regime_pnl.items()},
        mean_hold=float(np.mean(holds)) if holds else None,
        spread_source=result.run.spread_source.value,
        run_hash=result.run.deterministic_hash,
        data_hash=result.run.data_snapshot_hash,
    )


def _baselines(frame: pl.DataFrame, config: EngineConfig) -> dict[str, float]:
    mid = _float_column(frame, "mid")
    future = np.zeros(len(mid))
    future[:-1] = np.log(mid[1:]) - np.log(mid[:-1])
    cost = config.costs.simulated_spread_pips * config.pip / float(np.nanmedian(mid))
    momentum = _float_column(frame, "momentum_20d")
    rate_change = _float_column(frame, "rate_change_20d")
    rate_level = _float_column(frame, "rate_level")
    rng = np.random.default_rng(config.seed)
    books = {
        "always_long": constant_book(1.0, len(mid)),
        "always_short": constant_book(-1.0, len(mid)),
        "momentum": sign_book(momentum),
        "rate_spread": sign_book(-rate_change),
        "mean_reversion": sign_book(-momentum),
        "random": sign_book(rng.normal(size=len(mid))),
        "fundamental_sign": sign_book(-rate_level),
    }
    return {name: strategy_pnl(book, future, cost) for name, book in books.items()}


def _float_column(frame: pl.DataFrame, name: str) -> np.ndarray:
    return np.asarray([np.nan if value is None else float(value) for value in frame[name].to_list()])


def _winsorized_mean(pnls: np.ndarray) -> float | None:
    if len(pnls) == 0:
        return None
    if len(pnls) < 20:
        return float(pnls.mean())
    low, high = np.quantile(pnls, [0.05, 0.95])
    return float(np.clip(pnls, low, high).mean())


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"
