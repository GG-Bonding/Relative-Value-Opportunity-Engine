"""Official daily UK2Y and DE2Y. A missing print stays missing and is not filled with zero.

UK2Y is the Bank of England GLC Nominal spot curve at a maturity of 2.0 years.
DE2Y is the Bundesbank Svensson yield with a residual maturity of exactly 2.0 years.
The spread is UK2Y minus DE2Y, in percentage points, and only on a shared trading day.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

import httpx
from openpyxl import load_workbook

from domain.errors import DataValidationError
from domain.timeutil import ensure_utc
from forward.rates import RateSnapshot

BOE_YIELD_CURVE_URL = (
    "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/latest-yield-curve-data.zip"
)
BOE_NOMINAL_ARCHIVE_URL = (
    "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/glcnominalddata.zip"
)
BUNDESBANK_DE2Y_URL = (
    "https://api.statistiken.bundesbank.de/rest/download/"
    "BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R02XX.R.A.A._Z._Z.A?format=csv&lang=en"
)
SPOT_SHEET = "4. spot curve"
SPOT_SHEETS = (SPOT_SHEET, "4. nominal spot curve")
TENOR_YEARS = 2.0
UK_SOURCE = "bank-of-england-glc-nominal-spot-2y"
DE_SOURCE = "bundesbank-svensson-residual-2y"
_LONDON = ZoneInfo("Europe/London")
_EXCEL_EPOCH = datetime(1899, 12, 30)
_DATE_ROW = re.compile(r"^(\d{4}-\d{2}-\d{2}),([^,]*)")


def download_uk2y(client: httpx.Client) -> dict[date, float]:
    response = client.get(BOE_YIELD_CURVE_URL)
    response.raise_for_status()
    return parse_boe_nominal_spot(response.content)


def download_uk2y_history(client: httpx.Client) -> dict[date, float]:
    """Archive spot curve, then the current-month file overwrites any shared day."""
    points: dict[date, float] = {}
    errors: list[str] = []
    try:
        response = client.get(BOE_NOMINAL_ARCHIVE_URL)
        response.raise_for_status()
        points.update(parse_boe_nominal_archive(response.content))
    except (DataValidationError, httpx.HTTPError, OSError, ValueError) as exc:
        errors.append(str(exc))
    try:
        points.update(download_uk2y(client))
    except (DataValidationError, httpx.HTTPError, OSError, ValueError) as exc:
        errors.append(str(exc))
    if not points:
        detail = "; ".join(errors) or "empty"
        raise DataValidationError(f"UK2Y history is missing: {detail}")
    return points


def download_de2y(client: httpx.Client) -> dict[date, float]:
    response = client.get(BUNDESBANK_DE2Y_URL)
    response.raise_for_status()
    return parse_bundesbank_de2y(response.text)


def parse_boe_nominal_spot(payload: bytes) -> dict[date, float]:
    """Read tenor 2.0 from the GLC Nominal spot sheet only."""
    try:
        archive = ZipFile(BytesIO(payload))
    except BadZipFile as exc:
        raise DataValidationError("Bank of England yield-curve download is not a zip") from exc
    with archive:
        name = _nominal_member(archive.namelist())
        workbook_bytes = archive.read(name)
    return _spot_points(workbook_bytes)


def parse_boe_nominal_archive(payload: bytes) -> dict[date, float]:
    """Read tenor 2.0 from each daily GLC Nominal workbook in the archive zip."""
    try:
        archive = ZipFile(BytesIO(payload))
    except BadZipFile as exc:
        raise DataValidationError("Bank of England yield-curve archive is not a zip") from exc
    points: dict[date, float] = {}
    with archive:
        names = _archive_members(archive.namelist())
        if not names:
            raise DataValidationError("yield-curve archive has no GLC Nominal workbook")
        for name in names:
            points.update(_spot_points(archive.read(name)))
    return points


def _spot_points(workbook_bytes: bytes) -> dict[date, float]:
    workbook = load_workbook(BytesIO(workbook_bytes), read_only=True, data_only=True)
    try:
        sheet = _spot_sheet(workbook.sheetnames)
        rows = [tuple(row) for row in workbook[sheet].iter_rows(values_only=True)]
    finally:
        workbook.close()
    column = _tenor_column(rows)
    if column is None:
        raise DataValidationError("spot curve has no 2.0 year tenor")
    points: dict[date, float] = {}
    for row in rows:
        if column >= len(row):
            continue
        day = _as_day(row[0])
        rate = _as_rate(row[column])
        if day is None or rate is None:
            continue
        points[day] = rate
    return points


def _spot_sheet(names: list[str]) -> str:
    for name in SPOT_SHEETS:
        if name in names:
            return name
    known = ", ".join(names)
    raise DataValidationError(f"GLC Nominal has no spot sheet {SPOT_SHEETS[0]}. Sheets: {known}")


def parse_bundesbank_de2y(text: str) -> dict[date, float]:
    """One daily Svensson yield. A dot or a blank is missing, not zero."""
    points: dict[date, float] = {}
    for line in text.splitlines():
        matched = _DATE_ROW.match(line.strip())
        if matched is None:
            continue
        raw = matched.group(2).strip()
        if raw in {"", "."}:
            continue
        try:
            rate = float(raw)
        except ValueError:
            continue
        points[date.fromisoformat(matched.group(1))] = rate
    return points


def align_same_day(uk: dict[date, float], de: dict[date, float], as_of: datetime) -> RateSnapshot:
    """Use the latest day both curves have already published. Do not pair different days."""
    limit = ensure_utc(as_of)
    common = [day for day in sorted(set(uk) & set(de)) if _known_at(day) <= limit]
    if common:
        day = common[-1]
        stamp = _known_at(day)
        return RateSnapshot(uk2y=uk[day], de2y=de[day], uk2y_time=stamp, de2y_time=stamp)
    uk_day = _latest_usable(uk, limit)
    de_day = _latest_usable(de, limit)
    return RateSnapshot(
        uk2y=None if uk_day is None else uk[uk_day],
        de2y=None if de_day is None else de[de_day],
        uk2y_time=None if uk_day is None else _known_at(uk_day),
        de2y_time=None if de_day is None else _known_at(de_day),
    )


def _archive_members(names: list[str]) -> list[str]:
    chosen = [
        name
        for name in names
        if name.lower().endswith(".xlsx") and "nominal" in name.lower() and "glc" in name.lower()
    ]
    recent = [name for name in chosen if any(token in name for token in ("2016", "2025", "present"))]
    return recent or chosen


def _nominal_member(names: list[str]) -> str:
    matches = [
        name
        for name in names
        if name.lower().endswith(".xlsx") and "nominal" in name.lower() and "glc" in name.lower()
    ]
    if len(matches) != 1:
        raise DataValidationError(f"yield-curve zip does not contain one GLC Nominal workbook: {matches}")
    return matches[0]


def _tenor_column(rows: list[tuple[Any, ...]]) -> int | None:
    for row in rows[:12]:
        values = [_as_float(cell) for cell in row]
        if TENOR_YEARS in values and 0.5 in values and 2.5 in values:
            return values.index(TENOR_YEARS)
    return None


def _latest_usable(points: dict[date, float], limit: datetime) -> date | None:
    usable = [day for day in sorted(points) if _known_at(day) <= limit]
    if not usable:
        return None
    return usable[-1]


def _known_at(day: date) -> datetime:
    local = datetime.combine(day, time(18, 0), tzinfo=_LONDON)
    return local.astimezone(UTC)


def _as_day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    serial = _as_float(value)
    if serial is None or serial < 20_000 or serial > 80_000:
        return None
    return (_EXCEL_EPOCH + timedelta(days=int(serial))).date()


def _as_rate(value: Any) -> float | None:
    return _as_float(value)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {".", "#VALUE!"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None
