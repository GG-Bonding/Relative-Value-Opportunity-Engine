from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from api.cli import main
from data.jin10_client import Jin10Error, token_from_env
from data.jin10_ingest import ingest_jin10
from data.jin10_map import calendar_records, cross_observations
from data.store import PitStore
from domain.timeutil import UTC


class _FakeJin10:
    def __init__(self) -> None:
        self.quotes = {
            "EURUSD": {"close": "1.12990", "time": "2026-10-01T16:04:14+08:00"},
            "GBPUSD": {"close": "1.32324", "time": "2026-10-01T16:04:14+08:00"},
        }
        self.bars = {
            "EURUSD": [
                ["2026-10-01 16:03:00", 1.0, 1.0, 1.0, 1.13, 1],
                {"time": 1759276800, "close": 1.12},
            ],
            "GBPUSD": [
                ["2026-10-01 16:03:00", 1.0, 1.0, 1.0, 1.32, 1],
                {"time": 1759276800, "close": 1.31},
            ],
        }
        self.rows = [
            {
                "actual": "0.5",
                "consensus": "0.40",
                "previous": "0.40",
                "pub_time": "2026-09-30 14:00",
                "revised": None,
                "title": "英国第二季度GDP季率终值",
            },
            {
                "actual": None,
                "consensus": "52",
                "previous": "52",
                "pub_time": "2026-10-01 16:30",
                "revised": None,
                "title": "英国9月制造业PMI终值",
            },
            {
                "actual": "1.4",
                "consensus": None,
                "previous": "1.50",
                "pub_time": "2026-09-29 07:01",
                "revised": None,
                "title": "英国9月BRC商店物价指数年率",
            },
            {
                "actual": None,
                "consensus": "2.2",
                "previous": "2.1",
                "pub_time": "2026-10-02 17:00",
                "revised": None,
                "title": "欧元区9月核心CPI年率初值",
            },
        ]

    def quote(self, code: str) -> dict[str, object]:
        return self.quotes[code]

    def kline(self, code: str, count: int) -> list[object]:
        _ = count
        return self.bars[code]

    def calendar(self) -> list[dict[str, object]]:
        return self.rows


def test_cross_uses_both_legs_and_leaves_the_book_missing() -> None:
    ingested = datetime(2026, 10, 1, 8, 5, tzinfo=UTC)
    rows = cross_observations(
        {"close": "1.12990", "time": "2026-10-01T16:04:14+08:00"},
        {"close": "1.32324", "time": "2026-10-01T16:04:14+08:00"},
        ingested_at=ingested,
    )
    assert len(rows) == 1
    assert rows[0].bar_size == "1m"
    assert rows[0].mid == pytest.approx(1.12990 / 1.32324)
    assert rows[0].bid is None
    assert rows[0].ask is None
    assert rows[0].source == "jin10"


def test_calendar_keeps_published_uk_gdp_and_skips_unmapped_and_future_actuals() -> None:
    ingested = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    macros, consensus, counts = calendar_records(_FakeJin10().calendar(), ingested_at=ingested)
    assert [row.series_id for row in macros] == ["UK.GDP.HEADLINE.QOQ"]
    assert macros[0].period == "2026Q2"
    assert macros[0].revision_number == 2
    assert macros[0].actual == pytest.approx(0.5)
    assert counts["unmapped"] == 1
    assert counts["unpublished"] == 1
    assert counts["future"] == 1
    assert any(row.series_id == "EZ.CORE_CPI.HEADLINE.YOY" for row in consensus)
    future = next(row for row in consensus if row.series_id == "EZ.CORE_CPI.HEADLINE.YOY")
    assert future.observed_at == ingested


def test_unpublished_print_is_not_visible_as_an_actual(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "jin10.duckdb")
    first = ingest_jin10(store, _FakeJin10(), ingested_at=datetime(2026, 10, 1, 8, 5, tzinfo=UTC))
    second = ingest_jin10(store, _FakeJin10(), ingested_at=datetime(2026, 10, 1, 8, 6, tzinfo=UTC))
    assert first["macro_inserted"] == 1
    assert first["market_inserted"] == 3
    assert second["macro_skipped"] == 1
    assert first["events_inserted"] == 4
    assert second["events_skipped"] == 4
    assert all(row["pressure"] is None for row in store.rows("information_events"))
    assert store.count("macro_releases") == 1
    before_release = store.history("macro_releases", datetime(2026, 9, 30, 5, 0, tzinfo=UTC))
    after_release = store.history("macro_releases", datetime(2026, 10, 1, 7, 0, tzinfo=UTC))
    assert before_release.is_empty()
    assert after_release.height == 1
    store.close()


def test_cli_refuses_jin10_without_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIN10_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    config = Path(__file__).resolve().parents[1] / "configs" / "eurgbp.toml"
    code = main(["--db", str(tmp_path / "empty.duckdb"), "--config", str(config), "ingest", "--jin10"])
    assert code == 2


def test_missing_token_does_not_invent_a_tape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIN10_TOKEN", raising=False)
    with pytest.raises(Jin10Error, match="JIN10_TOKEN"):
        token_from_env()
