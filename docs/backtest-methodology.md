# Backtest methodology

The replay is event driven and deterministic. Bars are ordered by `observed_at`. Information at a bar is the earlier bar allowed by the data delay. The fill is the later bar allowed by processing, signal, and execution delay. A zero delay still fills the same bar, and it still pays the bid or the ask.

## Fills

A buy is the ask plus slippage. A sell is the bid minus slippage. If either side of the book is missing, the fill uses `SIMULATED_SPREAD` and the run says so. There is no mid-price fill. Fees and the latency charge are subtracted in return space.

PnL is a capital return: sign times quantity over entry mid, times the price change, minus fees. Drawdown is on wealth that starts at 1. Position size is volatility targeting, capped by max position, pair exposure, daily loss, and drawdown. Kelly is not implemented.

## Walk forward

Fair value at t is trained on a window that ends before t. Forward-return IC uses purged folds: the training label must finish before the test block, and an embargo sits between them. Normalization and the fit use only that past window. There is no random split.

## Replay identity

`BacktestRun` stores the git commit, config hash, data snapshot hash, model version, seed, start, and end. `deterministic_hash` covers entry time, exit time, direction, and pnl. Two runs on the same store match.

## Costs, delay, and robustness

The base book uses the quoted spread when the laboratory supplies a bid and ask. Robustness around that book, computed in the V1 study, includes a doubled execution cost, a one-day execution delay, a higher minimum gap, a winsorized trade-pnl mean, a bootstrap of the mean, and a Monte Carlo reorder of trades. Reordering does not change total pnl. It does change path drawdown.

Close-to-close baselines on the same calendar are always long, always short, momentum, rate-spread change, mean reversion, a seeded random book, and the sign of the rate level. They pay one simulated spread on turnover. They are a check against adding a more complicated model. They are not the same fill model as the event-driven book.

## Attribution

Each trade records the change in grouped factor contributions between entry and exit, the unexplained remainder versus the mid-to-mid move, the execution cost, the holding period, and a review label: thesis correct with good or bad execution, thesis wrong, alpha decay, or regime change.
