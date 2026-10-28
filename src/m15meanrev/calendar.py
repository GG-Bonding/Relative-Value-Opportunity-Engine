"""UK and euro-area release times for the entry blackout.

The historical cache is Forex Factory, collected with the clock displayed in
Asia/Tehran (dataset Ehsanrs2/Forex_Factory_Calendar). A stamp of 00:00 in that
zone has no clock. Iran stopped daylight-saving time on 2022-09-22; stamps that
still say +04:30 after that date are read as +03:30.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_CURRENCIES = frozenset({"GBP", "EUR"})
_INCLUDE = (
    "cpi",
    "gdp",
    "employment",
    "unemployment",
    "claimant",
    "payroll",
    "wage",
    "earning",
    "retail sales",
    "pmi",
    "industrial production",
    "manufacturing production",
    "bank rate",
    "interest rate",
    "refinancing",
    "deposit rate",
    "monetary policy",
    "press conference",
)
_EXCLUDE = ("speech", "speaks", "hearing", "testimony", "election", "auction", "holiday")
_POLICY = ("bank rate", "refinancing", "deposit rate", "monetary policy", "press conference", "asset purchase")
_IRAN_STANDARD_FROM = "2022-09-22"
_BEFORE = timedelta(minutes=15)
_AFTER = timedelta(minutes=60)


@dataclass(frozen=True)
class Release:
    time: datetime
    currency: str
    event: str
    timing: str


@dataclass(frozen=True)
class CalendarBuild:
    releases: tuple[Release, ...]
    dropped_untimed: int
    dropped_titles: tuple[tuple[str, int], ...]


def event_kept(title: str) -> bool:
    """High-impact macro titles. Speeches, hearings, and elections stay out."""
    folded = title.casefold()
    if any(word in folded for word in _EXCLUDE):
        return False
    return any(word in folded for word in _INCLUDE)


def quoted_instant(raw: str) -> datetime | None:
    """UTC instant of a cache stamp. Midnight in the source zone has no clock."""
    if len(raw) < 19 or raw[11:19] in {"00:00:00", "23:59:59"}:
        return None
    stamp = datetime.fromisoformat(raw)
    if raw.endswith("+04:30") and raw[:10] >= _IRAN_STANDARD_FROM:
        stamp += timedelta(hours=1)
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def select_releases(rows: Iterable[Mapping[str, str]]) -> CalendarBuild:
    """Keep GBP/EUR high-impact data prints and borrow a clock only inside one cluster."""
    pending: list[tuple[str, str, str, datetime | None]] = []
    for row in rows:
        currency = (row.get("Currency") or "").strip()
        if currency not in _CURRENCIES:
            continue
        if "high" not in (row.get("Impact") or "").casefold():
            continue
        event = (row.get("Event") or "").strip()
        if not event or not event_kept(event):
            continue
        raw = (row.get("DateTime") or "").strip()
        if len(raw) < 10:
            continue
        pending.append((currency, event, raw[:10], quoted_instant(raw)))

    clocks: dict[tuple[str, str, str], set[datetime]] = {}
    for currency, event, day, instant in pending:
        if instant is None:
            continue
        clocks.setdefault((currency, day, _cluster(event)), set()).add(instant)

    chosen: dict[tuple[datetime, str, str], Release] = {}
    dropped: Counter[str] = Counter()
    for currency, event, day, instant in pending:
        timing = "quoted"
        if instant is None:
            options = clocks.get((currency, day, _cluster(event)), set())
            if len(options) != 1:
                dropped[event] += 1
                continue
            instant = next(iter(options))
            timing = "imputed"
        key = (instant, currency, event)
        current = chosen.get(key)
        if current is None or (current.timing == "imputed" and timing == "quoted"):
            chosen[key] = Release(instant, currency, event, timing)
    ordered = sorted(chosen.values(), key=lambda item: (item.time, item.currency, item.event))
    return CalendarBuild(tuple(ordered), sum(dropped.values()), tuple(dropped.most_common()))


def entry_windows(
    releases: Iterable[datetime],
    *,
    before: timedelta = _BEFORE,
    after: timedelta = _AFTER,
) -> tuple[tuple[datetime, datetime], ...]:
    """Half-open entry blackout [release − 15 minutes, release + 60 minutes)."""
    return tuple((stamp.astimezone(UTC) - before, stamp.astimezone(UTC) + after) for stamp in releases)


def write_calendar(build: CalendarBuild, path: Path, *, source: str, snapshot_through: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["release_time_utc", "currency", "event", "timing"])
        for release in build.releases:
            writer.writerow(
                [
                    release.time.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    release.currency,
                    release.event,
                    release.timing,
                ]
            )
    meta = {
        "source": source,
        "snapshot_through": snapshot_through,
        "releases": len(build.releases),
        "quoted": sum(item.timing == "quoted" for item in build.releases),
        "imputed": sum(item.timing == "imputed" for item in build.releases),
        "dropped_untimed": build.dropped_untimed,
        "dropped_titles": [{"event": name, "count": count} for name, count in build.dropped_titles],
    }
    path.with_name(path.stem + ".meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def load_events(path: Path) -> tuple[Release, ...]:
    releases: list[Release] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            stamp = datetime.fromisoformat(row["release_time_utc"]).astimezone(UTC)
            releases.append(Release(stamp, row["currency"], row["event"], row["timing"]))
    return tuple(releases)


def describe_calendar(path: Path) -> str:
    """Chinese note for the report: coverage, the offset fix, and the 2025 gap."""
    releases = load_events(path)
    meta_path = path.with_name(path.stem + ".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    first = releases[0].time.strftime("%Y-%m-%d %H:%M") if releases else "—"
    last = releases[-1].time.strftime("%Y-%m-%d %H:%M") if releases else "—"
    quoted = meta.get("quoted", sum(item.timing == "quoted" for item in releases))
    imputed = meta.get("imputed", sum(item.timing == "imputed" for item in releases))
    dropped = int(meta.get("dropped_untimed", 0))
    titles = meta.get("dropped_titles") or []
    dropped_text = "、".join(f"{item['event']} {item['count']}" for item in titles[:5])
    hole = f"没有钟点又借不到的 {dropped} 条不进入黑名单。"
    if dropped_text:
        hole = f"{hole}其中较多的是 {dropped_text}。"
    return (
        "事件黑名单来自 Forex Factory 历史缓存（Ehsanrs2/Forex_Factory_Calendar，"
        f"快照收到 {meta.get('snapshot_through', '2025-04-07')}）。"
        f"英镑和欧元区高影响数据公布 {len(releases)} 条：原文件自带钟点 {quoted} 条，"
        f"同一货币、同一日期、同一类事件里只有一个钟点时借用该钟点 {imputed} 条。"
        f"{hole}"
        "借不到钟点的那条不进黑名单；同一天另一条有钟点的公布仍然挡开仓。"
        f"覆盖 {first} UTC 至 {last} UTC。"
        "2022-09-22 之后仍标 +04:30 的钟点按 +03:30 解释，伊朗从那天起使用固定的 UTC+3:30。"
        "开仓窗口是 [公布 − 15 分钟, 公布 + 60 分钟)。只挡开仓，出场照常。"
        "缓存收到 2025-04-07。最后一条进入黑名单的公布之后，直到样本结束，黑名单是空的。"
    )


def _cluster(event: str) -> str:
    folded = event.casefold()
    if any(word in folded for word in _POLICY):
        return "policy"
    return folded
