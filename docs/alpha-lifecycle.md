# Alpha lifecycle

States: `DISCOVERY`, `VALIDATING`, `ACTIVE`, `DECAYING`, `DORMANT`, `RETIRED`, `REJECTED`.

The signal used for health is the negative of the price residual. Its information coefficient is against the next five-day log return. An outcome is usable only when the five-day window has already ended. A window with no variance has an IC of zero. Fewer than twenty finite points has no IC.

## Promotion

- Too few realized outcomes: `DISCOVERY`.
- A positive but short sample: `VALIDATING`. `VALIDATING` is a watch, not a trade.
- `ACTIVE` requires a 252-day IC at or above the configured floor and a short-window IC that has not collapsed relative to it.
- `DECAYING` when the 252-day IC is still meaningful and the 63-day or 21-day IC has fallen through the configured ratios.

## Lag, separate from IC

A second rule does not wait for the IC to die. If the 63-day median reaction lag is one session or less, the 756-day median is at least three times longer, and the 252-day sample is full, the state is `DECAYING`. The economic mechanism can still be true. The tradable delay is gone.

Reaction lag is the first event horizon (5m, 15m, 30m, 1h, 4h, 1d, 3d, 5d) that captures half of the five-day move in the same direction. A horizon with no print stays missing. A later daily bar is not treated as a five-minute print.

## Health fields

Rolling IC, rank IC, out-of-sample R², hit rate, Sharpe, after-cost pnl, turnover, lead time, reaction lag, signal half-life, and the stability of coefficients, factors, residuals, and correlations. Fields without a measurement stay null.

## Drift

The backtest records the lifecycle on each bar. A live comparison of expected versus realized IC, pnl, volatility, and reaction lag is the same series: when the short-window lag or IC leaves the long-window one, the book stops adding risk. That is the V1 drift monitor. It does not refit online.
