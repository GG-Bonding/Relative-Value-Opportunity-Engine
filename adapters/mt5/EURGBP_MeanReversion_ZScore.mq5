// EURGBP closed-M5 bid deviation mean reversion.
// Signals use the last closed M5 bid close. The forming bar is not readable.
// A position on the book is the only proof of a fill. Retcode 10009 does not
// by itself move the state to open.
// Live and demo accounts stay flat unless both enable flags are true.
// The Strategy Tester may send only when InpEnableTrading is true.
// Server UTC offset is an explicit input. This file does not assume Anzo's clock.
// BEGIN_M5Z_PARITY
// ema=12 window=20
// 0,0.85000,nan
// 1,0.85020,nan
// 2,0.85039,nan
// 3,0.85056,nan
// 4,0.85072,nan
// 5,0.85084,nan
// 6,0.85093,nan
// 7,0.85099,nan
// 8,0.85100,nan
// 9,0.85097,nan
// 10,0.85091,nan
// 11,0.85081,nan
// 12,0.85068,nan
// 13,0.85052,nan
// 14,0.85033,nan
// 15,0.85014,nan
// 16,0.84994,nan
// 17,0.84974,nan
// 18,0.84956,nan
// 19,0.84939,nan
// 20,0.84924,nan
// 21,0.84913,nan
// 22,0.84905,nan
// 23,0.84901,nan
// 24,0.84900,nan
// 25,0.84904,nan
// 26,0.84912,nan
// 27,0.84923,nan
// 28,0.84937,nan
// 29,0.84954,nan
// 30,0.84972,nan
// 31,0.84992,nan
// 32,0.85012,2.4093556172
// 33,0.85031,2.2896774191
// 34,0.85049,2.0948464864
// 35,0.85066,1.8893223831
// 36,0.85079,1.6490981511
// 37,0.85090,1.4315917860
// 38,0.85097,1.2007947981
// 39,0.85100,0.9569581299
// 40,0.85099,0.6927578337
// 41,0.85094,0.3940737273
// 42,0.85085,0.0401234370
// 43,0.85073,-0.3768467204
// 44,0.85058,-0.8799665519
// 45,0.85041,-1.4420238748
// 46,0.85022,-1.9744506033
// 47,0.85002,-2.3046184891
// 48,0.84983,-2.3435356085
// 49,0.84963,-2.2517846817
// 50,0.84946,-2.0357215151
// 51,0.84930,-1.8239490264
// 52,0.84917,-1.5985669479
// 53,0.84908,-1.3596770980
// 54,0.84902,-1.1283062761
// 55,0.84900,-0.8810562445
// 56,0.84902,-0.6092115745
// 57,0.84908,-0.2970807188
// 58,0.84918,0.0783637985
// 59,0.84931,0.5255785869
// 60,0.84946,1.0405756528
// 61,0.84964,1.6221131901
// 62,0.84983,2.1019688012
// 63,0.85003,2.3515068287
// 64,0.85023,2.3527868076
// 65,0.85042,2.2009922081
// 66,0.85059,1.9841785119
// 67,0.85074,1.7601684261
// 68,0.85086,1.5318189886
// 69,0.85094,1.2923796812
// 70,0.85099,1.0598456761
// 71,0.85100,0.8089655491
// 72,0.85097,0.5296097357
// 73,0.85089,0.1869221516
// 74,0.85079,-0.1877137432
// 75,0.85065,-0.6620667306
// 76,0.85049,-1.2027213542
// 77,0.85030,-1.7872207449
// 78,0.85011,-2.1967178189
// 79,0.84991,-2.3660072432
// END_M5Z_PARITY

#property copyright "Relative Value Engine"
#property version   "1.00"
#property description "EURGBP M5 deviation z-score. Closed bars only."
#property strict

input group "Switches"
input bool   InpEnableTrading          = false;
input bool   InpAllowLiveTrading       = false;
input bool   InpServerOffsetConfirmed  = false;
input double InpServerUtcOffsetHours   = 0.0;
input string InpSymbolPrefix           = "EURGBP";
input int    InpMagic                  = 20261024;
input string InpComment                = "M5ZREV";
input int    InpHistoryBars            = 5000;
input int    InpDeviationPoints        = 20;

input group "Signal"
input int    InpEmaPeriod              = 24;
input int    InpDevWindow              = 72;
input int    InpRetWindow              = 48;
input double InpZEntry                 = 2.0;
input double InpRetZ                   = 2.0;
input bool   InpUseReturnFilter        = false;
input int    InpRetLookback            = 2;
input double InpReversalGap            = 0.25;
input int    InpConfirmBars            = 3;
input bool   InpAllowLong              = true;
input bool   InpAllowShort             = true;

input group "Exit and risk"
input int    InpAtrPeriod              = 14;
input double InpAtrMult                = 1.5;
input double InpMinRR                  = 1.2;
input int    InpMaxHoldMinutes         = 60;
input int    InpCooldownBars           = 3;
input double InpMaxSpreadPips          = 1.5;
input double InpPip                    = 0.0001;
input double InpRiskFraction           = 0.005;
input double InpDailyLossFraction      = 0.02;
input int    InpMaxTradesPerDay        = 3;
input double InpSlippagePips           = 0.0;
input double InpCommissionPerLotSide   = 0.0;
input double InpMaxJumpAtr             = 0.5;

input group "H1 filter"
input bool   InpUseTrendFilter         = false;
input int    InpH1EmaPeriod            = 20;
input int    InpH1AdxPeriod            = 14;
input double InpH1AdxLevel             = 25.0;
input int    InpH1SlopeBars            = 3;

input group "Session and events"
input int    InpSessionStart           = 480;
input int    InpSessionEnd             = 1020;
input int    InpEventBeforeMinutes     = 15;
input int    InpEventAfterMinutes      = 15;
input string InpEventFilter            = "optional";
input int    InpCalendarMaxAgeDays     = 14;
input string InpEventFile              = "M5ZREV\\events.txt";

#define PHASE_IDLE 0
#define PHASE_ARMED 1
#define PHASE_CONFIRMED 2
#define PHASE_PENDING 3
#define PHASE_OPEN 4
#define PHASE_COOLDOWN 5

int      g_phase = PHASE_IDLE;
int      g_direction = 0;
datetime g_trigger_open = 0;
int      g_seen_after = 0;
datetime g_signal_server = 0;
double   g_frozen_ema = 0.0;
double   g_frozen_atr = 0.0;
double   g_signal_bid = 0.0;
datetime g_entry_gmt = 0;
double   g_stop = 0.0;
double   g_target = 0.0;
int      g_cooldown_left = 0;
datetime g_cooldown_marked = 0;
datetime g_last_closed = 0;
bool     g_attempted = false;
datetime g_pending_gmt = 0;
datetime g_consumed_server = 0;
ulong    g_position_id = 0;
datetime g_flat_since = 0;
int      g_day_key = 0;
double   g_day_pnl = 0.0;
int      g_day_trades = 0;
double   g_day_start = 0.0;
bool     g_symbol_ok = false;
bool     g_offset_warned = false;
bool     g_calendar_warned = false;
string   g_state_file;
string   g_calendar = "missing";
datetime g_events[];
int      g_event_count = 0;
bool     g_block_long = false;
bool     g_block_short = false;
bool     g_trend_ready = false;

int OnInit()
{
   g_state_file = "M5ZREV_" + _Symbol + "_" + IntegerToString(InpMagic) + ".txt";
   if(StringFind(_Symbol, InpSymbolPrefix) != 0)
   {
      Print("M5ZREV symbol ", _Symbol, " does not start with ", InpSymbolPrefix);
      return INIT_FAILED;
   }
   if(InpEmaPeriod < 2 || InpDevWindow < 2 || InpRetWindow < 2 || InpZEntry <= 0.0 || InpPip <= 0.0)
      return INIT_PARAMETERS_INCORRECT;
   if(!ParitySelfCheck())
   {
      Print("M5ZREV parity self-check failed");
      return INIT_FAILED;
   }
   g_symbol_ok = true;
   LoadState();
   Restore();
   ulong ticket;
   if(OurPosition(ticket))
      Adopt(ticket);
   Print("M5ZREV ready. Live orders require InpEnableTrading and InpAllowLiveTrading. Offset confirmed=",
         InpServerOffsetConfirmed, " hours=", InpServerUtcOffsetHours);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   SaveState();
}

void OnTick()
{
   if(!g_symbol_ok)
      return;
   ulong ticket;
   if(OurPosition(ticket))
   {
      g_flat_since = 0;
      Adopt(ticket);
      MaybeTimeExit();
   }
   else if(g_phase == PHASE_OPEN)
      NoteFlat();
   ExpirePending();
   MqlRates rates[];
   int count = ClosedM5(rates);
   if(count < 3)
      return;
   datetime closed_open = rates[count - 1].time;
   if(closed_open != g_last_closed)
   {
      g_last_closed = closed_open;
      StepClosedBar(rates, count);
      SaveState();
   }
   TryEntry();
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(request.magic != 0 && request.magic != InpMagic)
      return;
   if(trans.symbol != "" && trans.symbol != _Symbol)
      return;
   ulong ticket;
   if(OurPosition(ticket))
   {
      Adopt(ticket);
      return;
   }
   if(g_phase == PHASE_OPEN)
      NoteFlat();
}

bool ParitySelfCheck()
{
   double closes[];
   LoadParityCloses(closes);
   int count = ArraySize(closes);
   double z[];
   bool ok[];
   if(!DeviationZ(closes, count, 12, 20, z, ok))
      return false;
   if(count != 80 || ok[31] || !ok[32] || !ok[79])
      return false;
   if(!Near(z[32], 2.4093556172) || !Near(z[79], -2.3660072432))
   {
      PrintFormat("M5ZREV parity z32=%.10f z79=%.10f", z[32], z[79]);
      return false;
   }
   return true;
}

void LoadParityCloses(double &closes[])
{
   ArrayResize(closes, 80);
   closes[0] = 0.85000;
   closes[1] = 0.85020;
   closes[2] = 0.85039;
   closes[3] = 0.85056;
   closes[4] = 0.85072;
   closes[5] = 0.85084;
   closes[6] = 0.85093;
   closes[7] = 0.85099;
   closes[8] = 0.85100;
   closes[9] = 0.85097;
   closes[10] = 0.85091;
   closes[11] = 0.85081;
   closes[12] = 0.85068;
   closes[13] = 0.85052;
   closes[14] = 0.85033;
   closes[15] = 0.85014;
   closes[16] = 0.84994;
   closes[17] = 0.84974;
   closes[18] = 0.84956;
   closes[19] = 0.84939;
   closes[20] = 0.84924;
   closes[21] = 0.84913;
   closes[22] = 0.84905;
   closes[23] = 0.84901;
   closes[24] = 0.84900;
   closes[25] = 0.84904;
   closes[26] = 0.84912;
   closes[27] = 0.84923;
   closes[28] = 0.84937;
   closes[29] = 0.84954;
   closes[30] = 0.84972;
   closes[31] = 0.84992;
   closes[32] = 0.85012;
   closes[33] = 0.85031;
   closes[34] = 0.85049;
   closes[35] = 0.85066;
   closes[36] = 0.85079;
   closes[37] = 0.85090;
   closes[38] = 0.85097;
   closes[39] = 0.85100;
   closes[40] = 0.85099;
   closes[41] = 0.85094;
   closes[42] = 0.85085;
   closes[43] = 0.85073;
   closes[44] = 0.85058;
   closes[45] = 0.85041;
   closes[46] = 0.85022;
   closes[47] = 0.85002;
   closes[48] = 0.84983;
   closes[49] = 0.84963;
   closes[50] = 0.84946;
   closes[51] = 0.84930;
   closes[52] = 0.84917;
   closes[53] = 0.84908;
   closes[54] = 0.84902;
   closes[55] = 0.84900;
   closes[56] = 0.84902;
   closes[57] = 0.84908;
   closes[58] = 0.84918;
   closes[59] = 0.84931;
   closes[60] = 0.84946;
   closes[61] = 0.84964;
   closes[62] = 0.84983;
   closes[63] = 0.85003;
   closes[64] = 0.85023;
   closes[65] = 0.85042;
   closes[66] = 0.85059;
   closes[67] = 0.85074;
   closes[68] = 0.85086;
   closes[69] = 0.85094;
   closes[70] = 0.85099;
   closes[71] = 0.85100;
   closes[72] = 0.85097;
   closes[73] = 0.85089;
   closes[74] = 0.85079;
   closes[75] = 0.85065;
   closes[76] = 0.85049;
   closes[77] = 0.85030;
   closes[78] = 0.85011;
   closes[79] = 0.84991;
}

bool Near(const double left, const double right)
{
   return MathAbs(left - right) <= 1.0e-6;
}

bool DeviationZ(const double &close[], const int count, const int ema_period, const int window,
                double &z[], bool &ok[])
{
   ArrayResize(z, count);
   ClearBool(ok, count);
   if(count <= 0 || ema_period < 2 || window < 2)
      return false;
   double ema[];
   bool ema_ok[];
   if(!LogEma(close, count, ema_period, ema, ema_ok))
      return false;
   double deviation[];
   bool dev_ok[];
   ArrayResize(deviation, count);
   ClearBool(dev_ok, count);
   for(int i = ema_period; i < count; i++)
   {
      if(!ema_ok[i - 1] || close[i] <= 0.0)
         continue;
      deviation[i] = MathLog(close[i]) - ema[i - 1];
      dev_ok[i] = true;
   }
   int start = ema_period + window;
   for(int i = start; i < count; i++)
   {
      if(!dev_ok[i])
         continue;
      double sum = 0.0;
      bool ready = true;
      for(int k = i - window; k < i; k++)
      {
         if(!dev_ok[k])
         {
            ready = false;
            break;
         }
         sum += deviation[k];
      }
      if(!ready)
         continue;
      double mean = sum / window;
      double acc = 0.0;
      for(int k = i - window; k < i; k++)
      {
         double delta = deviation[k] - mean;
         acc += delta * delta;
      }
      double scale = MathSqrt(acc / (window - 1.0));
      if(scale == 0.0)
         continue;
      z[i] = (deviation[i] - mean) / scale;
      ok[i] = true;
   }
   return true;
}

bool LogEma(const double &close[], const int count, const int period, double &ema[], bool &ok[])
{
   ArrayResize(ema, count);
   ClearBool(ok, count);
   if(count == 0 || period < 2 || close[0] <= 0.0)
      return false;
   double alpha = 2.0 / (period + 1.0);
   ema[0] = MathLog(close[0]);
   ok[0] = true;
   for(int i = 1; i < count; i++)
   {
      if(close[i] <= 0.0 || !ok[i - 1])
         continue;
      ema[i] = alpha * MathLog(close[i]) + (1.0 - alpha) * ema[i - 1];
      ok[i] = true;
   }
   return true;
}

bool ReturnZ(const double &close[], const int count, const int window, double &log_return[], bool &ret_ok[],
             double &z[], bool &ok[])
{
   ArrayResize(log_return, count);
   ArrayResize(z, count);
   ClearBool(ret_ok, count);
   ClearBool(ok, count);
   if(count < 2 || window < 2)
      return false;
   for(int i = 1; i < count; i++)
   {
      if(close[i] <= 0.0 || close[i - 1] <= 0.0)
         continue;
      log_return[i] = MathLog(close[i]) - MathLog(close[i - 1]);
      ret_ok[i] = true;
   }
   int start = 1 + window;
   for(int i = start; i < count; i++)
   {
      if(!ret_ok[i])
         continue;
      double sum = 0.0;
      bool ready = true;
      for(int k = i - window; k < i; k++)
      {
         if(!ret_ok[k])
         {
            ready = false;
            break;
         }
         sum += log_return[k];
      }
      if(!ready)
         continue;
      double mean = sum / window;
      double acc = 0.0;
      for(int k = i - window; k < i; k++)
      {
         double delta = log_return[k] - mean;
         acc += delta * delta;
      }
      double scale = MathSqrt(acc / (window - 1.0));
      if(scale == 0.0)
         continue;
      z[i] = (log_return[i] - mean) / scale;
      ok[i] = true;
   }
   return true;
}

bool WilderAtr(const double &high[], const double &low[], const double &close[], const int count,
               const int period, double &atr[], bool &ok[])
{
   ArrayResize(atr, count);
   ClearBool(ok, count);
   if(period < 2 || count <= period)
      return false;
   double tr[];
   bool tr_ok[];
   TrueRange(high, low, close, count, tr, tr_ok);
   double seed = 0.0;
   for(int i = 1; i <= period; i++)
   {
      if(!tr_ok[i])
         return false;
      seed += tr[i];
   }
   atr[period] = seed / period;
   ok[period] = true;
   for(int i = period + 1; i < count; i++)
   {
      if(!tr_ok[i] || !ok[i - 1])
         continue;
      atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period;
      ok[i] = true;
   }
   return true;
}

void TrueRange(const double &high[], const double &low[], const double &close[], const int count,
               double &tr[], bool &ok[])
{
   ArrayResize(tr, count);
   ClearBool(ok, count);
   if(count == 0)
      return;
   tr[0] = high[0] - low[0];
   ok[0] = true;
   for(int i = 1; i < count; i++)
   {
      double first = high[i] - low[i];
      double second = MathAbs(high[i] - close[i - 1]);
      double third = MathAbs(low[i] - close[i - 1]);
      tr[i] = MathMax(first, MathMax(second, third));
      ok[i] = true;
   }
}

bool WilderAdx(const double &high[], const double &low[], const double &close[], const int count,
               const int period, double &adx[], bool &ok[])
{
   ArrayResize(adx, count);
   ClearBool(ok, count);
   if(period < 2 || count <= period)
      return false;
   double plus_dm[];
   double minus_dm[];
   bool dm_ok[];
   ArrayResize(plus_dm, count);
   ArrayResize(minus_dm, count);
   ClearBool(dm_ok, count);
   plus_dm[0] = 0.0;
   minus_dm[0] = 0.0;
   dm_ok[0] = true;
   for(int i = 1; i < count; i++)
   {
      double up = high[i] - high[i - 1];
      double down = low[i - 1] - low[i];
      plus_dm[i] = (up > down && up > 0.0) ? up : 0.0;
      minus_dm[i] = (down > up && down > 0.0) ? down : 0.0;
      dm_ok[i] = true;
   }
   double tr[];
   bool tr_ok[];
   TrueRange(high, low, close, count, tr, tr_ok);
   double smooth_tr[];
   double smooth_plus[];
   double smooth_minus[];
   bool tr_sum_ok[];
   bool plus_ok[];
   bool minus_ok[];
   if(!WilderSum(tr, tr_ok, count, period, smooth_tr, tr_sum_ok))
      return false;
   if(!WilderSum(plus_dm, dm_ok, count, period, smooth_plus, plus_ok))
      return false;
   if(!WilderSum(minus_dm, dm_ok, count, period, smooth_minus, minus_ok))
      return false;
   double dx[];
   bool dx_ok[];
   ArrayResize(dx, count);
   ClearBool(dx_ok, count);
   for(int i = period; i < count; i++)
   {
      if(!tr_sum_ok[i] || !plus_ok[i] || !minus_ok[i] || smooth_tr[i] == 0.0)
         continue;
      double plus_di = 100.0 * smooth_plus[i] / smooth_tr[i];
      double minus_di = 100.0 * smooth_minus[i] / smooth_tr[i];
      double total = plus_di + minus_di;
      if(total == 0.0)
         continue;
      dx[i] = 100.0 * MathAbs(plus_di - minus_di) / total;
      dx_ok[i] = true;
   }
   int first = 2 * period - 1;
   if(count <= first)
      return true;
   double sum = 0.0;
   for(int i = period; i <= first; i++)
   {
      if(!dx_ok[i])
         return true;
      sum += dx[i];
   }
   adx[first] = sum / period;
   ok[first] = true;
   for(int i = first + 1; i < count; i++)
   {
      if(!dx_ok[i] || !ok[i - 1])
         continue;
      adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period;
      ok[i] = true;
   }
   return true;
}

bool WilderSum(const double &values[], const bool &value_ok[], const int count, const int period,
               double &smooth[], bool &ok[])
{
   ArrayResize(smooth, count);
   ClearBool(ok, count);
   if(count <= period)
      return false;
   double seed = 0.0;
   for(int i = 1; i <= period; i++)
   {
      if(!value_ok[i])
         return false;
      seed += values[i];
   }
   smooth[period] = seed;
   ok[period] = true;
   for(int i = period + 1; i < count; i++)
   {
      if(!value_ok[i] || !ok[i - 1])
         continue;
      smooth[i] = smooth[i - 1] - smooth[i - 1] / period + values[i];
      ok[i] = true;
   }
   return true;
}

bool PriceEma(const double &close[], const int count, const int period, double &ema[], bool &ok[])
{
   ArrayResize(ema, count);
   ClearBool(ok, count);
   if(count == 0 || period < 2 || close[0] <= 0.0)
      return false;
   double alpha = 2.0 / (period + 1.0);
   ema[0] = close[0];
   ok[0] = true;
   for(int i = 1; i < count; i++)
   {
      if(close[i] <= 0.0 || !ok[i - 1])
         continue;
      ema[i] = alpha * close[i] + (1.0 - alpha) * ema[i - 1];
      ok[i] = true;
   }
   return true;
}

void ClearBool(bool &flags[], const int count)
{
   ArrayResize(flags, count);
   for(int i = 0; i < count; i++)
      flags[i] = false;
}

int ClosedM5(MqlRates &rates[])
{
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(_Symbol, PERIOD_M5, 0, InpHistoryBars, rates);
   if(copied < 3)
      return 0;
   int count = copied;
   // drop the forming bar; its close is still in the future
   if(rates[count - 1].time + PeriodSeconds(PERIOD_M5) > TimeCurrent())
      count--;
   return count;
}

void StepClosedBar(const MqlRates &rates[], const int count)
{
   if(!InpServerOffsetConfirmed)
   {
      if(!g_offset_warned)
      {
         Print("M5ZREV entries blocked until InpServerOffsetConfirmed is true");
         g_offset_warned = true;
      }
      return;
   }
   double close[];
   double high[];
   double low[];
   ArrayResize(close, count);
   ArrayResize(high, count);
   ArrayResize(low, count);
   for(int i = 0; i < count; i++)
   {
      close[i] = rates[i].close;
      high[i] = rates[i].high;
      low[i] = rates[i].low;
   }
   double z[];
   bool z_ok[];
   double ema[];
   bool ema_ok[];
   double log_return[];
   bool ret_ok[];
   double z_ret[];
   bool z_ret_ok[];
   double atr[];
   bool atr_ok[];
   if(!DeviationZ(close, count, InpEmaPeriod, InpDevWindow, z, z_ok))
      return;
   LogEma(close, count, InpEmaPeriod, ema, ema_ok);
   ReturnZ(close, count, InpRetWindow, log_return, ret_ok, z_ret, z_ret_ok);
   WilderAtr(high, low, close, count, InpAtrPeriod, atr, atr_ok);
   int index = count - 1;
   datetime closed_open = rates[index].time;
   datetime closed_server = closed_open + PeriodSeconds(PERIOD_M5);
   datetime closed_utc = ServerToUtc(closed_server);
   UpdateTrend(closed_server);
   LoadEvents();
   bool session = InLondon(closed_utc);
   bool blocked = EventBlocked(closed_utc);
   double spread_pips = rates[index].spread * SymbolInfoDouble(_Symbol, SYMBOL_POINT) / InpPip;

   if(g_phase == PHASE_COOLDOWN && closed_open != g_cooldown_marked)
   {
      g_cooldown_left--;
      g_cooldown_marked = closed_open;
      if(g_cooldown_left > 0)
         return;
      g_phase = PHASE_IDLE;
      return;
   }
   if(g_phase == PHASE_OPEN || g_phase == PHASE_PENDING)
      return;
   if(g_phase == PHASE_CONFIRMED)
   {
      if(TimeCurrent() >= g_signal_server + PeriodSeconds(PERIOD_M5))
         g_phase = PHASE_IDLE;
      else
         return;
   }
   if(g_phase == PHASE_ARMED)
   {
      if(closed_open <= g_trigger_open)
         return;
      g_seen_after++;
      if(CanConfirm(z, z_ok, log_return, ret_ok, ema, ema_ok, atr, atr_ok, index, spread_pips, session, blocked))
      {
         g_phase = PHASE_CONFIRMED;
         g_signal_server = closed_server;
         g_frozen_ema = ema[index];
         g_frozen_atr = atr[index];
         g_signal_bid = close[index];
         g_attempted = false;
         return;
      }
      if(g_seen_after >= InpConfirmBars)
         g_phase = PHASE_IDLE;
      else
         return;
   }
   if(g_phase == PHASE_IDLE)
      TryArm(z, z_ok, z_ret, z_ret_ok, index, closed_open, session, blocked);
}

bool CanConfirm(const double &z[], const bool &z_ok[], const double &log_return[], const bool &ret_ok[],
                const double &ema[], const bool &ema_ok[], const double &atr[], const bool &atr_ok[],
                const int index, const double spread_pips, const bool session, const bool blocked)
{
   if(index < 1 || !z_ok[index] || !z_ok[index - 1] || !ret_ok[index] || !ema_ok[index] || !atr_ok[index])
      return false;
   if(!session || blocked || spread_pips > InpMaxSpreadPips || atr[index] <= 0.0)
      return false;
   if(g_direction > 0)
   {
      if(!(z[index] > z[index - 1] && z[index] - z[index - 1] >= InpReversalGap))
         return false;
      if(!(log_return[index] > 0.0))
         return false;
      if(InpUseTrendFilter && (!g_trend_ready || g_block_long))
         return false;
   }
   else if(g_direction < 0)
   {
      if(!(z[index] < z[index - 1] && z[index - 1] - z[index] >= InpReversalGap))
         return false;
      if(!(log_return[index] < 0.0))
         return false;
      if(InpUseTrendFilter && (!g_trend_ready || g_block_short))
         return false;
   }
   else
      return false;
   return true;
}

void TryArm(const double &z[], const bool &z_ok[], const double &z_ret[], const bool &z_ret_ok[],
            const int index, const datetime closed_open, const bool session, const bool blocked)
{
   if(!session || blocked || index < 1 || !z_ok[index] || !z_ok[index - 1])
      return;
   bool long_cross = InpAllowLong && z[index - 1] > -InpZEntry && z[index] <= -InpZEntry;
   bool short_cross = InpAllowShort && z[index - 1] < InpZEntry && z[index] >= InpZEntry;
   if(long_cross == short_cross)
      return;
   int direction = long_cross ? 1 : -1;
   if(InpUseReturnFilter && !ReturnShock(z_ret, z_ret_ok, index, direction))
      return;
   g_phase = PHASE_ARMED;
   g_direction = direction;
   g_trigger_open = closed_open;
   g_seen_after = 0;
   g_attempted = false;
}

bool ReturnShock(const double &z_ret[], const bool &ok[], const int index, const int direction)
{
   int start = index - InpRetLookback;
   if(start < 0)
      start = 0;
   for(int i = start; i <= index; i++)
   {
      if(!ok[i])
         continue;
      if(direction > 0 && z_ret[i] <= -InpRetZ)
         return true;
      if(direction < 0 && z_ret[i] >= InpRetZ)
         return true;
   }
   return false;
}

void UpdateTrend(const datetime m5_close_server)
{
   g_block_long = false;
   g_block_short = false;
   g_trend_ready = false;
   if(!InpUseTrendFilter)
      return;
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(_Symbol, PERIOD_H1, 0, InpHistoryBars, rates);
   if(copied < 3)
      return;
   int count = copied;
   while(count > 0 && rates[count - 1].time + PeriodSeconds(PERIOD_H1) > TimeCurrent())
      count--;
   while(count > 0 && rates[count - 1].time + PeriodSeconds(PERIOD_H1) > m5_close_server)
      count--;
   if(count < 3)
      return;
   double high[];
   double low[];
   double close[];
   ArrayResize(high, count);
   ArrayResize(low, count);
   ArrayResize(close, count);
   for(int i = 0; i < count; i++)
   {
      high[i] = rates[i].high;
      low[i] = rates[i].low;
      close[i] = rates[i].close;
   }
   double ema[];
   bool ema_ok[];
   double adx[];
   bool adx_ok[];
   if(!PriceEma(close, count, InpH1EmaPeriod, ema, ema_ok))
      return;
   WilderAdx(high, low, close, count, InpH1AdxPeriod, adx, adx_ok);
   int index = count - 1;
   int previous = index - InpH1SlopeBars;
   int first = (int)MathMax(InpH1EmaPeriod - 1 + InpH1SlopeBars, 2 * InpH1AdxPeriod - 1);
   if(previous < 0 || index < first || !ema_ok[index] || !ema_ok[previous] || !adx_ok[index])
      return;
   g_trend_ready = true;
   g_block_long = adx[index] > InpH1AdxLevel && ema[index] < ema[previous] && close[index] < ema[index];
   g_block_short = adx[index] > InpH1AdxLevel && ema[index] > ema[previous] && close[index] > ema[index];
}

void TryEntry()
{
   if(g_phase != PHASE_CONFIRMED || g_attempted)
      return;
   if(!InpServerOffsetConfirmed || !CanSend())
      return;
   if(g_signal_server != 0 && g_signal_server == g_consumed_server)
      return;
   if(TimeCurrent() < g_signal_server || TimeCurrent() >= g_signal_server + PeriodSeconds(PERIOD_M5))
   {
      if(TimeCurrent() >= g_signal_server + PeriodSeconds(PERIOD_M5))
         g_phase = PHASE_IDLE;
      return;
   }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick) || tick.bid <= 0.0 || tick.ask < tick.bid)
      return;
   g_attempted = true;
   if(ForeignNetPosition() || !DirectionAllowed(g_direction))
   {
      Print("M5ZREV entry skipped because the symbol already has another position or the side is disabled");
      g_phase = PHASE_IDLE;
      return;
   }
   double spread_pips = (tick.ask - tick.bid) / InpPip;
   if(spread_pips > InpMaxSpreadPips || !InLondon(TimeGMT()) || EventBlocked(TimeGMT()) || !EntriesAllowed())
   {
      Print("M5ZREV entry cancelled spread=", DoubleToString(spread_pips, 2));
      g_phase = PHASE_IDLE;
      return;
   }
   double mid = (tick.bid + tick.ask) / 2.0;
   if(MathAbs(mid - g_signal_bid) > InpMaxJumpAtr * g_frozen_atr)
   {
      Print("M5ZREV entry cancelled on a jump");
      g_phase = PHASE_IDLE;
      return;
   }
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tick_size <= 0.0 || tick_value <= 0.0 || g_frozen_atr <= 0.0)
   {
      Print("M5ZREV tick spec or ATR missing; no order");
      g_phase = PHASE_IDLE;
      return;
   }
   double unit = tick_value / tick_size;
   double slip = InpSlippagePips * InpPip;
   double quoted = (g_direction > 0) ? tick.ask : tick.bid;
   double fill = RoundTick(g_direction > 0 ? quoted + slip : quoted - slip);
   double stop = RoundTick(g_direction > 0 ? fill - InpAtrMult * g_frozen_atr : fill + InpAtrMult * g_frozen_atr);
   double target = RoundTick(MathExp(g_frozen_ema));
   double reward = (g_direction > 0) ? target - fill : fill - target;
   double risk_distance = (g_direction > 0) ? fill - stop : stop - fill;
   if(risk_distance <= 0.0)
   {
      g_phase = PHASE_IDLE;
      return;
   }
   double min_distance = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(risk_distance + 1.0e-12 < min_distance)
   {
      Print("M5ZREV stop is inside the broker minimum");
      g_phase = PHASE_IDLE;
      return;
   }
   double commission_price = (2.0 * InpCommissionPerLotSide) / unit;
   double risk_price = risk_distance + slip + commission_price;
   double net_reward = reward - slip - commission_price;
   if(net_reward <= 0.0 || net_reward / risk_price < InpMinRR)
   {
      Print("M5ZREV reward to risk below the minimum");
      g_phase = PHASE_IDLE;
      return;
   }
   double profit = 0.0;
   ENUM_ORDER_TYPE order_type = (g_direction > 0) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcProfit(order_type, _Symbol, 1.0, fill, stop, profit) || profit >= 0.0)
   {
      Print("M5ZREV OrderCalcProfit failed ", GetLastError());
      g_phase = PHASE_IDLE;
      return;
   }
   double loss_per_lot = MathAbs(profit) + slip * unit + 2.0 * InpCommissionPerLotSide;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double budget = equity * InpRiskFraction;
   double lots = FloorVolume(budget / loss_per_lot);
   while(lots > 0.0 && lots * loss_per_lot > budget + 1.0e-9)
      lots = FloorVolume(lots - SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP));
   if(lots <= 0.0)
   {
      Print("M5ZREV minimum lot exceeds the risk budget");
      g_phase = PHASE_IDLE;
      return;
   }
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);
   request.action = TRADE_ACTION_DEAL;
   request.symbol = _Symbol;
   request.magic = InpMagic;
   request.comment = InpComment;
   request.volume = lots;
   request.type = order_type;
   request.price = quoted;
   request.sl = stop;
   request.tp = target;
   request.deviation = InpDeviationPoints;
   request.type_filling = Filling();
   request.type_time = ORDER_TIME_GTC;
   g_phase = PHASE_PENDING;
   g_pending_gmt = TimeGMT();
   g_stop = stop;
   g_target = target;
   g_consumed_server = g_signal_server;
   if(!OrderSend(request, result))
      Print("M5ZREV OrderSend returned false retcode=", result.retcode);
   ulong ticket;
   if(OurPosition(ticket))
   {
      Adopt(ticket);
      return;
   }
   if(IsReject(result.retcode))
   {
      Print("M5ZREV rejected ", result.retcode);
      g_phase = PHASE_IDLE;
      SaveState();
      return;
   }
   g_phase = PHASE_PENDING;
   Print("M5ZREV waiting for a position retcode=", result.retcode);
   SaveState();
}

void MaybeTimeExit()
{
   if(g_phase != PHASE_OPEN || g_entry_gmt <= 0)
      return;
   if(TimeGMT() < g_entry_gmt + InpMaxHoldMinutes * 60)
      return;
   if(!CanSend())
      return;
   ulong ticket;
   if(!OurPosition(ticket) || !PositionSelectByTicket(ticket))
      return;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick) || tick.bid <= 0.0 || tick.ask < tick.bid)
   {
      Print("M5ZREV time exit is waiting for a quote");
      return;
   }
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);
   request.action = TRADE_ACTION_DEAL;
   request.position = ticket;
   request.symbol = _Symbol;
   request.magic = InpMagic;
   request.comment = InpComment;
   request.volume = PositionGetDouble(POSITION_VOLUME);
   long side = PositionGetInteger(POSITION_TYPE);
   request.type = (side == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   request.price = (request.type == ORDER_TYPE_SELL) ? tick.bid : tick.ask;
   request.deviation = InpDeviationPoints;
   request.type_filling = Filling();
   request.type_time = ORDER_TIME_GTC;
   if(!OrderSend(request, result))
   {
      Print("M5ZREV time exit not confirmed retcode=", result.retcode);
      return;
   }
   Print("M5ZREV time exit sent retcode=", result.retcode);
}

void NoteFlat()
{
   if(g_flat_since == 0)
      g_flat_since = TimeGMT();
   double pnl = 0.0;
   bool known = PositionPnl(g_position_id, pnl);
   if(!known && TimeGMT() - g_flat_since < 5)
      return;
   if(!known)
      Print("M5ZREV closed position has no deal yet; day pnl was not updated");
   RollDay(TimeGMT());
   if(known)
      g_day_pnl += pnl;
   g_position_id = 0;
   g_entry_gmt = 0;
   g_flat_since = 0;
   g_phase = PHASE_COOLDOWN;
   g_cooldown_left = InpCooldownBars;
   g_cooldown_marked = g_last_closed;
   SaveState();
}

bool PositionPnl(const ulong identifier, double &pnl)
{
   pnl = 0.0;
   if(identifier == 0 || !HistorySelectByPosition(identifier))
      return false;
   bool saw_out = false;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0 || HistoryDealGetInteger(deal, DEAL_MAGIC) != InpMagic)
         continue;
      pnl += HistoryDealGetDouble(deal, DEAL_PROFIT);
      pnl += HistoryDealGetDouble(deal, DEAL_COMMISSION);
      pnl += HistoryDealGetDouble(deal, DEAL_SWAP);
      long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
      if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_INOUT || entry == DEAL_ENTRY_OUT_BY)
         saw_out = true;
   }
   return saw_out;
}

void ExpirePending()
{
   if(g_phase != PHASE_PENDING)
      return;
   ulong ticket;
   if(OurPosition(ticket) || WorkingOrder())
      return;
   if(g_pending_gmt == 0)
      g_pending_gmt = TimeGMT();
   if(TimeGMT() - g_pending_gmt < 60)
      return;
   Print("M5ZREV pending expired without a position; the signal is consumed");
   g_consumed_server = g_signal_server;
   g_phase = PHASE_IDLE;
   SaveState();
}

bool WorkingOrder()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0)
         continue;
      if(OrderGetString(ORDER_SYMBOL) == _Symbol && OrderGetInteger(ORDER_MAGIC) == InpMagic)
         return true;
   }
   return false;
}

void Adopt(const ulong ticket)
{
   if(!PositionSelectByTicket(ticket))
      return;
   if((ulong)PositionGetInteger(POSITION_MAGIC) != (ulong)InpMagic)
      return;
   if(PositionGetString(POSITION_SYMBOL) != _Symbol)
      return;
   g_position_id = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
   if(g_entry_gmt == 0)
   {
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
      g_entry_gmt = opened - OffsetSeconds();
   }
   if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
      g_direction = 1;
   else
      g_direction = -1;
   MarkOpen();
}

void MarkOpen()
{
   if(g_phase == PHASE_OPEN)
      return;
   g_day_trades++;
   g_phase = PHASE_OPEN;
   if(g_entry_gmt == 0)
      g_entry_gmt = TimeGMT();
   SaveState();
}

void Restore()
{
   ulong ticket;
   if(OurPosition(ticket))
   {
      Adopt(ticket);
      return;
   }
   if(g_phase == PHASE_OPEN)
   {
      g_phase = PHASE_COOLDOWN;
      if(g_cooldown_left <= 0)
         g_cooldown_left = InpCooldownBars;
      g_cooldown_marked = g_last_closed;
      return;
   }
   if(g_phase == PHASE_PENDING)
      return;
   if(g_phase == PHASE_CONFIRMED)
   {
      if(g_signal_server == 0 || TimeCurrent() >= g_signal_server + PeriodSeconds(PERIOD_M5))
         g_phase = PHASE_IDLE;
   }
}

bool OurPosition(ulong &ticket)
{
   ticket = 0;
   long mode = AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   if(mode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong candidate = PositionGetTicket(i);
         if(candidate == 0 || !PositionSelectByTicket(candidate))
            continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol)
            continue;
         if((ulong)PositionGetInteger(POSITION_MAGIC) != (ulong)InpMagic)
            continue;
         ticket = candidate;
         return true;
      }
      return false;
   }
   if(mode != ACCOUNT_MARGIN_MODE_RETAIL_NETTING)
      return false;
   if(!PositionSelect(_Symbol))
      return false;
   if((ulong)PositionGetInteger(POSITION_MAGIC) != (ulong)InpMagic)
      return false;
   ticket = (ulong)PositionGetInteger(POSITION_TICKET);
   return ticket != 0;
}

bool ForeignNetPosition()
{
   long mode = AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   if(mode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
      return false;
   if(mode != ACCOUNT_MARGIN_MODE_RETAIL_NETTING)
      return true;
   if(!PositionSelect(_Symbol))
      return false;
   return (ulong)PositionGetInteger(POSITION_MAGIC) != (ulong)InpMagic;
}

bool DirectionAllowed(const int direction)
{
   long mode = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE);
   if(mode == SYMBOL_TRADE_MODE_DISABLED || mode == SYMBOL_TRADE_MODE_CLOSEONLY)
      return false;
   if(direction > 0 && mode == SYMBOL_TRADE_MODE_SHORTONLY)
      return false;
   if(direction < 0 && mode == SYMBOL_TRADE_MODE_LONGONLY)
      return false;
   return true;
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

bool IsReject(const uint code)
{
   return code == 10006 || code == 10013 || code == 10014 || code == 10015 || code == 10016
          || code == 10017 || code == 10018 || code == 10019 || code == 10030;
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

double RoundTick(const double price)
{
   double tick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   if(tick <= 0.0)
      return NormalizeDouble(price, digits);
   return NormalizeDouble(MathRound(price / tick) * tick, digits);
}

double FloorVolume(const double raw)
{
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(raw < vmin || step <= 0.0)
      return 0.0;
   double steps = MathFloor((raw + 1.0e-12) / step);
   double lots = NormalizeDouble(steps * step, 8);
   if(lots > vmax)
      lots = NormalizeDouble(MathFloor((vmax + 1.0e-12) / step) * step, 8);
   if(lots < vmin)
      return 0.0;
   return lots;
}

bool EntriesAllowed()
{
   RollDay(TimeGMT());
   if(g_day_trades >= InpMaxTradesPerDay)
      return false;
   if(g_day_start <= 0.0)
      return false;
   return g_day_pnl > -InpDailyLossFraction * g_day_start;
}

void RollDay(const datetime utc)
{
   int key = LondonDayKey(utc);
   if(key == g_day_key)
      return;
   g_day_key = key;
   g_day_start = AccountInfoDouble(ACCOUNT_EQUITY);
   g_day_pnl = 0.0;
   g_day_trades = 0;
}

int LondonDayKey(const datetime utc)
{
   datetime local = utc + (LondonIsBst(utc) ? 3600 : 0);
   MqlDateTime parts;
   TimeToStruct(local, parts);
   return parts.year * 10000 + parts.mon * 100 + parts.day;
}

bool InLondon(const datetime utc)
{
   datetime local = utc + (LondonIsBst(utc) ? 3600 : 0);
   MqlDateTime parts;
   TimeToStruct(local, parts);
   if(parts.day_of_week == 0 || parts.day_of_week == 6)
      return false;
   int minute = parts.hour * 60 + parts.min;
   return minute >= InpSessionStart && minute < InpSessionEnd;
}

bool LondonIsBst(const datetime utc)
{
   MqlDateTime parts;
   TimeToStruct(utc, parts);
   datetime start = LastSundayUtc(parts.year, 3);
   datetime end = LastSundayUtc(parts.year, 10);
   return utc >= start && utc < end;
}

datetime LastSundayUtc(const int year, const int month)
{
   int day = DaysInMonth(year, month);
   while(Weekday(year, month, day) != 0)
      day--;
   MqlDateTime parts;
   parts.year = year;
   parts.mon = month;
   parts.day = day;
   parts.hour = 1;
   parts.min = 0;
   parts.sec = 0;
   return StructToTime(parts);
}

int Weekday(const int year, const int month, const int day)
{
   MqlDateTime parts;
   parts.year = year;
   parts.mon = month;
   parts.day = day;
   parts.hour = 0;
   parts.min = 0;
   parts.sec = 0;
   datetime stamp = StructToTime(parts);
   MqlDateTime out;
   TimeToStruct(stamp, out);
   return out.day_of_week;
}

int DaysInMonth(const int year, const int month)
{
   if(month == 2)
   {
      bool leap = ((year % 4 == 0) && (year % 100 != 0)) || (year % 400 == 0);
      return leap ? 29 : 28;
   }
   if(month == 4 || month == 6 || month == 9 || month == 11)
      return 30;
   return 31;
}

int OffsetSeconds()
{
   return (int)MathRound(InpServerUtcOffsetHours * 3600.0);
}

datetime ServerToUtc(const datetime server)
{
   return server - OffsetSeconds();
}

void LoadEvents()
{
   ArrayResize(g_events, 0);
   g_event_count = 0;
   g_calendar = "missing";
   if(InpEventFilter == "off")
      return;
   int handle = FileOpen(InpEventFile, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      if(!g_calendar_warned && InpEventFilter == "optional")
      {
         Print("M5ZREV event file missing; optional filter is unverified");
         g_calendar_warned = true;
      }
      return;
   }
   while(!FileIsEnding(handle))
   {
      string line = FileReadString(handle);
      StringReplace(line, "\r", "");
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) == 0 || StringGetCharacter(line, 0) == '#')
         continue;
      int comma = StringFind(line, ",");
      string field = (comma < 0) ? line : StringSubstr(line, 0, comma);
      if(StringGetCharacter(field, 0) < '0' || StringGetCharacter(field, 0) > '9')
         continue;
      datetime stamp = (datetime)StringToInteger(field);
      if(stamp <= 0)
         continue;
      g_event_count++;
      ArrayResize(g_events, g_event_count);
      g_events[g_event_count - 1] = stamp;
   }
   FileClose(handle);
   SortEvents();
   if(g_event_count == 0)
      return;
   datetime latest = g_events[g_event_count - 1];
   if(latest < TimeGMT() - (datetime)InpCalendarMaxAgeDays * 86400)
      g_calendar = "stale";
   else
      g_calendar = "current";
}

void SortEvents()
{
   for(int i = 0; i < g_event_count; i++)
   {
      for(int j = i + 1; j < g_event_count; j++)
      {
         if(g_events[j] < g_events[i])
         {
            datetime swap = g_events[i];
            g_events[i] = g_events[j];
            g_events[j] = swap;
         }
      }
   }
}

bool EventBlocked(const datetime utc)
{
   if(InpEventFilter == "off")
      return false;
   if(InpEventFilter == "required" && g_calendar != "current")
      return true;
   int before = InpEventBeforeMinutes * 60;
   int after = InpEventAfterMinutes * 60;
   for(int i = 0; i < g_event_count; i++)
   {
      if(utc >= g_events[i] - before && utc < g_events[i] + after)
         return true;
   }
   return false;
}

void SaveState()
{
   int handle = FileOpen(g_state_file, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("M5ZREV state file was not written ", GetLastError());
      return;
   }
   FileWriteString(handle, "phase=" + IntegerToString(g_phase) + "\n");
   FileWriteString(handle, "direction=" + IntegerToString(g_direction) + "\n");
   FileWriteString(handle, "trigger_open=" + IntegerToString((long)g_trigger_open) + "\n");
   FileWriteString(handle, "seen_after=" + IntegerToString(g_seen_after) + "\n");
   FileWriteString(handle, "signal_server=" + IntegerToString((long)g_signal_server) + "\n");
   FileWriteString(handle, "frozen_ema=" + DoubleToString(g_frozen_ema, 10) + "\n");
   FileWriteString(handle, "frozen_atr=" + DoubleToString(g_frozen_atr, 10) + "\n");
   FileWriteString(handle, "signal_bid=" + DoubleToString(g_signal_bid, 10) + "\n");
   FileWriteString(handle, "entry_gmt=" + IntegerToString((long)g_entry_gmt) + "\n");
   FileWriteString(handle, "cooldown_left=" + IntegerToString(g_cooldown_left) + "\n");
   FileWriteString(handle, "cooldown_marked=" + IntegerToString((long)g_cooldown_marked) + "\n");
   FileWriteString(handle, "day_key=" + IntegerToString(g_day_key) + "\n");
   FileWriteString(handle, "day_pnl=" + DoubleToString(g_day_pnl, 4) + "\n");
   FileWriteString(handle, "day_trades=" + IntegerToString(g_day_trades) + "\n");
   FileWriteString(handle, "day_start=" + DoubleToString(g_day_start, 4) + "\n");
   FileWriteString(handle, "consumed_server=" + IntegerToString((long)g_consumed_server) + "\n");
   FileWriteString(handle, "position_id=" + IntegerToString((long)g_position_id) + "\n");
   FileClose(handle);
}

void LoadState()
{
   int handle = FileOpen(g_state_file, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
      return;
   while(!FileIsEnding(handle))
   {
      string line = FileReadString(handle);
      StringReplace(line, "\r", "");
      int sep = StringFind(line, "=");
      if(sep < 0)
         continue;
      string key = StringSubstr(line, 0, sep);
      string value = StringSubstr(line, sep + 1);
      if(key == "phase")
         g_phase = (int)StringToInteger(value);
      else if(key == "direction")
         g_direction = (int)StringToInteger(value);
      else if(key == "trigger_open")
         g_trigger_open = (datetime)StringToInteger(value);
      else if(key == "seen_after")
         g_seen_after = (int)StringToInteger(value);
      else if(key == "signal_server")
         g_signal_server = (datetime)StringToInteger(value);
      else if(key == "frozen_ema")
         g_frozen_ema = StringToDouble(value);
      else if(key == "frozen_atr")
         g_frozen_atr = StringToDouble(value);
      else if(key == "signal_bid")
         g_signal_bid = StringToDouble(value);
      else if(key == "entry_gmt")
         g_entry_gmt = (datetime)StringToInteger(value);
      else if(key == "cooldown_left")
         g_cooldown_left = (int)StringToInteger(value);
      else if(key == "cooldown_marked")
         g_cooldown_marked = (datetime)StringToInteger(value);
      else if(key == "day_key")
         g_day_key = (int)StringToInteger(value);
      else if(key == "day_pnl")
         g_day_pnl = StringToDouble(value);
      else if(key == "day_trades")
         g_day_trades = (int)StringToInteger(value);
      else if(key == "day_start")
         g_day_start = StringToDouble(value);
      else if(key == "consumed_server")
         g_consumed_server = (datetime)StringToInteger(value);
      else if(key == "position_id")
         g_position_id = (ulong)StringToInteger(value);
   }
   FileClose(handle);
   if(g_phase < PHASE_IDLE || g_phase > PHASE_COOLDOWN)
      g_phase = PHASE_IDLE;
}
