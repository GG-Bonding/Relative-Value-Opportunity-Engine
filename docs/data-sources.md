# Data sources

Two ingest paths write the same append-only store. They are not interchangeable.

## Laboratory

`rv ingest --laboratory` builds the seeded tape used by the V1 research report. Its `source` is `laboratory`. It is the only history long enough to fit the rolling ridge. It is not a market.

## Jin10

`rv ingest --jin10` reads the Jin10 MCP server. The token is `JIN10_TOKEN`. The URL defaults to `https://mcp.jin10.com/mcp` and can be overridden with `JIN10_MCP_URL`. A local `.env` fills those names only when the environment does not already have them. The repository does not contain a token.

The client follows the same session as the gold-signal Jin10 client: initialize, then `tools/call`. ShortAlpha does not call Jin10. What is copied from ShortAlpha is the failure rule: a missing token stops the command. The laboratory is not used as a substitute.

What the current Jin10 tools actually return:

| Need | Jin10 | What is stored |
| --- | --- | --- |
| EURGBP quote | No code named EURGBP | Mid = EURUSD close / GBPUSD close, `source=jin10`, `version=cross-eurusd-gbpusd-v1` |
| Bid / ask | Not on the quote | Left missing. The backtest then uses `SIMULATED_SPREAD` and says so |
| Minute bars | `get_kline` on EURUSD and GBPUSD | A 1-minute cross where both stamps match and are already in the past |
| Daily bar | No official London close | One `1d` row at 16:00 UTC only after the cross is at or after that time |
| UK and euro-area releases | `list_calendar`, current week only | GDP, PMI, retail sales, employment, industrial production, CPI, core CPI, wages. Other titles are counted and dropped |
| Actual before publication | `actual` is null | No macro row. A consensus snapshot is stored at the time we observed it |
| Revisions | `revised` is the previous print as restated on this release | `previous_as_known` uses `revised` when it is present. A new row is not written for the prior period, because the feed does not give that period's own timestamp |
| Rates, OIS, policy path, CFTC | Not on these tools | Stay missing |

Naive calendar times are Shanghai local time, then stored as UTC. A second ingest of the same vintage is skipped. It is not an update.

This feed cannot replace the laboratory backtest. One week of releases and a short minute window do not identify whether EURGBP has out-of-sample alpha.

Jin10 is the information layer for forward evaluation: calendar, flash, and news. It does not supply the fill. `rv round` reads that MCP feed and does not call Jin10 for a quote or a yield.

UK2Y is the Bank of England GLC Nominal spot curve at 2.0 years, sheet `4. spot curve` in the latest yield-curve zip. DE2Y is the Bundesbank Svensson yield with residual maturity 2.0 years, series `BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604.R02XX.R.A.A._Z._Z.A`. The spread is calculated only on a shared trading day. EURGBP bid and ask come from the local MetaTrader 5 `SymbolInfoTick` on Windows. The tick file written by `adapters/mt5/EURGBP_TickExport.mq5` remains the file input for `rv forward`. See `docs/forward-evaluation.md`.
