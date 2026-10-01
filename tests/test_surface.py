from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.cli import main
from api.service import EngineService
from data.store import PitStore
from domain.config import EngineConfig
from domain.enums import OpportunityStatus
from domain.models import BacktestRun, SpreadSource
from domain.timeutil import UTC
from evaluation.baselines import constant_book, strategy_pnl
from evaluation.champion import ModelScorecard, promotion_allowed
from evaluation.paper import record_paper_session
from evaluation.report import HEADINGS, V1Study, render_report
from opportunities.status import transition


def test_opportunity_cannot_leave_a_terminal_state() -> None:
    assert transition(OpportunityStatus.DISCOVERED, OpportunityStatus.READY) is OpportunityStatus.READY
    with pytest.raises(ValueError, match="cannot move"):
        transition(OpportunityStatus.EXITED, OpportunityStatus.ENTERED)


def test_challenger_must_beat_every_gate() -> None:
    champion = ModelScorecard("ridge", 0.04, 0.02, 0.05, True, True, 0.01)
    weaker = ModelScorecard("ols", 0.04, 0.03, 0.04, True, True, 0.02)
    allowed, reasons = promotion_allowed(champion, weaker)
    assert not allowed
    assert any("IC" in reason for reason in reasons)


def test_report_keeps_the_required_sections_and_an_inconclusive_market_claim() -> None:
    text = render_report(
        V1Study(
            market_rows=1,
            macro_rows=1,
            trades=0,
            pnl=0.0,
            sharpe=None,
            max_drawdown=None,
            alive_trades=0,
            alive_pnl=0.0,
            decay_trades=0,
        )
    )
    for heading in HEADINGS:
        assert f"## {heading}" in text
    assert text.count("INCONCLUSIVE") >= 2
    assert "PROMISING" not in text
    assert "REJECTED" not in text.split("## Conclusion")[-1]


def test_constant_long_pays_its_spread_once() -> None:
    pnl = strategy_pnl(constant_book(1.0, 3), np.array([0.01, 0.0, 0.0]), cost=0.002)
    assert pnl == pytest.approx(0.008)


def test_empty_api_refuses_other_pairs_and_an_empty_book(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "empty.duckdb")
    client = TestClient(create_app(EngineService(store, EngineConfig())))
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503
    assert client.get("/v1/pairs/AUDNZD/mechanisms").status_code == 404
    body = client.get("/v1/pairs/EURGBP/mechanisms").json()
    assert body["pair"] == "EURGBP"
    assert body["mechanisms"]
    response = client.post(
        "/v1/backtests",
        json={
            "start": datetime(2014, 1, 1, tzinfo=UTC).isoformat(),
            "end": datetime(2014, 6, 1, tzinfo=UTC).isoformat(),
        },
    )
    assert response.status_code == 409
    assert client.get("/v1/opportunities").status_code == 409
    store.close()


def test_cli_mechanisms_do_not_need_a_store() -> None:
    assert main(["mechanisms", "EURGBP"]) == 0
    assert main(["mechanisms", "NVDAAMD"]) == 2
    assert main(["--db", "missing-store.duckdb", "factors", "EURGBP"]) == 2
    assert main(["factors", "EURGBP", "--db", "missing-store.duckdb"]) == 2


def test_paper_session_is_labeled_and_not_a_broker_fill(tmp_path: Path) -> None:
    moment = datetime(2020, 1, 2, tzinfo=UTC)
    run = BacktestRun(
        run_id="paper-1",
        pair="EURGBP",
        git_commit="UNKNOWN",
        config_hash="abc",
        data_snapshot_hash="def",
        model_version="eurgbp-rolling-ridge-v1",
        random_seed=7,
        start=moment,
        end=moment,
        spread_source=SpreadSource.SIMULATED_SPREAD,
        n_trades=0,
        pnl=0.0,
        pnl_after_costs=0.0,
        sharpe=None,
        max_drawdown=None,
        deterministic_hash="0",
    )
    path = tmp_path / "paper.jsonl"
    record_paper_session(path, run)
    text = path.read_text(encoding="utf-8")
    assert '"mode": "PAPER"' in text
