# M5 deviation book

This book is separate from the ridge fair-value engine and from `m15meanrev`. It does not change either one.

Signals use the closed M5 bid. A tick mid-price study is a different experiment and is not mixed into these signals.

## Phase 1 — data and indicators

Status: committed. Unit tests cover the closed-bar math.

- Log EMA seeds on the first price, with `alpha = 2 / (period + 1)`. Deviation starts only after one full period, and the z-score uses the previous window with sample standard deviation. A zero deviation is not a signal.
- Return z uses the previous window only. The current return is excluded.
- H1 EMA and Wilder ADX are computed on closed hours. An hour that has not closed is not readable.
- London 08:00–17:00 follows `Europe/London`. The MT5 server offset is an explicit input. The EA uses the last-Sunday BST rule, checked against the zone database.
- Missing bars are reported and left empty. A missing terminal does not create a price.

## Phase 2 — state machine and backtest

Status: committed. The engine is tested on constructed bars.

- One deviation arms once. Confirmation has three closed M5 bars. The next bar gets one entry attempt.
- A broker reject or a success code without a position does not become an open trade.
- Restart trusts a live position. A journalled open position that is gone becomes a cooldown, not a new order.
- Tick fills buy the ask and sell the bid. No tick means no fill.
- An OHLC bar that can reach both the stop and the target exits at the worse price and is marked path-uncertain.
- A stop can fire before 60 minutes. A weekend with no quote exits on the next tradable tick and is labelled late.
- Position size uses tick value. A minimum lot that exceeds the 0.5% budget is skipped. After a loss the next size does not increase.

## Phase 3 — ablation

Status: the comparison runs on the HistData study. Return z and the H1 filter stay off in the pre-registered book. The holdout rule calls an add-on an improvement only when expectancy rises, stays positive, profit factor does not fall, and drawdown stays within 1.2 times the simple book.

## Phase 4 — walk-forward and cost

Status: 12-month train, 3-month validation, 3-month test, step 3 months. The last 3 months are sealed and are not passed to selection. Stage 1 is the price-deviation grid only. A fold that fails that gate is `NO_EDGE` and does not continue to the return-z or exit grids. Doubling a zero configured commission does not count as a completed broker-cost stress. The report also reruns a research slippage of 0.2 and 0.4 pip and constant spreads of 1.0, 1.5, and 2.0 pip. Those spreads were not read from Anzo Capital.

## Phase 5 — MQL5 EA

Status: `adapters/mt5/EURGBP_MeanReversion_ZScore.mq5` is source. The header carries the Python golden vector. A unit test checks that vector to `1e-6`. `OnInit` repeats the same deviation function and refuses to start if bar 32 or bar 79 disagrees. MetaEditor was not on PATH or under `/Applications`, so this machine did not compile the EA. The Strategy Tester has not been run.

Live and demo orders stay off unless `InpEnableTrading` and `InpAllowLiveTrading` are both true. The tester still needs `InpEnableTrading`. The server offset is not assumed.

## Phase 6 — market tape

Status: no MetaTrader 5 package and no Anzo terminal on this machine. The study uses HistData M1 bid aggregated to complete M5 and H1 closes, from `data/m15/raw`. Ten conflicting minutes were dropped rather than assigned a price. There is no ask and no tick. The dollar spec assumes GBPUSD 1.25 and is marked `verified_from_terminal = false`.

The written conclusion is `artifacts/m5zrev/verdict.md`: **FAIL**. The sealed both-side book has 48 trades and expectancy -0.3804 R. The full sample has 2129 trades and expectancy -0.2332 R. All 21 walk-forward folds are `NO_EDGE`. Return z is `insufficient` on the holdout. The H1 filter is `no_added_value`. Hold limits of 30, 45, and 60 minutes are all negative, so the comparison is `NO_EDGE`.

This is a HistData bid study with a 1 pip ask assumption. It is not a tick backtest and it is not an Anzo cost result. The ten answers are in `docs/m5zrev.md`.
