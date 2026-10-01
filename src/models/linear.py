"""Rolling linear fits on a training window that ends before the prediction row."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LinearRegression, Ridge


@dataclass(frozen=True)
class LinearFit:
    intercept: float
    coefficients: dict[str, float]
    means: dict[str, float]
    scales: dict[str, float]
    r2: float
    n: int


def fit_linear(
    features: np.ndarray,
    target: np.ndarray,
    names: list[str],
    alpha: float | None,
) -> LinearFit | None:
    if features.ndim != 2 or len(target) != len(features) or len(target) < 3:
        return None
    if not np.isfinite(features).all() or not np.isfinite(target).all():
        return None
    std = features.std(axis=0, ddof=1)
    means = features.mean(axis=0)
    keep = std >= 1e-12
    if int(keep.sum()) == 0:
        return None
    kept_names = [name for name, flag in zip(names, keep, strict=True) if flag]
    scaled = (features[:, keep] - means[keep]) / std[keep]
    if alpha is None:
        estimator: LinearRegression | Ridge = LinearRegression(fit_intercept=True)
    else:
        estimator = Ridge(alpha=float(alpha), fit_intercept=True)
    estimator.fit(scaled, target)
    coefficients = {name: float(coef) for name, coef in zip(kept_names, np.asarray(estimator.coef_), strict=True)}
    return LinearFit(
        intercept=float(estimator.intercept_),
        coefficients=coefficients,
        means={name: float(mu) for name, mu in zip(kept_names, means[keep], strict=True)},
        scales={name: float(scale) for name, scale in zip(kept_names, std[keep], strict=True)},
        r2=float(estimator.score(scaled, target)),
        n=int(len(target)),
    )


def predict_row(fit: LinearFit, values: np.ndarray, names: list[str]) -> tuple[float, dict[str, float]]:
    index = {name: position for position, name in enumerate(names)}
    prediction = fit.intercept
    contributions: dict[str, float] = {}
    for name, coefficient in fit.coefficients.items():
        raw = float(values[index[name]])
        scaled = (raw - fit.means[name]) / fit.scales[name]
        piece = float(scaled * coefficient)
        contributions[name] = piece
        prediction += piece
    return float(prediction), contributions
