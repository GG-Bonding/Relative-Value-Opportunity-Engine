"""Load and check EURGBP bars and ticks. Missing data is an error, not a fill."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from m5zrev.session import require_utc


class DataUnavailableError(RuntimeError):
    """Raised when the terminal or the requested file cannot supply the tape."""


@dataclass(frozen=True, slots=True)
class BarTable:
    open_time: list[datetime]
    close_time: list[datetime]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    spread_pips: np.ndarray

    def __post_init__(self) -> None:
        count = len(self.close_time)
        lengths = (
            len(self.open_time),
            len(self.open),
            len(self.high),
            len(self.low),
            len(self.close),
            len(self.spread_pips),
        )
        if any(length != count for length in lengths):
            raise ValueError("bar columns have different lengths")


@dataclass(frozen=True, slots=True)
class TickTable:
    time: list[datetime]
    bid: np.ndarray
    ask: np.ndarray

    def __post_init__(self) -> None:
        if not (len(self.time) == len(self.bid) == len(self.ask)):
            raise ValueError("tick columns have different lengths")


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    """Contract facts. `tick_value_account` is account currency for one tick on one lot."""

    symbol: str
    digits: int
    point: float
    tick_size: float
    tick_value_account: float
    volume_min: float
    volume_step: float
    volume_max: float
    stops_level_points: int
    contract_size: float
    profit_currency: str
    account_currency: str
    commission_per_lot_side: float
    source: str
    verified_from_terminal: bool

    def __post_init__(self) -> None:
        if self.symbol.upper().startswith("EURGBP") is False:
            raise ValueError("symbol must be EURGBP")
        if self.digits < 0 or self.point <= 0 or self.tick_size <= 0 or self.tick_value_account <= 0:
            raise ValueError("price precision and tick value must be positive")
        if self.volume_min <= 0 or self.volume_step <= 0 or self.volume_max < self.volume_min:
            raise ValueError("volume limits are invalid")
        if self.stops_level_points < 0 or self.contract_size <= 0:
            raise ValueError("stops level or contract size is invalid")
        if self.commission_per_lot_side < 0:
            raise ValueError("commission cannot be negative")
        if not self.profit_currency or not self.account_currency:
            raise ValueError("currencies are required")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bars: int = 0
    ticks: int = 0
    duplicate_timestamps: int = 0
    gaps: int = 0
    abnormal_returns: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class Probe:
    available: bool
    detail: str
    symbol: str | None = None
    server_utc_offset_hours: float | None = None
    bars: int = 0


def validate_bars(bars: BarTable, *, expected_duration: timedelta, pip: float) -> ValidationReport:
    report = ValidationReport(bars=len(bars.close_time))
    if pip <= 0:
        report.errors.append("pip must be positive")
    previous: datetime | None = None
    for index, stamp in enumerate(bars.close_time):
        current = _aware(report, stamp, index)
        if current is None:
            continue
        if previous is not None and current <= previous:
            report.duplicate_timestamps += 1
            report.errors.append(f"timestamp at index {index} is not strictly increasing")
        previous = current
        _check_price_bar(report, bars, index)
        if index == 0:
            continue
        try:
            earlier = require_utc(bars.close_time[index - 1])
        except ValueError:
            continue
        gap = current - earlier
        if _session_gap(earlier, current, expected_duration):
            report.gaps += 1
            report.warnings.append(f"gap before bar {index}: {gap}")
        move = _return(float(bars.close[index - 1]), float(bars.close[index]))
        if move is not None and abs(move) > 0.02:
            report.abnormal_returns += 1
            report.warnings.append(f"abnormal move at bar {index}: {move:.4f}")
        spread = float(bars.spread_pips[index])
        if spread == spread and spread < 0:
            report.errors.append(f"bar {index} has a negative spread")
    if report.gaps:
        report.warnings.insert(0, f"{report.gaps} weekday gaps were left empty")
    return report


def validate_ticks(ticks: TickTable) -> ValidationReport:
    report = ValidationReport(ticks=len(ticks.time))
    previous: datetime | None = None
    for index, stamp in enumerate(ticks.time):
        current = _aware(report, stamp, index)
        if current is None:
            continue
        if previous is not None and current <= previous:
            report.duplicate_timestamps += 1
            report.errors.append(f"timestamp at index {index} is not strictly increasing")
        previous = current
        bid = float(ticks.bid[index])
        ask = float(ticks.ask[index])
        if not (bid > 0.0 and ask > 0.0):
            report.errors.append(f"tick {index} has a non-positive price")
        elif bid > ask:
            report.errors.append(f"tick {index} is crossed")
    return report


def require_valid(report: ValidationReport) -> None:
    if not report.ok:
        raise DataUnavailableError("; ".join(report.errors))


def load_bars(path: Path, *, duration: timedelta, timestamp_is_open: bool = False) -> BarTable:
    """CSV columns: `close_time_utc` or `datetime`, plus OHLC. Optional `spread_pips`.

    Naive timestamps are rejected. Pass `timestamp_is_open` when the file stores
    the MT5 bar open and the column is already UTC.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise DataUnavailableError(f"{path} has no header")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        time_name = fields.get("close_time_utc") or fields.get("datetime") or fields.get("time")
        if time_name is None:
            raise DataUnavailableError(f"{path} has no timestamp column")
        for name in ("open", "high", "low", "close"):
            if name not in fields:
                raise DataUnavailableError(f"{path} is missing {name}")
        open_time: list[datetime] = []
        close_time: list[datetime] = []
        opens: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        closes: list[float] = []
        spreads: list[float] = []
        for line, row in enumerate(reader, start=2):
            stamp = _parse_time(row[time_name], path, line)
            close_stamp = stamp + duration if timestamp_is_open else stamp
            open_stamp = stamp if timestamp_is_open else stamp - duration
            open_time.append(open_stamp)
            close_time.append(close_stamp)
            opens.append(_number(row[fields["open"]], path, line, "open"))
            highs.append(_number(row[fields["high"]], path, line, "high"))
            lows.append(_number(row[fields["low"]], path, line, "low"))
            closes.append(_number(row[fields["close"]], path, line, "close"))
            spread_field = fields.get("spread_pips")
            if spread_field is None or row[spread_field].strip() == "":
                spreads.append(float("nan"))
            else:
                spreads.append(_number(row[spread_field], path, line, "spread_pips"))
    if not close_time:
        raise DataUnavailableError(f"{path} has no bars")
    return BarTable(
        open_time=open_time,
        close_time=close_time,
        open=np.array(opens, dtype=np.float64),
        high=np.array(highs, dtype=np.float64),
        low=np.array(lows, dtype=np.float64),
        close=np.array(closes, dtype=np.float64),
        spread_pips=np.array(spreads, dtype=np.float64),
    )


def load_ticks(path: Path) -> TickTable:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise DataUnavailableError(f"{path} has no header")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        for name in ("time_utc", "bid", "ask"):
            if name not in fields:
                raise DataUnavailableError(f"{path} is missing {name}")
        times: list[datetime] = []
        bids: list[float] = []
        asks: list[float] = []
        for line, row in enumerate(reader, start=2):
            times.append(_parse_time(row[fields["time_utc"]], path, line))
            bids.append(_number(row[fields["bid"]], path, line, "bid"))
            asks.append(_number(row[fields["ask"]], path, line, "ask"))
    if not times:
        raise DataUnavailableError(f"{path} has no ticks")
    return TickTable(time=times, bid=np.array(bids), ask=np.array(asks))


def load_spec(path: Path) -> SymbolSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DataUnavailableError(f"{path} must contain an object")
    try:
        return SymbolSpec(
            symbol=str(payload["symbol"]),
            digits=int(payload["digits"]),
            point=float(payload["point"]),
            tick_size=float(payload["tick_size"]),
            tick_value_account=float(payload["tick_value_account"]),
            volume_min=float(payload["volume_min"]),
            volume_step=float(payload["volume_step"]),
            volume_max=float(payload["volume_max"]),
            stops_level_points=int(payload["stops_level_points"]),
            contract_size=float(payload["contract_size"]),
            profit_currency=str(payload["profit_currency"]),
            account_currency=str(payload["account_currency"]),
            commission_per_lot_side=float(payload.get("commission_per_lot_side", 0.0)),
            source=str(payload.get("source", "user-file")),
            verified_from_terminal=bool(payload.get("verified_from_terminal", False)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataUnavailableError(f"{path} is not a usable EURGBP contract spec") from exc


def load_events(path: Path) -> tuple[datetime, ...]:
    """Read `release_time_utc`. Rows naming a currency other than GBP or EUR are dropped."""
    events: list[datetime] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "release_time_utc" not in reader.fieldnames:
            raise DataUnavailableError(f"{path} needs release_time_utc")
        for row in reader:
            currency = (row.get("currency") or "").strip().upper()
            if currency and currency not in {"GBP", "EUR"}:
                continue
            title = (row.get("event") or "").strip()
            if title and not _relevant_event(title):
                continue
            events.append(require_utc(datetime.fromisoformat(row["release_time_utc"])))
    return tuple(sorted(events))


def probe_mt5(importer: Callable[[], Any] | None = None) -> Probe:
    """Ask the local MetaTrader 5 package for a connection. Never invent prices."""
    loader = importer if importer is not None else _import_mt5
    try:
        module = loader()
    except ImportError as exc:
        return Probe(False, f"MetaTrader5 package is not available: {exc}")
    except OSError as exc:
        return Probe(False, f"MetaTrader5 could not be loaded: {exc}")
    initialize = getattr(module, "initialize", None)
    if not callable(initialize):
        return Probe(False, "MetaTrader5 package has no initialize()")
    try:
        ok = bool(initialize())
    except Exception as exc:  # noqa: BLE001 — the terminal boundary is allowed to fail closed
        return Probe(False, f"MT5 initialize failed: {exc}")
    if not ok:
        detail = _last_error(module)
        _shutdown(module)
        return Probe(False, f"MT5 terminal did not initialize: {detail}")
    try:
        return _probe_connected(module)
    finally:
        _shutdown(module)


def fetch_mt5(
    out_dir: Path,
    *,
    start: datetime,
    end: datetime,
    server_utc_offset_hours: float | None,
    importer: Callable[[], Any] | None = None,
) -> Probe:
    """Copy closed EURGBP bars from a connected terminal.

    The offset is required. This function does not guess the Anzo server clock
    and does not write a substitute price if the copy fails.
    """
    if server_utc_offset_hours is None:
        raise DataUnavailableError("server UTC offset is required and was not provided")
    probe = probe_mt5(importer)
    if not probe.available:
        raise DataUnavailableError(probe.detail)
    loader = importer if importer is not None else _import_mt5
    module = loader()
    if not bool(module.initialize()):
        raise DataUnavailableError(f"MT5 terminal did not initialize: {_last_error(module)}")
    try:
        _write_terminal_export(module, out_dir, start, end, server_utc_offset_hours)
    finally:
        _shutdown(module)
    return Probe(True, "exported closed bars", "EURGBP", server_utc_offset_hours)


def _probe_connected(module: Any) -> Probe:
    info = module.symbol_info("EURGBP")
    if info is None:
        return Probe(False, "EURGBP is not in the connected terminal")
    return Probe(
        True,
        "connected; server offset is not inferred from the broker name",
        symbol="EURGBP",
        server_utc_offset_hours=None,
    )


def _write_terminal_export(module: Any, out_dir: Path, start: datetime, end: datetime, offset_hours: float) -> None:
    rates = module.copy_rates_range("EURGBP", module.TIMEFRAME_M5, start, end)
    if rates is None or len(rates) == 0:
        raise DataUnavailableError(f"MT5 returned no EURGBP M5 bars: {_last_error(module)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "eurgbp-m5.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["close_time_utc", "open", "high", "low", "close", "spread_pips"])
        for row in rates:
            open_server = datetime.fromtimestamp(int(row["time"]), tz=UTC).replace(tzinfo=None)
            open_utc = open_server - timedelta(hours=offset_hours)
            close_utc = open_utc + timedelta(minutes=5)
            point = 0.00001
            spread_pips = float(row["spread"]) * point / 0.0001 if "spread" in row.dtype.names else ""
            writer.writerow(
                [
                    close_utc.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    spread_pips,
                ]
            )
    (out_dir / "clock.json").write_text(
        json.dumps(
            {
                "server_utc_offset_hours": offset_hours,
                "source": "user",
                "note": "Offset was passed in by the caller. It was not inferred from a broker name.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _import_mt5() -> Any:
    import MetaTrader5 as mt5

    return mt5


def _shutdown(module: Any) -> None:
    shutdown = getattr(module, "shutdown", None)
    if callable(shutdown):
        shutdown()


def _last_error(module: Any) -> str:
    last = getattr(module, "last_error", None)
    if callable(last):
        return str(last())
    return "no last_error()"


def _relevant_event(title: str) -> bool:
    folded = title.casefold()
    if any(word in folded for word in ("speech", "speaks", "hearing", "election", "auction", "holiday")):
        return False
    keys = (
        "cpi",
        "employment",
        "unemployment",
        "claimant",
        "payroll",
        "average earnings",
        "wage",
        "bank rate",
        "official bank rate",
        "interest rate",
        "refinancing",
        "deposit facility",
        "monetary policy",
        "ecb",
        "boe",
    )
    return any(key in folded for key in keys)


def _aware(report: ValidationReport, stamp: datetime, index: int) -> datetime | None:
    try:
        return require_utc(stamp)
    except ValueError:
        report.errors.append(f"bar or tick {index} has a naive timestamp")
        return None


def _check_price_bar(report: ValidationReport, bars: BarTable, index: int) -> None:
    prices = [float(bars.open[index]), float(bars.high[index]), float(bars.low[index]), float(bars.close[index])]
    if not all(price > 0.0 and price == price for price in prices):
        report.errors.append(f"bar {index} has a non-positive or non-finite price")
        return
    if prices[1] < prices[2]:
        report.errors.append(f"bar {index} has high below low")
    if not (prices[2] <= prices[0] <= prices[1] and prices[2] <= prices[3] <= prices[1]):
        report.errors.append(f"bar {index} has open or close outside the high-low range")


def _session_gap(previous: datetime, current: datetime, duration: timedelta) -> bool:
    """True when weekday bars skipped a print. Weekend closes are not filled or flagged."""
    start = require_utc(previous)
    end = require_utc(current)
    gap = end - start
    if gap <= duration:
        return False
    if start.weekday() >= 5 or end.weekday() >= 5:
        return False
    if start.weekday() == 4 and end.weekday() == 0:
        return False
    return True


def _return(previous: float, current: float) -> float | None:
    if previous <= 0.0 or current <= 0.0:
        return None
    return (current / previous) - 1.0


def _parse_time(value: str, path: Path, line: int) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DataUnavailableError(f"{path}:{line} has an unreadable timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataUnavailableError(f"{path}:{line} timestamp is naive; refusing to assume UTC")
    return parsed.astimezone(UTC)


def _number(value: str, path: Path, line: int, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise DataUnavailableError(f"{path}:{line} {name} is not a number") from exc
