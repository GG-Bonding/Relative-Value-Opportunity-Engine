// EURGBP M15 return-z mirror mean reversion, for the MetaTrader 5 Strategy Tester.
// Parameters are the fine-grid book: return z 0.50, previous-UTC-day price z 0.10,
// window 300 closed M15 bars, take profit 0.38%, stop 0.46%, hold 13137 minutes.
// The return z is the just-closed M15 bar. Price z uses the last M15 close of each
// earlier UTC date, never the broker D1 and never the still-open date.
// Live orders stay off unless InpAllowLiveTrading is also true.
//
// Tester: EURGBP, deposit 1000 USD, leverage 1:1000.
// Model: Every tick, or 1 minute OHLC. Open prices only skips the stop inside a bar.
// On a 5-digit symbol set Spread to 3 points (0.3 pip) to sit next to the 0.25 pip model.
// InpCommissionPerLot only sizes the lot. The tester charges whatever the symbol specifies.
// Swap is the symbol's swap. The researched account did not charge swap.
// Blackout: 235 merged windows, 2019-12-12 12:30 UTC through 2025-03-26 08:00 UTC.
// Releases after 2025-03-26 are not in the file, so later entries are not blocked.

#property copyright "RelativeValueEngine"
#property version   "1.00"
#property description "EURGBP M15 return-z mirror. Closed bar in, broker stop out."

#include <Trade/Trade.mqh>

input group "交易开关"
input bool   InpEnableTrading          = true;  // 允许交易（回测保持打开）
input bool   InpAllowLiveTrading       = false; // 允许实盘。回测不需要
input long   InpMagic                  = 20261028; // 魔术号
input int    InpDeviationPoints        = 20;    // 市价滑点（点）

input group "信号"
input double InpZMin                   = 0.50;  // 收益z门槛
input double InpPriceZMin              = 0.10;  // 价值z门槛
input int    InpReturnWindow           = 300;   // 收益z根数（已收盘M15）
input int    InpPriceWindow            = 60;    // 价值z天数
input int    InpSessionStartHour       = 7;     // 开仓开始（UTC，含）
input int    InpSessionEndHour         = 17;    // 开仓结束（UTC，不含）
input bool   InpUseBlacklist           = true;  // 新闻前后禁止开仓

input group "出场和手数"
input double InpTakeProfitPercent      = 0.38;  // 止盈（价格%）
input double InpStopLossPercent        = 0.46;  // 止损（价格%）
input int    InpMaxHoldMinutes         = 13137; // 最长持仓（分钟）
input double InpRiskPercent            = 5.0;   // 止损占余额（%）
input double InpCommissionPerLot       = 7.0;   // 每手往返手续费（账户货币，只用于算手数）
input int    InpMaxStopsPerDay         = 2;     // 同一UTC日几笔止损后停止开仓，0为关闭

input group "时钟"
input bool   InpManualOffset           = false; // 手动指定服务器减UTC
input int    InpServerUtcOffsetHours   = 0;     // 服务器减UTC的小时

#define BLACKOUT_N 235

datetime BlackoutStart[BLACKOUT_N] =
  {
   1576153800, 1576483200, 1576660500, 1576746900, 1576755900, 1579079700,
   1579176900, 1579252500, 1579782600, 1579852800, 1580384700, 1581412500,
   1581662700, 1582103700, 1582190100, 1582200900, 1582272000, 1583909100,
   1584016200, 1584627300, 1585036800, 1585118700, 1585223100, 1586430900,
   1587625200, 1587707100, 1588246200, 1589348700, 1589528700, 1589867100,
   1590044400, 1590126300, 1590146100, 1591172100, 1591270200, 1591940700,
   1592895600, 1594899000, 1595574000, 1596095100, 1597993200, 1599737400,
   1600339500, 1600844400, 1603436400, 1603974600, 1606118400, 1607603400,
   1608105600, 1608205500, 1611232200, 1611302400, 1613544300, 1613721600,
   1615465800, 1616067900, 1616572800, 1619091000, 1619161200, 1621581300,
   1623324600, 1624432500, 1626953400, 1627024500, 1629265500, 1629702900,
   1631187000, 1631684700, 1634886900, 1635420600, 1637131500, 1637655300,
   1639550700, 1639657800, 1643012100, 1643891400, 1644993900, 1645431300,
   1646915400, 1647517500, 1648017900, 1649828700, 1649935800, 1650611700,
   1651747500, 1652852700, 1653376500, 1654774200, 1655376300, 1655876700,
   1655968500, 1656665100, 1658404800, 1658473200, 1659609900, 1660283100,
   1660715100, 1661238000, 1662638400, 1662961500, 1663134300, 1663843500,
   1663916400, 1665553500, 1666158300, 1666594800, 1666872000, 1667475900,
   1668581100, 1669190400, 1670827500, 1671000300, 1671104700, 1671111000,
   1671177600, 1673592300, 1674024300, 1674551700, 1675338300, 1676011500,
   1676443500, 1676970900, 1678776300, 1678971600, 1679467500, 1679571900,
   1679644800, 1681796700, 1681883100, 1682060400, 1683201600, 1683801900,
   1683870300, 1684215900, 1684825200, 1684907100, 1685609100, 1686830400,
   1687326420, 1687430700, 1687503600, 1689227100, 1689745500, 1690182000,
   1690459200, 1691059500, 1691732700, 1692078300, 1692164700, 1692774000,
   1694583900, 1694692800, 1695293100, 1695361500, 1695883500, 1697089500,
   1697521500, 1697607900, 1698130800, 1698321600, 1698925500, 1699598700,
   1699944300, 1700203500, 1700726400, 1701243900, 1702449900, 1702554300,
   1702627200, 1705041900, 1705473900, 1706083200, 1706187600, 1706787900,
   1707806700, 1707893100, 1707979500, 1708588800, 1709816400, 1711008000,
   1711021500, 1712836800, 1713332700, 1713855600, 1715251500, 1715319900,
   1716356700, 1716447600, 1717145100, 1717675200, 1718084700, 1718775900,
   1718880300, 1718948700, 1719909900, 1721195100, 1721304000, 1721804400,
   1722415500, 1722509100, 1723527900, 1723614300, 1723700700, 1723787100,
   1724310000, 1725007500, 1726033500, 1726142400, 1726638300, 1726742700,
   1727074800, 1727772300, 1729057500, 1729166400, 1729230300, 1729753200,
   1730979900, 1732262400, 1734008400, 1734336000, 1734417900, 1734504300,
   1734608700, 1737096300, 1737441900, 1737705600, 1738242000, 1738842300,
   1739861100, 1739947500, 1740120300, 1741266000, 1742471100, 1742803200,
   1742971500
  };

datetime BlackoutEnd[BLACKOUT_N] =
  {
   1576161000, 1576492200, 1576665000, 1576751400, 1576760400, 1579084200,
   1579181400, 1579257000, 1579789800, 1579858200, 1580389200, 1581417000,
   1581667200, 1582108200, 1582194600, 1582205400, 1582281000, 1583913600,
   1584023400, 1584631800, 1585045800, 1585123200, 1585227600, 1586435400,
   1587634200, 1587711600, 1588250700, 1589353200, 1589533200, 1589871600,
   1590049800, 1590130800, 1590150600, 1591176600, 1591274700, 1591945200,
   1592901000, 1594903500, 1595579400, 1596099600, 1597998600, 1599744600,
   1600344000, 1600849800, 1603441800, 1603981800, 1606123800, 1607610600,
   1608111000, 1608210000, 1611236700, 1611307800, 1613548800, 1613730600,
   1615473000, 1616072400, 1616578200, 1619095500, 1619166600, 1621585800,
   1623331800, 1624437000, 1626960600, 1627029000, 1629270000, 1629707400,
   1631194200, 1631689200, 1634891400, 1635427800, 1637136000, 1637659800,
   1639555200, 1639662300, 1643016600, 1643898600, 1644998400, 1645435800,
   1646922600, 1647522000, 1648022400, 1649833200, 1649940300, 1650616200,
   1651752000, 1652857200, 1653381000, 1654781400, 1655380800, 1655881200,
   1655973000, 1656669600, 1658411100, 1658482200, 1659614400, 1660287600,
   1660719600, 1661247000, 1662644700, 1662966000, 1663138800, 1663848000,
   1663925400, 1665558000, 1666162800, 1666603800, 1666878300, 1667480400,
   1668585600, 1669199400, 1670832000, 1671004800, 1671109200, 1671115500,
   1671186600, 1673596800, 1674028800, 1674556200, 1675349100, 1676016000,
   1676448000, 1676975400, 1678780800, 1678977900, 1679472000, 1679576400,
   1679653800, 1681801200, 1681887600, 1682069400, 1683207900, 1683806400,
   1683874800, 1684220400, 1684834200, 1684911600, 1685613600, 1686836700,
   1687330920, 1687435200, 1687512600, 1689231600, 1689750000, 1690191000,
   1690465500, 1691064000, 1691737200, 1692082800, 1692169200, 1692783000,
   1694588400, 1694699100, 1695297600, 1695375000, 1695888000, 1697094000,
   1697526000, 1697612400, 1698139800, 1698327900, 1698930000, 1699603200,
   1699948800, 1700208000, 1700735400, 1701248400, 1702454400, 1702565100,
   1702636200, 1705046400, 1705478400, 1706092200, 1706193900, 1706792400,
   1707811200, 1707897600, 1707984000, 1708597800, 1709822700, 1711017000,
   1711026000, 1712843100, 1713337200, 1713864600, 1715256000, 1715324400,
   1716361200, 1716456600, 1717149600, 1717681500, 1718089200, 1718780400,
   1718884800, 1718962200, 1719914400, 1721199600, 1721310300, 1721813400,
   1722420000, 1722513600, 1723532400, 1723618800, 1723705200, 1723791600,
   1724319000, 1725012000, 1726038000, 1726148700, 1726642800, 1726747200,
   1727083800, 1727776800, 1729062000, 1729172700, 1729234800, 1729762200,
   1730984400, 1732271400, 1734014700, 1734345000, 1734422400, 1734508800,
   1734613200, 1737100800, 1737446400, 1737714600, 1738248300, 1738846800,
   1739865600, 1739952000, 1740133800, 1741272300, 1742475600, 1742812200,
   1742976000
  };

CTrade Trade;
datetime LastM15Open = 0;
datetime LastRetryMinute = 0;
bool WaitingForHistory = false;
int LastOffsetSeen = 2147483647;

int OffsetSeconds()
  {
   if(InpManualOffset)
      return InpServerUtcOffsetHours * 3600;
   return (int)(TimeTradeServer() - TimeGMT());
  }

int Ymd(const datetime stamp)
  {
   MqlDateTime parts;
   TimeToStruct(stamp, parts);
   return parts.year * 10000 + parts.mon * 100 + parts.day;
  }

bool CanSend()
  {
   if(!InpEnableTrading)
      return false;
   if(MQLInfoInteger(MQL_TESTER) != 0)
      return true;
   if(!InpAllowLiveTrading)
      return false;
   if(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) == 0)
      return false;
   if(MQLInfoInteger(MQL_TRADE_ALLOWED) == 0)
      return false;
   return true;
  }

ENUM_ORDER_TYPE_FILLING Filling()
  {
   int filling = (int)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return ORDER_FILLING_FOK;
   if((filling & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
  }

double SampleZ(const double &sample[], const int count)
  {
   if(count < 2)
      return EMPTY_VALUE;
   double sum = 0.0;
   for(int index = 0; index < count; index++)
     {
      if(sample[index] != sample[index])
         return EMPTY_VALUE;
      sum += sample[index];
     }
   double mean = sum / count;
   double square = 0.0;
   for(int index = 0; index < count; index++)
     {
      double delta = sample[index] - mean;
      square += delta * delta;
     }
   double deviation = MathSqrt(square / (count - 1));
   if(deviation <= 0.0)
      return EMPTY_VALUE;
   return (sample[count - 1] - mean) / deviation;
  }

bool OurPosition(ulong &ticket)
  {
   for(int index = PositionsTotal() - 1; index >= 0; index--)
     {
      ulong candidate = PositionGetTicket(index);
      if(candidate == 0 || !PositionSelectByTicket(candidate))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      ticket = candidate;
      return true;
     }
   ticket = 0;
   return false;
  }

bool InBlackout(const datetime close_utc)
  {
   if(!InpUseBlacklist || BLACKOUT_N == 0)
      return false;
   int low = 0;
   int high = BLACKOUT_N;
   while(low < high)
     {
      int mid = (low + high) / 2;
      if(BlackoutStart[mid] <= close_utc)
         low = mid + 1;
      else
         high = mid;
     }
   int pos = low - 1;
   return pos >= 0 && close_utc < BlackoutEnd[pos];
  }

int EntryYmd(const ulong position_id)
  {
   int total = HistoryDealsTotal();
   for(int index = total - 1; index >= 0; index--)
     {
      ulong deal = HistoryDealGetTicket(index);
      if((ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID) != position_id)
         continue;
      if(HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_IN)
         continue;
      string comment = HistoryDealGetString(deal, DEAL_COMMENT);
      int at = StringFind(comment, "d=");
      if(at >= 0)
        {
         int stamped = (int)StringToInteger(StringSubstr(comment, at + 2, 8));
         if(stamped >= 20000101)
            return stamped;
        }
      datetime when = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
      return Ymd(when - OffsetSeconds());
     }
   return 0;
  }

int StopCount(const int entry_ymd)
  {
   if(InpMaxStopsPerDay <= 0)
      return 0;
   if(!HistorySelect(TimeCurrent() - 12 * 86400, TimeCurrent() + 60))
      return 0;
   int count = 0;
   int total = HistoryDealsTotal();
   for(int index = 0; index < total; index++)
     {
      ulong deal = HistoryDealGetTicket(index);
      if(HistoryDealGetInteger(deal, DEAL_MAGIC) != InpMagic)
         continue;
      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;
      if(HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_OUT)
         continue;
      if(HistoryDealGetInteger(deal, DEAL_REASON) != DEAL_REASON_SL)
         continue;
      ulong position_id = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      if(EntryYmd(position_id) == entry_ymd)
         count++;
     }
   return count;
  }

datetime LastExitServerTime()
  {
   if(!HistorySelect(TimeCurrent() - 12 * 86400, TimeCurrent() + 60))
      return 0;
   for(int index = HistoryDealsTotal() - 1; index >= 0; index--)
     {
      ulong deal = HistoryDealGetTicket(index);
      if(HistoryDealGetInteger(deal, DEAL_MAGIC) != InpMagic)
         continue;
      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;
      long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY)
         continue;
      return (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
     }
   return 0;
  }

double NormalizePrice(const double price)
  {
   double tick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   if(tick <= 0.0)
      return NormalizeDouble(price, digits);
   return NormalizeDouble(MathRound(price / tick) * tick, digits);
  }

double LotForLoss(const double loss_per_lot)
  {
   if(loss_per_lot <= 0.0 || InpRiskPercent <= 0.0)
      return 0.0;
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double raw = balance * InpRiskPercent / 100.0 / loss_per_lot;
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minimum = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maximum = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0)
      step = 0.01;
   double lot = MathFloor(raw / step + 1e-8) * step;
   if(maximum > 0.0 && lot > maximum)
      lot = MathFloor(maximum / step + 1e-8) * step;
   int digits = 0;
   double unit = step;
   while(unit < 1.0 && digits < 8)
     {
      unit *= 10.0;
      digits++;
     }
   lot = NormalizeDouble(lot, digits);
   if(lot + 1e-8 < minimum)
      return 0.0;
   return lot;
  }

bool BuildPreviousDailyCloses(const MqlRates &rates[], const int copied, const int offset_seconds, double &closes[])
  {
   ArrayResize(closes, 0);
   int current_ymd = 0;
   double last_close = 0.0;
   bool have = false;
   for(int index = copied - 1; index >= 1; index--)
     {
      if(rates[index].close <= 0.0)
         continue;
      datetime close_utc = rates[index].time + PeriodSeconds(PERIOD_M15) - offset_seconds;
      int ymd = Ymd(close_utc);
      if(!have)
        {
         current_ymd = ymd;
         last_close = rates[index].close;
         have = true;
         continue;
        }
      if(ymd != current_ymd)
        {
         int size = ArraySize(closes);
         ArrayResize(closes, size + 1);
         closes[size] = last_close;
         current_ymd = ymd;
        }
      last_close = rates[index].close;
     }
   return have;
  }

void Show(const double z_value, const double price_z, const string state)
  {
   string z_text = (z_value == EMPTY_VALUE ? "n/a" : DoubleToString(z_value, 2));
   string price_text = (price_z == EMPTY_VALUE ? "n/a" : DoubleToString(price_z, 2));
   Comment("收益z ", z_text, "\n价值z ", price_text, "\n", state);
  }

void RememberOffset()
  {
   int offset = OffsetSeconds();
   if(offset == LastOffsetSeen)
      return;
   LastOffsetSeen = offset;
   Print("服务器时间减UTC = ", offset, " 秒。K线收盘按这个差值换到UTC。");
   if(!InpManualOffset && offset == 0)
      Print("自动偏移是0。如果服务器不是UTC，时段会错，请改用手动偏移。");
  }

void ManageTimeExit()
  {
   ulong ticket;
   if(!OurPosition(ticket) || !PositionSelectByTicket(ticket))
      return;
   datetime deadline = 0;
   string comment = PositionGetString(POSITION_COMMENT);
   int mark = StringFind(comment, "t=");
   if(mark >= 0)
      deadline = (datetime)StringToInteger(StringSubstr(comment, mark + 2));
   if(deadline <= 0)
     {
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
      deadline = opened - OffsetSeconds() + InpMaxHoldMinutes * 60;
     }
   if(TimeGMT() < deadline)
      return;
   if(!Trade.PositionClose(ticket))
      Print("时间出场失败 ", Trade.ResultRetcode(), " ", Trade.ResultRetcodeDescription());
   else
      Print("时间出场 ", TimeToString(TimeGMT(), TIME_DATE | TIME_MINUTES), " UTC");
  }

bool Evaluate()
  {
   RememberOffset();
   int offset = OffsetSeconds();
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, PERIOD_M15, 0, 20000, rates);
   if(copied < InpReturnWindow + 2)
      return false;

   datetime close_utc = rates[1].time + PeriodSeconds(PERIOD_M15) - offset;
   MqlDateTime clock;
   TimeToStruct(close_utc, clock);
   int ymd = clock.year * 10000 + clock.mon * 100 + clock.day;
   int minute = clock.hour * 60 + clock.min;
   bool session = clock.day_of_week >= 1 && clock.day_of_week <= 5
                  && minute >= InpSessionStartHour * 60
                  && minute < InpSessionEndHour * 60;

   double returns[];
   ArrayResize(returns, InpReturnWindow);
   double z_value = EMPTY_VALUE;
   bool returns_ok = true;
   for(int index = 0; index < InpReturnWindow; index++)
     {
      int newer = InpReturnWindow - index;
      double close_new = rates[newer].close;
      double close_old = rates[newer + 1].close;
      if(close_new <= 0.0 || close_old <= 0.0)
        {
         returns_ok = false;
         break;
        }
      returns[index] = MathLog(close_new / close_old);
     }
   if(returns_ok)
      z_value = SampleZ(returns, InpReturnWindow);

   double daily[];
   BuildPreviousDailyCloses(rates, copied, offset, daily);
   double price_z = EMPTY_VALUE;
   if(ArraySize(daily) >= InpPriceWindow)
     {
      double window[];
      ArrayResize(window, InpPriceWindow);
      int begin = ArraySize(daily) - InpPriceWindow;
      for(int index = 0; index < InpPriceWindow; index++)
         window[index] = daily[begin + index];
      price_z = SampleZ(window, InpPriceWindow);
     }

   string state = "无信号";
   int side = 0;
   if(!session)
      state = "非交易时段";
   else if(InBlackout(close_utc))
      state = "新闻禁开仓";
   else if(z_value == EMPTY_VALUE)
      state = "收益z不足";
   else if(price_z == EMPTY_VALUE)
      state = "价值z不足";
   else if(z_value > InpZMin && price_z > InpPriceZMin)
     {
      side = -1;
      state = "做空";
     }
   else if(z_value < -InpZMin && price_z < -InpPriceZMin)
     {
      side = 1;
      state = "做多";
     }

   ulong ticket;
   if(side != 0 && OurPosition(ticket))
     {
      side = 0;
      state = "持仓中";
     }
   if(side != 0 && InpMaxStopsPerDay > 0 && StopCount(ymd) >= InpMaxStopsPerDay)
     {
      side = 0;
      state = "当日止损已满";
     }
   if(side != 0)
     {
      datetime exit_server = LastExitServerTime();
      if(exit_server > 0 && close_utc <= exit_server - offset)
        {
         side = 0;
         state = "等待退出之后的K线";
        }
     }
   Show(z_value, price_z, state);
   if(side == 0)
      return true;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(bid <= 0.0 || ask < bid)
     {
      Print("没有买卖价，跳过 ", TimeToString(close_utc, TIME_DATE | TIME_MINUTES));
      return true;
     }
   double spread = ask - bid;
   double entry = rates[1].close;
   double stop_ratio = InpStopLossPercent / 100.0;
   double target_ratio = InpTakeProfitPercent / 100.0;
   double stop_loss;
   double take_profit;
   double open_price;
   ENUM_ORDER_TYPE order_type;
   if(side < 0)
     {
      open_price = entry;
      stop_loss = NormalizePrice(entry * (1.0 + stop_ratio) + spread);
      take_profit = NormalizePrice(entry * (1.0 - target_ratio) + spread);
      order_type = ORDER_TYPE_SELL;
      if(stop_loss <= ask || take_profit >= ask)
        {
         Print("空单止损止盈在错误的一侧 sl=", stop_loss, " tp=", take_profit, " ask=", ask);
         return true;
        }
     }
   else
     {
      open_price = entry + spread;
      stop_loss = NormalizePrice(entry * (1.0 - stop_ratio));
      take_profit = NormalizePrice(entry * (1.0 + target_ratio));
      order_type = ORDER_TYPE_BUY;
      if(stop_loss >= bid || take_profit <= bid)
        {
         Print("多单止损止盈在错误的一侧 sl=", stop_loss, " tp=", take_profit, " bid=", bid);
         return true;
        }
     }

   double profit = 0.0;
   if(!OrderCalcProfit(order_type, _Symbol, 1.0, open_price, stop_loss, profit))
     {
      Print("算不出止损金额 ", GetLastError());
      return true;
     }
   double loss_per_lot = MathAbs(profit) + MathMax(InpCommissionPerLot, 0.0);
   double lot = LotForLoss(loss_per_lot);
   if(lot <= 0.0)
     {
      Print("手数低于最小值，跳过。1手止损约 ", DoubleToString(loss_per_lot, 2));
      return true;
     }

   datetime deadline = close_utc + InpMaxHoldMinutes * 60;
   string comment = StringFormat("d=%08d;t=%I64d", ymd, (long)deadline);
   bool sent = false;
   if(side > 0)
      sent = Trade.Buy(lot, _Symbol, 0.0, stop_loss, take_profit, comment);
   else
      sent = Trade.Sell(lot, _Symbol, 0.0, stop_loss, take_profit, comment);
   if(!sent)
     {
      Print("下单失败 ", Trade.ResultRetcode(), " ", Trade.ResultRetcodeDescription());
      return true;
     }
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double planned = loss_per_lot * lot;
   Print((side > 0 ? "多 " : "空 "),
         TimeToString(close_utc, TIME_DATE | TIME_MINUTES), " UTC",
         " z=", DoubleToString(z_value, 2),
         " pz=", DoubleToString(price_z, 2),
         " 手数=", DoubleToString(lot, 2),
         " 止损=", DoubleToString(stop_loss, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " 止盈=", DoubleToString(take_profit, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " 计划亏损=", DoubleToString(planned, 2),
         " 占余额=", DoubleToString(balance > 0.0 ? 100.0 * planned / balance : 0.0, 2), "%");
   return true;
  }

int OnInit()
  {
   string symbol = _Symbol;
   StringToUpper(symbol);
   if(StringFind(symbol, "EURGBP") < 0)
     {
      Print("这个EA只跑EURGBP，当前是 ", _Symbol);
      return INIT_FAILED;
     }
   if(InpReturnWindow < 2 || InpPriceWindow < 2 || InpMaxHoldMinutes <= 0)
     {
      Print("窗口和持仓时间必须为正");
      return INIT_FAILED;
     }
   if(InpSessionStartHour < 0 || InpSessionEndHour > 24 || InpSessionStartHour >= InpSessionEndHour)
     {
      Print("交易时段不成立");
      return INIT_FAILED;
     }
   Trade.SetExpertMagicNumber(InpMagic);
   Trade.SetDeviationInPoints(InpDeviationPoints);
   Trade.SetTypeFilling(Filling());
   Trade.SetAsyncMode(false);
   string currency = AccountInfoString(ACCOUNT_CURRENCY);
   Print("EURGBP M15 收益z>", InpZMin,
         " 价值z>", InpPriceZMin,
         " 止盈", InpTakeProfitPercent, "% 止损", InpStopLossPercent, "%",
         " 持仓", InpMaxHoldMinutes, "分钟 风险", InpRiskPercent, "%");
   Print("点差点数 ", (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD),
         "。5位报价要贴近0.25点，测试器点差填3。建模用每个即时价格或1分钟OHLC。");
   Print("手续费输入 ", InpCommissionPerLot, " ", currency,
         " 只用于算手数。账户里实际扣的手续费以品种设置为准。");
   Print("新闻禁开仓 ", BLACKOUT_N, " 段，",
         TimeToString(BlackoutStart[0], TIME_DATE | TIME_MINUTES), " 到 ",
         TimeToString(BlackoutEnd[BLACKOUT_N - 1], TIME_DATE | TIME_MINUTES),
         " UTC。2025-03-26之后没有新闻数据。");
   if(currency != "USD")
      Print("账户货币是 ", currency, "。7是按美元账户准备的，这里要改成该货币的每手往返手续费。");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   Comment("");
  }

void OnTick()
  {
   if(!CanSend())
     {
      Comment("未允许下单。回测保持允许交易。实盘还要打开允许实盘。");
      return;
     }
   ManageTimeExit();
   datetime opened = iTime(_Symbol, PERIOD_M15, 0);
   if(opened == 0 || opened == LastM15Open)
      return;
   if(WaitingForHistory && iTime(_Symbol, PERIOD_M1, 0) == LastRetryMinute)
      return;
   if(!Evaluate())
     {
      WaitingForHistory = true;
      LastRetryMinute = iTime(_Symbol, PERIOD_M1, 0);
      return;
     }
   WaitingForHistory = false;
   LastM15Open = opened;
  }
