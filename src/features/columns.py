"""Names shared by the snapshot, the fair-value model, and attribution."""

from __future__ import annotations

RATE_FIELDS = (
    "level",
    "change_1d",
    "change_5d",
    "change_20d",
    "slope_5d",
    "slope_20d",
    "zscore_60d",
    "zscore_252d",
    "percentile_252d",
)

POLICY_FIELDS = (
    "policy_diff_1m",
    "policy_diff_3m",
    "policy_diff_6m",
    "policy_diff_12m",
    "repricing_1d",
    "repricing_5d",
    "curve_slope_diff",
)

GROWTH_FIELDS = ("growth_diff", "growth_diff_index")
INFLATION_FIELDS = ("inflation_diff",)
RISK_FIELDS = ("risk_premium_diff",)
POSITIONING_FIELDS = ("cftc_net", "risk_reversal", "implied_vol", "open_interest")
MOMENTUM_FIELDS = ("momentum_5d", "momentum_20d", "momentum_60d", "realized_vol_20d")
EXPECTATION_FIELDS = (
    "expected_rate_diff_1m",
    "expected_rate_diff_3m",
    "expected_policy_diff_3m",
    "expected_policy_diff_6m",
    "expected_growth_diff",
    "expected_inflation_diff",
)

# Fair value is a relative-fundamental value. Own-price momentum is excluded on
# purpose: putting the jump into X would hide the mispricing the book is built to see.
# expected_policy_diff_3m is the same OIS path as policy_diff_3m. It stays in the
# expectation block and is left out of the regression so the design is not singular.
FAIR_VALUE_FEATURES = (
    "rate_level",
    "rate_change_20d",
    "policy_diff_3m",
    "policy_repricing_5d",
    "growth_diff_index",
    "inflation_diff",
    "risk_premium_diff",
    "expected_rate_diff_3m",
    "expected_growth_diff",
    "expected_inflation_diff",
)

FEATURE_GROUPS = {
    "rate": ("rate_level", "rate_change_20d", "expected_rate_diff_3m"),
    "policy": ("policy_diff_3m", "policy_repricing_5d"),
    "growth": ("growth_diff_index", "expected_growth_diff"),
    "inflation": ("inflation_diff", "expected_inflation_diff"),
    "risk": ("risk_premium_diff",),
    "positioning": (),
    "momentum": (),
}

EXPECTED_LOG_PRICE_SIGNS = {
    "rate_level": -1,
    "policy_diff_3m": -1,
    "growth_diff_index": -1,
    "inflation_diff": -1,
    "risk_premium_diff": -1,
    "expected_rate_diff_3m": -1,
    "expected_policy_diff_3m": -1,
    "expected_growth_diff": -1,
    "expected_inflation_diff": -1,
}

GROWTH_CATEGORIES = ("GDP", "PMI", "RETAIL_SALES", "EMPLOYMENT", "INDUSTRIAL_PRODUCTION")
INFLATION_CATEGORIES = ("CPI", "CORE_CPI", "SERVICES_INFLATION", "WAGES")
RISK_EUR = ("OAT_BUND", "BTP_BUND", "EU_CREDIT")
RISK_GBP = ("GILT_RISK", "FISCAL_STRESS", "UK_CREDIT")
