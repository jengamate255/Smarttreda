# Three-month entry strategy backtest — 2026-10-15

No eligible simulated entries; no edge or improvement can be established.

Frozen UTC period: **2026-07-15T00:00:00+00:00 to 2026-10-14T22:35:36+00:00**. Chronological 70/30 holdout starts **2026-09-17T08:36:55+00:00**.

| Symbol | Previous trades | Updated trades | Previous net R | Updated net R | Updated holdout trades |
| --- | ---: | ---: | ---: | ---: | ---: |
| USOIL | 0 | 0 | 0.0 | 0.0 | 0 |
| AUDUSD | 0 | 0 | 0.0 | 0.0 | 0 |
| BTCUSD | 0 | 0 | 0.0 | 0.0 | 0 |
| ETHUSD | 0 | 0 | 0.0 | 0.0 | 0 |
| SPX500 | 0 | 0 | 0.0 | 0.0 | 0 |
| XAUUSD | 0 | 0 | 0.0 | 0.0 | 0 |

## Shared entry funnel

These sequential filters apply to both rule versions before the new structural checks. Counts are scan observations, not independent trades.

| Symbol | Allowed-hour scans | Usable history/ATR/spread | H1/H4 agree | Fresh zone proximity | Entry confirmation | Confirmed H1 pattern |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| USOIL | 918 | 918 | 368 | 1 | 0 | 0 |
| AUDUSD | 922 | 922 | 434 | 0 | 0 | 0 |
| BTCUSD | 1061 | 1061 | 430 | 0 | 0 | 0 |
| ETHUSD | 1061 | 1061 | 586 | 0 | 0 | 0 |
| SPX500 | 920 | 920 | 224 | 0 | 0 | 0 |
| XAUUSD | 922 | 922 | 508 | 0 | 0 | 0 |

Across 5,804 allowed-hour scan observations, all had usable history/ATR/spread. 2,550 had aligned closed H1/H4 trends; only 1 also met fresh-zone proximity, 0 had entry confirmation, and 0 had the required confirmed H1 pattern. These are the shared existing filters: the new structural checks received no real-data candidates. The combined entry policy was effectively inactive in this period.


Aggregate previous / updated trades: **0 / 0**.
Aggregate previous / updated holdout trades: **0 / 0**.

A profit factor is unavailable when there are no completed trades. Zero net R and zero closed drawdown from zero trades do not imply a profitable or safe strategy.

## What was compared

Both branches use the same frozen broker candles, point sizes, allowed UTC hours, closed H1/H4 trend alignment, confirmed H1 pattern, fresh unbroken same-side zone, closed-entry-candle confirmation, configured stop floors and entry drift/RR checks. Ranking trust is neutral in both because historical learned factors are unavailable.

The previous branch ranks confirmed patterns by score and generates TP from configured R. The updated branch filters structural invalidation/target geometry, checks remaining reward against the actual stop, caps TP at pattern target and rechecks structure at simulated fill. The baseline removes only those newly introduced structural checks; it retains all preceding quality checks.

Fills start on the next full future M5 bar consistent with the paper engine. Historical bid/ask spread, adverse stop gaps and configured extra cost in R are applied. Ambiguous stop/target bars are stop-first. Missing historical spreads are forward-filled from earlier observations only; gapped outcome windows are rejected. One open simulated trade and one entry per UTC day per symbol are permitted.

This does not reproduce historical health/news, consensus, learned score adjustments, confluence ranking, correlation/prop/account sizing, or live exit management. It therefore cannot verify the entire updated desk. Existing desk setup blocks remain active; a replay applying the frozen blocked `desk:`/`desk-` prefixes permits zero desk trades regardless of price-rule results.

## Verification

Independent verifier passed: **True**. Frozen history files checked: **18**. Actual simulated trades independently recomputed: **0**.

The verifier separately checks entry/exit prices, spread and R accounting, gap/ambiguous fills, candle availability, event order, overlapping entries, daily duplication, incomplete candles and input hashes. Its 20 synthetic regression tests include incorrect P&L, tampered input and missing-symbol cases, a matched previous/updated structural-rejection case, and exact production/accelerated pivot-count comparisons. With zero actual trades it verifies zero actual fills; synthetic cases do not supply profitability evidence.

Data capture succeeded for all six allowed symbols and 18 M5/H1/H4 files. Exact row counts, periods and SHA-256 hashes are in `manifest.json`. Computed results and independent checks are in `results.json` and `verification.json`.

No orders, notifications, live journal/model writes, risk settings, allowlist edits, or service/task actions were performed. Broker use was read-only history and metadata; replay used the captured cache.

## Reproduce

```powershell
python scripts/backtest_entry_selection.py --start 2026-07-15 --output reports/entry_backtest_3months_20261015 --cached
python scripts/verify_entry_backtest.py reports/entry_backtest_3months_20261015
python scripts/report_entry_backtest.py reports/entry_backtest_3months_20261015
```

Run finished on October 15, 2026, Nairobi time (capture ended 01:35:36 EAT). Start is July 15 00:00 UTC (03:00 EAT). All 20 offline replay regression tests passed in this run. The generic report renderer uses a fixed older title; this delivered report corrects that title and the reproduction paths.
