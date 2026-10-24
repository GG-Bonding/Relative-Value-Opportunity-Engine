# EURGBP M5 偏离回归

结论：**FAIL**

样本外平均 R 不是正数。

## 这次实际跑了什么

MetaTrader5 package is not available: No module named 'MetaTrader5'

PATH 和 /Applications 里没有找到 MetaEditor。EA 源码没有编译。

点差假设 1 pip。合约 `research-assumption-not-terminal`，终端核对 False。

经济日历 `stale`。收益 Z 和 H1 过滤在基准里是关闭的。

全样本：成交 2129，净额 -872.4875，胜率 0.3288，平均 R -0.2332，Profit Factor 0.6299，回撤 0.8735，Sharpe -3.1445，中位持仓 25.0000 分钟，30–60 分钟 955 笔，路径不确定 51 笔。

封存区间：成交 48，净额 -9.4750，胜率 0.2708，平均 R -0.3804，Profit Factor 0.4249，回撤 0.0097，Sharpe -4.5787，中位持仓 17.5000 分钟，30–60 分钟 16 笔，路径不确定 1 笔。

预先登记门槛：至少 30 笔，平均 R 为正，Profit Factor 大于 1，回撤不超过 10%。

Anzo Capital 的真实点差、佣金、Tick 和服务器时差没有从终端读到。上面的美元盈亏依赖研究规格里的 GBPUSD 1.25 假设。平均 R 不依赖这个汇率，但手数取整会受它影响。

走步明细在 `walkforward-report.md`。消融在 `ablation-report.md`。

有足够测试成交、因而计入门槛的走步折：0。明细在走步报告里。
