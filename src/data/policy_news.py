"""Central-bank headlines are regime context. They do not set an EURGBP factor.

UK and euro-area policy still name the region and the stance. A Fed headline is
a global liquidity event. Neither one adds a signed pressure: the relative
effect of a Fed shock is regime-dependent, and it has not been validated.
"""

from __future__ import annotations

_TIGHT = ("加息", "升息", "上调利率", "提高利率", "rate hike", "hikes", "hike", "hawkish", "鹰派")
_EASE = ("降息", "减息", "下调利率", "降低利率", "rate cut", "cuts rates", "dovish", "鸽派")


def policy_pressure(headline: str) -> float | None:
    """No signed EURGBP pressure. Kept so a headline cannot be turned into a factor."""
    return None


def policy_context(headline: str) -> str | None:
    """Regime note for a recognised policy action. None when the action is unclear."""
    region = _region(headline)
    stance = _stance(headline)
    if region is None or stance is None:
        return None
    action = "tighter" if stance > 0 else "easier"
    if region == "US":
        return f"US policy {action} is a global liquidity event and does not set an EURGBP factor"
    return f"{region} policy {action} is regime context and does not set an EURGBP factor"


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


def _has(headline: str, lowered: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in lowered or token in headline for token in tokens)
