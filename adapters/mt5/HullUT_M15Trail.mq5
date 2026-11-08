#property strict
#property version "1.00"
#property description "M15：Hull 只判断方向，UT 穿越进场并跟踪止损。"

#include <Trade/Trade.mqh>

// Hull(55) 是方向许可，不是进场事件。
// UT（倍数 2、ATR 14）收盘穿越才在下一根开盘进场。
// 止损放在信号 K 线的 UT 轨迹上，之后只向有利方向移动。
// 收盘打回轨迹另一侧，下一根开盘平仓。
// 不设固定止盈，也不在很小的浮盈处保本。
// UTC 07:00 至 19:00 允许进场，20:00 起强制平仓。
// 单笔风险为权益的 0.35%，手数另有上限；小于最小手数或止损不足点差的 4 倍则跳过。

CTrade trade;

//==================================================
// 参数
//==================================================
input group "交易"
input ENUM_TIMEFRAMES SignalTimeframe = PERIOD_M15; // 信号周期
input double RiskPercent = 0.35;                    // 单笔风险（占权益%）
input double MaxLots = 2.0;                         // 手数上限
input ulong MagicNumber = 20261108;                 // 魔术号
input int DeviationPoints = 20;                     // 最大滑点（点）
input bool AllowLong = true;                        // 允许做多
input bool AllowShort = true;                       // 允许做空

input group "Hull 与 UT"
input int HullLength = 55;          // Hull 长度
input int AtrLength = 14;           // ATR 周期
input double UtMultiplier = 2.0;    // UT 倍数

input group "时段与点差（UTC）"
input int SessionStartHourUTC = 7;  // 开始小时（含）
input int EntryStopHourUTC = 19;    // 停止入场小时（不含）
input int FlatHourUTC = 20;         // 强制平仓小时（不含持仓）
input int SpreadMultiple = 4;       // 止损至少为点差的倍数
input int ServerUtcOffsetHours = 2; // 服务器相对 UTC 的小时

input group "回测日期（UTC）"
input datetime StartDateUTC = D'2024.01.01 00:00:00'; // 开始（含）
input datetime EndDateUTC = D'2030.01.01 00:00:00';   // 结束（不含）

input group "指标历史起点（服务器时间）"
input datetime IndicatorHistoryStartServer =
   D'2023.12.01 00:00:00'; // UT 递推起点

//==================================================
// 状态
//==================================================
struct SignalSet
{
   bool regimeLong;
   bool regimeShort;
   bool utBuy;
   bool utSell;
   double utStop;
   double atr;
   double closePrice;
};

datetime lastBarOpen = 0;
datetime skipEntryBarOpen = 0;
datetime plannedSkipBar = 0;
datetime lastFailLogBar = 0;
bool loggedHistoryShort = false;

bool closePending = false;
string pendingCloseReason = "";

bool signalsReady = false;
bool loggedWarmup = false;
int loadedBars = 0;
SignalSet lastSignal;

double tickSize = 0.0;
int priceDigits = 0;

//==================================================
// 时间与价格
//==================================================
double RoundPrice(const double price)
{
   return NormalizeDouble(
      MathRound(price / tickSize) * tickSize,
      priceDigits
   );
}

datetime ToUTC(const datetime serverTime)
{
   return (datetime)(
      (long)serverTime - (long)ServerUtcOffsetHours * 3600
   );
}

bool InDateRange(const datetime utcTime)
{
   return utcTime >= StartDateUTC && utcTime < EndDateUTC;
}

bool HourInside(
   const datetime utcTime,
   const int startHour,
   const int endHour
)
{
   MqlDateTime tm;
   TimeToStruct(utcTime, tm);
   return tm.hour >= startHour && tm.hour < endHour;
}

bool InHoldWindow(const datetime utcTime)
{
   return HourInside(
      utcTime, SessionStartHourUTC, FlatHourUTC
   );
}

bool InEntryWindow(const datetime utcTime)
{
   return HourInside(
      utcTime, SessionStartHourUTC, EntryStopHourUTC
   );
}

bool SameUtcDate(const datetime left, const datetime right)
{
   MqlDateTime a;
   MqlDateTime b;
   TimeToStruct(left, a);
   TimeToStruct(right, b);
   return a.year == b.year &&
          a.mon == b.mon &&
          a.day == b.day;
}

// 持仓跨越了强制平仓时点时，下一次报价就要平掉。
bool HeldAcrossFlat(
   const datetime openedServer,
   const datetime nowServer
)
{
   datetime openedUtc = ToUTC(openedServer);
   datetime nowUtc = ToUTC(nowServer);

   if(nowUtc <= openedUtc)
      return false;

   if(!InHoldWindow(openedUtc))
      return true;

   return !SameUtcDate(openedUtc, nowUtc);
}

bool MustFlatten(const datetime utcNow)
{
   if(!InDateRange(utcNow))
      return true;

   return !InHoldWindow(utcNow);
}

double SpreadPrice()
{
   MqlTick quote;

   if(SymbolInfoTick(_Symbol, quote) &&
      quote.ask > quote.bid)
   {
      return quote.ask - quote.bid;
   }

   long points = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   if(points > 0 && point > 0.0)
      return points * point;

   return 0.0;
}

bool TradeSucceeded()
{
   uint code = trade.ResultRetcode();

   return code == TRADE_RETCODE_DONE ||
          code == TRADE_RETCODE_DONE_PARTIAL ||
          code == TRADE_RETCODE_NO_CHANGES;
}

int LotDigits(const double step)
{
   int digits = 0;
   double value = step;

   while(digits < 8 &&
         MathAbs(value - MathRound(value)) > 1e-8)
   {
      value *= 10.0;
      digits++;
   }

   return digits;
}

void LogOncePerBar(const string message)
{
   datetime bar = iTime(_Symbol, SignalTimeframe, 0);

   if(bar == 0 || bar == lastFailLogBar)
      return;

   lastFailLogBar = bar;
   Print(message);
}

//==================================================
// 持仓
//==================================================
bool FindOurPosition(ulong &ticket)
{
   ticket = 0;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong candidate = PositionGetTicket(i);

      if(candidate == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;

      if((ulong)PositionGetInteger(POSITION_MAGIC)
         != MagicNumber)
      {
         continue;
      }

      ticket = candidate;
      return true;
   }

   return false;
}

bool HasAnySymbolPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      if(PositionGetTicket(i) == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol)
         return true;
   }

   return false;
}

datetime BarOpenContaining(const datetime when)
{
   int shift = iBarShift(
      _Symbol, SignalTimeframe, when, false
   );

   if(shift < 0)
      return 0;

   return iTime(_Symbol, SignalTimeframe, shift);
}

//==================================================
// 指标
//==================================================
double WMAAt(
   const double &values[],
   const int index,
   const int length
)
{
   if(length < 1 || index < length - 1)
      return EMPTY_VALUE;

   double sum = 0.0;
   double weights = 0.0;

   for(int j = 0; j < length; ++j)
   {
      double value = values[index - j];

      if(value == EMPTY_VALUE)
         return EMPTY_VALUE;

      double weight = length - j;
      sum += value * weight;
      weights += weight;
   }

   return sum / weights;
}

bool BuildSignals(SignalSet &sig)
{
   sig.regimeLong = false;
   sig.regimeShort = false;
   sig.utBuy = false;
   sig.utSell = false;
   sig.utStop = 0.0;
   sig.atr = 0.0;
   sig.closePrice = 0.0;

   datetime currentOpen = iTime(
      _Symbol, SignalTimeframe, 0
   );

   if(currentOpen == 0)
      return false;

   MqlRates rates[];
   ArraySetAsSeries(rates, false);

   int count = CopyRates(
      _Symbol,
      SignalTimeframe,
      IndicatorHistoryStartServer,
      currentOpen - 1,
      rates
   );

   int rootLength = (int)MathRound(
      MathSqrt((double)HullLength)
   );

   rootLength = MathMax(1, rootLength);

   int minimumBars = MathMax(
      HullLength + rootLength + 4,
      AtrLength + 4
   );

   if(count < minimumBars)
   {
      if(!loggedHistoryShort)
      {
         loggedHistoryShort = true;
         PrintFormat(
            "指标历史不足：收到 %d 根，至少需要 %d 根。起点 %s。",
            count,
            minimumBars,
            TimeToString(IndicatorHistoryStartServer)
         );
      }
      return false;
   }

   loadedBars = count;

   double closes[];
   double rawHull[];
   double hull[];
   double atr[];
   double ut[];

   if(ArrayResize(closes, count) != count ||
      ArrayResize(rawHull, count) != count ||
      ArrayResize(hull, count) != count ||
      ArrayResize(atr, count) != count ||
      ArrayResize(ut, count) != count)
   {
      LogOncePerBar("指标数组分配失败。");
      return false;
   }

   ArrayInitialize(rawHull, EMPTY_VALUE);
   ArrayInitialize(hull, EMPTY_VALUE);
   ArrayInitialize(atr, EMPTY_VALUE);
   ArrayInitialize(ut, EMPTY_VALUE);

   for(int i = 0; i < count; ++i)
      closes[i] = rates[i].close;

   int halfLength = MathMax(1, HullLength / 2);

   for(int i = 0; i < count; ++i)
   {
      double halfWma = WMAAt(closes, i, halfLength);
      double fullWma = WMAAt(closes, i, HullLength);

      if(halfWma != EMPTY_VALUE &&
         fullWma != EMPTY_VALUE)
      {
         rawHull[i] = 2.0 * halfWma - fullWma;
      }
   }

   for(int i = 0; i < count; ++i)
      hull[i] = WMAAt(rawHull, i, rootLength);

   double trSum = 0.0;

   for(int i = 0; i < count; ++i)
   {
      double tr = rates[i].high - rates[i].low;

      if(i > 0)
      {
         tr = MathMax(
            tr,
            MathMax(
               MathAbs(rates[i].high - closes[i - 1]),
               MathAbs(rates[i].low - closes[i - 1])
            )
         );
      }

      if(i < AtrLength)
         trSum += tr;

      if(i == AtrLength - 1)
      {
         atr[i] = trSum / AtrLength;
      }
      else if(i >= AtrLength)
      {
         atr[i] =
            (atr[i - 1] * (AtrLength - 1) + tr) /
            AtrLength;
      }

      if(atr[i] == EMPTY_VALUE)
         continue;

      double previousStop = 0.0;

      if(i > 0 && ut[i - 1] != EMPTY_VALUE)
         previousStop = ut[i - 1];

      double previousClose =
         i > 0 ? closes[i - 1] : closes[i];

      double distance = atr[i] * UtMultiplier;

      if(closes[i] > previousStop &&
         previousClose > previousStop)
      {
         ut[i] = MathMax(
            previousStop, closes[i] - distance
         );
      }
      else if(closes[i] < previousStop &&
              previousClose < previousStop)
      {
         ut[i] = MathMin(
            previousStop, closes[i] + distance
         );
      }
      else
      {
         ut[i] = closes[i] > previousStop
            ? closes[i] - distance
            : closes[i] + distance;
      }
   }

   int n = count - 1;

   if(hull[n] == EMPTY_VALUE ||
      hull[n - 2] == EMPTY_VALUE ||
      ut[n] == EMPTY_VALUE ||
      ut[n - 1] == EMPTY_VALUE ||
      atr[n] == EMPTY_VALUE ||
      atr[n] <= 0.0)
   {
      LogOncePerBar("Hull 或 UT 尚未完成预热。");
      return false;
   }

   if(!loggedWarmup)
   {
      loggedWarmup = true;
      PrintFormat(
         "Hull/UT 已用 %d 根已收盘 K 线完成预热。",
         count
      );
   }

   sig.regimeLong = hull[n] > hull[n - 2];
   sig.regimeShort = hull[n] < hull[n - 2];
   sig.utBuy =
      closes[n] > ut[n] &&
      closes[n - 1] <= ut[n - 1];
   sig.utSell =
      closes[n] < ut[n] &&
      closes[n - 1] >= ut[n - 1];
   sig.utStop = ut[n];
   sig.atr = atr[n];
   sig.closePrice = closes[n];

   return true;
}

//==================================================
// 止损与手数
//==================================================
bool BrokerStopAllowed(const bool buy, const double sl)
{
   MqlTick quote;

   if(!SymbolInfoTick(_Symbol, quote))
      return false;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   long stopsLevel = SymbolInfoInteger(
      _Symbol, SYMBOL_TRADE_STOPS_LEVEL
   );

   long freezeLevel = SymbolInfoInteger(
      _Symbol, SYMBOL_TRADE_FREEZE_LEVEL
   );

   double minimumDistance =
      MathMax((double)stopsLevel, (double)freezeLevel) *
      point;

   double tolerance = tickSize * 0.01;

   if(buy)
   {
      if(sl >= quote.bid)
         return false;

      return quote.bid - sl + tolerance >= minimumDistance;
   }

   if(sl <= quote.ask)
      return false;

   return sl - quote.ask + tolerance >= minimumDistance;
}

bool LossPerLot(
   const bool buy,
   const double entry,
   const double sl,
   double &loss
)
{
   loss = 0.0;

   ENUM_ORDER_TYPE type =
      buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

   double profit = 0.0;

   if(OrderCalcProfit(
         type, _Symbol, 1.0, entry, sl, profit
      ) &&
      profit != 0.0)
   {
      loss = MathAbs(profit);
      return loss > 0.0;
   }

   double tickValue = SymbolInfoDouble(
      _Symbol, SYMBOL_TRADE_TICK_VALUE
   );

   if(tickValue <= 0.0 || tickSize <= 0.0)
      return false;

   loss = MathAbs(entry - sl) / tickSize * tickValue;
   return loss > 0.0;
}

double VolumeForRisk(
   const bool buy,
   const double entry,
   const double sl,
   double &actualRisk,
   bool &capped
)
{
   actualRisk = 0.0;
   capped = false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   if(equity <= 0.0)
      return 0.0;

   double lossPerLot = 0.0;

   if(!LossPerLot(buy, entry, sl, lossPerLot))
      return 0.0;

   double riskMoney = equity * RiskPercent / 100.0;
   double lots = riskMoney / lossPerLot;

   double minimum = SymbolInfoDouble(
      _Symbol, SYMBOL_VOLUME_MIN
   );

   double maximum = SymbolInfoDouble(
      _Symbol, SYMBOL_VOLUME_MAX
   );

   double step = SymbolInfoDouble(
      _Symbol, SYMBOL_VOLUME_STEP
   );

   if(step <= 0.0 || minimum <= 0.0 || maximum <= 0.0)
      return 0.0;

   double cap = MathMin(MaxLots, maximum);

   if(lots > cap)
   {
      lots = cap;
      capped = true;
   }

   int digits = LotDigits(step);
   lots = NormalizeDouble(
      MathFloor(lots / step + 1e-8) * step,
      digits
   );

   if(lots > cap + 1e-8)
      lots = NormalizeDouble(lots - step, digits);

   if(lots < minimum - 1e-9 || lots <= 0.0)
      return 0.0;

   actualRisk = lots * lossPerLot;
   return lots;
}

//==================================================
// 平仓
//==================================================
void TryPendingClose(const ulong ticket)
{
   if(!closePending)
      return;

   if(!PositionSelectByTicket(ticket))
   {
      if(plannedSkipBar > 0)
         skipEntryBarOpen = plannedSkipBar;

      plannedSkipBar = 0;
      closePending = false;
      pendingCloseReason = "";
      return;
   }

   bool sent = trade.PositionClose(ticket);

   if(sent && TradeSucceeded() &&
      !PositionSelectByTicket(ticket))
   {
      Print("平仓请求完成，原因：", pendingCloseReason);

      if(plannedSkipBar > 0)
         skipEntryBarOpen = plannedSkipBar;

      plannedSkipBar = 0;
      closePending = false;
      pendingCloseReason = "";
      return;
   }

   PrintFormat(
      "平仓失败，原因=%s，返回=%u %s",
      pendingCloseReason,
      trade.ResultRetcode(),
      trade.ResultRetcodeDescription()
   );
}

void RequestClose(
   const ulong ticket,
   const string reason,
   const datetime barToSkip
)
{
   closePending = true;
   pendingCloseReason = reason;

   if(barToSkip > 0)
      plannedSkipBar = barToSkip;

   TryPendingClose(ticket);
}

datetime BarBeingEntered()
{
   datetime current = iTime(_Symbol, SignalTimeframe, 0);

   if(lastBarOpen != 0 &&
      current != 0 &&
      current != lastBarOpen)
   {
      return lastBarOpen;
   }

   return current;
}

//==================================================
// 开仓与跟踪
//==================================================
void OpenPosition(const bool buy, const double utStop)
{
   if(closePending || HasAnySymbolPosition())
      return;

   MqlTick quote;

   if(!SymbolInfoTick(_Symbol, quote))
      return;

   double entry = buy ? quote.ask : quote.bid;
   double sl = RoundPrice(utStop);
   double spread = SpreadPrice();
   double distance = buy ? entry - sl : sl - entry;

   if(spread <= 0.0)
   {
      Print("点差不可用，跳过入场。");
      return;
   }

   if(distance <= 0.0 ||
      distance + tickSize * 0.01 < spread * SpreadMultiple)
   {
      PrintFormat(
         "跳过入场：止损距离 %s，点差 %s，需要至少 %d 倍点差。",
         DoubleToString(distance, priceDigits),
         DoubleToString(spread, priceDigits),
         SpreadMultiple
      );
      return;
   }

   if(!BrokerStopAllowed(buy, sl))
   {
      Print("跳过入场：止损不满足当前报价或经纪商限制。");
      return;
   }

   double actualRisk = 0.0;
   bool capped = false;
   double lots = VolumeForRisk(
      buy, entry, sl, actualRisk, capped
   );

   if(lots <= 0.0)
   {
      PrintFormat(
         "跳过入场：%.2f%% 权益风险换算的手数低于最小手数，或无法计价。",
         RiskPercent
      );
      return;
   }

   long stopTicks = (long)MathRound(distance / tickSize);
   string comment = StringFormat("HUTM15:%I64d", stopTicks);

   if(StringLen(comment) > 31)
      comment = "HUTM15";

   bool sent = false;

   if(buy)
      sent = trade.Buy(lots, _Symbol, 0.0, sl, 0.0, comment);
   else
      sent = trade.Sell(lots, _Symbol, 0.0, sl, 0.0, comment);

   if(!sent || !TradeSucceeded())
   {
      PrintFormat(
         "开仓失败：手数=%s，返回=%u %s",
         DoubleToString(lots, LotDigits(
            SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP)
         )),
         trade.ResultRetcode(),
         trade.ResultRetcodeDescription()
      );
      return;
   }

   double filled = trade.ResultPrice();

   if(filled <= 0.0)
      filled = entry;

   PrintFormat(
      "开%s：手数=%s，价格=%s，止损=%s，点差=%s，预计亏损=%s %s%s。",
      buy ? "多" : "空",
      DoubleToString(lots, LotDigits(
         SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP)
      )),
      DoubleToString(filled, priceDigits),
      DoubleToString(sl, priceDigits),
      DoubleToString(spread, priceDigits),
      DoubleToString(actualRisk, 2),
      AccountInfoString(ACCOUNT_CURRENCY),
      capped ? "，已按手数上限缩小" : ""
   );
}

void UpdateTrail(const ulong ticket, const double utStop)
{
   if(!PositionSelectByTicket(ticket))
      return;

   bool buy =
      PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;

   double desired = RoundPrice(utStop);
   double current = PositionGetDouble(POSITION_SL);

   if(buy)
   {
      if(current > 0.0 &&
         desired <= current + tickSize * 0.5)
      {
         return;
      }
   }
   else if(current > 0.0 &&
           desired >= current - tickSize * 0.5)
   {
      return;
   }

   if(!BrokerStopAllowed(buy, desired))
      return;

   bool sent = trade.PositionModify(ticket, desired, 0.0);

   if(!sent || !TradeSucceeded())
   {
      PrintFormat(
         "跟踪止损修改失败：%u %s",
         trade.ResultRetcode(),
         trade.ResultRetcodeDescription()
      );
   }
}

//==================================================
// 收盘信号处理
//==================================================
void ProcessClosedBar(const datetime currentOpen)
{
   datetime signalOpen = iTime(
      _Symbol, SignalTimeframe, 1
   );

   int period = PeriodSeconds(SignalTimeframe);

   if(signalOpen <= 0 || period <= 0)
      return;

   bool contiguous = (signalOpen + period == currentOpen);

   SignalSet sig;

   if(!BuildSignals(sig))
      return;

   lastSignal = sig;
   signalsReady = true;

   ulong ticket;

   if(FindOurPosition(ticket))
   {
      if(!PositionSelectByTicket(ticket))
         return;

      bool buy =
         PositionGetInteger(POSITION_TYPE) ==
         POSITION_TYPE_BUY;

      // 收盘已在 UT 轨迹另一侧时离场。穿越当根会触发，
      // 漏掉穿越当根之后的报价也会补上这次离场。
      bool invalidated = buy
         ? sig.closePrice <= sig.utStop
         : sig.closePrice >= sig.utStop;

      if(invalidated)
      {
         RequestClose(ticket, "UT_CROSS", signalOpen);
         return;
      }

      UpdateTrail(ticket, sig.utStop);
      return;
   }

   if(!contiguous)
   {
      LogOncePerBar("K 线不连续，跳过入场。");
      return;
   }

   if(closePending)
      return;

   if(signalOpen == skipEntryBarOpen)
      return;

   datetime signalUtc = ToUTC(signalOpen + period);
   datetime nowUtc = ToUTC(TimeCurrent());

   if(!InDateRange(signalUtc) || !InDateRange(nowUtc))
      return;

   if(!InEntryWindow(signalUtc) || !InEntryWindow(nowUtc))
      return;

   bool wantLong = AllowLong && sig.regimeLong && sig.utBuy;
   bool wantShort = AllowShort && sig.regimeShort && sig.utSell;

   if(!wantLong && !wantShort)
      return;

   if(HasAnySymbolPosition())
   {
      LogOncePerBar("该品种已有其他持仓，跳过入场。");
      return;
   }

   OpenPosition(wantLong, sig.utStop);
}

//==================================================
// 面板
//==================================================
void UpdatePanel()
{
   ulong ticket;
   bool hasPosition = FindOurPosition(ticket);

   string state = "空仓";
   string stopText = "-";

   if(hasPosition)
   {
      long type = PositionGetInteger(POSITION_TYPE);

      state = type == POSITION_TYPE_BUY
         ? "持有多单"
         : "持有空单";

      double sl = PositionGetDouble(POSITION_SL);

      if(sl > 0.0)
         stopText = DoubleToString(sl, priceDigits);
   }

   string regime = signalsReady ? "无方向" : "等待指标";

   if(signalsReady && lastSignal.regimeLong)
      regime = "允许多";
   else if(signalsReady && lastSignal.regimeShort)
      regime = "允许空";

   string cross = "无";

   if(signalsReady && lastSignal.utBuy)
      cross = "上穿";
   else if(signalsReady && lastSignal.utSell)
      cross = "下穿";

   Comment(
      "Hull 方向 + UT 跟踪\n",
      "品种：", _Symbol,
      "  周期：", EnumToString(SignalTimeframe), "\n",
      "单笔风险：", DoubleToString(RiskPercent, 2),
      "%  手数上限：", DoubleToString(MaxLots, 2), "\n",
      "状态：", state,
      "  止损：", stopText, "\n",
      "方向：", regime,
      "  最近 UT：", cross, "\n",
      "已收盘 K 线：", IntegerToString(loadedBars), "\n",
      "进场 UTC ",
      IntegerToString(SessionStartHourUTC),
      ":00-",
      IntegerToString(EntryStopHourUTC),
      ":00  平仓 ",
      IntegerToString(FlatHourUTC),
      ":00"
   );
}

//==================================================
// 初始化
//==================================================
void WarnIfOffsetLooksWrong()
{
   if(MQLInfoInteger(MQL_TESTER) != 0)
      return;

   datetime gmt = TimeGMT();
   datetime server = TimeTradeServer();

   if(gmt <= 0 || server <= 0)
      return;

   int observed = (int)MathRound(
      (double)(server - gmt) / 3600.0
   );

   if(observed != ServerUtcOffsetHours)
   {
      PrintFormat(
         "服务器与 UTC 的时差约为 %d 小时，输入为 %d。时段按输入计算。",
         observed,
         ServerUtcOffsetHours
      );
   }
}

void RestoreSkipBar()
{
   datetime now = TimeCurrent();

   if(!HistorySelect(now - 86400 * 7, now))
      return;

   for(int i = HistoryDealsTotal() - 1; i >= 0; --i)
   {
      ulong deal = HistoryDealGetTicket(i);

      if(deal == 0)
         continue;

      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;

      if((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC)
         != MagicNumber)
      {
         continue;
      }

      long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);

      if(entry != DEAL_ENTRY_OUT &&
         entry != DEAL_ENTRY_OUT_BY &&
         entry != DEAL_ENTRY_INOUT)
      {
         continue;
      }

      datetime when = (datetime)HistoryDealGetInteger(
         deal, DEAL_TIME
      );

      datetime bar = BarOpenContaining(when);

      if(bar > 0)
         skipEntryBarOpen = bar;

      break;
   }
}

void FlattenIfNeeded(const ulong ticket)
{
   if(!PositionSelectByTicket(ticket))
      return;

   datetime opened = (datetime)PositionGetInteger(
      POSITION_TIME
   );

   datetime now = TimeCurrent();
   datetime nowUtc = ToUTC(now);

   if(!MustFlatten(nowUtc) &&
      !HeldAcrossFlat(opened, now))
   {
      return;
   }

   string reason = MustFlatten(nowUtc)
      ? "SESSION_OR_DATE"
      : "SESSION_GAP";

   RequestClose(ticket, reason, BarBeingEntered());
}

int OnInit()
{
   lastBarOpen = 0;
   skipEntryBarOpen = 0;
   plannedSkipBar = 0;
   lastFailLogBar = 0;
   closePending = false;
   pendingCloseReason = "";
   signalsReady = false;
   loggedWarmup = false;
   loggedHistoryShort = false;
   loadedBars = 0;

   tickSize = SymbolInfoDouble(
      _Symbol, SYMBOL_TRADE_TICK_SIZE
   );

   priceDigits = (int)SymbolInfoInteger(
      _Symbol, SYMBOL_DIGITS
   );

   if(tickSize <= 0.0 ||
      PeriodSeconds(SignalTimeframe) <= 0 ||
      HullLength < 4 ||
      AtrLength < 1 ||
      UtMultiplier <= 0.0 ||
      RiskPercent <= 0.0 ||
      RiskPercent > 5.0 ||
      MaxLots <= 0.0 ||
      DeviationPoints < 0 ||
      SpreadMultiple < 1 ||
      StartDateUTC >= EndDateUTC ||
      ServerUtcOffsetHours < -12 ||
      ServerUtcOffsetHours > 14 ||
      SessionStartHourUTC < 0 ||
      SessionStartHourUTC > 23 ||
      EntryStopHourUTC < 0 ||
      EntryStopHourUTC > 23 ||
      FlatHourUTC < 0 ||
      FlatHourUTC > 23 ||
      SessionStartHourUTC >= EntryStopHourUTC ||
      EntryStopHourUTC > FlatHourUTC)
   {
      Print("基础参数无效。");
      return INIT_PARAMETERS_INCORRECT;
   }

   SymbolSelect(_Symbol, true);

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(DeviationPoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);

   WarnIfOffsetLooksWrong();

   if(PeriodSeconds(SignalTimeframe) <
      PeriodSeconds(PERIOD_M15))
   {
      Print("信号周期低于 M15，止损可能重新被点差吃掉。");
   }

   RestoreSkipBar();

   lastBarOpen = iTime(_Symbol, SignalTimeframe, 0);

   ulong ticket;

   if(FindOurPosition(ticket))
      FlattenIfNeeded(ticket);

   PrintFormat(
      "EA 初始化：%s %s，单笔风险 %.2f%%，手数上限 %.2f，进场 %02d:00-%02d:00 UTC，%02d:00 UTC 平仓。",
      _Symbol,
      EnumToString(SignalTimeframe),
      RiskPercent,
      MaxLots,
      SessionStartHourUTC,
      EntryStopHourUTC,
      FlatHourUTC
   );

   UpdatePanel();
   return INIT_SUCCEEDED;
}

//==================================================
// 报价
//==================================================
void OnTick()
{
   ulong ticket;
   bool have = FindOurPosition(ticket);

   if(have)
   {
      if(closePending)
         TryPendingClose(ticket);
      else
         FlattenIfNeeded(ticket);
   }
   else if(closePending)
   {
      if(plannedSkipBar > 0)
         skipEntryBarOpen = plannedSkipBar;

      plannedSkipBar = 0;
      closePending = false;
      pendingCloseReason = "";
   }

   datetime currentOpen = iTime(
      _Symbol, SignalTimeframe, 0
   );

   if(currentOpen == 0)
   {
      UpdatePanel();
      return;
   }

   if(lastBarOpen == 0)
   {
      lastBarOpen = currentOpen;
      UpdatePanel();
      return;
   }

   if(currentOpen != lastBarOpen)
   {
      lastBarOpen = currentOpen;

      if(!closePending)
         ProcessClosedBar(currentOpen);
   }

   UpdatePanel();
}

void OnTradeTransaction(
   const MqlTradeTransaction &trans,
   const MqlTradeRequest &request,
   const MqlTradeResult &result
)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD ||
      trans.deal == 0)
   {
      return;
   }

   if(!HistoryDealSelect(trans.deal))
      return;

   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL)
      != _Symbol)
   {
      return;
   }

   if((ulong)HistoryDealGetInteger(trans.deal, DEAL_MAGIC)
      != MagicNumber)
   {
      return;
   }

   long entry = HistoryDealGetInteger(
      trans.deal, DEAL_ENTRY
   );

   if(entry != DEAL_ENTRY_OUT &&
      entry != DEAL_ENTRY_OUT_BY &&
      entry != DEAL_ENTRY_INOUT)
   {
      return;
   }

   datetime when = (datetime)HistoryDealGetInteger(
      trans.deal, DEAL_TIME
   );

   if(plannedSkipBar > 0)
      skipEntryBarOpen = plannedSkipBar;
   else
   {
      datetime bar = BarOpenContaining(when);

      if(bar > 0)
         skipEntryBarOpen = bar;
   }

   plannedSkipBar = 0;

   ENUM_DEAL_REASON reason =
      (ENUM_DEAL_REASON)HistoryDealGetInteger(
         trans.deal, DEAL_REASON
      );

   double profit = HistoryDealGetDouble(
      trans.deal, DEAL_PROFIT
   );

   PrintFormat(
      "退出成交：原因=%s，成交盈亏=%.2f。",
      EnumToString(reason),
      profit
   );
}

void OnDeinit(const int reason)
{
   Comment("");
}
