from __future__ import annotations

from datetime import datetime

import pytest

from domain.config import EngineConfig, load_config
from domain.errors import PointInTimeError, UnsupportedPairError
from domain.models import MacroRelease, MarketObservation, Pair, PolicyExpectation
from domain.timeutil import UTC
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


def test_only_eurgbp_is_a_tradable_pair() -> None:
    pair = Pair(symbol="EURGBP", base="EUR", quote="GBP")
    assert pair.pip == 0.0001
    with pytest.raises(UnsupportedPairError):
        Pair(symbol="AUDNZD", base="AUD", quote="NZD")


def test_naive_timestamps_are_rejected() -> None:
    naive = datetime(2024, 8, 15, 8, 0, 0)
    with pytest.raises(PointInTimeError):
        MacroRelease(
            series_id="UK_GDP",
            region="UK",
            category="GDP",
            period="2024Q2",
            actual=0.4,
            consensus=0.3,
            previous_as_known=0.2,
            revision_number=0,
            effective_at=naive,
            released_at=naive,
            observed_at=naive,
            ingested_at=naive,
            source="ons",
            version="1",
        )


def test_policy_path_is_not_the_spot_bank_rate() -> None:
    with pytest.raises(ValueError, match="horizon"):
        PolicyExpectation(
            central_bank="BOE",
            horizon="SPOT",
            expected_rate=5.0,
            **_provenance("2024-06-01T12:00:00+00:00"),
        )


def test_market_book_cannot_be_crossed() -> None:
    with pytest.raises(ValueError, match="bid"):
        MarketObservation(
            instrument="EURGBP",
            pair="EURGBP",
            bar_size="1d",
            bid=0.86,
            ask=0.85,
            mid=0.855,
            **_provenance("2024-06-01T16:00:00+00:00"),
        )


def test_default_research_windows_match_the_spec() -> None:
    config = EngineConfig()
    assert config.fair_value.window == 504
    assert config.fair_value.min_observations == 252
    loaded = load_config(__import__("pathlib").Path("configs/eurgbp.toml"))
    assert loaded.fair_value.window == 504
    assert loaded.fair_value.min_observations == 252
    assert loaded.hash() == EngineConfig().hash()


def test_aware_utc_macro_release_keeps_the_first_print() -> None:
    release = MacroRelease(
        series_id="UK_GDP",
        region="UK",
        category="GDP",
        period="2024Q2",
        actual=0.4,
        consensus=0.3,
        previous_as_known=0.2,
        revision_number=0,
        **_provenance("2024-08-15T08:00:00+00:00"),
    )
    assert release.actual == 0.4
    assert release.observed_at.tzinfo is not None
    assert release.observed_at.tzinfo == UTC or release.observed_at.utcoffset() == UTC.utcoffset(release.observed_at)
