# Architecture

The product is an opportunity, not a signal. A feature row is built once, as of a timestamp, and every later model reads that row. Nothing in the decision path queries the store for a newer vintage.

## Flow

1. Append-only observations land in DuckDB with `effective_at`, `released_at`, `observed_at`, `ingested_at`, source, version, and revision number.
2. `build_panel` joins rates, policy paths, macro surprises, risk spreads, and the EURGBP book with `join_asof` backward. Missing positioning stays null.
3. `FeatureSnapshot` is the only object a model may consume for one decision time. It records missing fields, stale fields, and source versions.
4. Expectations (forwards, the policy path, survey levels) are stored beside realized surprises. They are not the same series.
5. Rolling ridge fair value predicts `log(EURGBP)` from relative factors and expected relative fundamentals. Own-price momentum is not an input, so a jump cannot explain itself.
6. The residual is classified before it can be called a mispricing. Model error, a regime break, an event dislocation, and a liquidity distortion are not trades.
7. Alpha lifecycle uses realized forward returns and the median reaction lag. Only `ACTIVE` can pass the entry gate.
8. The entry engine chooses when. A gap that has already closed is a pullback wait, not a chase.
9. The exit engine watches the live fair value and the thesis. A stop is one reason among several.
10. The backtest walks bars in `observed_at` order, fills on the bid or ask, and writes a hash of the trade list.

## What is deliberately absent

No message bus, no cluster, no deep model, no hidden Markov regime, no Kalman filter, and no second currency pair. Those are complexity-gate items. The gate is written in `docs/development-plan.md`.

## Interfaces

`rv` and the FastAPI app call `EngineService`. A snapshot without a completed walk-forward leaves alpha in `DISCOVERY`, so the live opportunity endpoint watches instead of trading.
