# Three-month Volatility50 pullback research backtest

**Finding: insufficient evidence of a profitable strategy.** One simulated trade in the requested three months; none before18 October. The sole trade occurred in the recent chart period used to select this market and formulate the discretionary idea. It cannot supply independent validation.

## Scope and dates

- Instrument: standard Volatility50 Index, BUY only; other scanned symbols and the full live desk/EA were not backtested.
- Measurement:18 July2026 00:00 to18 October2026 04:00 Nairobi (UTC+3). Forty-five days of extra indicator warmup are excluded from results.
- Chronological70/30 split at 2026-09-20T12:15:00+03:00. Both segments begin flat, with forced segment-end closes. No parameter search/optimization.
- This is one frozen mechanical interpretation of the chart idea. Confirmed higher-low/rejection rules stand in for discretionary supporting structure; the full H1 pattern/fresh-zone/entry-quality gate is not reproduced. Static82.05/82.7273/83.28 levels were not reused across three months; EMA/overhead levels were recomputed causally at each decision.

## Results

| Case | First70% trades | Final30% trades | Total | Net R | Modeled USD | Reference return |
|---|---:|---:|---:|---:|---:|---:|
| Recorded-spread model | 0 | 1 | 1 | +2.00 | +19.05 | +0.190% |
|1.5x spread stress | 0 | 1 | 1 | +1.67 | +15.87 | +0.159% |

USD figures assume an independent10,000USD reference account, fixed maximum10USD planned risk including the assumed extra-cost reserve, current contract metadata and downward volume rounding. They are hypothetical, not actual earnings or an account forecast. Profit factor is undefined with zero losses; a one-trade win rate or zero closed-trade drawdown is not a reliable performance estimate.

## Sole modeled trade

- Signal/entry: 2026-10-18T03:15:00+03:00 (before the proposed plan was written).
- Buy entry: 82.1453; structural stop: 81.9016; calculated target: 82.644900; volume: 39.08 lots.
- Price risk: 9.52380USD plus0.05R assumed extra costs; planned total risk 9.99999USD.
- Base M5 replay target hit in the final bar, recorded conservatively at its04:00 close; +2.00R /+19.05USD.
- Wider spread required a farther target that was not reached before the fixed end; stress case closed at the boundary at about+1.67R.

## Tick sensitivity for the executed candidate

- Retrieved 1,347 ticks covering03:15–04:00. First actual ask 82.1453; same stop and rule-derived target. Target crossed at 2026-10-18T03:57:08.126000+03:00.
- Tick-based result with the same assumed extra-cost reserve: +2.00R /+19.05USD.
- Largest interval between returned quotes: 4.016s. This checks the executed example; it does not prove every tick was delivered or validate all rejected opportunities. Tick fills are counterfactual, not actual broker orders. No overnight swap occurs in this45-minute example; costs still use a reserve rather than measured trading charges.

## Signal funnel

| Stage | Count |
|---|---:|
| Closed M15 decisions | 8848 |
| Decisions skipped while a modeled position was open | 2 |
| H1/H4 trend failures | 7852 |
| M15 trend failures | 315 |
| Rejection/touch failures | 660 |
| Higher-low failures | 5 |
| Broken supporting structure | 6 |
| Complete structural candidates | 8 |
| Insufficient2R room to overhead reference | 7 |
| Executed model trades | 1 |

## Frozen rules

- Closed H1/H4 close>EMA20>EMA50; closed M15 close>EMA20>EMA50.
- Bullish M15 candle closes in its upper third, lower wick at least its body, touching the previous EMA20 within0.10 previous ATR.
- Last two2-left/2-right confirmed M15 swing lows within40 bars rise. Signal must not invalidate latest swing by more than0.25 prior ATR.
- Stop below the lower of signal low and confirmed swing low, with0.25 prior ATR buffer.
- Entry at next M5 open plus strictly earlier positive recorded spread. A2R net target (2.05R price move minus0.05R assumed costs) must fit before nearest above-entry20-bar maximum on M15/H1/H4, capped one tick below.
- Current broker minimum stop/volume and volume step honored; one open position, two entries per Nairobi day,24h maximum holding, no averaging/trailing/stop widening. Full details in rules.json.

## Data and verification

- Four continuous history files, no missing bars or zero-spread bars. Stored hashes, row counts, valid OHLC and timestamps checked. M15/H1/H4 include warmup; M5 includes one extra hour for causal spread lookup.
- M15: 13,168 bars; missing 0; SHA256 8817a0f285766e995111337701099245ca2a4f567631c4880bf6fabc0c11e450.
- H1: 3,292 bars; missing 0; SHA256 fdfca83d0584e96c9242bc2f170c6562cd2e16663127f1298fa11c02dd00c49b.
- H4: 822 bars; missing 0; SHA256 b65a6c00299cf1485ccbf0aca42faff00b2f7dab0bf66c9fc4ada7958783834a.
- M5: 26,556 bars; missing 0; SHA256 e7a6691a5176fb537a2c725a2d7b6178d4119080638327eac92a00cfa93cd233.
- Eleven regression checks passed: stop/target ambiguity, adverse gaps, capped favorable target gaps, boundary settlement, costs/nearest resistance, unavailable targets, pivot confirmation timing, nonrepainting past indicators, zero-trade accounting, full-vs-prefix executed signal, and broker tick-size target rounding.
- Separate verifier passed four histories and two executed trade cases (base/stress), covering saved-data integrity, executed signal chronology/geometry, accounting, sizing and daily/nonoverlap checks. It does not independently prove exhaustive detection completeness.
- A cached replay repeated the same trade counts and numerical outcomes. Strategy parameters were not tuned.

## Limits and interpretation

- The terminal100,000-bar setting truncates full-period M1 history. The initial M1 attempt was rejected and produced no valid strategy result; it is preserved separately. Full-period M5 execution was used rather than silently shortening the window; conservative stop-first handling limits intrabar ambiguity.
- Earlier bar spread is a causal proxy, not a guaranteed executable historical quote. Actual fees/slippage/swap history and historical contract changes were not reconstructed. Costs use an explicitly assumed0.05R reserve.
- A chronological split does not remove instrument/idea selection bias from today’s chart. The only trade occurred this morning; the historical period excluding today produced zero trades. No win-rate/edge claim or live promotion is justified.
- Performance is a counterfactual price-rule test, not full account/portfolio/margin/prop compliance replay. Existing synthetic allowlist restrictions and learning quarantine were untouched.
- No orders, live journal/model/configuration writes, Telegram sends, account switching, service/task changes or changes to the seven-day paper comparison.

## Reproduce

Saved source copies are alongside this report. With the project Python interpreter, run:

```powershell
python scripts/backtest_deriv_ema_pullback.py --cached --output "E:\Project\Mt5 Ea\reports\deriv_v50_pullback_3months_20261018_m5"
python tests/test_deriv_ema_pullback_backtest.py
python scripts/verify_deriv_ema_pullback.py
```

Primary artifacts: rules.json, metadata.json, manifest.json, results.json, verification.json, tick_sensitivity.json, four OHLC parquet files and winning_case_ticks.parquet. The current verifier and optional historical integration test point to this saved evidence directory.

Product limitations: https://deriv.com/markets/derived-indices/synthetic-indices?tab=cfds
