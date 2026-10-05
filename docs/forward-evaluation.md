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

`rv forward --mode shadow|paper|live` reads three files.

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
