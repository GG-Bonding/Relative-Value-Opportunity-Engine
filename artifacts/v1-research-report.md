# EURGBP V1 Research Report

This report separates two claims. The laboratory claim is that the engine follows its own rules on a known data-generating process. The market claim, that UK and euro-area relative fundamentals identify tradable EURGBP mispricing in vendor point-in-time history, is not established.

Conclusion: INCONCLUSIVE

## Data Coverage

Laboratory market rows: 3391. Macro release rows: 3252. The tape is synthetic, seeded, and labeled `source=laboratory`. It is not a substitute for OIS, consensus vintages, or revised national accounts.

## Point-in-Time Validation

Macros keep every revision. An as-of query returns the latest vintage whose `observed_at` is at or before the query, and it leaves the earlier print in the table. Surprise standard deviations use only earlier releases of the same series.

## Leakage Tests

Appending a rate or a macro revision after T does not change the snapshot hash or the factor row at T. Fair value at t is fit on rows strictly before t. Forward labels are purged by the label horizon plus an embargo. There is no random split.

## Economic Mechanisms

Six mechanisms are registered before the fit: rate, policy path, growth surprise, inflation-through-policy, risk premium, and positioning. Positioning is a pattern, not a fair-value input. Expected signs on log EURGBP are negative for a rise in UK-minus-euro relative carry, policy, growth, inflation pressure, and euro risk premium.

## Expectation Modeling

Expected rate gaps come from forward curves. Expected policy gaps come from the BoE and ECB path, not from spot Bank Rate. Expected growth and inflation are survey levels carried forward. They are not the surprise index and they are not filled with zero.

## Factor Definitions

Rate is UK2Y minus DE2Y, with changes, slopes, z-scores, and a trailing percentile. Policy is the expected-path gap at 1M, 3M, 6M, and 12M. Growth and inflation are relative surprises. Risk is the euro premium minus the sterling premium. Momentum and realized volatility are tape descriptors. Missing positioning stays missing.

## Factor IC

Walk-forward factor model, 5-day horizon: IC 0.3425, rank IC 0.3413, OOS R² -0.0157. This is a test of whether the factors forecast the next return, separate from the fair-value residual. A positive rank correlation with a negative R² means the sign lines up more often than the size of the forecast.

## Rolling IC

Alpha state uses the information coefficient of the residual fade on 21, 63, and 252 realized five-day outcomes. A window with no variance is an IC of zero, not a missing trade.

## IC Decay

The registered decay path is a long-window IC that is still positive while the 63-day and 21-day IC collapse, or a reaction lag that compresses from multi-day incorporation to one session. Either path sets the lifecycle to DECAYING and blocks new entries.

## Fair Value Accuracy

V1 fair value is a rolling ridge of log EURGBP on relative factors and expected relative fundamentals. The window is 504 sessions, the minimum is 252, and the fit excludes the prediction day. In the laboratory the price is a known function of those factors, lagged by five sessions until 2021.

## Coefficient Stability

Instability is the trailing standard deviation of the standardized UK-DE coefficient divided by the absolute mean of that coefficient. An unstable or wrong-signed fit is model error, not a mispricing.

## Residual Stability

Residual z-scores use the mean and standard deviation of earlier out-of-sample residuals only. A large residual is not tradable until the mechanism signs agree, the fit clears the R² floor, and the regime supports a fade.

## Event Response

Horizons are 5m, 15m, 30m, 1h, 4h, 1d, 3d, and 5d. A horizon with no print is missing. A later daily bar is not credited to a five-minute horizon.

## Reaction Lag

Response lag is the first horizon that captures at least half of the five-day move in the same direction. Half-life is the later horizon that gives back half of the peak, when that give-back exists.

## Reaction Lag Drift

The book compares the median lag of macro events over 756, 252, and 63 days, using only events whose five-day window is already known. When the short window is one session and the long window is still multi-day, alpha is DECAYING even if a noisy 21-day IC is still positive.

## Regime Analysis

Regimes are ordered rules. An extended residual is dislocation or mean reversion before it is allowed to be labeled trend or carry. Trend and carry do not authorize a fade. Liquidity stress does not authorize a trade.

## Mispricing Analysis

Classifications are possible mispricing, model error, regime break, event dislocation, liquidity distortion, and unknown. Only possible mispricing can become a trade, and only after the net-edge, crowding, data, and chase gates.

## Alpha Lifecycle

States are discovery, validating, active, decaying, dormant, retired, and rejected. The laboratory book is active while the five-day lag is the price process and decaying once incorporation collapses to the same session.

## Opportunity Performance

Trades: 60. After-cost pnl (return on capital): 0.143405. Sharpe: 1.7333. Max drawdown: 0.0010.

Decision counts: {'NO_TRADE': 1160, 'LONG': 27, 'WATCH': 231, 'ALPHA_DECAYING': 501, 'SHORT': 33, 'MODEL_UNCERTAIN': 73, 'WAIT_PULLBACK': 5, 'WAIT_CONFIRMATION': 4}.

## Entry Performance

Entries while the lag was alive, before 2021-01-01: 60, pnl 0.143405. Entries on or after 2021-05-01, when the lag had collapsed: 0.

A correct fundamental sign with a closed gap is NO_TRADE. A gap that has already mean-reverted most of the way is WAIT_PULLBACK.

## Exit Performance

Mean holding period: 4.3500 days. Exits are mispricing closed, thesis invalidated, alpha decayed, time expired, stop, dynamic target, regime change, or a liquidity event. The target is the live fair value, not the fair value stored at entry.

## PnL After Costs

Fills buy the ask plus slippage and sell the bid minus slippage. Spread source on the run: OBSERVED_BOOK. The reported pnl is after spread, slippage, fees, and the latency charge. PnL 0.143405.

## Subperiod Performance

The alive window is the pre-2021 lag. The decay window is 2021 onward. Positive pnl is concentrated in the alive window; the decay window is not allowed to add trades once the lag gate trips.

## Regime Performance

After-cost pnl by the regime at entry: {'MEAN_REVERSION': 0.084379, 'DISLOCATION': 0.059026}. Fades are permitted in dislocation, mean reversion, neutral, and the divergence regimes. They are refused in trend, carry, and liquidity stress.

## PnL Attribution

Sum of grouped log-contribution changes, in return space: {'rate': -0.023885, 'policy': -0.012892, 'growth': -0.003421, 'inflation': 0.004236, 'risk': -0.007882, 'positioning': 0.0, 'momentum': 0.0}. Unexplained is the residual of that identity versus the mid-to-mid move. Execution cost is reported separately and is not called alpha.

## Execution Impact

Same-bar decisions with a zero second delay still pay the quoted book. A one-day execution delay is a robustness case, not the base fill. There is no mid-price fill.

## Robustness

Bootstrap mean of trade pnl: {'p05': 0.0018409974894766778, 'p50': 0.0023594769190897705, 'p95': 0.0029931298145320947}. Monte Carlo reorder of trades, total pnl unchanged, drawdown distribution: {'total': 0.1434052360349822, 'drawdown_p50': 0.00101734968254924, 'drawdown_p95': 0.0014387911522197767}.

Doubling realized execution cost, which already contains the spread, leaves pnl 0.1312. A one-day execution delay produces 60 trades and pnl 0.1280. A one-day signal delay produces 60 trades and pnl 0.1280. Raising the minimum gap by half produces 60 trades. Rolling OLS, on the same window and the same gates, produces 60 trades and pnl 0.1429. Winsorized mean trade pnl: 0.0024.

Close-to-close baselines on the same calendar, one spread of cost on turnover: {'always_long': 0.0557488997769907, 'always_short': -0.056014115237301, 'momentum': 0.02711658743821125, 'rate_spread': 0.3588376546516122, 'mean_reversion': -0.14778962187939595, 'random': -0.45766489753652007, 'fundamental_sign': 0.051483396058457115}. They are a refusal test for a more complicated model. They are not the same position size or the same fill as the event-driven book, and they are not a live-market result. On this tape the close-to-close rate-change book sums to a larger log return than the vol-targeted residual book. That book is not promoted: it trades through the window where the reaction lag has already collapsed, and it does not pay the bid or the ask.

## Failure Cases

The permanent price jumps after the lag dies are the failure case the gate is there to refuse. Individual macro-event lags on daily bars are noisy; the book uses the median, not one release. A fundamental score that strongly disagrees with the fade is model uncertainty, not a trade.

## Known Limitations

No vendor point-in-time consensus, no real OIS history, no CFTC tape, and no executable FX book are in this repository. Kalman filtering and additional pairs are not implemented: the complexity gate requires a stable EURGBP out-of-sample result on real vintages first. The laboratory sharpe is evidence about the engine, not about EURGBP.

## Conclusion

Conclusion: INCONCLUSIVE

Question 1. Relative UK and euro-area fundamentals explain the laboratory price by construction. They have not been shown to explain live EURGBP on revision-safe data.

Question 2. The factor model and the residual fade have laboratory forward correlation. Out-of-sample power in the market is unmeasured.

Question 3. The engine repeatedly flags the planted five-day lag. That is not yet evidence of a repeatable market mispricing.

Question 4. Laboratory pnl is after a quoted spread, slippage, and fees. Live cost and impact are unmeasured.

Question 5. The laboratory lag is several days and then collapses to one session, and the book stops. Whether any live reaction lag remains is unknown.

Question 6. In the laboratory the opportunity is visible on the close that first contains the gap. That is not a claim about trading ahead of a real release.

Question 7. The chase rule refuses a gap that has already closed. Whether that improves live capture is unmeasured.

Question 8. Exits follow the live fair value and the thesis. A comparison with a fixed take-profit on live trades has not been run.

Question 9. The laboratory pnl is produced by a lag that was written into the generator. It is not evidence against data mining in a live sample, because there is no live sample.

Run hash `f54dbc60571d9353f66424a1f6db2a4824747c9cebdec75959fdda24d7819fa6`. Data snapshot hash `377b2d55134a32f6394f148ad8d4cefb777aec06c781ed6b5387c2d8f023880e`.
