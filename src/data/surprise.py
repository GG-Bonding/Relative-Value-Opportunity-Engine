"""Consensus surprise from releases that were already observable.

surprise = (actual - consensus) / sample_std(prior raw errors)

The current release and every later release are excluded from the standard
deviation. A short history is missing, not zero.
"""

from __future__ import annotations

import numpy as np
import polars as pl


def with_surprise(releases: pl.DataFrame, min_history: int = 8) -> pl.DataFrame:
    if releases.is_empty():
        return releases.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("historical_surprise_std"),
            pl.lit(None, dtype=pl.Float64).alias("surprise"),
        )
    ordered = releases.sort(["series_id", "observed_at", "revision_number", "version"])
    surprises: list[float | None] = []
    stds: list[float | None] = []
    for _key, group in ordered.group_by("series_id", maintain_order=True):
        history: list[float] = []
        for row in group.iter_rows(named=True):
            consensus = row["consensus"]
            if consensus is None or len(history) < min_history:
                surprises.append(None)
                stds.append(None)
            else:
                std = float(np.std(np.asarray(history, dtype=float), ddof=1))
                if not np.isfinite(std) or std < 1e-12:
                    surprises.append(None)
                    stds.append(None)
                else:
                    surprises.append(float((row["actual"] - consensus) / std))
                    stds.append(std)
            if consensus is not None:
                history.append(float(row["actual"] - consensus))
    return ordered.with_columns(
        pl.Series("historical_surprise_std", stds, dtype=pl.Float64),
        pl.Series("surprise", surprises, dtype=pl.Float64),
    )
