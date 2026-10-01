"""Structural checks that do not impute missing values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import polars as pl

from data.store import RATE_CURVES, TABLES, PitStore
from domain.timeutil import UTC


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_store(store: PitStore) -> ValidationReport:
    report = ValidationReport()
    audit_as_of = datetime(2099, 1, 1, tzinfo=UTC)
    for table in TABLES:
        frame = store.history(table, audit_as_of)
        if frame.is_empty():
            report.warnings.append(f"{table} is empty")
            continue
        _require_columns(report, table, frame)
        _check_time_order(report, table, frame)
        if "source" in frame.columns and frame.filter(pl.col("source") == "").height:
            report.errors.append(f"{table} has an empty source")
        if "version" in frame.columns and frame.filter(pl.col("version") == "").height:
            report.errors.append(f"{table} has an empty version")
    _check_markets(report, store)
    _check_rates(report, store)
    _check_macros(report, store)
    return report


def _require_columns(report: ValidationReport, table: str, frame: pl.DataFrame) -> None:
    required = {"observed_at", "source", "version", "record_id"}
    missing = sorted(required - set(frame.columns))
    if missing:
        report.errors.append(f"{table} missing columns {missing}")


def _check_time_order(report: ValidationReport, table: str, frame: pl.DataFrame) -> None:
    if {"effective_at", "released_at", "observed_at", "ingested_at"} - set(frame.columns):
        return
    bad_release = frame.filter(pl.col("observed_at") < pl.col("released_at"))
    bad_effective = frame.filter(pl.col("released_at") < pl.col("effective_at"))
    bad_ingest = frame.filter(pl.col("ingested_at") < pl.col("observed_at"))
    if bad_release.height:
        report.errors.append(f"{table} has observed_at before released_at")
    if bad_effective.height:
        report.errors.append(f"{table} has released_at before effective_at")
    if bad_ingest.height:
        report.errors.append(f"{table} has ingested_at before observed_at")


def _check_markets(report: ValidationReport, store: PitStore) -> None:
    frame = store.history("market_observations", datetime(2099, 1, 1, tzinfo=UTC))
    if frame.is_empty():
        return
    foreign = frame.filter(pl.col("pair") != "EURGBP")
    if foreign.height:
        report.errors.append("market observations contain a pair other than EURGBP")
    crossed = frame.filter(pl.col("bid").is_not_null() & pl.col("ask").is_not_null() & (pl.col("bid") > pl.col("ask")))
    if crossed.height:
        report.errors.append("market observations contain a crossed book")


def _check_rates(report: ValidationReport, store: PitStore) -> None:
    frame = store.history("rate_observations", datetime(2099, 1, 1, tzinfo=UTC))
    if frame.is_empty():
        return
    unknown = sorted(set(frame["curve_id"].to_list()) - RATE_CURVES)
    if unknown:
        report.errors.append(f"unsupported rate curves {unknown}")


def _check_macros(report: ValidationReport, store: PitStore) -> None:
    frame = store.history("macro_releases", datetime(2099, 1, 1, tzinfo=UTC))
    if frame.is_empty():
        return
    if frame.filter(pl.col("revision_number") < 0).height:
        report.errors.append("negative macro revision_number")
