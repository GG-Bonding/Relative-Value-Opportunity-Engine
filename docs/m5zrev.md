# EURGBP M5 偏离回归

这套书和 ridge 公允价值引擎、`m15meanrev` 分开。它只复用工程里的 Python 环境，不改那两套策略的信号。

基准只用已收盘 M5 Bid 的价格偏离 Z。收益 Z 和 H1 过滤默认关闭。消融会把它们打开，但只有同一条行情上真有改进才保留。

信号口径是 `ln(Bid Close)` 的 EMA。`alpha = 2 / (period + 1)`。Z 用当前偏离之前的窗口，样本标准差，当前根不进窗口。Python 和 `adapters/mt5/EURGBP_MeanReversion_ZScore.mq5` 用同一条黄金序列对照，允许误差 `1e-6`。这不是 MetaEditor 的运行结果。

## 环境

仓库用 Python 3.12，由 `uv` 管理。

```bash
uv sync
uv run ruff check src/m5zrev tests/test_m5zrev_histdata.py tests/test_m5zrev_study.py tests/test_m5zrev_parity.py tests/test_m5zrev_research.py
uv run mypy src/m5zrev
uv run pytest tests/test_m5zrev_indicators.py tests/test_m5zrev_data.py tests/test_m5zrev_session.py tests/test_m5zrev_state.py tests/test_m5zrev_backtest.py tests/test_m5zrev_research.py tests/test_m5zrev_histdata.py tests/test_m5zrev_study.py tests/test_m5zrev_parity.py
```

`python -m m5zrev` 和上面的检查一样，都在项目环境里跑。

## 数据

优先从已经连接的 MT5 读 EURGBP。服务器时差必须由调用者给出，程序不会按 Anzo Capital 的名字去猜。

```bash
uv run python -m m5zrev fetch \
  --start 2024-01-01T00:00:00+00:00 \
  --end 2024-02-01T00:00:00+00:00 \
  --server-utc-offset-hours 2 \
  --out-dir data/m5z
```

终端或 MetaTrader5 包不可用时，这条命令以状态码 2 退出，不写替代价格。

这台机器上的研究行情来自已经下载的 HistData M1 Bid，不是终端 Tick。时间是全年美东标准时间（UTC-5，没有夏令时），写出的时间是完整 M5 或 H1 桶的 UTC 收盘。不完整的桶被丢掉。同一分钟出现两个价格时，这一分钟整段删除，不挑选、不平均。

```bash
uv run python -m m5zrev prepare-histdata --raw data/m15/raw --out data/m5z
```

`data/m5z/source.txt` 写着实际根数和区间。`data/m5z/spec.json` 里的 `verified_from_terminal` 是 false。`tick_value_account = 1.25` 来自「10 万合约、GBPUSD 按 1.25」这个假设，不是经纪商读数。

经济日历给 EA 用时，先过滤成 UTC unix 秒。过滤规则和 Python 回测相同。

```bash
uv run python -m m5zrev export-events --csv configs/uk-ez-releases.csv --out data/m5z/events.txt
```

把 `events.txt` 放到 MT5 的 Common Files，路径保持 `M5ZREV/events.txt`。没有这份文件时，可选过滤不会挡住新仓，但报告会写明日历未验证。实盘如果把过滤改成 `required` 而文件缺失或过期，EA 不开新仓。

## 研究

```bash
uv run python -m m5zrev study \
  --m5 data/m5z/eurgbp-m5.csv \
  --h1 data/m5z/eurgbp-h1.csv \
  --spec data/m5z/spec.json \
  --events configs/uk-ez-releases.csv \
  --output artifacts/m5zrev \
  --study-spread-pips 1.0
```

这条命令会写：

- `verdict.md`：PASS、INCONCLUSIVE 或 FAIL
- `baseline-report.md`：多头、空头、双向，以及点差和滑点假设
- `ablation-report.md`：A/B/C/D
- `walkforward-report.md`：12 个月训练、3 个月验证、3 个月测试，最后 3 个月封存
- `sensitivity.svg`：第一折训练窗的价格偏离网格；样本不够时写明未运行
- `trades.csv`：基准的全部成交；没有成交时只有表头

预先登记的基准是 EMA 24、偏离窗口 72、阈值 ±2.0、确认 0.25、ATR 14 乘 1.5、最低盈亏比 1.2、最长 60 分钟、伦敦 08:00–17:00、单笔风险 0.5%。收益 Z 和 H1 过滤关闭。点差 1.0 pip 是研究假设，用来估计 Ask，不是 Anzo 的历史点差。

没有行情时：

```bash
uv run python -m m5zrev status --output artifacts/m5zrev-probe
```

它只探测本机终端，并写出「未运行」的报告。不要用它覆盖已经跑完的 `artifacts/m5zrev`。

## EA

源码是 `adapters/mt5/EURGBP_MeanReversion_ZScore.mq5`。把它放进 MetaEditor 编译，再挂到 EURGBP 图表上。图表周期可以不是 M5，EA 自己读取已收盘的 M5 和 H1。

默认三个开关都是 false：

- `InpEnableTrading`
- `InpAllowLiveTrading`
- `InpServerOffsetConfirmed`

策略测试器里把 `InpEnableTrading` 设为 true 才会发模拟单。模拟账户和真实账户还必须把 `InpAllowLiveTrading` 设为 true。`InpServerUtcOffsetHours` 填终端时间减 UTC 的小时数，并打开确认开关。填 0 不是 Anzo 的默认时差。

`InpMagic` 默认 20261024，订单注释是 `M5ZREV`。净额账户上如果已有别的魔术号持仓，EA 不动它。对冲账户只管理自己的魔术号。

止损和止盈随市价单发给服务器。`OrderCalcProfit` 用来算 1 手止损的账户货币亏损。最小手数超过 0.5% 权益时跳过。成交只认持仓出现，`TRADE_RETCODE_DONE` 本身不会被当成已开仓。重启后如果持仓还在，状态回到 OPEN；日志里写着 OPEN 但持仓没了，进入冷却，不补一笔新单。

EA 头部的 `BEGIN_M5Z_PARITY` 段必须和 Python 的 `m5zrev.parity.golden_block()` 一致。`OnInit` 会用同一套偏差函数重算第 32 根和第 79 根，误差超过 `1e-6` 就拒绝初始化。这个自检只有在 MetaEditor 或终端里加载后才会执行。

## 结论怎么读

只有终端 Tick、终端合约规格、样本外门槛和两倍额外成本都成立，结论才会是 PASS。HistData 的 Bid K 线即使赚到 R，也只能是 INCONCLUSIVE 或 FAIL。

美元盈亏依赖上面的 GBPUSD 假设。平均 R 不依赖这个汇率，但手数向下取整会受它影响。

## 这次研究的结论

结论是 **FAIL**。预先登记的双向基准在封存的最后三个月平均 R 为 -0.3804，Profit Factor 0.4249，48 笔。全样本 2129 笔，平均 R -0.2332，Profit Factor 0.6299。这不是可进入模拟盘的结果。

下面只引用 `artifacts/m5zrev` 里这次实际写出的报告。行情是 HistData M1 Bid 聚合成的已收盘 M5 和 H1，2020-01-01T22:05:00+00:00 到 2026-10-16T21:55:00+00:00，494840 根 M5。Ask 用 1 pip 的研究点差补上。10 个互相冲突的 M1 分钟被整段删除。合约规格没有从终端读取。

1. 这段 Bid 研究里，EUR/GBP 没有可交易的 30–60 分钟均值回归。封存区间平均 R 为负，全样本同样为负。中位持仓全样本 25 分钟、封存区间 17.5 分钟，多数单子不在 30–60 分钟里。
2. 价格偏离网格没有更稳的窗口。21 折里没有任何一组 EMA、窗口和阈值同时通过训练和验证。第一折训练热力图 36 格全部为负，最好是 -0.207。
3. 收益 Z 没有提供可保留的额外价值。封存区间 B 只有 28 笔，低于 30 笔门槛，记为 insufficient。全样本 B 的平均 R 仍是 -0.2139。
4. H1 趋势过滤没有改善封存区间。C 有 34 笔，平均 R -0.2560，规则判定 no_added_value。基准继续关闭这个过滤。
5. 全样本单独重放时，多头 900 笔平均 R -0.2167，空头 1294 笔平均 R -0.2439，两边都亏，空头更差。封存区间的单独重放是多头 14 笔、空头 35 笔，都为负。14 笔低于 30 笔门槛。这三组是三次独立回测，笔数不会加总成双向的 48 笔。
6. 基准只在伦敦 08:00–17:00 开仓。全样本里只有 13:00 这一小时平均 R 为正（169 笔，+0.0173）。这不是预先登记的时段检验，也不能当成样本外结论。
7. 封存区间把持仓上限改成 30、45、60 分钟，平均 R 分别是 -0.3650、-0.4011、-0.3804。比较结果是 NO_EDGE。基准保持预先登记的 60 分钟。
8. Anzo Capital 的点差、佣金、合约和服务器时差没有读到。在 1 pip 假设和 0 佣金下，账面已经是负的。1.5 pip 封存区间平均 R -0.5415。2 pip 超过 1.5 pip 的点差上限，成交为 0。0.4 pip 研究滑点的封存区间平均 R -0.5981，只有 10 笔。配置里的滑点和佣金都是 0，乘以 2 不会改变成交。
9. 没有一组参数在样本外稳定。21 折全部是 NO_EDGE。预先登记参数在折 4 的测试窗平均 R -0.0154、Profit Factor 1.0063，折 15 平均 R +0.0215、Profit Factor 0.9933。两折都没有同时过门槛，也不拿来改基准。
10. 不具备进入模拟盘的条件。PASS 需要 Tick、终端成本和样本外正期望。这里三项都不成立，而且封存期望本身为负，所以是 FAIL，不是 INCONCLUSIVE。

已执行：`uv run ruff`、`uv run mypy src/m5zrev`、`uv run pytest` 覆盖 `tests/test_m5zrev_*.py`；HistData 聚合；上面的 study 命令。黄金序列对照是源码测试，误差不超过 `1e-6`。

只做了代码检查：EA 源码、`OnInit` 自检的写法、下单路径。MetaEditor 不在 PATH，也不在 `/Applications`，所以没有编译，也没有跑策略测试器。

尚未验证：MT5 终端、Anzo 的真实点差和佣金、历史 Tick、EA 在终端里的初始化自检。`python -m MetaTrader5` 在这台机器上失败。
