"""Central-bank policy in a headline. A vague story still has no pressure.

UK tightening supports GBP and lowers the EURGBP factor. Euro-area tightening
supports EUR and raises it. A Fed tightening is treated as tighter dollar
liquidity, with GBP more sensitive than EUR, so the EURGBP factor rises.
"""

from __future__ import annotations

import re

_TIGHT = ("加息", "升息", "上调利率", "提高利率", "rate hike", "hikes", "hike", "hawkish", "鹰派")
_EASE = ("降息", "减息", "下调利率", "降低利率", "rate cut", "cuts rates", "dovish", "鸽派")
_HOLD = ("维持利率", "利率不变", "按兵不动", "holds rates", "unchanged")
_UNEXPECTED = ("意外", "超预期", "unexpectedly", "surprise")
_BP = re.compile(r"(\d+(?:\.\d+)?)\s*(?:个)?\s*(?:基点|bp)\b", re.IGNORECASE)


def policy_pressure(headline: str) -> float | None:
    """Signed EURGBP pressure from one policy headline. None when the action is unclear."""
    region = _region(headline)
    stance = _stance(headline)
    if region is None or stance is None:
        return None
    leg = {"UK": -1.0, "EZ": 1.0, "US": 1.0}[region]
    return max(-1.0, min(1.0, leg * stance * _magnitude(headline)))


def policy_narrative(headline: str) -> str | None:
    pressure = policy_pressure(headline)
    region = _region(headline)
    stance = _stance(headline)
    if pressure is None or region is None or stance is None:
        return None
    action = "tighter" if stance > 0 else "easier"
    if region == "US":
        channel = "GBP more sensitive to dollar liquidity than EUR"
    elif pressure < 0:
        channel = "GBP relative support → EURGBP bearish factor"
    else:
        channel = "EUR relative support → EURGBP bullish factor"
    if region == "US":
        tilt = "EURGBP bullish factor" if pressure > 0 else "EURGBP bearish factor"
        return f"US policy {action} → {channel} → {tilt}"
    return f"{region} policy {action} → {channel}"


def _region(headline: str) -> str | None:
    text = headline.lower()
    if any(token in headline for token in ("美联储", "联邦储备", "美联储局")) or any(
        token in text for token in ("federal reserve", "fomc", "fed ")
    ):
        return "US"
    if any(token in headline for token in ("欧洲央行", "欧央行", "欧洲中央银行")) or "ecb" in text:
        return "EZ"
    if any(token in headline for token in ("英国央行", "英央行", "英格兰银行")) or "boe" in text:
        return "UK"
    return None


def _stance(headline: str) -> float | None:
    text = headline.lower()
    tight = _has(headline, text, _TIGHT)
    ease = _has(headline, text, _EASE)
    if tight and ease:
        return None
    if tight:
        return 1.0
    if ease:
        return -1.0
    return None


def _magnitude(headline: str) -> float:
    text = headline.lower()
    found = _BP.search(headline) or _BP.search(text)
    if found is not None:
        return min(1.0, float(found.group(1)) / 50.0)
    if any(token in headline or token in text for token in _UNEXPECTED):
        return 0.7
    if _has(headline, text, ("鹰派", "鸽派", "hawkish", "dovish")):
        return 0.35
    return 0.5


def _has(headline: str, lowered: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in lowered or token in headline for token in tokens)
