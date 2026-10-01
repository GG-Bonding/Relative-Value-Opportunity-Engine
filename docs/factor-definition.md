# Factor definitions

Every input is a relative quantity. A UK level never enters the model without the euro-area counterpart.

## Rate

`rate_level = UK2Y - DE2Y`.

Also `change_1d`, `change_5d`, `change_20d`, `slope_5d`, `slope_20d`, `zscore_60d`, `zscore_252d`, `percentile_252d`. A z-score with fewer than the window's observations is missing, not zero.

## Policy

BoE expected path minus ECB expected path at 1M, 3M, 6M, and 12M.

`policy_diff_1m`, `policy_diff_3m`, `policy_diff_6m`, `policy_diff_12m`, `repricing_1d`, `repricing_5d`, `curve_slope_diff`.

`curve_slope_diff` is `(BoE 12M - BoE 1M) - (ECB 12M - ECB 1M)`.

## Growth

Categories: GDP, PMI, retail sales, employment, industrial production. Each release becomes a surprise against the consensus, scaled by the pre-release standard deviation of that series. `growth_diff` carries the latest relative surprise. `growth_diff_index` is an information EMA of those surprises. Categories with no release stay out of the average.

## Inflation

CPI, core CPI, services inflation, and wages, combined the same way into `inflation_diff`.

## Risk premium

Euro side: OAT-Bund, BTP-Bund, European credit. Sterling side: gilt risk, a fiscal-stress proxy, UK credit. A side is missing unless every metric on that side is present. `risk_premium_diff` is the euro mean minus the sterling mean.

## Positioning and tape

CFTC, risk reversal, implied volatility, and open interest are supported columns. V1's laboratory does not invent them. They stay missing.

The tape does compute `momentum_5d`, `momentum_20d`, `momentum_60d`, and `realized_vol_20d`. Realized volatility is a daily standard deviation and is missing until the window is full. Momentum is not a fair-value regressor.

## Fair-value regressors

`rate_level`, `rate_change_20d`, `policy_diff_3m`, `policy_repricing_5d`, `growth_diff_index`, `inflation_diff`, `risk_premium_diff`, `expected_rate_diff_3m`, `expected_growth_diff`, `expected_inflation_diff`.

`expected_policy_diff_3m` is omitted because it duplicates `policy_diff_3m`.
