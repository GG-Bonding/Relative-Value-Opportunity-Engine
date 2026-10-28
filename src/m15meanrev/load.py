"""CSV bars. `datetime` is the M15 close unless the caller shifts an open stamp."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class BarSeries:
    time: list[datetime]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    def __post_init__(self) -> None:
        count = len(self.time)
        if not (len(self.open) == len(self.high) == len(self.low) == len(self.close) == count):
            raise ValueError("OHLC columns have different lengths")
        if count == 0:
            raise ValueError("bar series is empty")
        previous: datetime | None = None
        for stamp in self.time:
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError("bar timestamps must be timezone-aware")
            if previous is not None and stamp <= previous:
                raise ValueError("bar timestamps must be strictly increasing")
            previous = stamp


@dataclass(frozen=True)
class DailySeries:
    day: list[date]
    close: np.ndarray

    def __post_init__(self) -> None:
        if len(self.day) != len(self.close):
            raise ValueError("daily dates and closes have different lengths")
        if not self.day:
            raise ValueError("daily series is empty")
        previous: date | None = None
        for day in self.day:
            if previous is not None and day <= previous:
                raise ValueError("daily dates must be strictly increasing")
            previous = day


def parse_timestamp(value: str) -> datetime:
    """Parse a bar time. A naive value is UTC. Aware values are converted to UTC."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_bars(path: Path, *, timestamp_is_open: bool = False) -> BarSeries:
    """Load `datetime, open, high, low, close`. Open stamps are shifted forward 15 minutes."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        required = ("datetime", "open", "high", "low", "close")
        missing = [name for name in required if name not in fields]
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(missing)}")
        times: list[datetime] = []
        opens: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        closes: list[float] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                stamp = parse_timestamp(row[fields["datetime"]])
                prices = tuple(float(row[fields[name]]) for name in ("open", "high", "low", "close"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number} is not a bar") from exc
            if timestamp_is_open:
                stamp = stamp + timedelta(minutes=15)
            times.append(stamp.astimezone(UTC))
            opens.append(prices[0])
            highs.append(prices[1])
            lows.append(prices[2])
            closes.append(prices[3])
    order = sorted(range(len(times)), key=times.__getitem__)
    series = BarSeries(
        time=[times[index].astimezone(UTC) for index in order],
        open=_column(opens, order),
        high=_column(highs, order),
        low=_column(lows, order),
        close=_column(closes, order),
    )
    _require_positive_ohlc(series, path)
    return series


def load_daily(path: Path) -> DailySeries:
    """Load `date, close`. A blank close is an error, not a zero."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        if "date" not in fields or "close" not in fields:
            raise ValueError(f"{path} must have date and close columns")
        rows: list[tuple[date, float]] = []
        for line_number, row in enumerate(reader, start=2):
            raw_day = (row.get(fields["date"]) or "").strip()
            raw_close = (row.get(fields["close"]) or "").strip()
            if not raw_day or not raw_close:
                raise ValueError(f"{path}:{line_number} has a missing date or close")
            try:
                day = date.fromisoformat(raw_day[:10])
                close = float(raw_close)
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number} is not a daily close") from exc
            if not np.isfinite(close) or close <= 0:
                raise ValueError(f"{path}:{line_number} close must be positive")
            rows.append((day, close))
    rows.sort(key=lambda item: item[0])
    return DailySeries(day=[item[0] for item in rows], close=np.array([item[1] for item in rows], dtype=np.float64))


def daily_from_m15(bars: BarSeries) -> DailySeries:
    """Last M15 close of each UTC date. Bars on that date do not receive this z-score."""
    days: list[date] = []
    closes: list[float] = []
    for stamp, close in zip(bars.time, bars.close, strict=True):
        day = stamp.astimezone(UTC).date()
        if days and days[-1] == day:
            closes[-1] = float(close)
        else:
            days.append(day)
            closes.append(float(close))
    return DailySeries(day=days, close=np.array(closes, dtype=np.float64))


def write_bars(path: Path, bars: BarSeries) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["datetime", "open", "high", "low", "close"])
        for index, stamp in enumerate(bars.time):
            utc = stamp.astimezone(UTC)
            writer.writerow(
                [
                    utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    f"{bars.open[index]:.6f}",
                    f"{bars.high[index]:.6f}",
                    f"{bars.low[index]:.6f}",
                    f"{bars.close[index]:.6f}",
                ]
            )


def write_daily(path: Path, daily: DailySeries) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "close"])
        for day, close in zip(daily.day, daily.close, strict=True):
            writer.writerow([day.isoformat(), f"{float(close):.6f}"])


def _column(values: list[float], order: list[int]) -> np.ndarray:
    return np.array([values[index] for index in order], dtype=np.float64)


def _require_positive_ohlc(bars: BarSeries, path: Path) -> None:
    for index in range(len(bars.time)):
        prices = (float(bars.open[index]), float(bars.high[index]), float(bars.low[index]), float(bars.close[index]))
        if not all(np.isfinite(price) and price > 0 for price in prices):
            raise ValueError(f"{path} bar {index} has a non-positive price")
        if prices[1] + 1e-12 < max(prices[0], prices[3]) or prices[2] - 1e-12 > min(prices[0], prices[3]):
            raise ValueError(f"{path} bar {index} has high or low outside the open/close")
