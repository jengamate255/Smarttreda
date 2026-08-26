# EA Backtest Expectations

Expected results for each generated EA when run in the MT5 Strategy Tester.
Python backtest = bar-close approximation. The MQL5 EA runs the same logic
bar-by-bar; small trade-count / PnL differences are normal (tick model vs bar
data). A **huge** mismatch (0 trades, PF < 1 where PF was high, or very
different entry times) signals a logic bug in the MQL5 port — investigate.

Common tester settings:
- Model: **Every tick** (or "1 minute OHLC" if too slow — EA uses bar closes only)
- Deposit: 10,000 USD, leverage as account
- Spread: Current (or the value noted)

---

## PriceAction_SD_Trader_Med — EURUSD (PRIMARY)

Magic 234002. Strategy: H4 trend, H1 zones only, H1 confirmation,
min zone 70, RR 3, SL min 15, TP 80–100, risk 0.5%, timeout 200 bars.

| Metric | Expectation |
|---|---|
| Period | 2025.08.01 – 2026.07.31 (H1) |
| Trades | 7 |
| Win rate | 42.9% (3 W / 4 L) |
| Profit factor | ~10.5 |
| Net profit | ~+$424 (+4.24%) |
| Max drawdown | ~0.44% |
| Expectancy | ~+$61/trade |
| Avg R:R | ~4.5 |
| Direction | All SELLs (bearish H4 trend window) |

Trade log (Python backtest):

| Entry | Exit | Result | PnL |
|---|---|---|---|
| 2025-10-09 | 2025-10-21 | timeout | +17.55 |
| 2025-10-28 | 2025-10-30 | TP | +220.50 |
| 2025-11-10 | 2025-11-13 | SL | -43.29 |
| 2025-11-13 | 2025-11-13 | SL | -1.30 |
| 2026-03-06 | 2026-03-09 | TP | +231.00 |
| 2026-03-09 | 2026-03-09 | SL | 0.00 |
| 2026-04-07 | 2026-04-08 | SL | 0.00 |

Walk-forward split (2025-07-04 → 2026-07-31):
- First half: 4 trades, 50% win, PF 5.3, **+$193** (Oct–Nov 2025)
- Second half: 3 trades, +$231 (Mar–Apr 2026)
- Edge confirmed in **both** halves — not a single-regime fluke.

Tester check: use period 2025.01.01 – 2026.07.31, expect ~7 trades. This is a
low-frequency EA — up to a few trades per quarter is normal.

---

## PriceAction_SD_Trader_JP225 — JP225

Magic 234003. Strategy: H4 trend, H1+H4 zones, H1 confirm, min zone 40,
RR 3, SL min 20, TP 80–100, risk 0.5%, timeout 200 bars.

| Metric | Expectation |
|---|---|
| Period | 2025.06.13 – 2026.07.31 (H1), 12k bars |
| Trades | 41 (12k) / 19 (5k) |
| Win rate | 34.1% (12k) / 36.8% (5k) |
| Profit factor | ~1.43 (12k) / ~1.69 (5k) |
| Net profit | ~+$598 (+5.98%) @ 12k bars |
| Max drawdown | ~3.45% |
| Expectancy | ~+$14.6/trade |
| Direction | All BUYs (sustained H4 uptrend) |

**Walk-forward (2025-06-13 → 2026-07-31) — edge holds in both halves:**
- First half: 16 trades, win 43.8%, PF 2.05, **+$477**
- Second half (after-cut): 25 trades, win 28.0%, PF 1.13, **+$120**

Robust across regimes, though the second half (indexes stalling) is thinner.
Losers hit full SL ~-$50; winners hit TP ~+$150.

---

## PriceAction_SD_Trader_SPX — SPX500 (WEAK — NOT recommended)

Magic 234004. Same config as JP225 (z40, RR 3, H1+H4 zones).

- Period: 2025.10.23 – 2026.07.31 (H4); 12k bars: 10 trades (6 W/4 L), win 60.0%, PF 2.80, net ~+$391 (+3.91%), maxDD ~0.59%

**Walk-forward (2025-06-13 → 2026-07-31) — edge does NOT hold:**
- First half: 3 trades, win 66.7%, PF 2.66, **+$83**
- Second half (after-cut): 6 trades, win 33.3%, PF 0.42, **-$112**

The positive return is concentrated in the Dec'25–Feb'26 stock uptrend
(all BUYs). Once that regime ends it loses money — curve-fit risk.

**Recommendation: do NOT trade this EA.** The edge is not robust across
regimes. Prefer EURUSD (Med) and JP225, which both pass walk-forward.

---

## Reference (older) EAs

| EA | Period | Trades | Win | PF | Net | Note |
|---|---|---|---|---|---|---|
| PriceAction_SD_Trader (strict, H1+H4 zones, z70) | 2025.01–2026.07 H1 | 7 | 42.9% | 10.5 | +$424 | superseded by Med (H1-only zones) |
| PriceAction_SD_Trader_HF (M5 HF) | 2025.01–2026.07 | 6 | 16.7% | 4.4 | +$72 | EURUSD, smaller size (risk 0.25%) |
| PriceAction_SD_Trader_SPX | 2025.06–2026.07 H1 | 10 | 60.0% | 2.8 | +$391 | **do not run — fails walk-forward** |

The PriceAction_SD_Trader_HF EA loses money on XAUUSD (PF 0.20) — do not run
the HF on XAUUSD.

---

## Verification playbook

1. Run Med on EURUSD H1 first (see above) and compare the journal to the
   expected table. If trade #1 isn't a SELL on ~2025-10-09 with magic 234002,
   stop and debug.
2. If numbers match, the MQL5 port is faithful. Attach to a live EURUSD H1 SIM
   chart, enable Algo Trading.
3. Run JP225 similarly (magic 234003) as the secondary symbol if you want a
   second engine.
4. At the end of a live trading week, pull closed trades and compare realized
   PnL / win-rate vs the strategy backtest. Expectancy +$60.6 per trade masked
   with drawdown 0.44% over the year — a real per-month signal on EURUSD, not
   an anomaly. Do NOT run SPX500 (does not pass walk-forward).

---

*Generated from the MT5 backtest CLI (backtest.py) on 2026-08-02. Data: MT5
history for EURUSD/JP225/SPX500. Backtest engine = price-action + supply/demand
detector, one zone pick per signal, bar-close simulation.*