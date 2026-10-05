"""Append-only opportunity journal. Sequence 0 is the frozen call."""

from __future__ import annotations

import json
from typing import Any

from data.store import PitStore
from domain.errors import DataValidationError

TERMINAL = frozenset({"EXITED", "INVALIDATED", "EXPIRED", "REJECTED"})


def dump_detail(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def load_detail(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise DataValidationError("journal detail is not an object")
    return parsed


def active_journal(store: PitStore, mode: str) -> dict[str, Any] | None:
    latest: dict[str, dict[str, Any]] = {}
    for row in store.rows("opportunity_journal"):
        if row["mode"] != mode:
            continue
        current = latest.get(str(row["opportunity_id"]))
        if current is None or int(row["sequence"]) >= int(current["sequence"]):
            latest[str(row["opportunity_id"])] = row
    open_rows = [row for row in latest.values() if row["status"] not in TERMINAL]
    if not open_rows:
        return None
    return min(open_rows, key=lambda row: (str(row["observed_at"]), str(row["opportunity_id"])))


def opening_row(store: PitStore, opportunity_id: str) -> dict[str, Any]:
    for row in store.rows("opportunity_journal"):
        if row["opportunity_id"] == opportunity_id and int(row["sequence"]) == 0:
            return row
    raise DataValidationError(f"opportunity {opportunity_id} has no frozen opening row")


def append_journal(store: PitStore, row: dict[str, Any]) -> dict[str, Any]:
    try:
        store.insert_records("opportunity_journal", [row])
    except DataValidationError:
        existing = _at(store, str(row["opportunity_id"]), str(row["observed_at"]))
        if existing is None:
            raise
        return existing
    return row


def _at(store: PitStore, opportunity_id: str, observed_at: str) -> dict[str, Any] | None:
    for row in store.rows("opportunity_journal"):
        if row["opportunity_id"] == opportunity_id and row["observed_at"] == observed_at:
            return row
    return None
