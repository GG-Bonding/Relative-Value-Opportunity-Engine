"""Point-in-time records and the opportunity lifecycle.

Records are append-only. A later revision is a new object, never an update.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.enums import (
    AlphaLifecycle,
    DecisionState,
    Direction,
    EntryStrategy,
    ExitReason,
    ExperimentConclusion,
    FillSide,
    MechanismKind,
    MispricingClass,
    OpportunityStatus,
    RegimeName,
    SpreadSource,
    TradeReviewLabel,
)
from domain.errors import UnsupportedPairError
from domain.timeutil import ensure_utc

SUPPORTED_PAIR = "EURGBP"


def require_eurgbp(pair: str) -> str:
    if pair != SUPPORTED_PAIR:
        raise UnsupportedPairError(
            f"V1 trades {SUPPORTED_PAIR} only; refused pair {pair!r}. "
            "Other pairs wait until EURGBP has a stable out-of-sample result."
        )
    return pair


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _timestamps_are_aware(self) -> DomainModel:
        for name in self.__class__.model_fields:
            value = getattr(self, name)
            if isinstance(value, datetime):
                ensure_utc(value)
        return self


class Instrument(DomainModel):
    symbol: str
    asset_class: str
    currency: str


class Pair(DomainModel):
    symbol: str
    base: str
    quote: str
    pip: float = 0.0001

    @model_validator(mode="after")
    def _v1_pair(self) -> Pair:
        require_eurgbp(self.symbol)
        if self.base != "EUR" or self.quote != "GBP":
            raise UnsupportedPairError("EURGBP must be quoted as EUR per GBP convention: base EUR, quote GBP")
        return self


class Provenance(DomainModel):
    effective_at: datetime
    released_at: datetime
    observed_at: datetime
    ingested_at: datetime
    source: str
    version: str

    @model_validator(mode="after")
    def _order(self) -> Provenance:
        if not self.source or not self.version:
            raise ValueError("source and version are required")
        if self.observed_at < self.released_at:
            raise ValueError("observed_at cannot precede released_at")
        if self.released_at < self.effective_at:
            raise ValueError("released_at cannot precede effective_at")
        if self.ingested_at < self.observed_at:
            raise ValueError("ingested_at cannot precede observed_at")
        return self


class MarketObservation(Provenance):
    instrument: str
    pair: str
    bar_size: str
    bid: float | None = None
    ask: float | None = None
    mid: float
    volume: float | None = None

    @model_validator(mode="after")
    def _book(self) -> MarketObservation:
        require_eurgbp(self.pair)
        if self.instrument != SUPPORTED_PAIR:
            raise UnsupportedPairError("market observations in V1 are EURGBP only")
        if self.mid <= 0:
            raise ValueError("mid must be positive")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid cannot exceed ask")
        if self.bar_size not in {"1d", "1m"}:
            raise ValueError("bar_size must be 1d or 1m")
        return self


class RateObservation(Provenance):
    curve_id: str
    rate: float


class PolicyExpectation(Provenance):
    central_bank: str
    horizon: str
    expected_rate: float

    @model_validator(mode="after")
    def _policy(self) -> PolicyExpectation:
        if self.central_bank not in {"BOE", "ECB"}:
            raise ValueError("central_bank must be BOE or ECB")
        if self.horizon not in {"1M", "3M", "6M", "12M"}:
            raise ValueError("policy horizon must be an expected-path tenor, not the spot bank rate")
        return self


class MacroRelease(Provenance):
    series_id: str
    region: str
    category: str
    period: str
    actual: float
    consensus: float | None
    previous_as_known: float | None
    revision_number: int

    @model_validator(mode="after")
    def _macro(self) -> MacroRelease:
        if self.region not in {"UK", "EZ"}:
            raise ValueError("region must be UK or EZ")
        if self.revision_number < 0:
            raise ValueError("revision_number cannot be negative")
        return self


class ConsensusSnapshot(Provenance):
    series_id: str
    region: str
    category: str
    horizon: str
    expected_value: float


class PositioningObservation(Provenance):
    pair: str
    metric: str
    value: float | None

    @model_validator(mode="after")
    def _pair(self) -> PositioningObservation:
        require_eurgbp(self.pair)
        return self


class OptionObservation(Provenance):
    pair: str
    risk_reversal: float | None = None
    implied_vol: float | None = None
    open_interest: float | None = None

    @model_validator(mode="after")
    def _pair(self) -> OptionObservation:
        require_eurgbp(self.pair)
        return self


class CommodityObservation(Provenance):
    """Non-tradable event input. V1 does not trade commodities."""

    instrument: str
    price: float

    @model_validator(mode="after")
    def _brent_only(self) -> CommodityObservation:
        if self.instrument != "BRENT":
            raise UnsupportedPairError("V1 commodity input is BRENT, and only as an event driver")
        return self


class RiskObservation(Provenance):
    leg: str
    metric: str
    spread: float

    @model_validator(mode="after")
    def _leg(self) -> RiskObservation:
        if self.leg not in {"EUR", "GBP"}:
            raise ValueError("risk leg must be EUR or GBP")
        return self


class Mechanism(DomainModel):
    mechanism_id: str
    pair: str
    name: str
    kind: MechanismKind
    statement: str
    expected_sign_on_log_price: int
    feature_names: list[str]
    transmission: str

    @model_validator(mode="after")
    def _sign(self) -> Mechanism:
        require_eurgbp(self.pair)
        if self.expected_sign_on_log_price not in {-1, 1}:
            raise ValueError("expected sign must be -1 or +1")
        return self


class MechanismState(DomainModel):
    as_of: datetime
    mechanism_id: str
    structural: bool
    pattern: bool
    tradable_alpha: bool
    evidence: str


class FactorValue(DomainModel):
    name: str
    as_of: datetime
    value: float | None
    stale: bool = False


class ExpectationState(DomainModel):
    as_of: datetime
    pair: str
    expected_rate_diff_1m: float | None
    expected_rate_diff_3m: float | None
    expected_policy_diff_3m: float | None
    expected_policy_diff_6m: float | None
    expected_growth_diff: float | None
    expected_inflation_diff: float | None
    sources: dict[str, str]


class FeatureSnapshot(DomainModel):
    as_of: datetime
    pair: str
    data_snapshot_id: str
    rate: dict[str, float | None]
    policy: dict[str, float | None]
    growth: dict[str, float | None]
    inflation: dict[str, float | None]
    risk: dict[str, float | None]
    positioning: dict[str, float | None]
    momentum: dict[str, float | None]
    expectations: dict[str, float | None]
    market: dict[str, float | None]
    missing_fields: list[str]
    stale_fields: list[str]
    source_versions: dict[str, str]

    @model_validator(mode="after")
    def _pair(self) -> FeatureSnapshot:
        require_eurgbp(self.pair)
        return self


class RegimeState(DomainModel):
    as_of: datetime
    pair: str
    regime: RegimeName
    reasons: list[str]
    compatible_with_fade: bool


class FairValueEstimate(DomainModel):
    as_of: datetime
    pair: str
    market_price: float | None
    fair_value: float | None
    residual: float | None
    residual_z: float | None
    coefficients: dict[str, float]
    factor_contributions: dict[str, float]
    training_start: datetime | None
    training_end: datetime | None
    model_version: str
    r_squared: float | None
    n_train: int
    mechanism_valid: bool
    coefficient_instability: float | None


class MispricingState(DomainModel):
    as_of: datetime
    pair: str
    classification: MispricingClass
    absolute_deviation: float | None
    pips: float | None
    bps: float | None
    residual_z: float | None
    percentile: float | None
    tradable: bool
    reasons: list[str]


class AlphaHealth(DomainModel):
    ic_21d: float | None
    ic_63d: float | None
    ic_252d: float | None
    rank_ic_63d: float | None
    oos_r2: float | None
    hit_rate: float | None
    sharpe: float | None
    after_cost_pnl: float | None
    turnover: float | None
    lead_time_minutes: float | None
    reaction_lag_minutes: float | None
    signal_half_life_minutes: float | None
    coefficient_stability: float | None
    factor_stability: float | None
    residual_stability: float | None
    correlation_stability: float | None


class AlphaState(DomainModel):
    as_of: datetime
    pair: str
    lifecycle: AlphaLifecycle
    health: AlphaHealth
    reasons: list[str]
    reaction_lag_by_year: dict[str, float] = Field(default_factory=dict)


class TradeThesis(DomainModel):
    why: str
    dominant_drivers: list[str]
    expected_transmission: str
    expected_holding_period_days: float
    expected_convergence: str
    invalidation: list[str]
    potential_risks: list[str]


class Signal(DomainModel):
    as_of: datetime
    pair: str
    direction: Direction | None
    strength: float | None
    source: str
    model_version: str


class EntryZone(DomainModel):
    low: float | None
    high: float | None
    strategy: EntryStrategy


class Opportunity(DomainModel):
    opportunity_id: str
    pair: str
    direction: Direction | None
    discovered_at: datetime
    market_price: float | None
    fair_value: float | None
    mispricing: float | None
    mispricing_z: float | None
    fundamental_score: float | None
    fundamental_attribution: dict[str, float | None]
    expected_fundamentals: dict[str, float | None]
    regime: RegimeName
    alpha_state: AlphaLifecycle
    alpha_health: AlphaHealth
    positioning_state: str
    crowding: float | None
    opportunity_score: float | None
    score_components: dict[str, float | None]
    expected_edge: float | None
    expected_cost: float | None
    expected_net_edge: float | None
    entry_strategy: EntryStrategy
    entry_zone: EntryZone
    target_zone: EntryZone
    expected_holding_period: float | None
    trade_thesis: TradeThesis
    invalidation_conditions: list[str]
    status: OpportunityStatus
    decision: DecisionState
    decision_reasons: list[str]


class Order(DomainModel):
    order_id: str
    opportunity_id: str
    pair: str
    side: FillSide
    quantity: float
    created_at: datetime
    executable_at: datetime
    strategy: EntryStrategy


class Fill(DomainModel):
    fill_id: str
    order_id: str
    pair: str
    side: FillSide
    quantity: float
    price: float
    filled_at: datetime
    spread_source: SpreadSource
    spread_pips: float
    slippage_pips: float
    fee_pips: float


class Position(DomainModel):
    position_id: str
    pair: str
    direction: Direction
    quantity: float
    entry_price: float
    opened_at: datetime
    opportunity_id: str
    entry_fair_value: float
    entry_snapshot: dict[str, float | None]


class Trade(DomainModel):
    trade_id: str
    pair: str
    direction: Direction
    entry: Fill
    exit: Fill
    pnl: float
    pnl_pips: float
    expected_pnl: float | None
    captured_edge: float | None
    holding_period_days: float
    execution_cost: float
    attribution: dict[str, float]
    unexplained_pnl: float
    review: TradeReviewLabel
    exit_reasons: list[ExitReason]
    opportunity_id: str
    entry_regime: str = ""


class DataSnapshot(DomainModel):
    snapshot_id: str
    as_of: datetime
    pair: str
    row_counts: dict[str, int]
    content_hash: str


class ModelVersion(DomainModel):
    model_version: str
    family: str
    description: str
    champion: bool


class ResearchExperiment(DomainModel):
    experiment_id: str
    hypothesis: str
    mechanism: str
    dataset: str
    features: list[str]
    model: str
    train_period: str
    validation_period: str
    test_period: str
    cost_assumption: str
    oos_result: dict[str, Any]
    conclusion: ExperimentConclusion
    status: str


class BacktestRun(DomainModel):
    run_id: str
    pair: str
    git_commit: str
    config_hash: str
    data_snapshot_hash: str
    model_version: str
    random_seed: int
    start: datetime
    end: datetime
    spread_source: SpreadSource
    n_trades: int
    pnl: float
    pnl_after_costs: float
    sharpe: float | None
    max_drawdown: float | None
    deterministic_hash: str
