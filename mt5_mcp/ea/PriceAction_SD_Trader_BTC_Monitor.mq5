//+------------------------------------------------------------------+
//|                        PriceAction_SD_Trader_BTC_Monitor.mq5     |
//|  MONITOR-ONLY variant for BTCUSD. NEVER opens market positions. |
//|  Runs the same supply/demand + price-action strategy used by     |
//|  the Med EA, but instead of sending orders it tracks a VIRTUAL   |
//|  position + equity so live behaviour can be compared with the    |
//|  Python backtests (which showed no deployable BTC edge).         |
//+------------------------------------------------------------------+
#property copyright "MT5 MCP"
#property version   "1.00"
#property strict
#property description "BTC monitor-only - no market orders"

#include <Trade/Trade.mqh>

input group "=== Strategy ==="
input ENUM_TIMEFRAMES InpTrendTF    = PERIOD_H4;  // Trend timeframe
input ENUM_TIMEFRAMES InpZoneTF1    = PERIOD_H1;  // Zone timeframe 1
input ENUM_TIMEFRAMES InpZoneTF2    = PERIOD_H1;  // Zone timeframe 2
input ENUM_TIMEFRAMES InpConfirmTF  = PERIOD_H1;  // Confirmation timeframe
input int            InpFastEMA     = 50;         // Trend fast EMA
input int            InpSlowEMA     = 200;        // Trend slow EMA
input int            InpSwingLeft   = 5;          // Structure swing left bars
input int            InpSwingRight  = 5;          // Structure swing right bars
input int            InpLookback    = 400;        // Zone detection lookback

input group "=== Zone detection ==="
input int            InpBaseBars    = 4;          // Quiet base bars before impulse
input double         InpImpulseMult = 1.5;        // Impulse body >= mult * ATR
input int            InpATRPeriod   = 14;         // ATR period
input double         InpMinZoneScore= 70.0;       // Minimum zone strength (1-100)

input group "=== Virtual trade management ==="
input double         InpRiskPercent = 0.5;        // Virtual risk % per trade
input double         InpTpMinPips   = 80.0;       // Min TP pips
input double         InpTpMaxPips   = 100.0;      // Max TP pips
input double         InpSlMinPips   = 15.0;       // Min SL pips
input double         InpMinRR       = 3.0;        // Minimum risk:reward
input int            InpMaxHoldBars = 200;        // Timeout close after N bars
input int            InpMagic       = 235001;     // Magic number
input int            InpDeviation   = 20;         // Max deviation points

//--- zone
struct Zone
{
   bool   demand;          // true = demand, false = supply
   double lower;
   double upper;
   double strength;        // 0-100
   double impulseAtr;
   int    touches;
   bool   broken;
   int    formation;
};

//--- virtual position
struct VPos
{
   bool     active;
   int      side;          // +1 long, -1 short
   datetime openTime;
   double   entry;
   double   sl;
   double   tp;
   double   slPips;
   double   tpPips;
};

CTrade  trade;
datetime lastConfirmedBar = 0;
int     emaFastHandle = INVALID_HANDLE;
int     emaSlowHandle = INVALID_HANDLE;
int     atrHandle = INVALID_HANDLE;

VPos    vp;
double  vEquity = 10000.0;
double  vPeak = 10000.0;
double  vMaxDD = 0.0;
int     vSignals = 0;
int     vWins = 0;
double  vGrossWin = 0.0;
double  vGrossLoss = 0.0;

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   emaFastHandle = iMA(_Symbol, InpTrendTF, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   emaSlowHandle = iMA(_Symbol, InpTrendTF, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   atrHandle = iATR(_Symbol, InpZoneTF1, InpATRPeriod);
   if(emaFastHandle == INVALID_HANDLE || emaSlowHandle == INVALID_HANDLE || atrHandle == INVALID_HANDLE)
   {
      Print("Indicator handle creation failed");
      return INIT_FAILED;
   }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviation);
   trade.SetTypeFillingBySymbol(_Symbol);
   vp.active = false;
   vp.side = 0;
   Print("BTC_Monitor started | trend TF ", EnumToString(InpTrendTF),
         " | zones ", EnumToString(InpZoneTF1), "+", EnumToString(InpZoneTF2),
         " | risk ", DoubleToString(InpRiskPercent, 1),
         "% | magic ", InpMagic, " | VIRTUAL ONLY - no orders");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(emaFastHandle != INVALID_HANDLE) IndicatorRelease(emaFastHandle);
   if(emaSlowHandle != INVALID_HANDLE) IndicatorRelease(emaSlowHandle);
   if(atrHandle != INVALID_HANDLE)     IndicatorRelease(atrHandle);

   double winRate = (vSignals > 0) ? 100.0 * vWins / vSignals : 0.0;
   double pf = (vGrossLoss > 0) ? vGrossWin / vGrossLoss
              : ((vGrossWin > 0) ? 9e9 : 0.0);
   Print("BTC_MONITOR final summary | signals=", vSignals,
         " wins=", vWins, " winrate=", DoubleToString(winRate, 1), "%",
         " PF=", DoubleToString(pf, 2),
         " vEquity=", DoubleToString(vEquity, 2),
         " return=", DoubleToString(vEquity - 10000.0, 2),
         " maxDD=", DoubleToString(vMaxDD, 2), "%");
}

//+------------------------------------------------------------------+
//| OnTick - only acts on a new confirmation-TF bar                  |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime barTime = iTime(_Symbol, InpConfirmTF, 0);
   if(barTime == lastConfirmedBar)
      return;
   lastConfirmedBar = barTime;

   //--- manage virtual position first (may close it)
   ManageVirtual();

   //--- only evaluate a new signal if no virtual trade already open
   if(!vp.active)
      EvaluateSignal();

   UpdateChart();
}

//+------------------------------------------------------------------+
//| EMA + market structure trend on the trend timeframe              |
//+------------------------------------------------------------------+
string GetTrend()
{
   int need = MathMax(InpSlowEMA, InpLookback) + InpSwingRight + 20;
   MqlRates rates[];
   int got = CopyRates(_Symbol, InpTrendTF, 0, need, rates);
   if(got < InpSlowEMA + InpSwingRight + 20)
      return "sideways";

   double fastBuf[], slowBuf[];
   if(CopyBuffer(emaFastHandle, 0, 0, need, fastBuf) < InpSlowEMA)
      return "sideways";
   if(CopyBuffer(emaSlowHandle, 0, 0, need, slowBuf) < InpSlowEMA)
      return "sideways";

   int last = got - 1;
   double eFast = fastBuf[last];
   double eSlow = slowBuf[last];

   int hh = 0, hl = 0, lh = 0, ll = 0;
   int start = InpSwingRight + 1;
   int end = got - InpSwingLeft - 1;
   int prevHigh = -1, prevLow = -1;
   for(int i = start; i < end; i++)
   {
      bool isHigh = true, isLow = true;
      for(int k = -InpSwingLeft; k <= InpSwingRight; k++)
      {
         if(k == 0) continue;
         if(rates[i + k].high >= rates[i].high) isHigh = false;
         if(rates[i + k].low  <= rates[i].low)  isLow = false;
      }
      if(isHigh)
      {
         if(prevHigh >= 0)
         {
            if(rates[i].high > rates[prevHigh].high) hh++; else lh++;
         }
         prevHigh = i;
      }
      if(isLow)
      {
         if(prevLow >= 0)
         {
            if(rates[i].low > rates[prevLow].low) hl++; else ll++;
         }
         prevLow = i;
      }
   }

   int up = hh + hl, down = lh + ll;
   if(eFast > eSlow && up >= down && up > 0)
      return "bullish";
   if(eFast < eSlow && down >= up && down > 0)
      return "bearish";
   return "sideways";
}

//+------------------------------------------------------------------+
//| Detect supply/demand zones on a timeframe (port of detect_zones) |
//+------------------------------------------------------------------+
int DetectZones(ENUM_TIMEFRAMES tf, Zone &out[], int maxZones = 50)
{
   ArrayResize(out, 0);
   int need = InpLookback + 60;
   MqlRates rates[];
   int got = CopyRates(_Symbol, tf, 0, need, rates);
   if(got < InpBaseBars + 40)
      return 0;

   int atrTfHandle = iATR(_Symbol, tf, InpATRPeriod);
   if(atrTfHandle == INVALID_HANDLE)
      return 0;
   double atrBuf[];
   if(CopyBuffer(atrTfHandle, 0, 0, got, atrBuf) < got)
   {
      IndicatorRelease(atrTfHandle);
      return 0;
   }
   IndicatorRelease(atrTfHandle);

   struct Candidate
   {
      bool   demand;
      int    formation;
      double lower, upper;
      double impulseAtr;
      double baseSizeAtr;
   };
   Candidate cands[];
   ArrayResize(cands, 0);

   for(int i = InpBaseBars + 1; i < got - 1; i++)
   {
      double body = MathAbs(rates[i].close - rates[i].open);
      double a = atrBuf[i];
      if(a <= 0 || !MathIsValidNumber(a))
         continue;
      if(body < InpImpulseMult * a)
         continue;

      double baseHigh = -DBL_MAX, baseLow = DBL_MAX, baseBodySum = 0.0;
      for(int k = 1; k <= InpBaseBars; k++)
      {
         int j = i - k;
         if(j < 0) { baseHigh = -DBL_MAX; break; }
         baseHigh = MathMax(baseHigh, rates[j].high);
         baseLow  = MathMin(baseLow,  rates[j].low);
         baseBodySum += MathAbs(rates[j].close - rates[j].open);
      }
      if(baseHigh == -DBL_MAX)
         continue;
      double baseRange = baseHigh - baseLow;
      double baseAvgBody = baseBodySum / InpBaseBars;
      if(baseRange > 1.0 * a)
         continue;
      if(baseAvgBody > 0.5 * a)
         continue;

      int size = ArraySize(cands);
      ArrayResize(cands, size + 1);
      cands[size].demand = rates[i].close > rates[i].open;
      cands[size].formation = i;
      cands[size].lower = baseLow;
      cands[size].upper = baseHigh;
      cands[size].impulseAtr = body / a;
      cands[size].baseSizeAtr = baseRange / a;
   }

   //--- bubble sort by formation
   for(int i = 0; i < ArraySize(cands) - 1; i++)
      for(int j = i + 1; j < ArraySize(cands); j++)
         if(cands[i].formation > cands[j].formation)
         {
            Candidate tmp = cands[i]; cands[i] = cands[j]; cands[j] = tmp;
         }

   Zone zonesTmp[];
   ArrayResize(zonesTmp, 0);
   int currentIndex = got;

   for(int c = 0; c < ArraySize(cands); c++)
   {
      bool merged = false;
      int lastIdx = ArraySize(zonesTmp) - 1;
      if(lastIdx >= 0 && zonesTmp[lastIdx].demand == cands[c].demand &&
         cands[c].lower <= zonesTmp[lastIdx].upper)
      {
         zonesTmp[lastIdx].lower = MathMin(zonesTmp[lastIdx].lower, cands[c].lower);
         zonesTmp[lastIdx].upper = MathMax(zonesTmp[lastIdx].upper, cands[c].upper);
         zonesTmp[lastIdx].impulseAtr = MathMax(zonesTmp[lastIdx].impulseAtr, cands[c].impulseAtr);
         merged = true;
      }
      if(!merged)
      {
         Zone z;
         z.demand = cands[c].demand;
         z.lower = cands[c].lower;
         z.upper = cands[c].upper;
         z.impulseAtr = cands[c].impulseAtr;
         z.touches = 0;
         z.broken = false;
         z.formation = cands[c].formation;
         z.strength = 0.0;
         ArrayResize(zonesTmp, ArraySize(zonesTmp) + 1);
         zonesTmp[ArraySize(zonesTmp) - 1] = z;
      }
   }

   for(int zi = 0; zi < ArraySize(zonesTmp); zi++)
   {
      double a0 = atrBuf[zonesTmp[zi].formation];
      if(a0 <= 0) a0 = 1.0;

      int touches = 0;
      bool broken = false;
      double atrAny = 0.0;
      for(int j = zonesTmp[zi].formation + 1; j < currentIndex; j++)
      {
         double a = atrBuf[j];
         if(a > 0) atrAny = a;
         bool overlap = rates[j].low <= zonesTmp[zi].upper && rates[j].high >= zonesTmp[zi].lower;
         bool violated = zonesTmp[zi].demand
                         ? rates[j].close < zonesTmp[zi].lower - 0.25 * (atrAny > 0 ? atrAny : a0)
                         : rates[j].close > zonesTmp[zi].upper + 0.25 * (atrAny > 0 ? atrAny : a0);
         if(violated)
         {
            broken = true;
            break;
         }
         if(overlap)
            touches++;
      }

      double momentum = MathMin(zonesTmp[zi].impulseAtr / 1.5, 1.0);
      double sizeAtr = (zonesTmp[zi].upper - zonesTmp[zi].lower) / (atrAny > 0 ? atrAny : a0);
      double sizeScore = 0.5;
      if(sizeAtr <= 0) sizeScore = 0.5;
      else if(sizeAtr >= 0.15 && sizeAtr <= 0.5) sizeScore = 1.0;
      else if(sizeAtr < 0.15) sizeScore = 0.6 + sizeAtr / 0.15 * 0.4;
      else sizeScore = MathMax(0.3, 1.0 - (sizeAtr - 0.5) / 2.0);

      int ageBars = currentIndex - 1 - zonesTmp[zi].formation;
      double ageScore = MathMax(0.0, 1.0 - ageBars / 300.0);
      double touchScore = 1.0;
      if(touches > 2 && touches <= 4) touchScore = 0.7;
      else if(touches > 4) touchScore = 0.4;

      double strength = 100.0 * (0.35 * momentum + 0.25 * sizeScore +
                                 0.20 * ageScore + 0.20 * touchScore);
      if(broken)
         strength *= 0.3;
      strength = MathMax(1.0, MathMin(100.0, strength));

      zonesTmp[zi].touches = touches;
      zonesTmp[zi].broken = broken;
      zonesTmp[zi].strength = strength;
   }

   //--- sort by strength desc, filter min score
   for(int i = 0; i < ArraySize(zonesTmp) - 1; i++)
      for(int j = i + 1; j < ArraySize(zonesTmp); j++)
         if(zonesTmp[j].strength > zonesTmp[i].strength)
         {
            Zone tmp = zonesTmp[i]; zonesTmp[i] = zonesTmp[j]; zonesTmp[j] = tmp;
         }

   int count = 0;
   for(int i = 0; i < ArraySize(zonesTmp) && count < maxZones; i++)
      if(zonesTmp[i].strength >= InpMinZoneScore)
      {
         ArrayResize(out, count + 1);
         out[count] = zonesTmp[i];
         count++;
      }
   return count;
}

//+------------------------------------------------------------------+
//| Pick the strongest suitable zone price is revisiting             |
//+------------------------------------------------------------------+
bool GetBestZone(string trend, Zone &best, double &bestAtr)
{
   bool wantDemand = (trend == "bullish");
   best.strength = 0.0;
   bestAtr = 0.0;

   MqlRates r1[], r2[];
   bool ok1 = CopyRates(_Symbol, InpZoneTF1, 0, 10, r1) > 0;
   bool ok2 = CopyRates(_Symbol, InpZoneTF2, 0, 10, r2) > 0;
   if(!ok1 && !ok2)
      return false;
   double price = r1[ArraySize(r1) - 1].close;
   if(ok2 && ArraySize(r2) > 0)
      price = r2[ArraySize(r2) - 1].close;

   ENUM_TIMEFRAMES tfs[2] = {InpZoneTF1, InpZoneTF2};
   for(int t = 0; t < 2; t++)
   {
      Zone zones[];
      int n = DetectZones(tfs[t], zones);
      if(n == 0)
         continue;

      int h = iATR(_Symbol, tfs[t], InpATRPeriod);
      double atrBuf[];
      bool haveAtr = h != INVALID_HANDLE &&
                     CopyBuffer(h, 0, 0, 1, atrBuf) > 0;
      if(h != INVALID_HANDLE) IndicatorRelease(h);
      double atrVal = haveAtr ? atrBuf[0] : 1e-6;

      for(int i = 0; i < n; i++)
      {
         Zone cz = zones[i];
         if(cz.demand != wantDemand || cz.broken)
            continue;
         if(price < cz.lower - 0.5 * atrVal || price > cz.upper + 1.5 * atrVal)
            continue;
         double proximity = 1.0;
         if(price > cz.upper + 0.5 * atrVal) proximity = 0.6;
         else if(price > cz.upper) proximity = 0.8;
         double score = cz.strength * proximity;
         if(score > best.strength)
         {
            best = cz;
            best.strength = score;
            bestAtr = atrVal;
         }
      }
   }
   return best.strength > 0.0;
}

//+------------------------------------------------------------------+
//| Confirmation candle on the confirmation timeframe                |
//+------------------------------------------------------------------+
bool HasConfirmation(string trend)
{
   MqlRates rates[];
   int got = CopyRates(_Symbol, InpConfirmTF, 0, 5, rates);
   if(got < 3)
      return false;

   int cur = ArraySize(rates) - 1;
   int prev = cur - 1;

   double body = MathAbs(rates[cur].close - rates[cur].open);
   double total = rates[cur].high - rates[cur].low;
   double upWick = rates[cur].high - MathMax(rates[cur].close, rates[cur].open);
   double loWick = MathMin(rates[cur].close, rates[cur].open) - rates[cur].low;
   double prevBody = MathAbs(rates[prev].close - rates[prev].open);

   bool bullCandle = rates[cur].close > rates[cur].open;
   bool bearCandle = rates[cur].close < rates[cur].open;
   bool prevBull = rates[prev].close > rates[prev].open;
   bool prevBear = rates[prev].close < rates[prev].open;

   if(trend == "bullish")
   {
      if(prevBear && bullCandle && body >= prevBody * 1.1 && body > 0.3 * total &&
         rates[cur].close >= rates[prev].open && rates[cur].open <= rates[prev].close)
         return true;
      if(body <= 0.35 * total && loWick >= 2.0 * body && loWick >= 0.5 * total &&
         loWick > upWick)
         return true;
      if(bullCandle && loWick >= 2.0 * body && upWick <= 0.4 * total)
         return true;
   }
   else
   {
      if(prevBull && bearCandle && body >= prevBody * 1.1 && body > 0.3 * total &&
         rates[cur].close <= rates[prev].open && rates[cur].open >= rates[prev].close)
         return true;
      if(body <= 0.35 * total && upWick >= 2.0 * body && upWick >= 0.5 * total &&
         upWick > loWick)
         return true;
      if(bearCandle && upWick >= 2.0 * body && loWick <= 0.4 * total)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Virtual signal evaluation + open a virtual position              |
//+------------------------------------------------------------------+
void Evaluate()
{
   string trend = GetTrend();
   if(trend != "bullish" && trend != "bearish")
      return;

   Zone zone;
   double zoneAtr = 0.0;
   if(!GetBestZone(trend, zone, zoneAtr))
      return;

   if(!HasConfirmation(trend))
      return;

   double pip = PipSize();
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   double entry = (trend == "bullish") ? tick.ask : tick.bid;
   double slPips = 0.0, tpPips = 0.0;

   if(trend == "bullish")
   {
      double zoneDistPips = MathMax(0.0, (entry - zone.lower) / pip) + 0.2 * zoneAtr / pip;
      slPips = MathMax(InpSlMinPips, zoneDistPips);
   }
   else
   {
      double zoneDistPips = MathMax(0.0, (zone.upper - entry) / pip) + 0.2 * zoneAtr / pip;
      slPips = MathMax(InpSlMinPips, zoneDistPips);
   }
   tpPips = MathMin(InpTpMaxPips, MathMax(InpTpMinPips, (InpTpMinPips + InpTpMaxPips) / 2.0));
   double rr = tpPips / slPips;
   if(rr < InpMinRR)
   {
      tpPips = slPips * InpMinRR;
      if(tpPips > InpTpMaxPips)
      {
         Print("BTC_MONITOR skip: RR 1:", DoubleToString(InpMinRR, 0), " impossible");
         return;
      }
   }

   vp.active = true;
   vp.side = (trend == "bullish") ? 1 : -1;
   vp.entry = entry;
   vp.sl = (vp.side == 1) ? entry - slPips * pip : entry + slPips * pip;
   vp.tp = (vp.side == 1) ? entry + tpPips * pip : entry - tpPips * pip;
   vp.openTime = tick.time;
   vp.slPips = slPips;
   vp.tpPips = tpPips;

   vSignals++;
   Print("BTC_MONITOR VIRTUAL signal #", vSignals, " ", (vp.side == 1 ? "BUY" : "SELL"),
         " entry ", DoubleToString(entry, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)),
         " SL ", DoubleToString(slPips, 1), " pips TP ", DoubleToString(tpPips, 1),
         " pips RR 1:", DoubleToString(rr, 2),
         " | VIRTUAL ONLY - no trade sent");
}

//+------------------------------------------------------------------+
//| Manage the open virtual trade (SL/TP via current bid/ask)        |
//+------------------------------------------------------------------+
void ManageOpenVirtual()
{
   if(!vp.active)
      return;

   datetime now = TimeCurrent();
   if(now - vp.openTime >= InpMaxHoldBars * PeriodSeconds(InpConfirmTF))
   {
      CloseVirtual(0.0, "timeout");
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   double digits = SymbolInfoDouble(_Symbol, SYMBOL_DIGITS);
   double rrGain = InpRiskPercent * (vp.tpPips / vp.slPips);

   if(vp.side == 1)
   {
      if(tick.bid <= vp.sl)      CloseVirtual(-InpRiskPercent, "SL");
      else if(tick.bid >= vp.tp) CloseVirtual(rrGain, "TP");
   }
   else
   {
      if(tick.ask >= vp.sl)      CloseVirtual(-InpRiskPercent, "SL");
      else if(tick.ask <= vp.tp) CloseVirtual(rrGain, "TP");
   }
}

void CloseVirtual(double pnlPct, string reason)
{
   if(!vp.active)
      return;

   if(pnlPct > 0) { vWins++; vGrossWin += pnlPct; }
   else           { vGrossLoss += MathAbs(pnlPct); }

   vEquity = vEquity * (1.0 + pnlPct / 100.0);
   if(vEquity > vPeak) vPeak = vEquity;
   double dd = (vPeak - vEquity) / vPeak * 100.0;
   if(dd > vMaxDD) vMaxDD = dd;

   Print("BTC_MONITOR VIRTUAL close ", reason, " | result ", DoubleToString(pnlPct, 2),
         "% | vEquity ", DoubleToString(vEquity, 2));

   vp.active = false;
   vp.side = 0;

   UpdateChart();
}

//+------------------------------------------------------------------+
//| Chart comment                                                    |
//+------------------------------------------------------------------+
void UpdateChart()
{
   double winRate = (vSignals > 0) ? 100.0 * vWins / vSignals : 0.0;
   double pf = (vGrossLoss > 0) ? vGrossWin / vGrossLoss
               : ((vGrossWin > 0) ? 9e9 : 0.0);
   string info = StringFormat(
      "BTC_MONITOR (virtual only, no orders)\n"
      "signals=%d wins=%d (%.1f%%) PF=%.2f\n"
      "vEquity=%.0f (%.2f) maxDD=%.2f%%\n%s",
      vSignals, vWins, winRate, pf,
      vEquity, vEquity - 10000.0, vMaxDD,
      (vp.active ? "position open" : "flat"));
   Comment(info);
}

//+------------------------------------------------------------------+
//| Pip size for the symbol (0.0001 for 5-digit EURUSD); BTCUSD >99  |
//+------------------------------------------------------------------+
double PipSize()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double pip = point * MathPow(10, MathMax(0, digits - 5));
   return pip;
}
//+------------------------------------------------------------------+