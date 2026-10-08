"""The desk page reads the forward log. It does not send an order."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.cli import main
from api.desk import create_desk_app, read_desk


def test_empty_log_has_no_latest_call(tmp_path: Path) -> None:
    body = read_desk(tmp_path / "missing.jsonl")
    assert body["pair"] == "EURGBP"
    assert body["count"] == 0
    assert body["latest"] is None
    assert body["alpha"]["lifecycle"] == "DISCOVERY"
    assert body["alpha"]["n_samples"] == 0
    assert body["alpha"]["hit_rate"] is None


def test_desk_shows_the_newest_call_and_skips_a_broken_line(tmp_path: Path) -> None:
    log = tmp_path / "rounds.jsonl"
    older = _row("2026-10-05T12:00:00+00:00", "WATCH", bid=0.84710, ask=0.84712)
    newer = _row("2026-10-05T12:30:00+00:00", "SHORT", bid=0.84720, ask=0.84724, status="READY", fair_value=0.84)
    log.write_text("not-json\n" + _line(older) + _line(newer), encoding="utf-8")
    body = read_desk(log)
    assert body["count"] == 2
    assert body["latest"]["decision"] == "SHORT"
    assert body["latest"]["assessment"] == "SIGNAL"
    assert body["latest"]["quote"]["bid"] == 0.84720
    assert body["latest"]["quote"]["spread_pips"] == 0.4
    assert body["latest"]["headlines"][0]["headline"] == "英国9月服务业PMI终值"
    assert body["latest"]["gap_pips"] == 72.2
    assert body["history"][1]["decision"] == "WATCH"
    page = TestClient(create_desk_app(log)).get("/")
    assert page.status_code == 200
    assert "相对价值监控" in page.text
    assert "Alpha 证据" in page.text
    assert "/v1/desk" in page.text
    served = TestClient(create_desk_app(log)).get("/v1/desk").json()
    assert served["latest"]["rates"]["rate_diff"] == 1.39


def test_desk_reads_saved_alpha_evidence(tmp_path: Path) -> None:
    log = tmp_path / "rounds.jsonl"
    log.write_text("", encoding="utf-8")
    (tmp_path / "alpha.jsonl").write_text(
        json.dumps(
            {
                "lifecycle": "VALIDATING",
                "n_samples": 80,
                "hit_rate": 0.55,
                "convergence_rate": 0.4,
                "after_cost_pnl": 12.5,
                "ic_21": 0.03,
                "ic_63": 0.02,
                "ic_252": None,
                "reasons": ["trailing sample is shorter than 252 realized outcomes"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    alpha = read_desk(log)["alpha"]
    assert alpha["lifecycle"] == "VALIDATING"
    assert alpha["n_samples"] == 80
    assert alpha["hit_rate"] == 0.55
    assert alpha["convergence_rate"] == 0.4
    assert alpha["after_cost_pnl"] == 12.5
    assert alpha["ic_21"] == 0.03
    assert alpha["ic_63"] == 0.02
    assert alpha["ic_252"] is None


def test_desk_command_binds_the_local_page(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake(app, host: str, port: int, log_level: str) -> None:
        seen["host"] = host
        seen["port"] = port
        seen["paths"] = {getattr(route, "path", "") for route in app.routes}

    monkeypatch.setattr("uvicorn.run", fake)
    code = main(["--config", str(_config()), "desk", "--log", str(tmp_path / "rounds.jsonl"), "--port", "8766"])
    assert code == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8766
    assert "/" in seen["paths"]
    assert "/v1/desk" in seen["paths"]


def _config() -> Path:
    return Path(__file__).resolve().parents[1] / "configs" / "eurgbp.toml"


def _line(row: dict[str, object]) -> str:
    return json.dumps(row, ensure_ascii=False) + "\n"


def _row(
    ingested_at: str,
    decision: str,
    *,
    bid: float,
    ask: float,
    status: str | None = None,
    fair_value: float | None = None,
) -> dict[str, object]:
    return {
        "ingested_at": ingested_at,
        "mode": "SHADOW",
        "decision": decision,
        "status": status,
        "duplicate": False,
        "reasons": ["fair value is MODEL_NOT_READY"],
        "narratives": ["UK PMI above expectation → GBP relative support → EURGBP bearish factor"],
        "model_readiness": "MODEL_NOT_READY",
        "event_pressure": -0.23,
        "executed": False,
        "live_order": None,
        "inputs": {
            "fair_value": fair_value,
            "quote": {"bid": bid, "ask": ask},
            "rates": {"uk2y": 4.6, "de2y": 3.21, "rate_diff": 1.39},
            "information": [
                {
                    "headline": "澳大利亚9月服务业PMI终值",
                    "kind": "CALENDAR",
                    "country": "AU",
                    "surprise": None,
                },
                {
                    "headline": "英国9月服务业PMI终值",
                    "kind": "CALENDAR",
                    "country": "UK",
                    "indicator": "PMI",
                    "actual": 52.1,
                    "forecast": 51.7,
                    "surprise": 0.4,
                },
            ],
            "information_error": None,
            "rate_error": None,
            "quote_error": None,
        },
    }
