from __future__ import annotations

import polars as pl

from data.surprise import with_surprise


def _frame(rows: list[tuple[str, str, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "series_id": [row[0] for row in rows],
            "observed_at": [row[1] for row in rows],
            "revision_number": [0] * len(rows),
            "version": ["1"] * len(rows),
            "actual": [row[2] for row in rows],
            "consensus": [row[3] for row in rows],
        }
    )


def test_surprise_uses_only_prior_releases_and_sample_std() -> None:
    errors = [1.0, -1.0, 1.0, -1.0]
    rows = [("UK_CPI", f"2020-0{i + 1}-15T08:00:00+00:00", errors[i], 0.0) for i in range(4)]
    rows.append(("UK_CPI", "2020-05-15T08:00:00+00:00", 3.0, 0.0))
    frame = with_surprise(_frame(rows), min_history=4)
    last = frame.filter(pl.col("observed_at") == "2020-05-15T08:00:00+00:00")
    assert last["surprise"][0] == pytest_approx(3.0 / (4 / 3) ** 0.5)
    assert frame.filter(pl.col("observed_at") < "2020-05-15T08:00:00+00:00")["surprise"].to_list() == [
        None,
        None,
        None,
        None,
    ]


def test_a_future_release_does_not_change_past_surprises() -> None:
    base = [("UK_CPI", f"2020-{month:02d}-15T08:00:00+00:00", float(month), 0.0) for month in range(1, 11)]
    first = with_surprise(_frame(base), min_history=8)
    extended = base + [("UK_CPI", "2020-11-15T08:00:00+00:00", 100.0, 0.0)]
    second = with_surprise(_frame(extended), min_history=8)
    past = "2020-10-15T08:00:00+00:00"
    assert (
        first.filter(pl.col("observed_at") == past)["surprise"][0]
        == second.filter(pl.col("observed_at") == past)["surprise"][0]
    )


def test_short_history_is_missing_not_zero() -> None:
    rows = [("UK_CPI", "2020-01-15T08:00:00+00:00", 1.0, 0.0)]
    frame = with_surprise(_frame(rows), min_history=8)
    assert frame["surprise"][0] is None
    assert frame["surprise"][0] != 0


def pytest_approx(value: float) -> object:
    import pytest

    return pytest.approx(value)
