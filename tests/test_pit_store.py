from __future__ import annotations

from pathlib import Path

import pytest

from data.store import PitStore
from data.validate import validate_store
from domain.errors import DataValidationError, UnsupportedPairError
from domain.models import MacroRelease, MarketObservation, RateObservation
from tests.conftest import ts


def _provenance(when: str) -> dict[str, object]:
    moment = ts(when)
    return {
        "effective_at": moment,
        "released_at": moment,
        "observed_at": moment,
        "ingested_at": moment,
        "source": "lab",
        "version": "1",
    }


def test_revisions_are_appended_and_asof_hides_the_future_vintage(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "pit.duckdb")
    first = MacroRelease(
        series_id="UK_GDP",
        region="UK",
        category="GDP",
        period="2024Q2",
        actual=0.4,
        consensus=0.3,
        previous_as_known=0.1,
        revision_number=0,
        **_provenance("2024-08-15T08:00:00+00:00"),
    )
    revised = first.model_copy(
        update={
            "actual": 0.5,
            "previous_as_known": 0.4,
            "revision_number": 1,
            "observed_at": ts("2024-09-15T08:00:00+00:00"),
            "released_at": ts("2024-09-15T08:00:00+00:00"),
            "ingested_at": ts("2024-09-15T08:00:00+00:00"),
        }
    )
    store.append_macro(first)
    store.append_macro(revised)

    before = store.latest_macro_vintages(ts("2024-09-01T00:00:00+00:00"))
    after = store.latest_macro_vintages(ts("2024-10-01T00:00:00+00:00"))
    assert before["actual"].to_list() == [0.4]
    assert after["actual"].to_list() == [0.5]
    assert store.count("macro_releases") == 2
    original = store.history("macro_releases", ts("2024-10-01T00:00:00+00:00")).filter(
        __import__("polars").col("revision_number") == 0
    )
    assert original["actual"].to_list() == [0.4]
    assert original["previous_as_known"].to_list() == [0.1]
    store.close()


def test_duplicate_revision_is_rejected(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "pit.duckdb")
    release = MacroRelease(
        series_id="UK_GDP",
        region="UK",
        category="GDP",
        period="2024Q2",
        actual=0.4,
        consensus=0.3,
        previous_as_known=0.1,
        revision_number=0,
        **_provenance("2024-08-15T08:00:00+00:00"),
    )
    store.append_macro(release)
    with pytest.raises(DataValidationError, match="overwrite"):
        store.append_macro(release)
    store.close()


def test_future_insert_does_not_change_an_earlier_snapshot_hash(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "pit.duckdb")
    as_of = ts("2024-01-02T16:00:00+00:00")
    store.append_rate(RateObservation(curve_id="UK2Y", rate=4.0, **_provenance("2024-01-02T16:00:00+00:00")))
    before = store.snapshot_hash(as_of)
    store.append_rate(RateObservation(curve_id="UK2Y", rate=9.0, **_provenance("2024-02-02T16:00:00+00:00")))
    assert store.snapshot_hash(as_of) == before
    assert store.history("rate_observations", as_of)["rate"].to_list() == [4.0]
    store.close()


def test_other_traded_pairs_and_curves_are_refused(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "pit.duckdb")
    with pytest.raises(UnsupportedPairError):
        MarketObservation(
            instrument="AUDNZD",
            pair="AUDNZD",
            bar_size="1d",
            mid=1.1,
            **_provenance("2024-01-02T16:00:00+00:00"),
        )
    with pytest.raises(DataValidationError):
        store.append_rate(RateObservation(curve_id="US2Y", rate=4.0, **_provenance("2024-01-02T16:00:00+00:00")))
    store.close()


def test_validate_data_accepts_a_small_eurgbp_book(tmp_path: Path) -> None:
    store = PitStore(tmp_path / "pit.duckdb")
    store.append_market(
        MarketObservation(
            instrument="EURGBP",
            pair="EURGBP",
            bar_size="1d",
            bid=0.8590,
            ask=0.8602,
            mid=0.8596,
            **_provenance("2024-01-02T16:00:00+00:00"),
        )
    )
    report = validate_store(store)
    assert report.ok
    store.close()
