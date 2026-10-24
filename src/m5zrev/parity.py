"""Closed-bar values the MQL5 EA must keep in its header.

The numbers are the Python definition. MetaEditor has not been asked to recompute
them; the EA test checks that the source still carries this vector.
"""

from __future__ import annotations

import math

import numpy as np

from m5zrev.indicators import deviation_z


def golden_closes() -> np.ndarray:
    """Eighty deterministic bid closes. This is a parity vector, not a market."""
    return np.array([round(0.85 + 0.001 * math.sin(index / 5.0), 5) for index in range(80)])


def golden_block() -> str:
    closes = golden_closes()
    z_values = deviation_z(closes, 12, 20)[2]
    lines = ["// BEGIN_M5Z_PARITY", "// ema=12 window=20"]
    for index, price in enumerate(closes):
        value = float(z_values[index])
        text = "nan" if not math.isfinite(value) else f"{value:.10f}"
        lines.append(f"// {index},{price:.5f},{text}")
    lines.append("// END_M5Z_PARITY")
    return "\n".join(lines)


def parse_golden_block(text: str) -> tuple[np.ndarray, np.ndarray]:
    start = text.index("// BEGIN_M5Z_PARITY")
    end = text.index("// END_M5Z_PARITY", start)
    closes: list[float] = []
    values: list[float] = []
    for line in text[start:end].splitlines():
        if not line.startswith("// ") or line.startswith("// BEGIN") or line.startswith("// ema"):
            continue
        _index, price, raw = line[3:].split(",")
        closes.append(float(price))
        values.append(float("nan") if raw == "nan" else float(raw))
    return np.array(closes), np.array(values)
