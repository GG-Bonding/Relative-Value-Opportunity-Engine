"""A research sample cannot reach the broker."""

from __future__ import annotations

from pathlib import Path

from api.cli import main
from execution.live_gate import live_order_allowed

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "eurgbp.toml"


def test_trade_without_live_mode_is_refused(tmp_path: Path, capsys) -> None:
    code = main(["--db", str(tmp_path / "watch.duckdb"), "--config", str(CONFIG), "watch", "--trade"])
    assert code == 2
    assert "--trade requires --mode live" in capsys.readouterr().err


def test_discovery_long_is_not_a_live_order() -> None:
    allowed, reason = live_order_allowed(
        mode="LIVE",
        alpha="DISCOVERY",
        model_readiness="LOW",
        data_health="HEALTHY",
        decision="SHORT",
        net_edge_pips=20.0,
        min_net_edge_pips=1.0,
        assessment="SIGNAL",
    )
    assert allowed is False
    assert "ACTIVE" in reason


def test_validating_cannot_open_and_a_reversal_can_flatten() -> None:
    blocked, _reason = live_order_allowed(
        mode="LIVE",
        alpha="VALIDATING",
        model_readiness="LOW",
        data_health="HEALTHY",
        decision="LONG",
        net_edge_pips=20.0,
        min_net_edge_pips=1.0,
        assessment="SIGNAL",
    )
    assert blocked is False
    allowed, reason = live_order_allowed(
        mode="LIVE",
        alpha="DISCOVERY",
        model_readiness="LOW",
        data_health="DEGRADED",
        decision="WATCH",
        net_edge_pips=None,
        min_net_edge_pips=1.0,
        assessment="REVERSAL",
    )
    assert allowed is True
    assert reason == "flatten the open book"


def test_an_active_healthy_long_clears_the_gate() -> None:
    allowed, reason = live_order_allowed(
        mode="LIVE",
        alpha="ACTIVE",
        model_readiness="READY",
        data_health="HEALTHY",
        decision="LONG",
        net_edge_pips=4.0,
        min_net_edge_pips=1.0,
        assessment="SIGNAL",
    )
    assert allowed is True
    assert reason == "live gates passed"


def test_shadow_mode_never_clears_the_gate() -> None:
    allowed, reason = live_order_allowed(
        mode="SHADOW",
        alpha="ACTIVE",
        model_readiness="READY",
        data_health="HEALTHY",
        decision="LONG",
        net_edge_pips=4.0,
        min_net_edge_pips=1.0,
        assessment="SIGNAL",
    )
    assert allowed is False
    assert reason == "--trade requires --mode live"
