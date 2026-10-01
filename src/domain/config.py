"""All decision thresholds live here so tests can move one knob at a time."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.hashing import stable_hash
from domain.models import require_eurgbp

MODEL_VERSION = "eurgbp-rolling-ridge-v1"


class FairValueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: int = 504
    min_observations: int = 252
    ridge_alpha: float = 1.0
    estimator: str = "ridge"
    embargo: int = 1
    min_r2: float = 0.25
    stability_window: int = 60
    max_coef_instability: float = 1.5
    min_abs_coefficient: float = 0.001
    residual_z_lookback: int = 252
    min_residual_history: int = 60


class ForwardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horizons: list[int] = Field(default_factory=lambda: [1, 5, 20])
    embargo: int = 1
    fold_test_days: int = 63
    min_observations: int = 252


class CostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulated_spread_pips: float = 1.2
    slippage_pips: float = 0.3
    fee_pips: float = 0.1
    latency_cost_pips: float = 0.05


class DelayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_delay_seconds: int = 0
    processing_delay_seconds: int = 0
    signal_delay_seconds: int = 0
    execution_delay_seconds: int = 0


class AlphaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ic_horizon: int = 5
    active_ic: float = 0.03
    decaying_long_ic: float = 0.05
    short_to_long_ratio: float = 0.25
    mid_to_long_ratio: float = 0.6
    min_tradable_lag_minutes: float = 10.0
    lag_compression_ratio: float = 4.0


class OpportunityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_mispricing_pips: float = 12.0
    min_net_edge_pips: float = 1.0
    max_crowding: float = 0.85
    chase_fraction: float = 0.6
    fundamental_conflict: float = 0.35
    prior_convergence_probability: float = 0.55
    min_hit_rate_sample: int = 30
    residual_z_entry: float = 1.5
    event_confirmation_days: float = 1.0


class EntryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pullback_retrace: float = 0.3
    recent_lookback: int = 5


class ExitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    close_pips: float = 8.0
    stop_pips: float = 40.0
    max_hold_multiple: float = 2.0
    expected_hold_days: float = 8.0
    convergence_fraction: float = 0.3
    rate_invalidation: float = 0.25
    policy_invalidation: float = 0.15
    growth_invalidation: float = 0.75
    inflation_invalidation: float = 0.75
    risk_invalidation: float = 0.15
    momentum_invalidation: float = 0.01


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_annual_vol: float = 0.08
    max_position: float = 1.0
    max_pair_exposure: float = 1.0
    max_daily_loss: float = 0.02
    max_drawdown: float = 0.10
    risk_budget: float = 0.02


class FundamentalWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate: float = 0.30
    policy: float = 0.25
    growth: float = 0.20
    inflation: float = 0.15
    risk: float = 0.10


class ScoreWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fundamental_edge: float = 0.20
    mispricing: float = 0.25
    regime: float = 0.15
    alpha: float = 0.15
    positioning: float = 0.10
    cost: float = 0.10
    execution: float = 0.05


class FeatureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surprise_min_history: int = 8
    macro_stale_days: int = 90
    rate_stale_days: int = 5
    market_stale_days: int = 3
    info_half_life_days: float = 60.0


class EngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair: str = "EURGBP"
    pip: float = 0.0001
    seed: int = 7
    fair_value: FairValueConfig = Field(default_factory=FairValueConfig)
    forward: ForwardConfig = Field(default_factory=ForwardConfig)
    costs: CostConfig = Field(default_factory=CostConfig)
    delays: DelayConfig = Field(default_factory=DelayConfig)
    alpha: AlphaConfig = Field(default_factory=AlphaConfig)
    opportunity: OpportunityConfig = Field(default_factory=OpportunityConfig)
    entry: EntryConfig = Field(default_factory=EntryConfig)
    exit: ExitConfig = Field(default_factory=ExitConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    fundamental: FundamentalWeights = Field(default_factory=FundamentalWeights)
    score: ScoreWeights = Field(default_factory=ScoreWeights)
    features: FeatureConfig = Field(default_factory=FeatureConfig)

    @model_validator(mode="after")
    def _pair(self) -> EngineConfig:
        require_eurgbp(self.pair)
        if self.fair_value.min_observations < 1 or self.fair_value.window < self.fair_value.min_observations:
            raise ValueError("fair-value window must be at least the minimum observation count")
        if self.fair_value.embargo < 1:
            raise ValueError("fair-value embargo must exclude the prediction day")
        return self

    def hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


def load_config(path: Path | None = None) -> EngineConfig:
    if path is None:
        return EngineConfig()
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return EngineConfig.model_validate(payload)
