# Development plan

Phases follow the spec. Status is against this repository, not against a live EURGBP book.

| Phase | Work | Status |
| --- | --- | --- |
| 1 | Repository, domain objects, tests | Done |
| 2 | Point-in-time store, revisions, consensus surprise, market data | Done, laboratory tape only |
| 3 | Deterministic replay | Done |
| 4 | UK2Y − DE2Y | Done |
| 5 | BoE versus ECB expected path | Done |
| 6 | Growth and inflation surprises | Done |
| 7 | Risk premium, positioning columns, momentum | Positioning columns exist and stay missing when no rows are stored |
| 8 | Mechanism registry | Done |
| 9 | Feature snapshot | Done |
| 10 | Rolling ridge fair value and attribution | Done |
| 11 | Forward-return IC, purged | Done |
| 12 | Event response and reaction lag | Done |
| 13 | Rule regime engine | Done. HMM was not used |
| 14 | Mispricing classes, including model error | Done |
| 15 | Alpha lifecycle, health, decay | Done |
| 16 | Opportunity object and score | Done |
| 17 | Entry engine, including the chase rule | Done |
| 18 | Exit and thesis invalidation | Done |
| 19 | Event-driven backtest | Done |
| 20 | Bid/ask costs, fees, latency charge, delays | Done |
| 21 | Walk-forward, purge, cost and delay stress, bootstrap, reorder | Done in the V1 study |
| 22 | Post-trade attribution and review labels | Done |
| 23 | Champion / challenger gate | Gate is implemented. No challenger is promoted |
| 24 | Paper trading | The event-driven book, labeled `PAPER`. No broker |
| 24.5 | Live forward research | Shadow, paper, and an unarmed MT5-shaped book. Jin10 is information. No live order |
| 25 | Kalman, dynamic beta, online updating | Not implemented |
| 26 | AUDNZD, gold/silver, Brent/WTI, NVDA/AMD | Not implemented |

## Complexity gate for phase 25

Kalman would answer "which beta is moving inside the window" more smoothly than a rolling ridge. The ridge already stores the coefficient path, and the laboratory does not show a residual the ridge cannot represent. There is no live out-of-sample gain to trade against the extra state. It stays out.

## Complexity gate for phase 26

Another pair needs the same revision-safe data and a separate mechanism registry. EURGBP does not yet have a vendor-data result. The code rejects any other symbol.

## Phase 24.5

The laboratory backtest stays the machinery test. It is not a claim about live EURGBP. From the first forward run, each call is appended and left as it was. Later ticks append a new journal row for convergence, a closed gap, or a thesis break. `rv round` reads Jin10 calendars, flashes, and headlines as information, the official UK and German 2-year daily curves, and the local MT5 EURGBP book. A missing rate is not stored as zero. Shadow, paper, and live share the signal. `rv round --mode live` records it and does not send an order.

## Still outside V1

A real OIS history, consensus vintages, and CFTC. The forward path can record an MT5 book and a rate file. It does not by itself change the research report. Until that forward sample has been scored, the market conclusion stays `INCONCLUSIVE`.
