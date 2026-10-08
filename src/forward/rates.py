"""UK2Y and DE2Y prints. A missing curve stays missing and is not filled with zero."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from domain.errors import DataValidationError
from forward.mt5 import parse_feed_time


@dataclass(frozen=True)
class RateSnapshot:
    uk2y: float | None
    de2y: float | None
    uk2y_time: datetime | None
    de2y_time: datetime | None

    @property
    def rate_diff(self) -> float | None:
        """UK2Y minus DE2Y, in percentage points, only on one shared trading day."""
        if self.uk2y is None or self.de2y is None or self.uk2y_time is None or self.de2y_time is None:
            return None
        if self.uk2y_time.astimezone(UTC).date() != self.de2y_time.astimezone(UTC).date():
            return None
        return self.uk2y - self.de2y


def load_rates(path: str | Path) -> RateSnapshot:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DataValidationError("rate file must contain a JSON object")
    uk, uk_time = _curve(payload, "UK2Y")
    de, de_time = _curve(payload, "DE2Y")
    return RateSnapshot(uk2y=uk, de2y=de, uk2y_time=uk_time, de2y_time=de_time)


def _curve(payload: dict[str, Any], name: str) -> tuple[float | None, datetime | None]:
    if name not in payload or payload[name] is None:
        return None, None
    raw = payload[name]
    if not isinstance(raw, dict) or raw.get("rate") is None:
        return None, None
    try:
        rate = float(raw["rate"])
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"{name} rate is not a number") from exc
    return rate, _stamp(raw.get("time"), name)


def _stamp(value: Any, name: str) -> datetime:
    if value is None:
        raise DataValidationError(f"{name} is missing time")
    try:
        return parse_feed_time(value)
    except DataValidationError as exc:
        raise DataValidationError(f"{name} is missing time") from exc
