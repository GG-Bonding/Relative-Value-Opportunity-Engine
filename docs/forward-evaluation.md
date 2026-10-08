# Forward evaluation

The laboratory tape still tests the machinery. It does not decide whether live EURGBP has alpha. A forward run freezes what was known, then scores that frozen call on later prices.

```text
Jin10 calendar / flash / news  →  information
rate file UK2Y, DE2Y           →  RateDiff
MT5 bid / ask                  →  the book that would be traded
        ↓
same signal
        ↓
shadow records it
paper fills the bid or the ask
live records it and sends no order
```

`rv round` is one automatic pass. The default mode is shadow. It appends the inputs and the judgment to `data/forward/rounds.jsonl` and does not rewrite earlier lines. A later pass that is still in the same opportunity appends sequence 1, 2, and so on. Sequence 0 stays the frozen opening row.

`rv watch` stays up after that first pass. It judges again only when a new Jin10 item arrives, the official curves print a new shared day, or an open position sees a new tick. The line is SIGNAL for a new long or short, REVERSAL when that open book is invalidated or exited, and UPDATE otherwise. It does not send an order. The default gap between polls is 60 seconds. Official curves are refreshed about every 30 minutes.

`rv watch --trade` without `--mode live` exits with status 2 and sends nothing. `rv watch --mode live --trade` may send an order only when alpha is `ACTIVE`, the model is `READY`, the quote and both curves and the fair value are healthy, the decision is long or short, and the net edge clears the cost threshold. A discovery or validating sample never reaches the broker. A failed close does not open the other side. An exit may flatten this program's book. The default size is 0.01 lot. Positions opened by another program are left alone.

`rv desk` serves that same log at `http://127.0.0.1:8780/`. The page shows the latest judgment, bid and ask, the fair value, the gap, and the last order. It refreshes every five seconds.

```text
Jin10 MCP calendar / flash / news     what just happened
BoE GLC Nominal spot, tenor 2.0       UK2Y, percent
Bundesbank Svensson residual 2.0      DE2Y, percent
MT5 SymbolInfoTick EURGBP             bid, ask, mid, spread
        ↓
same decide() and research sample
        ↓
shadow records it
paper fills the bid or the ask
live records it and sends no order
```

UK2Y minus DE2Y is in percentage points, and only when both prints fall on the same trading day. A missing side stays missing. It is not written as zero. Jin10 does not supply the bid, the ask, UK2Y, or DE2Y. A flash headline does not become an order. A calendar factor is the indicator's own surprise z-score, `(actual - forecast) / historical surprise std`, and it stays missing until that indicator has eight earlier raw errors. Dividing a raw surprise by 3 is not a factor. A policy headline, including a Fed hike or cut, is regime context only and does not add a signed EURGBP pressure. Without a fair value the decision is `WATCH` and `model_readiness` is `MODEL_NOT_READY`. `rv watch` fills that price from a ridge of log EURGBP on UK2Y minus DE2Y and the 20-session change in that spread, once 252 aligned sessions exist. The fit uses the Bank of England nominal spot archive, the Bundesbank 2-year residual series, and MT5 daily closes. It leaves out policy, growth, inflation, risk, and expectations, and it does not use the price's own momentum. The latest session is scored from earlier sessions. A new tick is then judged against that price. A missing rate or a missing bid and ask still stops at `DATA_DEGRADED` and does not emit `LONG`, `SHORT`, or `NO_TRADE`.

`rv forward --mode shadow|paper|live` still reads three files.

The tick file is one JSON object, or the last line of a JSON lines file. `adapters/mt5/EURGBP_TickExport.mq5` overwrites that file from an EURGBP chart. `time` is `TimeGMT()` unix seconds.

```json
{"symbol": "EURGBP", "time": "2026-10-01T10:00:00+00:00", "bid": 0.8557, "ask": 0.8563}
```

The rate file is percent, not basis points. A missing curve is omitted. It is not written as zero.

```json
{
  "UK2Y": {"rate": 4.20, "time": "2026-10-01T10:00:00+00:00"},
  "DE2Y": {"rate": 2.55, "time": "2026-10-01T10:00:00+00:00"}
}
```

An event file carries a surprise in standard deviations when one is known. A headline alone has no surprise and no pressure. Pressure is a relative factor: a positive UK inflation surprise supports GBP and is bearish EURGBP. It is not a buy or a sell.

```json
[
  {
    "id": "uk-cpi",
    "kind": "CALENDAR",
    "region": "UK",
    "category": "CPI",
    "surprise": 0.7,
    "headline": "UK CPI",
    "time": "2026-10-01T10:00:00+00:00"
  }
]
```

`--fair-value` is optional. Without it the decision is `WATCH` and `model_readiness` is `MODEL_NOT_READY`. The ridge is not fit on a handful of live rows. Passing a fair value lets paper sample the residual while alpha is still `DISCOVERY`. The journal reason says `research sample; alpha is not ACTIVE`.

The first journal row for an opportunity is sequence 0. A later tick appends sequence 1, 2, and so on. Sequence 0 keeps the entry bid, ask, fair value, and rate differential. A move toward that frozen fair value is `CONVERGING`. A remaining gap inside 8 pips is `EXITED`. A UK2Y−DE2Y move of 0.25 against the book is `INVALIDATED`.

Paper sells at the bid minus slippage and buys back at the ask plus slippage. Shadow and live leave `fill_price` empty. `rv forward --mode live` prints the signal and exits with status 2.
