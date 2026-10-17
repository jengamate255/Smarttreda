# Trading edge and prop risk review - 2026-10-13

The completed backtest does **not confirm a deployable trading edge**. No strategy
was promoted, and the existing desk setup blocks remain in force.

## Backtest evidence

`quality_baseline_20261013/atlas_2026-10-13.json` contains 54 tests: six allowlisted
symbols and nine strategy variants, broker history from 2021, historical spread
and 1-point slippage, a chronological 70/30 split, trade-thirds stability and
1,000 bootstrap draws. Verdicts: **0 PROMOTE, 3 PAPER, 51 FAIL**.

| Candidate | Full trades / PF | Holdout trades / PF | Assessment |
| --- | --- | --- | --- |
| SPX500 EMA crossover | 429 / 1.213 | 122 / 1.343 | Promising paper candidate; full PF below 1.25 promotion floor |
| SPX500 BTC trend | 58 / 1.700 | 19 / 0.931 | Holdout lost money |
| AUDUSD BTC trend | 42 / 1.292 | 9 / 3.136 | Sparse holdout and unstable thirds |
| BTCUSD BTC trend | 51 / 1.040 | 17 / 0.583 | Fails holdout |

The canonical cost stress test also erases the apparent edge in the three PAPER
candidates. These are baseline strategy tests, not a replay of the complete live
desk, news, health, consensus, quality filters or active position management.
The separate six-month price-rule replay was stopped before producing results;
it supplies no evidence for promotion. The legacy trend-zone reference PFs were
not independently refreshed by this sweep. Sell-side intrabar fills in the atlas
are approximations, so passing its gates would still require forward validation.

Backtest selection itself introduces overfitting risk; past simulated results
cannot establish future profitability. See the
[backtest overfitting research](https://www.davidhbailey.com/dhbpapers/overfitting.pdf).

## Outcome sizing

Percentages mean account-equity risk at the planned stop, not a fixed lot size.
The most recent 20 fully closed, attributable bot positions on the current broker
account determine the regime. Entry/exit commission, swap and fees are included;
partial positions, reversals, incomplete histories and other accounts cannot
qualify the bot for increased risk.

- Losing, insufficient, unavailable or weak evidence: **0.1% ceiling**.
- Winning: **1% ceiling**, requiring 20 outcomes, positive net P&L, PF >= 1.2,
  and fewer than three consecutive losses.
- Prop, drawdown, consistency, correlation and aggregate exposure limits can
  only lower that budget. Broker volume is rounded down; minimum-size overshoots
  are skipped. The desk's fixed-dollar `.env` budget is overridden by this policy.

The read-only account audit verified original funding of **$10,000** against the
complete broker balance history. At the audit, balance was $9,446.33 and equity
$9,420.87. The latest 20 bot closes netted **-$164.34**, PF **0.572**, with **three
consecutive losses**. The selected regime is therefore 0.1%, approximately $9.42
before additional reductions. Values will change with live account activity.

## Prop protection

The existing internal limits remain **4% daily** and **8% overall**. Overall
protection now includes a static floor of **$9,200** from verified initial funding,
alongside the pre-existing drawdown restrictions. Daily reset is 00:00 platform
time, UTC+3. An additional $10 operational buffer is reserved.

The daily floor uses opening balance/equity evidence, and available risk deducts
floating loss, costs, incremental loss from current marks to existing stops and
pending-order risk. Unbounded positions, missing history or missing contract
metadata prevent further entries. Pending entries must fit the 0.1% regime even
while the bot is winning. Risk-increasing stop/order edits are blocked; tightening
stops, exits and cancellations remain available.

A durable SQLite admission latch coordinates native and raw-watcher entry sends
across processes. An uncertain broker response or process death retains the latch
and requires reconciliation before another entry. Do not clear that record without
checking broker history and outstanding exposure.

Two positions were carried overnight at the audit. Their exact opening equity
was unavailable, so **new entries are paused** until an authoritative daily
baseline is supplied or can be reconstructed on a later flat reset. Existing
position management continues. Do not substitute today's current equity for the
opening baseline.

The profile retains the project's documented 5%-daily FundingPips plan assumption.
[FundingPips Standard rules](https://help.fundingpips.com/hc/en-us/articles/34501809112081-2-Step-Standard)
currently describe a 10% static overall limit and either 3% or 5% daily depending
on the purchased configuration. A different purchased plan/add-on needs its own
profile; the internal 4% cap must not be used for a 3% daily plan. Stops and buffers
cannot guarantee compliance through gaps, slippage, external manual orders or
disconnected services.

## Deployment and verification

Pending final service restart and live verification. Focused tests cover outcome
selection, broker volume steps, daily/overall reservations, concurrent entry
admission, uncertain sends, missing baselines and protective modifications.
