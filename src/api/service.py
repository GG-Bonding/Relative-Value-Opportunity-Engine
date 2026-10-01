"""Shared operations for the CLI and the HTTP API."""

from __future__ import annotations

from datetime import datetime

from alpha.lifecycle import AlphaLifecycle
from data.store import PitStore
from data.validate import validate_store
from domain.config import EngineConfig
from domain.enums import RegimeName
from domain.models import Opportunity, require_eurgbp
from domain.timeutil import UTC, parse_ts
from execution.costs import expected_net_edge_pips
from execution.decision import DecisionInput, decide
from features.panel import build_panel
from features.snapshot import build_snapshot, expectation_state
from mechanisms.registry import assess_mechanisms, registry
from mispricing.engine import classify_mispricing
from models.fair_value import FairValuePath, attach_fair_value
from opportunities.live import opportunity_from_path
from regimes.engine import classify_regime


class EngineService:
    def __init__(self, store: PitStore, config: EngineConfig | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()
        self._path: FairValuePath | None = None
        self._key: str | None = None

    def ready(self) -> bool:
        return self.store.count("market_observations") > 0

    def mechanisms(self) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in registry()]

    def factors(self, as_of: datetime | None = None) -> dict[str, object]:
        snapshot = build_snapshot(self.store, as_of or self.latest_as_of(), self.config)
        return snapshot.model_dump(mode="json")

    def expectations(self, as_of: datetime | None = None) -> dict[str, object]:
        snapshot = build_snapshot(self.store, as_of or self.latest_as_of(), self.config)
        return expectation_state(snapshot).model_dump(mode="json")

    def fair_value(self, as_of: datetime | None = None) -> dict[str, object]:
        path = self._fair_path(as_of or self.latest_as_of())
        if path.frame.is_empty():
            return {"fair_value": None, "reason": "no EURGBP bar"}
        row = path.row(-1)
        return {
            "as_of": row["observed_at"],
            "market_price": row["mid"],
            "fair_value": row["fair_value"],
            "residual": row["residual"],
            "residual_z": row["residual_z"],
            "r_squared": row["fair_r2"],
            "mechanism_valid": row["mechanism_valid"],
            "coefficients": path.coefficients[-1],
            "factor_contributions": path.contributions[-1],
            "training_start": row["train_start"],
            "training_end": row["train_end"],
            "model_version": "eurgbp-rolling-ridge-v1",
        }

    def regime(self, as_of: datetime | None = None) -> dict[str, object]:
        path = self._fair_path(as_of or self.latest_as_of())
        row = path.row(-1)
        name, reasons = _regime(row)
        return {"as_of": row["observed_at"], "regime": name.value, "reasons": reasons}

    def current_opportunity(self, as_of: datetime | None = None) -> Opportunity:
        """One snapshot. Alpha stays unpromoted until a walk-forward backtest says otherwise."""
        path = self._fair_path(as_of or self.latest_as_of())
        row = path.row(-1)
        regime_name, _regime_reasons = _regime(row)
        residual = _opt(row.get("residual"))
        pips = None if residual is None else residual / self.config.pip
        classification, _class_reasons, _tradable = classify_mispricing(
            residual_z=_opt(row.get("residual_z")),
            r_squared=_opt(row.get("fair_r2")),
            mechanism_valid=_opt(row.get("mechanism_valid")) == 1.0,
            coefficient_instability=_opt(row.get("coefficient_instability")),
            event_age_days=_opt(row.get("event_age_days")),
            event_abs_surprise=_opt(row.get("last_event_abs_surprise")),
            factor_move=_opt(row.get("rate_change_5d")),
            price_jump=_opt(row.get("momentum_5d")),
            spread_z=None,
            config=self.config,
        )
        _edge, cost, net = expected_net_edge_pips(
            pips,
            self.config.opportunity.prior_convergence_probability,
            self.config.costs,
            self.config.costs.simulated_spread_pips,
        )
        decision = decide(
            DecisionInput(
                mispricing_pips=pips,
                fundamental=None,
                classification=classification,
                regime=regime_name,
                alpha=AlphaLifecycle.DISCOVERY,
                crowding=None,
                net_edge_pips=net,
                missing_critical=residual is None,
                stale_critical=False,
                chase=False,
                breakout_pending=False,
                reaction_lag_blocked=False,
            ),
            self.config,
        )
        return opportunity_from_path(
            path,
            -1,
            AlphaLifecycle.DISCOVERY,
            self.config,
            decision.state,
            decision.reasons,
            net,
            cost,
            regime_name,
        )

    def mechanism_state(self, as_of: datetime | None = None) -> list[dict[str, object]]:
        path = self._fair_path(as_of or self.latest_as_of())
        moment = parse_ts(str(path.row(-1)["observed_at"]))
        states = assess_mechanisms(
            as_of=moment,
            coefficients=path.coefficients[-1],
            alpha=AlphaLifecycle.VALIDATING,
            tradable_net_edge=None,
        )
        return [item.model_dump(mode="json") for item in states]

    def validate(self) -> dict[str, object]:
        report = validate_store(self.store)
        return {"ok": report.ok, "errors": report.errors, "warnings": report.warnings}

    def latest_as_of(self) -> datetime:
        frame = self.store.history("market_observations", datetime(2099, 1, 1, tzinfo=UTC))
        if frame.is_empty():
            raise ValueError("no EURGBP market data")
        return parse_ts(str(frame["observed_at"].max()))

    def require_pair(self, pair: str) -> str:
        return require_eurgbp(pair)

    def _fair_path(self, as_of: datetime) -> FairValuePath:
        key = self.store.snapshot_hash(as_of)
        if self._path is None or self._key != key:
            panel = build_panel(self.store, as_of, self.config)
            self._path = attach_fair_value(panel, self.config)
            self._key = key
        return self._path


def _regime(row: dict[str, object]) -> tuple[RegimeName, list[str]]:
    return classify_regime(
        residual_z=_opt(row.get("residual_z")),
        spread_z=None,
        policy_repricing_5d=_opt(row.get("policy_repricing_5d")),
        rate_change_20d=_opt(row.get("rate_change_20d")),
        inflation_diff=_opt(row.get("inflation_diff")),
        growth_diff=_opt(row.get("growth_diff")),
        risk_change_5d=None,
        realized_vol_20d=_opt(row.get("realized_vol_20d")),
        momentum_20d=_opt(row.get("momentum_20d")),
        momentum_60d=_opt(row.get("momentum_60d")),
        rate_level=_opt(row.get("rate_level")),
    )


def _opt(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number
