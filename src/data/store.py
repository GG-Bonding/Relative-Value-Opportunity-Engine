"""DuckDB store that inserts and never updates.

As-of reads keep only rows whose observed_at is at or before the query time.
Macro vintages keep every revision; the visible vintage is the latest revision
among rows already observed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
from pydantic import BaseModel

from domain.errors import DataValidationError, PointInTimeError
from domain.hashing import stable_hash, stable_id
from domain.models import (
    CommodityObservation,
    ConsensusSnapshot,
    MacroRelease,
    MarketObservation,
    OptionObservation,
    PolicyExpectation,
    PositioningObservation,
    RateObservation,
    RiskObservation,
)
from domain.timeutil import dump_ts, ensure_utc

TABLES: dict[str, str] = {
    "market_observations": """
        CREATE TABLE IF NOT EXISTS market_observations (
            record_id VARCHAR PRIMARY KEY,
            instrument VARCHAR NOT NULL,
            pair VARCHAR NOT NULL,
            bar_size VARCHAR NOT NULL,
            bid DOUBLE,
            ask DOUBLE,
            mid DOUBLE NOT NULL,
            volume DOUBLE,
            effective_at VARCHAR NOT NULL,
            released_at VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            UNIQUE(instrument, bar_size, observed_at, source, version)
        )
    """,
    "rate_observations": """
        CREATE TABLE IF NOT EXISTS rate_observations (
            record_id VARCHAR PRIMARY KEY,
            curve_id VARCHAR NOT NULL,
            rate DOUBLE NOT NULL,
            effective_at VARCHAR NOT NULL,
            released_at VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            UNIQUE(curve_id, observed_at, source, version)
        )
    """,
    "policy_expectations": """
        CREATE TABLE IF NOT EXISTS policy_expectations (
            record_id VARCHAR PRIMARY KEY,
            central_bank VARCHAR NOT NULL,
            horizon VARCHAR NOT NULL,
            expected_rate DOUBLE NOT NULL,
            effective_at VARCHAR NOT NULL,
            released_at VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            UNIQUE(central_bank, horizon, observed_at, source, version)
        )
    """,
    "macro_releases": """
        CREATE TABLE IF NOT EXISTS macro_releases (
            record_id VARCHAR PRIMARY KEY,
            series_id VARCHAR NOT NULL,
            region VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            period VARCHAR NOT NULL,
            actual DOUBLE NOT NULL,
            consensus DOUBLE,
            previous_as_known DOUBLE,
            revision_number INTEGER NOT NULL,
            effective_at VARCHAR NOT NULL,
            released_at VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            UNIQUE(series_id, period, revision_number, version)
        )
    """,
    "consensus_snapshots": """
        CREATE TABLE IF NOT EXISTS consensus_snapshots (
            record_id VARCHAR PRIMARY KEY,
            series_id VARCHAR NOT NULL,
            region VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            horizon VARCHAR NOT NULL,
            expected_value DOUBLE NOT NULL,
            effective_at VARCHAR NOT NULL,
            released_at VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            UNIQUE(series_id, horizon, observed_at, source, version)
        )
    """,
    "positioning_observations": """
        CREATE TABLE IF NOT EXISTS positioning_observations (
            record_id VARCHAR PRIMARY KEY,
            pair VARCHAR NOT NULL,
            metric VARCHAR NOT NULL,
            value DOUBLE,
            effective_at VARCHAR NOT NULL,
            released_at VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            UNIQUE(pair, metric, observed_at, source, version)
        )
    """,
    "option_observations": """
        CREATE TABLE IF NOT EXISTS option_observations (
            record_id VARCHAR PRIMARY KEY,
            pair VARCHAR NOT NULL,
            risk_reversal DOUBLE,
            implied_vol DOUBLE,
            open_interest DOUBLE,
            effective_at VARCHAR NOT NULL,
            released_at VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            UNIQUE(pair, observed_at, source, version)
        )
    """,
    "commodity_observations": """
        CREATE TABLE IF NOT EXISTS commodity_observations (
            record_id VARCHAR PRIMARY KEY,
            instrument VARCHAR NOT NULL,
            price DOUBLE NOT NULL,
            effective_at VARCHAR NOT NULL,
            released_at VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            UNIQUE(instrument, observed_at, source, version)
        )
    """,
    "risk_observations": """
        CREATE TABLE IF NOT EXISTS risk_observations (
            record_id VARCHAR PRIMARY KEY,
            leg VARCHAR NOT NULL,
            metric VARCHAR NOT NULL,
            spread DOUBLE NOT NULL,
            effective_at VARCHAR NOT NULL,
            released_at VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            UNIQUE(leg, metric, observed_at, source, version)
        )
    """,
    "information_events": """
        CREATE TABLE IF NOT EXISTS information_events (
            record_id VARCHAR PRIMARY KEY,
            event_id VARCHAR NOT NULL,
            kind VARCHAR NOT NULL,
            headline VARCHAR NOT NULL,
            region VARCHAR,
            category VARCHAR,
            surprise DOUBLE,
            raw_surprise DOUBLE,
            pressure DOUBLE,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            version VARCHAR NOT NULL,
            UNIQUE(event_id, source, version)
        )
    """,
    "opportunity_journal": """
        CREATE TABLE IF NOT EXISTS opportunity_journal (
            record_id VARCHAR PRIMARY KEY,
            opportunity_id VARCHAR NOT NULL,
            sequence INTEGER NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            mode VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            decision VARCHAR NOT NULL,
            direction VARCHAR,
            bid DOUBLE,
            ask DOUBLE,
            mid DOUBLE,
            fair_value DOUBLE,
            fill_price DOUBLE,
            rate_diff DOUBLE,
            model_readiness VARCHAR NOT NULL,
            detail VARCHAR NOT NULL,
            UNIQUE(opportunity_id, sequence),
            UNIQUE(opportunity_id, observed_at)
        )
    """,
    "forward_decisions": """
        CREATE TABLE IF NOT EXISTS forward_decisions (
            record_id VARCHAR PRIMARY KEY,
            mode VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            decision VARCHAR NOT NULL,
            direction VARCHAR,
            model_readiness VARCHAR NOT NULL,
            bid DOUBLE,
            ask DOUBLE,
            rate_diff DOUBLE,
            fair_value DOUBLE,
            detail VARCHAR NOT NULL,
            UNIQUE(mode, observed_at)
        )
    """,
    "forward_outcomes": """
        CREATE TABLE IF NOT EXISTS forward_outcomes (
            record_id VARCHAR PRIMARY KEY,
            opportunity_id VARCHAR NOT NULL,
            horizon VARCHAR NOT NULL,
            observed_at VARCHAR NOT NULL,
            ingested_at VARCHAR NOT NULL,
            direction VARCHAR NOT NULL,
            gross_pips DOUBLE NOT NULL,
            after_cost_pips DOUBLE NOT NULL,
            gap_entry_pips DOUBLE NOT NULL,
            gap_now_pips DOUBLE NOT NULL,
            converged BOOLEAN NOT NULL,
            hit BOOLEAN NOT NULL,
            entry_observed_at VARCHAR NOT NULL,
            UNIQUE(opportunity_id, horizon)
        )
    """,
    "forward_alpha": """
        CREATE TABLE IF NOT EXISTS forward_alpha (
            record_id VARCHAR PRIMARY KEY,
            observed_at VARCHAR NOT NULL,
            lifecycle VARCHAR NOT NULL,
            n_samples INTEGER NOT NULL,
            hit_rate DOUBLE,
            convergence_rate DOUBLE,
            after_cost_pnl DOUBLE,
            ic_21 DOUBLE,
            ic_63 DOUBLE,
            ic_252 DOUBLE,
            reasons VARCHAR NOT NULL,
            UNIQUE(observed_at)
        )
    """,
}

KEY_FIELDS: dict[str, list[str]] = {
    "market_observations": ["instrument", "bar_size", "observed_at", "source", "version"],
    "rate_observations": ["curve_id", "observed_at", "source", "version"],
    "policy_expectations": ["central_bank", "horizon", "observed_at", "source", "version"],
    "macro_releases": ["series_id", "period", "revision_number", "version"],
    "consensus_snapshots": ["series_id", "horizon", "observed_at", "source", "version"],
    "positioning_observations": ["pair", "metric", "observed_at", "source", "version"],
    "option_observations": ["pair", "observed_at", "source", "version"],
    "commodity_observations": ["instrument", "observed_at", "source", "version"],
    "risk_observations": ["leg", "metric", "observed_at", "source", "version"],
    "information_events": ["event_id", "source", "version"],
    "opportunity_journal": ["opportunity_id", "sequence"],
    "forward_decisions": ["mode", "observed_at"],
    "forward_outcomes": ["opportunity_id", "horizon"],
    "forward_alpha": ["observed_at"],
}

RATE_CURVES = frozenset(
    {
        "UK2Y",
        "DE2Y",
        "UK_FWD_1M",
        "DE_FWD_1M",
        "UK_FWD_3M",
        "DE_FWD_3M",
    }
)


class PitStore:
    """Append-only research store. Queries are as-of observed_at."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = duckdb.connect(self.path)
        for statement in TABLES.values():
            self._conn.execute(statement)
        self._conn.execute("ALTER TABLE information_events ADD COLUMN IF NOT EXISTS raw_surprise DOUBLE")

    def close(self) -> None:
        self._conn.close()

    def append_market(self, obs: MarketObservation) -> str:
        return self._insert("market_observations", obs)

    def append_rate(self, obs: RateObservation) -> str:
        if obs.curve_id not in RATE_CURVES:
            raise DataValidationError(f"unsupported rate curve {obs.curve_id}")
        return self._insert("rate_observations", obs)

    def append_policy(self, obs: PolicyExpectation) -> str:
        return self._insert("policy_expectations", obs)

    def append_macro(self, obs: MacroRelease) -> str:
        return self._insert("macro_releases", obs)

    def append_consensus(self, obs: ConsensusSnapshot) -> str:
        return self._insert("consensus_snapshots", obs)

    def append_positioning(self, obs: PositioningObservation) -> str:
        return self._insert("positioning_observations", obs)

    def append_option(self, obs: OptionObservation) -> str:
        return self._insert("option_observations", obs)

    def append_commodity(self, obs: CommodityObservation) -> str:
        return self._insert("commodity_observations", obs)

    def append_risk(self, obs: RiskObservation) -> str:
        return self._insert("risk_observations", obs)

    def rows(self, table: str) -> list[dict[str, Any]]:
        self._check_table(table)
        frame = self._conn.execute(f"SELECT * FROM {table} ORDER BY observed_at, record_id").pl()
        if frame.is_empty():
            return []
        return frame.to_dicts()

    def has_forward_decision(self, mode: str, observed_at: datetime) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM forward_decisions WHERE mode = ? AND observed_at = ? LIMIT 1",
            [mode, dump_ts(ensure_utc(observed_at))],
        ).fetchone()
        return row is not None

    def history(self, table: str, as_of: datetime) -> pl.DataFrame:
        self._check_table(table)
        as_of_text = dump_ts(ensure_utc(as_of))
        frame = self._conn.execute(
            f"SELECT * FROM {table} WHERE observed_at <= ? ORDER BY observed_at, record_id",
            [as_of_text],
        ).pl()
        return frame

    def count(self, table: str, as_of: datetime | None = None) -> int:
        self._check_table(table)
        if as_of is None:
            row = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        else:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE observed_at <= ?",
                [dump_ts(ensure_utc(as_of))],
            ).fetchone()
        if row is None:
            return 0
        return int(row[0])

    def latest_macro_vintages(self, as_of: datetime) -> pl.DataFrame:
        """One row per series and period: the newest revision already observable."""
        frame = self.history("macro_releases", as_of)
        if frame.is_empty():
            return frame
        ordered = frame.sort(["series_id", "period", "revision_number", "observed_at", "version"])
        return ordered.group_by(["series_id", "period"], maintain_order=True).tail(1)

    def snapshot_hash(self, as_of: datetime) -> str:
        payload: dict[str, list[dict[str, Any]]] = {}
        for table in TABLES:
            frame = self.history(table, as_of)
            if frame.is_empty():
                payload[table] = []
                continue
            sort_cols = [column for column in ("record_id",) if column in frame.columns]
            payload[table] = frame.sort(sort_cols).to_dicts()
        return stable_hash(payload)

    def row_counts(self, as_of: datetime) -> dict[str, int]:
        return {table: self.count(table, as_of) for table in TABLES}

    def insert_records(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Bulk append. Rows use the same columns as the pydantic records, without record_id."""
        self._check_table(table)
        if not rows:
            return
        keys = KEY_FIELDS[table]
        prepared: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            payload["record_id"] = stable_id(table, {field: payload[field] for field in keys})
            prepared.append(payload)
        columns = list(prepared[0])
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"
        try:
            self._conn.executemany(sql, [tuple(item[column] for column in columns) for item in prepared])
        except duckdb.ConstraintException as exc:
            raise DataValidationError(f"refusing to overwrite rows in {table}") from exc

    def _insert(self, table: str, model: BaseModel) -> str:
        payload = _payload(model)
        record = stable_id(table, {field: payload[field] for field in KEY_FIELDS[table]})
        payload["record_id"] = record
        columns = list(payload)
        placeholders = ", ".join(["?"] * len(columns))
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        try:
            self._conn.execute(sql, [payload[column] for column in columns])
        except duckdb.ConstraintException as exc:
            raise DataValidationError(
                f"refusing to overwrite {table} record {record}; append a new revision or version"
            ) from exc
        return record

    def _check_table(self, table: str) -> None:
        if table not in TABLES:
            raise PointInTimeError(f"unknown table {table}")


def _payload(model: BaseModel) -> dict[str, Any]:
    raw = model.model_dump()
    payload: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, datetime):
            payload[key] = dump_ts(value)
        else:
            payload[key] = value
    return payload
