"""Live orders are a separate gate from a research sample.

Shadow and paper may record a long or short while alpha is still DISCOVERY.
A real order needs live mode, an ACTIVE lifecycle, a ready model, healthy
inputs, a long or short, and a net edge above the cost threshold. Flattening
an open book does not require those research gates.
"""

from __future__ import annotations

from domain.enums import AlphaLifecycle, DataHealth, ExecutionMode, ModelReadiness


def assess_data_health(
    *,
    rate_diff: float | None,
    bid: float | None,
    ask: float | None,
    fair_value: float | None,
    decision: str | None,
) -> DataHealth:
    """Healthy means the quote, both curves, and a fair value are all present."""
    if decision == "DATA_DEGRADED":
        return DataHealth.DEGRADED
    if rate_diff is None or bid is None or ask is None or fair_value is None:
        return DataHealth.DEGRADED
    if ask < bid:
        return DataHealth.DEGRADED
    return DataHealth.HEALTHY


def live_order_allowed(
    *,
    mode: str | None,
    alpha: str | None,
    model_readiness: str | None,
    data_health: str | None,
    decision: str | None,
    net_edge_pips: float | None,
    min_net_edge_pips: float,
    assessment: str,
) -> tuple[bool, str]:
    """Whether this call may reach the broker. A refusal explains which gate failed."""
    if mode != ExecutionMode.LIVE.value:
        return False, "--trade requires --mode live"
    if assessment == "REVERSAL":
        return True, "flatten the open book"
    if decision not in {"LONG", "SHORT"}:
        return False, "decision is not long or short"
    if alpha != AlphaLifecycle.ACTIVE.value:
        shown = alpha or "missing"
        return False, f"alpha is {shown}; live orders require ACTIVE"
    if model_readiness != ModelReadiness.READY.value:
        return False, "model is not READY"
    if data_health != DataHealth.HEALTHY.value:
        return False, "data is not HEALTHY"
    if net_edge_pips is None or net_edge_pips <= min_net_edge_pips:
        return False, "expected net edge does not clear the cost threshold"
    return True, "live gates passed"
