"""The EA source must carry the Python closed-bar vector and the safety switches.

This does not compile the EA and it does not read a terminal.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from m5zrev.indicators import deviation_z
from m5zrev.parity import golden_block, parse_golden_block

ROOT = Path(__file__).resolve().parents[1]
EA = ROOT / "adapters" / "mt5" / "EURGBP_MeanReversion_ZScore.mq5"


def test_golden_vector_matches_python_within_one_millionth() -> None:
    text = EA.read_text(encoding="utf-8")
    assert golden_block() in text
    closes, values = parse_golden_block(text)
    z_values = deviation_z(closes, 12, 20)[2]
    np.testing.assert_allclose(values, z_values, atol=1e-6, equal_nan=True)
    assert "2.0 / (period + 1.0)" in text
    assert "k < i" in text
    assert "(window - 1.0)" in text
    assert "drop the forming bar" in text


def test_live_orders_stay_off_until_the_user_enables_them() -> None:
    text = EA.read_text(encoding="utf-8")
    assert "InpEnableTrading          = false" in text
    assert "InpAllowLiveTrading       = false" in text
    assert "InpServerOffsetConfirmed  = false" in text
    assert "InpMagic                  = 20261024" in text
    assert "OnTradeTransaction" in text
    assert "OrderCalcProfit" in text
    assert "OurPosition" in text
    assert "PHASE_PENDING" in text
    assert "MQL_TESTER" in text
    assert "LastSundayUtc" in text
    assert text.count("OrderSend(") == 2
    assert text.count("g_phase = PHASE_OPEN") == 1
    mark = text.split("void MarkOpen()", 1)[1].split("void Restore()", 1)[0]
    assert "g_phase = PHASE_OPEN" in mark
    assert "OrderSend" not in mark
    assert "10009" not in mark
