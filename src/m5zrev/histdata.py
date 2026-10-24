"""Build closed M5 and H1 bid bars from HistData M1 archives.

The archive clock is Eastern Standard Time all year, with no daylight-saving
shift. Each row is that minute's open. This module keeps only buckets whose
last minute is present, and it writes the bucket close in UTC.

These bars are not Anzo Capital ticks and they have no ask.
"""

from __future__ import annotations

import csv
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from m5zrev.data import BarTable, DataUnavailableError, SymbolSpec

_EST_TO_UTC = 5 * 3600


def research_spec() -> SymbolSpec:
    """Sizing convention used when the terminal contract is unavailable.

    1.25 USD per 0.00001 price on one lot is `100000 * 0.00001 * 1.25`.
    The 1.25 figure is an assumed GBPUSD rate. It was not read from a broker.
    """
    return SymbolSpec(
        symbol="EURGBP",
        digits=5,
        point=0.00001,
        tick_size=0.00001,
        tick_value_account=1.25,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=50.0,
        stops_level_points=0,
        contract_size=100_000.0,
        profit_currency="GBP",
        account_currency="USD",
        commission_per_lot_side=0.0,
        source="research-assumption-not-terminal",
        verified_from_terminal=False,
    )


def discover_archives(raw_dir: Path) -> tuple[Path, ...]:
    """Yearly zips win over monthly zips for the same year."""
    if not raw_dir.is_dir():
        raise DataUnavailableError(f"{raw_dir} is not a directory of HistData zips")
    found = sorted(raw_dir.glob("EURGBP_M1_*.zip"))
    yearly = [path for path in found if path.stem.count("_") == 2]
    covered = {_year(path) for path in yearly}
    monthly = [path for path in found if path.stem.count("_") == 3 and _year(path) not in covered]
    chosen = tuple(sorted(yearly + monthly, key=lambda path: path.name))
    if not chosen:
        raise DataUnavailableError(f"{raw_dir} has no EURGBP_M1 zip")
    return chosen


def prepare_histdata(raw_dir: Path, out_dir: Path) -> tuple[Path, Path]:
    """Read the local zips and write M5 and H1 CSVs. A missing zip is an error."""
    archives = discover_archives(raw_dir)
    unix, open_, high, low, close, conflicts = _load_archives(archives)
    m5 = aggregate_complete(unix, open_, high, low, close, 5)
    h1 = aggregate_complete(unix, open_, high, low, close, 60)
    out_dir.mkdir(parents=True, exist_ok=True)
    m5_path = out_dir / "eurgbp-m5.csv"
    h1_path = out_dir / "eurgbp-h1.csv"
    write_bars(m5_path, m5)
    write_bars(h1_path, h1)
    (out_dir / "spec.json").write_text(_spec_json(), encoding="utf-8")
    dropped_note = (
        "HistData.com Generic ASCII M1 bid. "
        "Clock is Eastern Standard Time (UTC-5, no daylight saving), converted to UTC. "
        "The M1 stamp is the minute open. Written stamps are the close of a complete M5 or H1 bucket. "
        "Incomplete buckets were dropped. There is no ask and no tick. "
        "This is not an Anzo Capital export.\n"
        f"Archives: {len(archives)}. M1 rows after duplicate removal: {len(unix)}. "
        f"Conflicting minutes removed without choosing a price: {conflicts}. "
        f"M5 bars: {len(m5.close)}. H1 bars: {len(h1.close)}. "
        f"M5 {m5.close_time[0].isoformat()} to {m5.close_time[-1].isoformat()}.\n"
    )
    (out_dir / "source.txt").write_text(dropped_note, encoding="utf-8")
    return m5_path, h1_path


def aggregate_complete(
    unix: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    minutes: int,
) -> BarTable:
    """Keep a bucket only when its last minute is present."""
    if minutes < 1:
        raise ValueError("bucket must be at least one minute")
    if not (len(unix) == len(open_) == len(high) == len(low) == len(close)):
        raise ValueError("M1 columns have different lengths")
    if len(unix) == 0:
        raise DataUnavailableError("no M1 bars")
    width = minutes * 60
    bucket = (unix // width) * width
    change = np.flatnonzero(bucket[1:] != bucket[:-1]) + 1
    starts = np.concatenate((np.array([0], dtype=int), change))
    ends = np.concatenate((change, np.array([len(unix)], dtype=int)))
    last = ends - 1
    complete = unix[last] >= bucket[starts] + (minutes - 1) * 60
    if not np.any(complete):
        raise DataUnavailableError(f"no complete {minutes}-minute bars")
    start_index = starts[complete]
    last_index = last[complete]
    bucket_start = bucket[start_index]
    close_unix = bucket_start + width
    opens = open_[start_index]
    highs = np.maximum.reduceat(high, starts)[complete]
    lows = np.minimum.reduceat(low, starts)[complete]
    closes = close[last_index]
    if np.any(close_unix[1:] <= close_unix[:-1]):
        raise DataUnavailableError("aggregated timestamps are not strictly increasing")
    if np.any(highs < lows) or np.any(closes <= 0) or np.any(opens <= 0):
        raise DataUnavailableError("aggregated bar has a non-positive or crossed price")
    close_time = [datetime.fromtimestamp(int(value), UTC) for value in close_unix]
    open_time = [datetime.fromtimestamp(int(value), UTC) for value in bucket_start]
    return BarTable(
        open_time=open_time,
        close_time=close_time,
        open=np.asarray(opens, dtype=np.float64),
        high=np.asarray(highs, dtype=np.float64),
        low=np.asarray(lows, dtype=np.float64),
        close=np.asarray(closes, dtype=np.float64),
        spread_pips=np.full(len(close_time), np.nan),
    )


def parse_m1_text(text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Parse HistData rows into UTC unix seconds and bid OHLC."""
    stamps: list[int] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split(";")
        if len(parts) < 5:
            raise DataUnavailableError(f"line {line_number} is not an M1 bar")
        stamps.append(_parse_est_unix(parts[0], line_number))
        try:
            prices = tuple(float(parts[offset]) for offset in range(1, 5))
        except ValueError as exc:
            raise DataUnavailableError(f"line {line_number} has a non-numeric price") from exc
        if not all(np.isfinite(price) and price > 0.0 for price in prices):
            raise DataUnavailableError(f"line {line_number} has a non-positive price")
        opens.append(prices[0])
        highs.append(prices[1])
        lows.append(prices[2])
        closes.append(prices[3])
    if not stamps:
        raise DataUnavailableError("HistData file has no bars")
    return (
        np.asarray(stamps, dtype=np.int64),
        np.asarray(opens, dtype=np.float64),
        np.asarray(highs, dtype=np.float64),
        np.asarray(lows, dtype=np.float64),
        np.asarray(closes, dtype=np.float64),
    )


def write_bars(path: Path, bars: BarTable) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["close_time_utc", "open", "high", "low", "close"])
        for index, stamp in enumerate(bars.close_time):
            writer.writerow(
                [
                    stamp.isoformat(),
                    f"{float(bars.open[index]):.6f}",
                    f"{float(bars.high[index]):.6f}",
                    f"{float(bars.low[index]):.6f}",
                    f"{float(bars.close[index]):.6f}",
                ]
            )


def _load_archives(
    archives: tuple[Path, ...],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    stamps: list[np.ndarray] = []
    opens: list[np.ndarray] = []
    highs: list[np.ndarray] = []
    lows: list[np.ndarray] = []
    closes: list[np.ndarray] = []
    for path in archives:
        unix, open_, high, low, close = _read_zip(path)
        stamps.append(unix)
        opens.append(open_)
        highs.append(high)
        lows.append(low)
        closes.append(close)
    return _drop_duplicate_minutes(
        np.concatenate(stamps),
        np.concatenate(opens),
        np.concatenate(highs),
        np.concatenate(lows),
        np.concatenate(closes),
    )


def _read_zip(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if not zipfile.is_zipfile(path):
        raise DataUnavailableError(f"{path} is not a zip")
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise DataUnavailableError(f"{path} should contain one CSV, found {names}")
        text = archive.read(names[0]).decode("utf-8-sig")
    return parse_m1_text(text)


def _drop_duplicate_minutes(
    unix: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """Keep one copy of an identical minute. A minute with two prices is removed."""
    order = np.argsort(unix, kind="mergesort")
    unix = unix[order]
    open_ = open_[order]
    high = high[order]
    low = low[order]
    close = close[order]
    if len(unix) < 2:
        return unix, open_, high, low, close, 0
    change = np.empty(len(unix), dtype=bool)
    change[0] = True
    change[1:] = unix[1:] != unix[:-1]
    starts = np.flatnonzero(change)
    ends = np.concatenate((starts[1:], np.array([len(unix)], dtype=int)))
    keep = np.ones(len(unix), dtype=bool)
    conflicts = 0
    for index in np.flatnonzero(ends - starts > 1):
        first = int(starts[index])
        last = int(ends[index])
        same = (
            np.all(open_[first:last] == open_[first])
            and np.all(high[first:last] == high[first])
            and np.all(low[first:last] == low[first])
            and np.all(close[first:last] == close[first])
        )
        if same:
            keep[first + 1 : last] = False
        else:
            keep[first:last] = False
            conflicts += 1
    return unix[keep], open_[keep], high[keep], low[keep], close[keep], conflicts


def _parse_est_unix(stamp: str, line_number: int) -> int:
    text = stamp.strip()
    if len(text) != 15 or text[8] != " ":
        raise DataUnavailableError(f"line {line_number} has a bad timestamp")
    try:
        year = int(text[0:4])
        month = int(text[4:6])
        day = int(text[6:8])
        hour = int(text[9:11])
        minute = int(text[11:13])
        second = int(text[13:15])
        days = _days_from_civil(year, month, day)
    except ValueError as exc:
        raise DataUnavailableError(f"line {line_number} has a bad timestamp") from exc
    if not (1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise DataUnavailableError(f"line {line_number} has a bad timestamp")
    return days * 86400 + hour * 3600 + minute * 60 + second + _EST_TO_UTC


def _days_from_civil(year: int, month: int, day: int) -> int:
    """Howard Hinnant's civil-to-days conversion. The result is Unix days."""
    year -= month <= 2
    era = (year if year >= 0 else year - 399) // 400
    year_of_era = year - era * 400
    day_of_year = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146097 + int(day_of_era) - 719468


def _year(path: Path) -> str:
    parts = path.stem.split("_")
    if len(parts) < 3:
        raise DataUnavailableError(f"{path} is not an EURGBP M1 archive name")
    return parts[2]


def _spec_json() -> str:
    spec = research_spec()
    return (
        "{\n"
        f'  "symbol": "{spec.symbol}",\n'
        f'  "digits": {spec.digits},\n'
        f'  "point": {spec.point},\n'
        f'  "tick_size": {spec.tick_size},\n'
        f'  "tick_value_account": {spec.tick_value_account},\n'
        f'  "volume_min": {spec.volume_min},\n'
        f'  "volume_step": {spec.volume_step},\n'
        f'  "volume_max": {spec.volume_max},\n'
        f'  "stops_level_points": {spec.stops_level_points},\n'
        f'  "contract_size": {spec.contract_size},\n'
        f'  "profit_currency": "{spec.profit_currency}",\n'
        f'  "account_currency": "{spec.account_currency}",\n'
        f'  "commission_per_lot_side": {spec.commission_per_lot_side},\n'
        f'  "source": "{spec.source}",\n'
        '  "verified_from_terminal": false,\n'
        '  "note": "GBPUSD 1.25 and a 100000 contract are assumptions. Anzo Capital was not read."\n'
        "}\n"
    )
