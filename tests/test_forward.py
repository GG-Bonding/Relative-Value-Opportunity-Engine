"""Forward evaluation freezes the call, then scores it on a later book."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from api.cli import main
from data.information import feed_events
from data.store import PitStore
from domain.config import EngineConfig
from domain.enums import ExecutionMode
from domain.errors import DataValidationError
from domain.timeutil import UTC
from forward.cycle import ForwardBook, run_forward
from forward.events import EventInput
from forward.mt5 import Mt5Tick, parse_mt5_tick
from forward.rates import RateSnapshot

INGESTED = datetime(2026, 10, 5, 12, tzinfo=UTC)
ENTRY = datetime(2026, 10, 1, 10, tzinfo=UTC)
DAY_2 = datetime(2026, 10, 2, 10, tzinfo=UTC)
DAY_3 = datetime(2026, 10, 3, 10, tzinfo=UTC)
FAIR = 0.8515


def test_event_pressure_is_not_an_order(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "watch.duckdb")
    result = _run(store, ExecutionMode.SHADOW, _book(ENTRY, 0.8557, 0.8563, events=[_cpi(ENTRY)]), None)
    assert result["decision"] == "WATCH"
    assert result["model_readiness"] == "MODEL_NOT_READY"
    assert result["direction"] is None
    assert result["event_pressure"] == pytest.approx(-0.7)
    assert result["narratives"] == ["UK CPI above expectation → GBP relative support → EURGBP bearish factor"]
    assert result["status"] is None
    store.close()


def test_flash_headline_does_not_create_a_trade(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "flash.duckdb")
    headline = EventInput(
        "flash-1",
        "FLASH",
        "BoE unexpectedly dovish BUY EURGBP",
        None,
        None,
        None,
        ENTRY,
    )
    result = _run(store, ExecutionMode.SHADOW, _book(ENTRY, 0.8557, 0.8563, events=[headline]), None)
    assert result["decision"] == "WATCH"
    assert result["event_pressure"] is None
    assert "BUY" not in " ".join(result["reasons"])
    assert any("does not set an EURGBP factor" in line for line in result["narratives"])
    stored = store.rows("information_events")
    assert stored[0]["pressure"] is None
    assert "BUY EURGBP" in stored[0]["headline"]
    store.close()


def test_missing_rate_is_not_stored_as_zero(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "rates.duckdb")
    rates = RateSnapshot(uk2y=4.2, de2y=None, uk2y_time=ENTRY, de2y_time=None)
    book = ForwardBook(Mt5Tick("EURGBP", ENTRY, 0.8557, 0.8563), rates, [])
    result = _run(store, ExecutionMode.PAPER, book, FAIR)
    assert result["decision"] == "DATA_DEGRADED"
    assert result["rate_diff"] is None
    assert result["executed"] is False
    stored = store.rows("rate_observations")
    assert [row["curve_id"] for row in stored] == ["UK2Y"]
    assert stored[0]["rate"] == pytest.approx(4.2)
    assert all(row["rate"] != 0 for row in stored)
    store.close()


def test_shadow_and_paper_share_the_signal_and_only_paper_fills(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "modes.duckdb")
    book = _book(ENTRY, 0.8557, 0.8563, events=[_cpi(ENTRY)])
    shadow = _run(store, ExecutionMode.SHADOW, book, FAIR)
    paper = _run(store, ExecutionMode.PAPER, book, FAIR)
    live = _run(store, ExecutionMode.LIVE, book, FAIR)
    assert shadow["decision"] == paper["decision"] == "SHORT"
    assert live["decision"] == "WATCH"
    assert shadow["reasons"] == paper["reasons"]
    assert "research sample; alpha is not ACTIVE" in paper["reasons"]
    assert "alpha is DISCOVERY" in " ".join(live["reasons"])
    assert "research sample" not in " ".join(live["reasons"])
    assert shadow["fill_price"] is None
    assert shadow["executed"] is False
    assert shadow["status"] == "READY"
    assert paper["executed"] is True
    assert paper["status"] == "ENTERED"
    assert paper["fill_price"] == pytest.approx(0.85567)
    assert paper["fill_price"] < paper["bid"]
    assert paper["fill_price"] != pytest.approx(paper["mid"])
    assert live["executed"] is False
    assert live["fill_price"] is None
    assert live["live_order"] == "not armed"
    assert live["status"] is None
    store.close()


def test_paper_tracks_convergence_without_editing_the_opening_call(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "paper.duckdb")
    opened = _run(store, ExecutionMode.PAPER, _book(ENTRY, 0.8557, 0.8563, events=[_cpi(ENTRY)]), FAIR)
    frozen = _opening(store, opened["opportunity_id"])
    again = _run(store, ExecutionMode.PAPER, _book(ENTRY, 0.8557, 0.8563, events=[_cpi(ENTRY)]), FAIR)
    assert again["duplicate"] is True
    converging = _run(store, ExecutionMode.PAPER, _book(DAY_2, 0.8529, 0.8535), 0.8514)
    assert converging["status"] == "CONVERGING"
    assert converging["executed"] is False
    closed = _run(store, ExecutionMode.PAPER, _book(DAY_3, 0.8515, 0.8521), 0.8514)
    assert closed["status"] == "EXITED"
    assert closed["research_pips"] == pytest.approx(42.0)
    assert closed["executable_pips"] == pytest.approx(35.4)
    detail = _latest_detail(store, opened["opportunity_id"])
    assert detail["review"] == "thesis held; mispricing closed"
    assert detail["captured"] == pytest.approx(42.0 / 45.0)
    assert detail["fair_value_now"] == pytest.approx(0.8514)
    still = _opening(store, opened["opportunity_id"])
    assert still["mid"] == pytest.approx(frozen["mid"])
    assert still["fair_value"] == pytest.approx(FAIR)
    assert still["fill_price"] == pytest.approx(frozen["fill_price"])
    assert still["sequence"] == 0
    with pytest.raises(DataValidationError):
        store.insert_records("opportunity_journal", [_without_id(still)])
    store.close()


def test_rate_move_against_the_short_invalidates_the_thesis(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "invalid.duckdb")
    opened = _run(store, ExecutionMode.PAPER, _book(ENTRY, 0.8557, 0.8563, events=[_cpi(ENTRY)]), FAIR)
    later = datetime(2026, 10, 1, 11, tzinfo=UTC)
    book = _book(later, 0.8569, 0.8575, uk=3.85, de=2.55)
    result = _run(store, ExecutionMode.PAPER, book, FAIR)
    assert result["status"] == "INVALIDATED"
    assert result["executable_pips"] == pytest.approx(-18.6)
    detail = _latest_detail(store, opened["opportunity_id"])
    assert detail["review"] == "new information invalidated the thesis"
    assert "THESIS_INVALIDATED" in detail["exit_reasons"]
    opening = _opening(store, opened["opportunity_id"])
    assert opening["rate_diff"] == pytest.approx(1.65)
    assert opening["status"] == "ENTERED"
    store.close()


def test_bid_above_ask_is_rejected() -> None:
    with pytest.raises(DataValidationError, match="bid cannot exceed ask"):
        parse_mt5_tick({"symbol": "EURGBP", "time": "2026-10-01T10:00:00+00:00", "bid": 0.8563, "ask": 0.8557})


def test_german_headline_is_stored_without_a_pressure() -> None:
    rows = feed_events(
        [{"title": "德国10月CPI年率"}],
        kind="CALENDAR",
        ingested_at=INGESTED,
        source="jin10",
        version="jin10-calendar-v1",
    )
    assert rows[0]["region"] == "DE"
    assert rows[0]["pressure"] is None
    assert rows[0]["surprise"] is None


def test_cli_paper_uses_the_mt5_file_and_live_does_not_order(tmp_path: Path) -> None:
    tick = tmp_path / "eurgbp_tick.json"
    rates = tmp_path / "rates.json"
    events = tmp_path / "events.json"
    tick.write_text(
        json.dumps({"symbol": "EURGBP", "time": "2026-10-01T10:00:00+00:00", "bid": 0.8557, "ask": 0.8563}),
        encoding="utf-8",
    )
    rates.write_text(
        json.dumps(
            {
                "UK2Y": {"rate": 4.2, "time": "2026-10-01T10:00:00+00:00"},
                "DE2Y": {"rate": 2.55, "time": "2026-10-01T10:00:00+00:00"},
            }
        ),
        encoding="utf-8",
    )
    events.write_text(
        json.dumps(
            [
                {
                    "id": "uk-cpi",
                    "kind": "CALENDAR",
                    "region": "UK",
                    "category": "CPI",
                    "surprise": 2.1,
                    "headline": "UK CPI",
                    "time": "2026-10-01T10:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    config = Path(__file__).resolve().parents[1] / "configs" / "eurgbp.toml"
    db = tmp_path / "forward.duckdb"
    code = main(
        [
            "--db",
            str(db),
            "--config",
            str(config),
            "forward",
            "--mode",
            "paper",
            "--tick",
            str(tick),
            "--rates",
            str(rates),
            "--events",
            str(events),
            "--fair-value",
            "0.8515",
        ]
    )
    assert code == 0
    store = PitStore(db)
    row = store.rows("opportunity_journal")[0]
    assert row["status"] == "ENTERED"
    assert row["fill_price"] == pytest.approx(0.85567)
    store.close()
    live = main(
        [
            "--db",
            str(tmp_path / "live.duckdb"),
            "--config",
            str(config),
            "forward",
            "--mode",
            "live",
            "--tick",
            str(tick),
            "--rates",
            str(rates),
            "--fair-value",
            "0.8515",
        ]
    )
    assert live == 2
    live_store = PitStore(tmp_path / "live.duckdb")
    assert live_store.rows("opportunity_journal") == []
    assert live_store.rows("forward_decisions")[0]["decision"] == "WATCH"
    live_store.close()


def _run(store: PitStore, mode: ExecutionMode, book: ForwardBook, fair_value: float | None) -> dict[str, object]:
    return run_forward(store, EngineConfig(), mode, book, ingested_at=INGESTED, fair_value=fair_value)


def _book(
    moment: datetime,
    bid: float,
    ask: float,
    *,
    uk: float = 4.20,
    de: float = 2.55,
    events: list[EventInput] | None = None,
) -> ForwardBook:
    return ForwardBook(
        Mt5Tick("EURGBP", moment, bid, ask),
        RateSnapshot(uk2y=uk, de2y=de, uk2y_time=moment, de2y_time=moment),
        [] if events is None else events,
    )


def _cpi(moment: datetime) -> EventInput:
    return EventInput("uk-cpi", "CALENDAR", "UK CPI", "UK", "CPI", 0.7, moment)


def _opening(store: PitStore, opportunity_id: object) -> dict[str, object]:
    rows = [
        row
        for row in store.rows("opportunity_journal")
        if row["opportunity_id"] == opportunity_id and row["sequence"] == 0
    ]
    assert len(rows) == 1
    return rows[0]


def _latest_detail(store: PitStore, opportunity_id: object) -> dict[str, object]:
    rows = [row for row in store.rows("opportunity_journal") if row["opportunity_id"] == opportunity_id]
    latest = max(rows, key=lambda row: int(row["sequence"]))
    detail = json.loads(str(latest["detail"]))
    assert isinstance(detail, dict)
    return detail


def _without_id(row: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in row.items() if key != "record_id"}
