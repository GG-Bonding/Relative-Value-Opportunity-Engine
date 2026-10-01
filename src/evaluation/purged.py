"""Walk-forward splits with a purge and an embargo. No random splits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fold:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def walk_forward_folds(
    n_obs: int,
    train_size: int,
    test_size: int,
    horizon: int,
    embargo: int,
) -> list[Fold]:
    if min(train_size, test_size, horizon) < 1:
        raise ValueError("sizes must be positive")
    if embargo < 0:
        raise ValueError("embargo cannot be negative")
    folds: list[Fold] = []
    test_start = train_size + horizon + embargo
    while test_start + test_size <= n_obs:
        train_end = test_start - horizon - embargo
        train_start = max(0, train_end - train_size)
        if train_end - train_start < train_size // 2:
            break
        folds.append(Fold(train_start, train_end, test_start, test_start + test_size))
        test_start += test_size
    return folds


def assert_no_label_overlap(folds: list[Fold], horizon: int, embargo: int) -> None:
    for fold in folds:
        if fold.train_end > fold.test_start - horizon - embargo:
            raise AssertionError("training label overlaps the test block")
        if fold.test_start < fold.train_end:
            raise AssertionError("test block is not strictly after training")
