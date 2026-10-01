"""EURGBP mechanisms registered before any fit."""

from __future__ import annotations

from domain.enums import AlphaLifecycle, MechanismKind
from domain.models import Mechanism, MechanismState

MECHANISMS: tuple[Mechanism, ...] = (
    Mechanism(
        mechanism_id="rate_differential",
        pair="EURGBP",
        name="Rate differential",
        kind=MechanismKind.STRUCTURAL_MECHANISM,
        statement=(
            "A rise in the UK 2Y yield relative to the German 2Y raises the carry of sterling assets, "
            "shifts private capital toward GBP, and lowers EURGBP."
        ),
        expected_sign_on_log_price=-1,
        feature_names=["rate_level", "rate_change_20d"],
        transmission="relative carry → portfolio allocation → spot demand",
    ),
    Mechanism(
        mechanism_id="policy_differential",
        pair="EURGBP",
        name="Policy differential",
        kind=MechanismKind.STRUCTURAL_MECHANISM,
        statement=(
            "What is priced is the expected BoE path versus the expected ECB path, "
            "not the spot Bank Rate. A hawkish repricing of that gap supports GBP and lowers EURGBP."
        ),
        expected_sign_on_log_price=-1,
        feature_names=["policy_diff_3m", "policy_repricing_5d", "policy_diff_12m"],
        transmission="expected policy path → rate expectations → FX",
    ),
    Mechanism(
        mechanism_id="growth_differential",
        pair="EURGBP",
        name="Growth differential",
        kind=MechanismKind.STRUCTURAL_MECHANISM,
        statement=(
            "A positive UK growth surprise relative to the eurozone raises relative sterling demand "
            "through expected returns and the policy response."
        ),
        expected_sign_on_log_price=-1,
        feature_names=["growth_diff", "growth_diff_index"],
        transmission="growth surprise → expected policy and asset demand → FX",
    ),
    Mechanism(
        mechanism_id="inflation_differential",
        pair="EURGBP",
        name="Inflation differential",
        kind=MechanismKind.STRUCTURAL_MECHANISM,
        statement=(
            "The registered channel is policy transmission: relative UK inflation pressure "
            "tightens the expected BoE path and supports GBP. Stagflation can break this sign; "
            "that break is a model-error case, not a silent change of the mechanism."
        ),
        expected_sign_on_log_price=-1,
        feature_names=["inflation_diff"],
        transmission="inflation pressure → expected policy path → FX",
    ),
    Mechanism(
        mechanism_id="risk_premium_differential",
        pair="EURGBP",
        name="Risk premium differential",
        kind=MechanismKind.STRUCTURAL_MECHANISM,
        statement=(
            "A wider euro sovereign or credit premium relative to gilts and UK credit "
            "is a risk premium on EUR, so EURGBP falls."
        ),
        expected_sign_on_log_price=-1,
        feature_names=["risk_premium_diff"],
        transmission="relative risk premium → required return → FX",
    ),
    Mechanism(
        mechanism_id="positioning",
        pair="EURGBP",
        name="Positioning and momentum",
        kind=MechanismKind.PATTERN,
        statement=(
            "Carry, options, open interest, and momentum describe crowding and the tape. "
            "They are not a relative fundamental, and they are excluded from fair value so that "
            "a price jump is not allowed to explain itself."
        ),
        expected_sign_on_log_price=-1,
        feature_names=["momentum_20d", "realized_vol_20d", "cftc_net", "risk_reversal"],
        transmission="crowding and flow can delay or accelerate convergence; they do not define fair value",
    ),
)


def registry() -> tuple[Mechanism, ...]:
    return MECHANISMS


def assess_mechanisms(
    *,
    as_of: object,
    coefficients: dict[str, float],
    alpha: AlphaLifecycle,
    tradable_net_edge: float | None,
) -> list[MechanismState]:
    from datetime import datetime

    if not isinstance(as_of, datetime):
        raise TypeError("as_of must be a datetime")
    states: list[MechanismState] = []
    for mechanism in MECHANISMS:
        signs = []
        for name in mechanism.feature_names:
            beta = coefficients.get(name)
            if beta is None or abs(beta) < 0.005:
                continue
            signs.append(int(np_sign(beta)) == mechanism.expected_sign_on_log_price)
        structural = bool(signs) and sum(signs) / len(signs) >= 0.5
        if mechanism.mechanism_id == "positioning":
            structural = False
        pattern = alpha in {AlphaLifecycle.ACTIVE, AlphaLifecycle.DECAYING, AlphaLifecycle.DORMANT}
        tradable = (
            alpha is AlphaLifecycle.ACTIVE
            and tradable_net_edge is not None
            and tradable_net_edge > 0
            and mechanism.kind is not MechanismKind.PATTERN
        )
        if structural and pattern and not tradable:
            evidence = "structural mechanism is still present; tradable lag is not"
        elif structural and tradable:
            evidence = "sign agrees and the traded residual still has positive net edge"
        elif structural:
            evidence = "coefficient sign agrees with the mechanism"
        else:
            evidence = "coefficient sign is missing or disagrees"
        states.append(
            MechanismState(
                as_of=as_of,
                mechanism_id=mechanism.mechanism_id,
                structural=structural,
                pattern=pattern,
                tradable_alpha=tradable,
                evidence=evidence,
            )
        )
    return states


def np_sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0
