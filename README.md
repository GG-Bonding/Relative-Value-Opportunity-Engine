# Relative Value Opportunity Engine

EURGBP only. The question the system answers is whether the market still contains a relative-value gap that is worth trading, not whether the next print is up or down.

A correct view of UK rates, policy, growth, inflation, or risk versus the euro area is not a trade. The book stays flat when the gap is already priced, the alpha is decaying, the regime does not support a fade, the residual looks like model error, or the expected edge does not clear the spread.

## What is in this repository

The engine stores point-in-time observations, including macro revisions. It builds relative factors and a separate expectation state, fits a rolling ridge fair value, classifies the residual, and walks an event-driven book that buys the ask and sells the bid. Replay of the same store, config, and seed produces the same trades.

`rv ingest --laboratory` builds a seeded tape whose price follows the registered factors with a five-day lag until 2021 and no lag after that. That tape is how the machinery is tested. It is not a backtest of live EURGBP. The V1 research report's market conclusion is **INCONCLUSIVE**.

`rv ingest --jin10` appends the current Jin10 week: calendar, flash, and news as information, plus the EURUSD/GBPUSD cross. Jin10 has no EURGBP code and no bid or ask. Set `JIN10_TOKEN`. See `docs/data-sources.md`.

`rv forward` is the live path. It reads an MT5 tick file for EURGBP bid and ask, a separate UK2Y and DE2Y file, and an optional event file. Shadow records the call. Paper sells the bid and buys the ask. Live uses the same signal and does not send an order. The opening journal row is not edited. See `docs/forward-evaluation.md`.

Kalman filtering and any pair other than EURGBP are not implemented. Both wait on a stable out-of-sample result on real vintages.

## Run

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run mypy
uv run rv ingest --laboratory --db data/normalized/eurgbp.duckdb
uv run rv ingest --jin10 --db data/normalized/eurgbp-jin10.duckdb
uv run rv forward --mode paper --tick data/mt5/eurgbp_tick.json --rates data/rates/latest.json
uv run rv mechanisms EURGBP
uv run rv research-report --db data/normalized/eurgbp.duckdb
```

The HTTP surface is `create_app` in `api.app`. It serves `/healthz`, `/readyz`, the EURGBP state routes, opportunities, and backtests.

## Layout

`src/domain` holds the records. `src/data` is the append-only store. Factors and snapshots live in `src/features`. Models, regimes, mispricing, alpha, opportunities, execution, and the backtest are separate packages so a fair-value residual cannot skip the decision gates. `docs/` records the mechanisms, the point-in-time rules, and the development plan.
