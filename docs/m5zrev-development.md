# M5 deviation book

This book is separate from the ridge fair-value engine and from `m15meanrev`. It does not change either one.

Signals use the closed M5 bid. A tick mid-price study is a different experiment and is not mixed into these signals.

## Phase 1 — data and indicators

Status: committed. Unit tests cover the closed-bar math. No terminal tape has been read.

- Log EMA seeds on the first price, with `alpha = 2 / (period + 1)`. Deviation starts only after one full period, and the z-score uses the previous window with sample standard deviation. A zero deviation is not a signal.
- Return z uses the previous window only. The current return is excluded.
- H1 EMA and Wilder ADX are computed on closed hours. An hour that has not closed is not readable.
- London 08:00–17:00 follows `Europe/London`. The MT5 server offset is an explicit input.
- Missing bars are reported and left empty. A missing terminal does not create a price.

## Phase 2 — state machine and backtest

Status: committed. The engine is tested on constructed bars, not on a market tape.

- One deviation arms once. Confirmation has three closed M5 bars. The next bar gets one entry attempt.
- A broker reject or a success code without a position does not become an open trade.
- Restart trusts a live position. A journalled open position that is gone becomes a cooldown, not a new order.
- Tick fills buy the ask and sell the bid. No tick means no fill.
- An OHLC bar that can reach both the stop and the target exits at the worse price and is marked path-uncertain.
- A stop can fire before 60 minutes. A weekend with no quote exits on the next tradable tick and is labelled late.
- Position size uses tick value. A minimum lot that exceeds the 0.5% budget is skipped. After a loss the next size does not increase.

## Later phases

Ablation, walk-forward, the MQL5 EA, and any market conclusion come after this. Nothing here is a backtest result.
