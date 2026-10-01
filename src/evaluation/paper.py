"""V1 paper trading is the event-driven book. It does not send an order."""

from __future__ import annotations

import json
from pathlib import Path

from domain.models import BacktestRun


def record_paper_session(path: Path, run: BacktestRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = run.model_dump(mode="json")
    payload["mode"] = "PAPER"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")
