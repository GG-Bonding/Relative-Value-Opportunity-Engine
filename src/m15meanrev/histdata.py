"""HistData M1 bid bars, aggregated to M15 closes in UTC.

The file clock is Eastern Standard Time all year, with no daylight-saving shift.
The stamp on each M1 row is that minute's open. An M15 datetime in the CSV this
module writes is the close of the 15-minute bucket.
"""

from __future__ import annotations

import re
import sys
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np

from m15meanrev.load import BarSeries, daily_from_m15, write_bars, write_daily

EST = timezone(timedelta(hours=-5))
_PAGE = (
    "https://www.histdata.com/download-free-forex-historical-data/"
    "?/ascii/1-minute-bar-quotes/eurgbp/{year}"
)
_TOKEN = re.compile(r'id="tk" value="([0-9a-f]+)"')
SOURCE_NOTE = (
    "HistData.com Generic ASCII M1 bid。"
    "时间是 Eastern Standard Time（UTC-5，无夏令时），已转为 UTC。"
    "M1 时间戳是该分钟开盘；写出的 datetime 是 M15 收盘。"
    "日线是每个 UTC 日期最后一根 M15 的 bid 收盘。"
)


def fetch_m15(start_year: int, end_year: int, out_dir: Path) -> tuple[Path, Path]:
    """Download each calendar year once and write `eurgbp-m15.csv` plus `eurgbp-d1.csv`."""
    if end_year < start_year:
        raise ValueError("end year is before start year")
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    archives: list[Path] = []
    with httpx.Client(timeout=600.0, trust_env=False, follow_redirects=True) as client:
        for year in range(start_year, end_year + 1):
            archive = raw / f"EURGBP_M1_{year}.zip"
            print(f"histdata {year}", file=sys.stderr, flush=True)
            _ensure_zip(client, year, archive)
            archives.append(archive)
    bars = _concat([read_histdata_zip(archive) for archive in archives])
    median = float(np.median(bars.close))
    if not 0.5 < median < 1.2:
        raise ValueError(f"EURGBP median close {median:.4f} is outside the expected range")
    m15_path = out_dir / "eurgbp-m15.csv"
    daily_path = out_dir / "eurgbp-d1.csv"
    write_bars(m15_path, bars)
    write_daily(daily_path, daily_from_m15(bars))
    (out_dir / "source.txt").write_text(SOURCE_NOTE + "\n", encoding="utf-8")
    return m15_path, daily_path


def read_histdata_zip(path: Path) -> BarSeries:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"{path} should contain one CSV, found {names}")
        text = archive.read(names[0]).decode("utf-8-sig")
    return m1_text_to_m15(text)


def m1_text_to_m15(text: str) -> BarSeries:
    """Aggregate HistData M1 rows into M15 bars whose timestamps are close times."""
    opens: list[datetime] = []
    o_px: list[float] = []
    h_px: list[float] = []
    l_px: list[float] = []
    c_px: list[float] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(";")
        if len(parts) < 5:
            raise ValueError(f"line {line_number} is not an M1 bar")
        opened = _parse_open(parts[0], line_number)
        try:
            prices = tuple(float(parts[offset]) for offset in range(1, 5))
        except ValueError as exc:
            raise ValueError(f"line {line_number} has a non-numeric price") from exc
        if not all(np.isfinite(price) and price > 0 for price in prices):
            raise ValueError(f"line {line_number} has a non-positive price")
        opens.append(opened)
        o_px.append(prices[0])
        h_px.append(prices[1])
        l_px.append(prices[2])
        c_px.append(prices[3])
    if not opens:
        raise ValueError("HistData file has no bars")
    order = sorted(range(len(opens)), key=opens.__getitem__)
    return _aggregate(*_drop_identical_minutes(opens, o_px, h_px, l_px, c_px, order))


def _parse_open(stamp: str, line_number: int) -> datetime:
    text = stamp.strip()
    if len(text) != 15 or text[8] != " ":
        raise ValueError(f"line {line_number} has a bad timestamp")
    try:
        opened = datetime(
            int(text[0:4]),
            int(text[4:6]),
            int(text[6:8]),
            int(text[9:11]),
            int(text[11:13]),
            int(text[13:15]),
            tzinfo=EST,
        )
    except ValueError as exc:
        raise ValueError(f"line {line_number} has a bad timestamp") from exc
    return opened.astimezone(UTC)


def _drop_identical_minutes(
    opens: list[datetime],
    o_px: list[float],
    h_px: list[float],
    l_px: list[float],
    c_px: list[float],
    order: list[int],
) -> tuple[list[datetime], list[float], list[float], list[float], list[float]]:
    """HistData repeats an hour of identical rows in each yearly file. Conflicting prices still fail."""
    kept_o: list[datetime] = []
    kept_open: list[float] = []
    kept_high: list[float] = []
    kept_low: list[float] = []
    kept_close: list[float] = []
    for index in order:
        if kept_o and opens[index] == kept_o[-1]:
            same = (
                o_px[index] == kept_open[-1]
                and h_px[index] == kept_high[-1]
                and l_px[index] == kept_low[-1]
                and c_px[index] == kept_close[-1]
            )
            if not same:
                raise ValueError(f"conflicting M1 bar at {opens[index].isoformat()}")
            continue
        kept_o.append(opens[index])
        kept_open.append(o_px[index])
        kept_high.append(h_px[index])
        kept_low.append(l_px[index])
        kept_close.append(c_px[index])
    return kept_o, kept_open, kept_high, kept_low, kept_close


def _aggregate(
    opens: list[datetime],
    o_px: list[float],
    h_px: list[float],
    l_px: list[float],
    c_px: list[float],
) -> BarSeries:
    times: list[datetime] = []
    out_o: list[float] = []
    out_h: list[float] = []
    out_l: list[float] = []
    out_c: list[float] = []
    index = 0
    count = len(opens)
    while index < count:
        start = opens[index]
        bucket = start.replace(minute=(start.minute // 15) * 15, second=0, microsecond=0)
        bucket_end = bucket + timedelta(minutes=15)
        nxt = index + 1
        while nxt < count and opens[nxt] < bucket_end:
            if opens[nxt] <= opens[nxt - 1]:
                raise ValueError("duplicate M1 timestamp")
            nxt += 1
        if index > 0 and opens[index] <= opens[index - 1]:
            raise ValueError("duplicate M1 timestamp")
        last = nxt - 1
        minute = timedelta(minutes=1)
        complete = opens[last] >= bucket_end - minute
        close_time = bucket_end if complete else opens[last] + minute
        times.append(close_time)
        out_o.append(o_px[index])
        out_h.append(max(h_px[index:nxt]))
        out_l.append(min(l_px[index:nxt]))
        out_c.append(c_px[last])
        index = nxt
    return BarSeries(
        time=times,
        open=np.array(out_o, dtype=np.float64),
        high=np.array(out_h, dtype=np.float64),
        low=np.array(out_l, dtype=np.float64),
        close=np.array(out_c, dtype=np.float64),
    )


def _concat(parts: list[BarSeries]) -> BarSeries:
    times: list[datetime] = []
    opens: list[np.ndarray] = []
    highs: list[np.ndarray] = []
    lows: list[np.ndarray] = []
    closes: list[np.ndarray] = []
    for part in parts:
        if times and part.time[0] <= times[-1]:
            raise ValueError("HistData years overlap")
        times.extend(part.time)
        opens.append(part.open)
        highs.append(part.high)
        lows.append(part.low)
        closes.append(part.close)
    return BarSeries(
        time=times,
        open=np.concatenate(opens),
        high=np.concatenate(highs),
        low=np.concatenate(lows),
        close=np.concatenate(closes),
    )


def _ensure_zip(client: httpx.Client, year: int, dest: Path) -> None:
    if dest.exists() and zipfile.is_zipfile(dest):
        return
    page = _PAGE.format(year=year)
    response = client.get(page, headers={"User-Agent": _agent()})
    response.raise_for_status()
    match = _TOKEN.search(response.text)
    if match is None or not match.group(1):
        raise ValueError(f"HistData did not issue a download token for {year}")
    posted = client.post(
        "https://www.histdata.com/get.php",
        data={
            "tk": match.group(1),
            "date": str(year),
            "datemonth": str(year),
            "platform": "ASCII",
            "timeframe": "M1",
            "fxpair": "EURGBP",
        },
        headers={"User-Agent": _agent(), "Referer": page},
    )
    posted.raise_for_status()
    if not posted.content.startswith(b"PK"):
        raise ValueError(f"HistData did not return a zip for {year}")
    dest.write_bytes(posted.content)
    if not zipfile.is_zipfile(dest):
        dest.unlink(missing_ok=True)
        raise ValueError(f"HistData zip for {year} is incomplete")


def _agent() -> str:
    return "Mozilla/5.0 (compatible; RelativeValueEngine/0.1; research)"
