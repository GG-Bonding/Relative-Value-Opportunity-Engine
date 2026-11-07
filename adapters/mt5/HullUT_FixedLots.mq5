#property strict
#property version "2.00"

#include <Trade/Trade.mqh>

CTrade trade;

//==================================================
// 参数
//==================================================
input group "交易与固定手数"
input ENUM_TIMEFRAMES SignalTimeframe = PERIOD_M1;
input double FixedLots = 0.02;
input ulong MagicNumber = 20261002;
input int DeviationPoints = 20;
input bool AllowLong = true;
input bool AllowShort = true;

input group "Hull 与 UT"
input int HullLength = 55;
input int AtrLength = 15;
input double UtMultiplier = 1.0;

input group "止损止盈与退出"
input double StopAtrMultiplier = 1.5;
input double TargetR = 3.1;
input int MaxHoldingMinutes = 240;
input bool ExitOnUtOpposite = true;

input group "保本与回撤保护"
input bool EnableProtection = true;
input double ProtectionTriggerR = 0.2;
input double ProtectedStopR = 0.0;

input group "交易时段（UTC）"
input bool UseSession = true;
input int SessionStartHourUTC = 7;
input int SessionEndHourUTC = 20;
input int ServerUtcOffsetHours = 2;

input group "回测日期（UTC）"
input datetime StartDateUTC = D'2024.01.01 00:00:00';
input datetime EndDateUTC = D'2030.01.01 00:00:00';

input group "指标历史起点（服务器时间）"
input datetime IndicatorHistoryStartServer =
    D'2023.12.01 00:00:00';

//==================================================
// 状态
//==================================================
datetime lastBarOpen = 0;
datetime lastExitTime = 0;

ulong trackedTicket = 0;
long initialStopTicks = 0;
long initialTargetTicks = 0;

bool protectionArmed = false;
bool closePending = false;
string pendingCloseReason = "";

int protectionActivations = 0;

double tickSize = 0.0;
int priceDigits = 0;

//==================================================
// 基础工具
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
   return serverTime - ServerUtcOffsetHours * 3600;
}

bool InDateRange(const datetime utcTime)
{
   return utcTime >= StartDateUTC &&
          utcTime < EndDateUTC;
}

bool InSession(const datetime utcTime)
{
   if(!UseSession)
      return true;

   if(SessionStartHourUTC == SessionEndHourUTC)
      return true;

   MqlDateTime tm;
   TimeToStruct(utcTime, tm);

   if(SessionStartHourUTC < SessionEndHourUTC)
   {
      return tm.hour >= SessionStartHourUTC &&
             tm.hour < SessionEndHourUTC;
   }

   return tm.hour >= SessionStartHourUTC ||
          tm.hour < SessionEndHourUTC;
}

bool TradeSucceeded()
{
   uint code = trade.ResultRetcode();

   return code == TRADE_RETCODE_DONE ||
          code == TRADE_RETCODE_DONE_PARTIAL ||
          code == TRADE_RETCODE_NO_CHANGES;
}

// 严格检查固定手数，不自动增大或缩小仓位。
bool ValidateFixedLots()
{
   double minimum = SymbolInfoDouble(
      _Symbol, SYMBOL_VOLUME_MIN
   );

   double maximum = SymbolInfoDouble(
      _Symbol, SYMBOL_VOLUME_MAX
   );

   double step = SymbolInfoDouble(
      _Symbol, SYMBOL_VOLUME_STEP
   );

   if(step <= 0.0 || minimum <= 0.0)
   {
      Print("无法读取品种的手数规格。");
      return false;
   }

   if(FixedLots < minimum - 1e-9 ||
      FixedLots > maximum + 1e-9)
   {
      PrintFormat(
         "FixedLots=%.8f 超出允许范围 %.8f 至 %.8f。",
         FixedLots, minimum, maximum
      );

      return false;
   }

   double aligned = MathRound(FixedLots / step) * step;

   if(MathAbs(aligned - FixedLots) > 1e-8)
   {
      PrintFormat(
         "FixedLots=%.8f 不符合手数步长 %.8f。",
         FixedLots, step
      );

      return false;
   }

   return true;
}

//==================================================
// 持仓查找
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

//==================================================
// 指标：WMA、HMA、ATR、UT
// 使用已经收盘的 K 线
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

bool BuildSignals(
   bool &turnUp,
   bool &turnDown,
   bool &utBuy,
   bool &utSell,
   double &lastClose,
   double &lastHull,
   double &lastAtr
)
{
   turnUp = false;
   turnDown = false;
   utBuy = false;
   utSell = false;

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

   int minimumBars =
      MathMax(HullLength + rootLength + 4, AtrLength + 4);

   if(count < minimumBars)
      return false;

   double closes[];
   double rawHull[];
   double hull[];
   double atr[];
   double ut[];

   ArrayResize(closes, count);
   ArrayResize(rawHull, count);
   ArrayResize(hull, count);
   ArrayResize(atr, count);
   ArrayResize(ut, count);

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
            (
               atr[i - 1] * (AtrLength - 1) + tr
            ) / AtrLength;
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
      hull[n - 1] == EMPTY_VALUE ||
      hull[n - 2] == EMPTY_VALUE ||
      hull[n - 3] == EMPTY_VALUE ||
      ut[n] == EMPTY_VALUE ||
      ut[n - 1] == EMPTY_VALUE ||
      atr[n] == EMPTY_VALUE)
   {
      return false;
   }

   turnUp =
      hull[n] > hull[n - 2] &&
      hull[n - 1] <= hull[n - 3];

   turnDown =
      hull[n] < hull[n - 2] &&
      hull[n - 1] >= hull[n - 3];

   utBuy =
      closes[n] > ut[n] &&
      closes[n - 1] <= ut[n - 1];

   utSell =
      closes[n] < ut[n] &&
      closes[n - 1] >= ut[n - 1];

   lastClose = closes[n];
   lastHull = hull[n];
   lastAtr = atr[n];

   return true;
}

//==================================================
// 恢复初始风险距离
// 写入订单备注，以便 EA 重启后恢复
//==================================================
bool RestorePositionState(const ulong ticket)
{
   if(!PositionSelectByTicket(ticket))
      return false;

   if(trackedTicket == ticket &&
      initialStopTicks > 0 &&
      initialTargetTicks > 0)
   {
      return true;
   }

   string comment = PositionGetString(POSITION_COMMENT);

   string pieces[];
   ushort separator = StringGetCharacter(":", 0);

   int parts = StringSplit(comment, separator, pieces);

   if(parts < 3 || pieces[0] != "HUT")
   {
      Print(
         "无法从持仓备注恢复初始风险距离：",
         comment
      );

      return false;
   }

   long stopTicks = StringToInteger(pieces[1]);
   long targetTicks = StringToInteger(pieces[2]);

   if(stopTicks <= 0 || targetTicks <= 0)
      return false;

   trackedTicket = ticket;
   initialStopTicks = stopTicks;
   initialTargetTicks = targetTicks;

   protectionArmed = false;
   closePending = false;
   pendingCloseReason = "";

   double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl = PositionGetDouble(POSITION_SL);

   long type = PositionGetInteger(POSITION_TYPE);

   int direction =
      type == POSITION_TYPE_BUY ? 1 : -1;

   double originalStop = RoundPrice(
      entry - direction * initialStopTicks * tickSize
   );

   if(sl > 0.0)
   {
      if(direction == 1)
      {
         protectionArmed =
            sl > originalStop + tickSize * 0.5;
      }
      else
      {
         protectionArmed =
            sl < originalStop - tickSize * 0.5;
      }
   }

   return true;
}

//==================================================
// 止损修改：遵守经纪商最小距离与冻结距离
//==================================================
bool CanModifyStops(
   const long type,
   const double sl,
   const double tp
)
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
      MathMax((double)stopsLevel, (double)freezeLevel)
      * point;

   double tolerance = tickSize * 0.01;

   if(type == POSITION_TYPE_BUY)
   {
      if(sl >= quote.bid || tp <= quote.bid)
         return false;

      if(quote.bid - sl + tolerance < minimumDistance ||
         tp - quote.bid + tolerance < minimumDistance)
      {
         return false;
      }
   }
   else
   {
      if(sl <= quote.ask || tp >= quote.ask)
         return false;

      if(sl - quote.ask + tolerance < minimumDistance ||
         quote.ask - tp + tolerance < minimumDistance)
      {
         return false;
      }
   }

   return true;
}

void UpdatePositionStops(const ulong ticket)
{
   if(!RestorePositionState(ticket))
      return;

   if(!PositionSelectByTicket(ticket))
      return;

   long type = PositionGetInteger(POSITION_TYPE);

   int direction =
      type == POSITION_TYPE_BUY ? 1 : -1;

   double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   double currentSl = PositionGetDouble(POSITION_SL);
   double currentTp = PositionGetDouble(POSITION_TP);

   long stopOffsetTicks = -initialStopTicks;

   if(protectionArmed)
   {
      stopOffsetTicks = (long)MathRound(
         initialStopTicks * ProtectedStopR
      );
   }

   double desiredSl = RoundPrice(
      entry + direction * stopOffsetTicks * tickSize
   );

   double desiredTp = RoundPrice(
      entry + direction * initialTargetTicks * tickSize
   );

   // 已经启动保护后，止损只收紧。
   if(protectionArmed && currentSl > 0.0)
   {
      if(direction == 1)
         desiredSl = MathMax(desiredSl, currentSl);
      else
         desiredSl = MathMin(desiredSl, currentSl);
   }

   bool slSame =
      MathAbs(currentSl - desiredSl) < tickSize * 0.5;

   bool tpSame =
      MathAbs(currentTp - desiredTp) < tickSize * 0.5;

   if(slSame && tpSame)
      return;

   if(!CanModifyStops(type, desiredSl, desiredTp))
      return;

   bool sent = trade.PositionModify(
      ticket, desiredSl, desiredTp
   );

   if(!sent || !TradeSucceeded())
   {
      PrintFormat(
         "止损止盈修改失败：%u %s",
         trade.ResultRetcode(),
         trade.ResultRetcodeDescription()
      );
   }
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
      closePending = false;
      return;
   }

   bool sent = trade.PositionClose(ticket);

   if(sent && TradeSucceeded())
   {
      Print("平仓请求完成，原因：", pendingCloseReason);

      if(!PositionSelectByTicket(ticket))
      {
         closePending = false;
         lastExitTime = TimeCurrent();
      }
   }
   else
   {
      PrintFormat(
         "平仓失败，原因=%s，返回=%u %s",
         pendingCloseReason,
         trade.ResultRetcode(),
         trade.ResultRetcodeDescription()
      );
   }
}

void RequestClose(
   const ulong ticket,
   const string reason
)
{
   closePending = true;
   pendingCloseReason = reason;

   TryPendingClose(ticket);
}

//==================================================
// 固定手数开仓
//==================================================
void OpenPosition(
   const bool buy,
   const double atrValue
)
{
   if(HasAnySymbolPosition())
      return;

   if(!ValidateFixedLots())
      return;

   MqlTick quote;

   if(!SymbolInfoTick(_Symbol, quote))
      return;

   long stopTicks = (long)MathMax(
      1.0,
      MathRound(
         atrValue * StopAtrMultiplier / tickSize
      )
   );

   long targetTicks = (long)MathMax(
      1.0,
      MathRound(stopTicks * TargetR)
   );

   int direction = buy ? 1 : -1;

   double entry = buy ? quote.ask : quote.bid;

   double sl = RoundPrice(
      entry - direction * stopTicks * tickSize
   );

   double tp = RoundPrice(
      entry + direction * targetTicks * tickSize
   );

   long type =
      buy ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;

   if(!CanModifyStops(type, sl, tp))
   {
      Print(
         "跳过入场：ATR 止损/止盈距离不符合当前报价或经纪商限制。"
      );

      return;
   }

   string comment = StringFormat(
      "HUT:%I64d:%I64d",
      stopTicks,
      targetTicks
   );

   // 核心改动：直接使用 FixedLots。
   bool sent;

   if(buy)
   {
      sent = trade.Buy(
         FixedLots, _Symbol, 0.0, sl, tp, comment
      );
   }
   else
   {
      sent = trade.Sell(
         FixedLots, _Symbol, 0.0, sl, tp, comment
      );
   }

   if(!sent || !TradeSucceeded())
   {
      PrintFormat(
         "开仓失败：手数=%.4f，返回=%u %s",
         FixedLots,
         trade.ResultRetcode(),
         trade.ResultRetcodeDescription()
      );

      return;
   }

   ulong ticket;

   if(FindOurPosition(ticket))
   {
      trackedTicket = ticket;
      initialStopTicks = stopTicks;
      initialTargetTicks = targetTicks;

      protectionArmed = false;
      closePending = false;
      pendingCloseReason = "";

      // 将 SL/TP 对齐实际成交价格。
      UpdatePositionStops(ticket);
   }
}

//==================================================
// 收盘信号处理
//==================================================
void ProcessClosedBar(
   const datetime currentOpen,
   const datetime previousOpen
)
{
   bool turnUp;
   bool turnDown;
   bool utBuy;
   bool utSell;

   double lastClose;
   double lastHull;
   double lastAtr;

   if(!BuildSignals(
      turnUp,
      turnDown,
      utBuy,
      utSell,
      lastClose,
      lastHull,
      lastAtr
   ))
   {
      return;
   }

   int periodSeconds = PeriodSeconds(SignalTimeframe);

   datetime signalCloseTime =
      previousOpen + periodSeconds;

   datetime signalUtc = ToUTC(signalCloseTime);

   // 若周末或断报价后才出现下一根K线，
   // 同时检查实际执行时刻，避免过期时段入场。
   datetime executionUtc = ToUTC(TimeCurrent());

   bool dateAllowed =
      InDateRange(signalUtc) &&
      InDateRange(executionUtc);

   bool sessionAllowed =
      InSession(signalUtc) &&
      InSession(executionUtc);

   ulong ticket;

   if(FindOurPosition(ticket))
   {
      if(!RestorePositionState(ticket))
         return;

      if(!PositionSelectByTicket(ticket))
         return;

      long type = PositionGetInteger(POSITION_TYPE);

      int direction =
         type == POSITION_TYPE_BUY ? 1 : -1;

      double entry = PositionGetDouble(
         POSITION_PRICE_OPEN
      );

      datetime opened = (datetime)PositionGetInteger(
         POSITION_TIME
      );

      double initialRiskDistance =
         initialStopTicks * tickSize;

      double closeProfitR =
         direction * (lastClose - entry)
         / initialRiskDistance;

      // 保护只在 K 线收盘确认后启动。
      if(EnableProtection &&
         !protectionArmed &&
         closeProfitR >= ProtectionTriggerR)
      {
         protectionArmed = true;
         protectionActivations++;

         PrintFormat(
            "保护启动：收盘浮盈 %.3fR，保护位置 %.3fR。",
            closeProfitR,
            ProtectedStopR
         );
      }

      string exitReason = "";

      if(!dateAllowed)
      {
         exitReason = "DATE_END";
      }
      else if(!sessionAllowed)
      {
         exitReason = "SESSION_END";
      }
      else if(MaxHoldingMinutes > 0 &&
              signalCloseTime - opened >=
              MaxHoldingMinutes * 60)
      {
         exitReason = "TIME";
      }
      else if(ExitOnUtOpposite &&
              ((direction == 1 && utSell) ||
               (direction == -1 && utBuy)))
      {
         exitReason = "UT_OPPOSITE";
      }
      else if((direction == 1 && turnDown) ||
              (direction == -1 && turnUp))
      {
         exitReason = "HULL_REVERSAL";
      }

      if(exitReason != "")
      {
         RequestClose(ticket, exitReason);
      }
      else
      {
         UpdatePositionStops(ticket);
      }

      // 沿用原逻辑：退出后不在同一次信号处理里反手。
      return;
   }

   if(!dateAllowed || !sessionAllowed)
      return;

   // 刚刚发生平仓的 K 线，不重复入场。
   if(lastExitTime >= previousOpen &&
      lastExitTime <= currentOpen)
   {
      return;
   }

   if(HasAnySymbolPosition())
      return;

   if(AllowLong && turnUp && lastClose > lastHull)
   {
      OpenPosition(true, lastAtr);
   }
   else if(AllowShort &&
           turnDown &&
           lastClose < lastHull)
   {
      OpenPosition(false, lastAtr);
   }
}

//==================================================
// 面板
//==================================================
void UpdatePanel()
{
   ulong ticket;
   bool hasPosition = FindOurPosition(ticket);

   string state = "空仓";
   string protection = "未启动";

   if(hasPosition)
   {
      long type = PositionGetInteger(POSITION_TYPE);

      state =
         type == POSITION_TYPE_BUY ? "持有多单" : "持有空单";

      if(protectionArmed)
         protection = "已启动";
   }

   Comment(
      "Hull + UT 固定手数 EA\n",
      "品种：", _Symbol,
      "  周期：", EnumToString(SignalTimeframe), "\n",
      "新订单固定手数：",
      DoubleToString(FixedLots, 4), "\n",
      "状态：", state, "\n",
      "保护：", protection,
      "  本次运行启动次数：",
      IntegerToString(protectionActivations), "\n",
      "触发：",
      DoubleToString(ProtectionTriggerR, 2), "R",
      "  保护止损：",
      DoubleToString(ProtectedStopR, 2), "R\n",
      "最长持仓：",
      IntegerToString(MaxHoldingMinutes), " 分钟"
   );
}

//==================================================
// 初始化
//==================================================
int OnInit()
{
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
      StopAtrMultiplier <= 0.0 ||
      TargetR <= 0.0 ||
      FixedLots <= 0.0 ||
      DeviationPoints < 0 ||
      MaxHoldingMinutes < 0 ||
      StartDateUTC >= EndDateUTC ||
      SessionStartHourUTC < 0 ||
      SessionStartHourUTC > 23 ||
      SessionEndHourUTC < 0 ||
      SessionEndHourUTC > 23)
   {
      Print("基础参数无效。");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(EnableProtection &&
      (ProtectionTriggerR <= 0.0 ||
       ProtectedStopR < -1.0 ||
       ProtectedStopR >= ProtectionTriggerR))
   {
      Print(
         "保护参数无效：触发R必须大于0，",
         "保护位置必须>=-1且小于触发R。"
      );

      return INIT_PARAMETERS_INCORRECT;
   }

   if(!ValidateFixedLots())
      return INIT_PARAMETERS_INCORRECT;

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(DeviationPoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);

   lastBarOpen = iTime(
      _Symbol, SignalTimeframe, 0
   );

   // 恢复近期平仓时间，避免重启后重复入场。
   datetime now = TimeCurrent();

   if(HistorySelect(now - 86400 * 7, now))
   {
      for(int i = HistoryDealsTotal() - 1; i >= 0; --i)
      {
         ulong deal = HistoryDealGetTicket(i);

         if(deal == 0)
            continue;

         if(HistoryDealGetString(
            deal, DEAL_SYMBOL
         ) != _Symbol)
         {
            continue;
         }

         if((ulong)HistoryDealGetInteger(
            deal, DEAL_MAGIC
         ) != MagicNumber)
         {
            continue;
         }

         long entry = HistoryDealGetInteger(
            deal, DEAL_ENTRY
         );

         if(entry == DEAL_ENTRY_OUT ||
            entry == DEAL_ENTRY_OUT_BY ||
            entry == DEAL_ENTRY_INOUT)
         {
            lastExitTime = (datetime)HistoryDealGetInteger(
               deal, DEAL_TIME
            );

            break;
         }
      }
   }

   ulong ticket;

   if(FindOurPosition(ticket))
   {
      if(!RestorePositionState(ticket))
      {
         Print(
            "已有持仓无法恢复风险参数，停止初始化。"
         );

         return INIT_FAILED;
      }
   }

   PrintFormat(
      "EA 初始化完成：%s，固定手数=%.4f。",
      _Symbol, FixedLots
   );

   UpdatePanel();

   return INIT_SUCCEEDED;
}

//==================================================
// 实时 Tick
//==================================================
void OnTick()
{
   ulong ticket;

   if(FindOurPosition(ticket))
   {
      if(RestorePositionState(ticket))
      {
         if(closePending)
            TryPendingClose(ticket);
         else
            UpdatePositionStops(ticket);
      }
   }
   else
   {
      trackedTicket = 0;
      initialStopTicks = 0;
      initialTargetTicks = 0;
      protectionArmed = false;
      closePending = false;
      pendingCloseReason = "";
   }

   datetime currentOpen = iTime(
      _Symbol, SignalTimeframe, 0
   );

   if(currentOpen == 0)
      return;

   if(lastBarOpen == 0)
   {
      lastBarOpen = currentOpen;
      return;
   }

   if(currentOpen != lastBarOpen)
   {
      lastBarOpen = currentOpen;

      datetime previousOpen = iTime(
         _Symbol, SignalTimeframe, 1
      );

      if(previousOpen > 0)
         ProcessClosedBar(currentOpen, previousOpen);
   }

   UpdatePanel();
}

//==================================================
// 成交记录
//==================================================
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

   if(HistoryDealGetString(
      trans.deal, DEAL_SYMBOL
   ) != _Symbol)
   {
      return;
   }

   if((ulong)HistoryDealGetInteger(
      trans.deal, DEAL_MAGIC
   ) != MagicNumber)
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

   lastExitTime = (datetime)HistoryDealGetInteger(
      trans.deal, DEAL_TIME
   );

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
