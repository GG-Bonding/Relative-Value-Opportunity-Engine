# M5 deviation book

This book is separate from the ridge fair-value engine and from `m15meanrev`. It does not change either one.

Signals use the closed M5 bid. A tick mid-price study is a different experiment and is not mixed into these signals.

## Phase 1 — data and indicators

Status: code and unit tests are in place. No terminal tape has been read yet.

- Log EMA seeds on the first price, with `alpha = 2 / (period + 1)`. Deviation starts only after one full period, and the z-score uses the previous window with sample standard deviation. A zero deviation is not a signal.
- Return z uses the previous window only. The current return is excluded.
- H1 EMA and Wilder ADX are computed on closed hours. An hour that has not closed is not readable.
- London 08:00–17:00 follows `Europe/London`. The MT5 server offset is an explicit input.
- Missing bars are reported and left empty. A missing terminal does not create a price.

## Later phases

State machine, backtest, ablation, walk-forward, the MQL5 EA, and any market conclusion come after this phase. Nothing here is a backtest result.
