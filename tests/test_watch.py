"""A resident watcher judges new information. It does not send an order."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from api.cli import main
from data.store import PitStore
from domain.config import EngineConfig
from domain.timeutil import UTC
from forward.mt5 import Mt5Tick
from forward.prints import InformationPrint
from forward.rates import RateSnapshot
from forward.round import RoundInputs
from forward.watch import WatchMemory, assessment, remember, watch_loop, watch_triggers

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
CONFIG = Path(__file__).resolve().parents[1] / "configs" / "eurgbp.toml"


def test_baseline_then_only_a_new_item_or_a_new_rate_day() -> None:
    first = _book([_flash("one")])
    memory = WatchMemory()
    assert watch_triggers(memory, first, ingested_at=T0, position_open=False) == ["baseline"]
    memory = remember(memory, first, T0)
    assert watch_triggers(memory, first, ingested_at=T1, position_open=False) == []
    later = _book([_flash("one"), _flash("two")])
    assert watch_triggers(memory, later, ingested_at=T1, position_open=False) == ["new-information"]
    changed = _book([_flash("one")], rates=RateSnapshot(4.3, 2.55, T1, T1))
    assert watch_triggers(memory, changed, ingested_at=T1, position_open=False) == ["rate-print"]


def test_an_open_book_is_rechecked_on_a_new_tick_only() -> None:
    opened = remember(WatchMemory(), _book([_flash("one")]), T0)
    same = _book([_flash("one")])
    assert watch_triggers(opened, same, ingested_at=T1, position_open=True) == []
    moved = _book([_flash("one")], tick=Mt5Tick("EURGBP", T1, 0.8477, 0.8479))
    assert watch_triggers(opened, moved, ingested_at=T1, position_open=True) == ["open-book"]
    assert watch_triggers(opened, moved, ingested_at=T1, position_open=False) == []
    assert watch_triggers(opened, moved, ingested_at=T1, position_open=False, reprice=True) == ["price"]


def test_signal_and_reversal_labels() -> None:
    assert assessment({"status": "INVALIDATED", "decision": "SHORT"}) == "REVERSAL"
    assert assessment({"status": "EXITED", "decision": "LONG"}) == "REVERSAL"
    assert assessment({"decision": "SHORT", "status": "READY", "duplicate": False}) == "SIGNAL"
    assert assessment({"decision": "SHORT", "status": "READY", "duplicate": True}) == "UPDATE"
    assert assessment({"decision": "WATCH", "status": None}) == "UPDATE"


def test_loop_stays_quiet_until_new_information(tmp_path: Path) -> None:
    first = _book([_flash("one")])
    second = _book([_flash("one")])
    third = _book([_flash("one"), _flash("two")])
    books = [first, second, third]
    notices: list[dict[str, object]] = []
    moments = iter([T0, T1, T1 + timedelta(minutes=1)])
    store = PitStore(tmp_path / "watch.duckdb")
    try:
        code = watch_loop(
            store,
            EngineConfig(),
            "shadow",
            terminal=None,
            fair_value=None,
            log_path=tmp_path / "rounds.jsonl",
            interval_seconds=60,
            load_inputs=lambda moment, curves: books.pop(0),
            sleep=lambda seconds: None,
            clock=lambda: next(moments),
            max_cycles=3,
            emit=notices.append,
            fetch_rates=lambda: ({}, {}, None),
        )
    finally:
        store.close()
    assert code == 0
    assert [item["trigger"] for item in notices] == [["baseline"], ["new-information"]]
    assert notices[0]["assessment"] == "UPDATE"
    assert notices[0]["decision"] == "WATCH"
    assert notices[1]["headlines"] == ["two"]
    assert notices[0]["order_sent"] is False
    assert notices[1]["order_sent"] is False
    lines = (tmp_path / "rounds.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_baseline_names_the_surprise_rather_than_every_headline(tmp_path: Path) -> None:
    cpi = InformationPrint("CALENDAR", T0, "UK", "CPI", 3.2, 3.0, 2.9, "3", "英国9月CPI年率")
    notices: list[dict[str, object]] = []
    store = PitStore(tmp_path / "baseline.duckdb")
    try:
        watch_loop(
            store,
            EngineConfig(),
            "shadow",
            terminal=None,
            fair_value=None,
            log_path=tmp_path / "rounds.jsonl",
            interval_seconds=60,
            load_inputs=lambda moment, curves: _book([_flash("noise"), cpi]),
            sleep=lambda seconds: None,
            clock=lambda: T0,
            max_cycles=1,
            emit=notices.append,
            fetch_rates=lambda: ({}, {}, None),
        )
    finally:
        store.close()
    assert notices[0]["headlines"] == ["英国9月CPI年率"]


def test_watch_does_not_send_an_order_and_live_stays_resident(tmp_path: Path, monkeypatch) -> None:
    source = Path(__file__).resolve().parents[1].joinpath("src/forward/watch.py").read_text(encoding="utf-8")
    assert "order_send" not in source
    seen: dict[str, object] = {}

    def fake(*args, **kwargs) -> int:
        seen["mode"] = args[2]
        seen["interval"] = kwargs["interval_seconds"]
        return 0

    monkeypatch.setattr("forward.watch.watch_loop", fake)
    code = main(
        ["--db", str(tmp_path / "watch.duckdb"), "--config", str(CONFIG), "watch", "--mode", "live"]
    )
    assert code == 0
    assert seen["mode"] == "live"
    assert seen["interval"] == 60.0


def _book(
    prints: list[InformationPrint],
    *,
    rates: RateSnapshot | None = None,
    tick: Mt5Tick | None = None,
) -> RoundInputs:
    return RoundInputs(
        prints=prints,
        information_error=None,
        rates=RateSnapshot(4.2, 2.55, T0, T0) if rates is None else rates,
        rate_error=None,
        tick=Mt5Tick("EURGBP", T0, 0.84761, 0.84763) if tick is None else tick,
        quote_error=None,
    )


def _flash(headline: str) -> InformationPrint:
    return InformationPrint("FLASH", T0, None, None, None, None, None, None, headline)
