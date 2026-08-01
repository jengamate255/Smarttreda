//+------------------------------------------------------------------+
//|                                  PriceAction_SD_Trader.mq5        |
//|  Supply/Demand + Price Action strategy (port of mt5_mcp)         |
//|  Trend: EMA50/200 + market structure on H4                       |
//|  Zones: Supply/Demand on H1 & H4 (drop-base-rally /              |
//|         rally-base-drop, scored 1-100)                           |
//|  Confirmation: engulfing / pin bar / hammer / shooting star      |
//|  Risk: 0.5% per trade, min RR 1:3, TP 80-100 pips                |
//+------------------------------------------------------------------+
#property copyright "MT5 MCP"
#property version   "1.00"
#property strict
#property description "Runs the mt5_mcp price-action strategy in the Strategy Tester"

#include <Trade/Trade.mqh>

input group "=== Strategy (HF: looser, more signals) ==="
input ENUM_TIMEFRAMES InpTrendTF    = PERIOD_H1;  // Trend timeframe
input ENUM_TIMEFRAMES InpZoneTF1    = PERIOD_M15; // Zone timeframe 1
input ENUM_TIMEFRAMES InpZoneTF2    = PERIOD_H1;  // Zone timeframe 2
input ENUM_TIMEFRAMES InpConfirmTF  = PERIOD_M15; // Confirmation timeframe
input int            InpFastEMA     = 20;         // Trend fast EMA
input int            InpSlowEMA     = 100;        // Trend slow EMA
input int            InpSwingLeft   = 3;          // Structure swing left bars
input int            InpSwingRight  = 3;          // Structure swing right bars
input int            InpLookback    = 200;        // Zone detection lookback
input bool           InpSoftTrend   = true;       // EMA-only trend (skip structure gate)

input group "=== Zone detection ==="
input int            InpBaseBars    = 3;          // Quiet base bars before impulse
input double         InpImpulseMult = 1.0;        // Impulse body >= mult * ATR
input int            InpATRPeriod   = 14;         // ATR period
input double         InpMinZoneScore= 35.0;       // Minimum zone strength (1-100)

input group "=== Trade management ==="
input double         InpRiskPercent = 0.25;       // Risk % per trade
input double         InpTpMinPips   = 30.0;       // Min TP pips
input double         InpTpMaxPips   = 50.0;       // Max TP pips
input double         InpSlMinPips   = 15.0;       // Min SL pips
input double         InpMinRR       = 1.5;        // Minimum risk:reward
input int            InpMaxPositions= 2;          // Max concurrent positions
input int            InpMaxHoldBars = 60;         // Timeout close after N bars
input int            InpMagic       = 234001;     // Magic number
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
   int    formation;       // bar index in the detection array
};

CTrade  trade;
datetime lastConfirmedBar = 0;
datetime lastZoneCacheBar = 0;
Zone    zoneCacheH1[];
Zone    zoneCacheH4[];
bool    cacheValid = false;
int     emaFastHandle = INVALID_HANDLE;
int     emaSlowHandle = INVALID_HANDLE;
int     atrHandle = INVALID_HANDLE;

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
   Print("PriceAction_SD_Trader started | trend TF ", EnumToString(InpTrendTF),
         " | zones ", EnumToString(InpZoneTF1), "+", EnumToString(InpZoneTF2),
         " | risk ", DoubleToString(InpRiskPercent, 1), "% | magic ", InpMagic);
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
}

//+------------------------------------------------------------------+
//| OnTick - only acts on a new confirmation-TF bar                  |
//+------------------------------------------------------------------+
void OnTick()
{
   if(InpConfirmTF == 0)
      return;
   datetime barTime = iTime(_Symbol, InpConfirmTF, 0);
   if(barTime == lastConfirmedBar)
      return;
   lastConfirmedBar = barTime;

   //--- 1) trend
   string trend = GetTrend();
   if(trend != "bullish" && trend != "bearish")
      return;

   //--- 2) best zone being revisited
   Zone zone;
   double zoneAtr = 0.0;
   if(!GetBestZone(trend, zone, zoneAtr))
      return;

   //--- 3) confirmation candle (last completed bar on confirm TF)
   if(!HasConfirmation(trend))
      return;

   //--- 4) entry math + sizing + execute (on current bar open)
   PlaceTrade(trend, zone, zoneAtr);

   //--- 5) manage timeout closes
   ManagePositions();
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

   //--- EMA values
   double fastBuf[], slowBuf[];
   if(CopyBuffer(emaFastHandle, 0, 0, need, fastBuf) < InpSlowEMA)
      return "sideways";
   if(CopyBuffer(emaSlowHandle, 0, 0, need, slowBuf) < InpSlowEMA)
      return "sideways";

   int last = got - 1;
   double eFast = fastBuf[last];
   double eSlow = slowBuf[last];

   //--- soft trend: EMA alignment alone decides direction
   if(InpSoftTrend)
   {
      if(eFast > eSlow)
         return "bullish";
      if(eFast < eSlow)
         return "bearish";
      return "sideways";
   }

   //--- market structure: HH/LH/HL/LL from swing points
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

   //--- ATR series on the zone timeframe
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

   //--- impulse + quiet base
   for(int i = InpBaseBars + 1; i < got - 1; i++)
   {
      double body = MathAbs(rates[i].close - rates[i].open);
      double a = atrBuf[i];
      if(a <= 0 || !MathIsValidNumber(a))
         continue;
      if(body < InpImpulseMult * a)
         continue;

      //--- base range and avg body before the impulse
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

   //--- merge overlapping same-type zones (sorted by formation)
   for(int i = 0; i < ArraySize(cands) - 1; i++)
   {
      for(int j = i + 1; j < ArraySize(cands); j++)
      {
         if(cands[i].formation > cands[j].formation)
         {
            Candidate tmp = cands[i]; cands[i] = cands[j]; cands[j] = tmp;
         }
      }
   }

   //--- evaluate + score each zone
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

   //--- score each merged zone
   for(int zi = 0; zi < ArraySize(zonesTmp); zi++)
   {
      double a0 = atrBuf[zonesTmp[zi].formation];
      if(a0 <= 0) a0 = 1.0;

      //--- touches and broken state since formation
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
   {
      for(int j = i + 1; j < ArraySize(zonesTmp); j++)
      {
         if(zonesTmp[j].strength > zonesTmp[i].strength)
         {
            Zone tmp = zonesTmp[i]; zonesTmp[i] = zonesTmp[j]; zonesTmp[j] = tmp;
         }
      }
   }

   int count = 0;
   for(int i = 0; i < ArraySize(zonesTmp) && count < maxZones; i++)
   {
      if(zonesTmp[i].strength >= InpMinZoneScore)
      {
         ArrayResize(out, count + 1);
         out[count] = zonesTmp[i];
         count++;
      }
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

      //--- ATR for proximity checks on this TF
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
         //--- proximity: zone.lower - 0.5*ATR <= price <= zone.upper + 1.5*ATR
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
      //--- bullish engulfing
      if(prevBear && bullCandle && body >= prevBody * 1.1 && body > 0.3 * total &&
         rates[cur].close >= rates[prev].open && rates[cur].open <= rates[prev].close)
         return true;
      //--- pin bar (long lower wick)
      if(body <= 0.35 * total && loWick >= 2.0 * body && loWick >= 0.5 * total &&
         loWick > upWick)
         return true;
      //--- hammer
      if(bullCandle && loWick >= 2.0 * body && upWick <= 0.4 * total)
         return true;
   }
   else // bearish
   {
      //--- bearish engulfing
      if(prevBull && bearCandle && body >= prevBody * 1.1 && body > 0.3 * total &&
         rates[cur].close <= rates[prev].open && rates[cur].open >= rates[prev].close)
         return true;
      //--- pin bar (long upper wick)
      if(body <= 0.35 * total && upWick >= 2.0 * body && upWick >= 0.5 * total &&
         upWick > loWick)
         return true;
      //--- shooting star
      if(bearCandle && upWick >= 2.0 * body && loWick <= 0.4 * total)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Entry math (port of _choose_entry_math) + order send             |
//+------------------------------------------------------------------+
void PlaceTrade(string trend, Zone &zone, double zoneAtr)
{
   double pip = PipSize();
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   double entry = (trend == "bullish") ? tick.ask : tick.bid;
   double sl = 0.0, tp = 0.0, slPips = 0.0, tpPips = 0.0;
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double digits = SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   if(trend == "bullish")
   {
      double zoneDistPips = MathMax(0.0, (entry - zone.lower) / pip) + 0.2 * zoneAtr / pip;
      slPips = MathMax(InpSlMinPips, zoneDistPips);
      tpPips = MathMin(InpTpMaxPips, MathMax(InpTpMinPips, (InpTpMinPips + InpTpMaxPips) / 2.0));
      double rr = tpPips / slPips;
      if(rr < InpMinRR)
      {
         tpPips = slPips * InpMinRR;
         if(tpPips > InpTpMaxPips)
         {
            Print("Skip: RR 1:", DoubleToString(InpMinRR, 0), " impossible");
            return;
         }
      }
      sl = entry - slPips * pip;
      tp = entry + tpPips * pip;
   }
   else
   {
      double zoneDistPips = MathMax(0.0, (zone.upper - entry) / pip) + 0.2 * zoneAtr / pip;
      slPips = MathMax(InpSlMinPips, zoneDistPips);
      tpPips = MathMin(InpTpMaxPips, MathMax(InpTpMinPips, (InpTpMinPips + InpTpMaxPips) / 2.0));
      double rr = tpPips / slPips;
      if(rr < InpMinRR)
      {
         tpPips = slPips * InpMinRR;
         if(tpPips > InpTpMaxPips)
         {
            Print("Skip: RR 1:", DoubleToString(InpMinRR, 0), " impossible");
            return;
         }
      }
      sl = entry + slPips * pip;
      tp = entry - tpPips * pip;
   }

   //--- risk 0.5% sizing
   double riskAmount = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPercent / 100.0;
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double pipValue = tickValue * pip / tickSize;
   double lots = riskAmount / (slPips * pipValue);

   double volMin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double volStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double volMax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   lots = MathFloor(lots / volStep) * volStep;
   if(lots < volMin)
   {
      Print("Skip: lot below min at 0.5% risk");
      return;
   }
   lots = MathMin(lots, volMax);

   //--- respect max concurrent positions on this symbol/magic
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket) &&
         PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic)
         count++;
   }
   if(count >= InpMaxPositions)
      return;

   sl = NormalizeDouble(sl, (int)digits);
   tp = NormalizeDouble(tp, (int)digits);
   bool done = false;
   if(trend == "bullish")
      done = trade.Buy(lots, _Symbol, entry, sl, tp, "SD+PA");
   else
      done = trade.Sell(lots, _Symbol, entry, sl, tp, "SD+PA");

   if(done)
   {
      Print("Entry ", trend, " lots ", DoubleToString(lots, 2),
            " entry ", DoubleToString(entry, (int)digits),
            " SL ", DoubleToString(sl, (int)digits),
            " TP ", DoubleToString(tp, (int)digits),
            " SL ", DoubleToString(slPips, 1), " pips, TP ", DoubleToString(tpPips, 1),
            " pips, RR 1:", DoubleToString(tpPips / slPips, 2));
   }
   else
   {
      Print("Order failed: ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Close positions that exceeded the max hold bars                  |
//+------------------------------------------------------------------+
void ManagePositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol ||
         PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      datetime now = TimeCurrent();
      if(now - openTime >= InpMaxHoldBars * PeriodSeconds(InpConfirmTF))
         trade.PositionClose(ticket);
   }
}

//+------------------------------------------------------------------+
//| Pip size for the symbol (0.0001 for 5-digit EURUSD)              |
//+------------------------------------------------------------------+
double PipSize()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double pip = point * MathPow(10, MathMax(0, digits - 3));
   return pip;
}
//+------------------------------------------------------------------+
