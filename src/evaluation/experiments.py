"""Failed experiments stay in the registry."""

from __future__ import annotations

import json
from pathlib import Path

from domain.models import ResearchExperiment


class ExperimentRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def add(self, experiment: ResearchExperiment) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(experiment.model_dump_json() + "\n")

    def all(self) -> list[ResearchExperiment]:
        if self.path.stat().st_size == 0:
            return []
        rows: list[ResearchExperiment] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(ResearchExperiment.model_validate(json.loads(line)))
        return rows
