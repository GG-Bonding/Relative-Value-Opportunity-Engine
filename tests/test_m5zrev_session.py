"""London daylight saving and the release blackout."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from m5zrev.session import (
    assess_calendar,
    event_block_mask,
    in_london_session,
    live_entries_allowed,
    server_to_utc,
)


def test_london_session_follows_british_summer_time() -> None:
    start, end = 8 * 60, 17 * 60
    assert in_london_session(datetime(2026, 1, 15, 8, 0, tzinfo=UTC), start, end)
    assert not in_london_session(datetime(2026, 1, 15, 7, 30, tzinfo=UTC), start, end)
    assert in_london_session(datetime(2026, 7, 15, 7, 0, tzinfo=UTC), start, end)
    assert not in_london_session(datetime(2026, 7, 15, 16, 0, tzinfo=UTC), start, end)
    assert in_london_session(datetime(2026, 10, 23, 15, 30, tzinfo=UTC), start, end)
    assert not in_london_session(datetime(2026, 10, 23, 16, 0, tzinfo=UTC), start, end)
    assert in_london_session(datetime(2026, 10, 26, 8, 0, tzinfo=UTC), start, end)
    assert not in_london_session(datetime(2026, 10, 26, 7, 0, tzinfo=UTC), start, end)
    assert not in_london_session(datetime(2026, 10, 24, 10, 0, tzinfo=UTC), start, end)


def test_server_clock_needs_an_explicit_offset() -> None:
    converted = server_to_utc(datetime(2026, 7, 15, 11, 0), 3)
    assert converted == datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="naive"):
        server_to_utc(datetime(2026, 7, 15, 11, 0, tzinfo=UTC), 3)


def test_release_window_is_half_open_and_a_missing_calendar_is_not_invented() -> None:
    event = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    stamps = [
        datetime(2026, 7, 15, 11, 44, tzinfo=UTC),
        datetime(2026, 7, 15, 11, 45, tzinfo=UTC),
        datetime(2026, 7, 15, 12, 14, tzinfo=UTC),
        datetime(2026, 7, 15, 12, 15, tzinfo=UTC),
    ]
    blocked = event_block_mask(
        stamps,
        (event,),
        before=timedelta(minutes=15),
        after=timedelta(minutes=15),
        mode="optional",
        status="current",
    )
    assert blocked.tolist() == [False, True, True, False]
    sample_end = datetime(2026, 8, 1, tzinfo=UTC)
    assert assess_calendar((), sample_end, timedelta(days=14)) == "missing"
    assert assess_calendar((event,), sample_end, timedelta(days=14)) == "stale"
    assert assess_calendar((sample_end,), sample_end, timedelta(days=14)) == "current"
    missing = event_block_mask(stamps, (), before=timedelta(0), after=timedelta(0), mode="optional", status="missing")
    required = event_block_mask(stamps, (), before=timedelta(0), after=timedelta(0), mode="required", status="missing")
    assert not missing.any()
    assert required.all()
    assert live_entries_allowed("required", "missing") is False
    assert live_entries_allowed("required", "stale") is False
    assert live_entries_allowed("optional", "missing") is True


def test_required_filter_blocks_the_unverified_tail_only() -> None:
    event = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    stamps = [
        datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
    ]
    blocked = event_block_mask(
        stamps,
        (event,),
        before=timedelta(minutes=15),
        after=timedelta(minutes=15),
        mode="required",
        status="stale",
    )
    assert blocked.tolist() == [True, True]
    optional = event_block_mask(
        stamps,
        (event,),
        before=timedelta(minutes=15),
        after=timedelta(minutes=15),
        mode="optional",
        status="stale",
    )
    assert optional.tolist() == [True, False]
