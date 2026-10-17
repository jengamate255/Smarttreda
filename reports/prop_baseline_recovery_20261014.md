# Prop entry admission and baseline recovery — 2026-10-14

## Cause

`prop_risk.snapshot()` scopes its saved baseline to account, server, reset date and
initial balance. No local `memory/prop_risk_state.json` existed during this review.
The October 13 risk review documented two carried positions and missing reset
equity. No live account query was made to establish the current position count.

Before this change the missing-baseline path requested only the preceding day of
broker deals. `opening_balance()` rejects open overnight positions and unmatched
daily entry/exit volume. After an overnight position closes, its exit still has no
same-day opening volume, so the baseline stays unavailable. `compliance.evaluate()`
turns the zero prop budget into a halted mode and zero effective risk ceiling;
the final order gate rejects new risk. Existing exit and stop-tightening paths do
not need opening admission.

Booked deal cashflows cannot reconstruct an overnight position's reset-time
floating equity. Simply subtracting its closing P&L from current balance and
calling that reset equity would bypass the existing protection. Today's closure
therefore does not by itself permit today's recovery.

## Recovery

1. Let existing position management continue. Recovery does not close positions,
   cancel orders, clear an uncertain admission, or send a trade.
2. A missing baseline waits for zero positions and zero pending entry orders.
3. Refresh / **Recheck baseline evidence** retries the normal prop snapshot.
   Automatic dashboard polling and executor gate snapshots use the same checks.
4. Complete broker history from epoch 0 must include the configured initial funding.
   Deal tickets, chronological position volumes, costs, timestamps and booked
   cashflows must be verifiable. All position volume must close; the complete
   cashflow ledger must reconcile to current balance within one cent. Unsupported
   balance operations, reversal ambiguity, truncated history and cash transfers in
   the current reset window remain blocked.
5. If history proves exposure crossed today's reset, the panel reports **Waiting
   for a flat reset**. Remain flat through a later reset and retry. With a verified
   flat reset, opening balance equals opening equity and is reconstructed as
   `current balance - today's profit - commission - swap - fee` (all costs carry
   their broker signs). Current equity never initializes the baseline.
6. The recovery holds the native admission transaction, rechecks account identity,
   balance and flat exposure, and refuses a reset crossing during collection.
   Successful verification saves an atomic daily baseline with source, verification
   time and history count. Existing verified baselines remain usable for their
   own reset day; the next reset requires fresh verification.

The dashboard separates baseline evidence from entry eligibility. Verified
baseline recovery cannot bypass exhausted daily/overall buffers or other gates.
Missing/stale telemetry does not enable entry. Existing daily loss percentage,
static initial-balance drawdown floor, safety margin, position/pending stop-risk
reservations and all other risk limits remain unchanged.

Full history may be large. Recovery queries it only while flat and missing a
baseline; saved daily baselines avoid repeat history collection. A broker that
does not expose the original funding or a reconcilable full ledger remains paused.

## Validation and deployment

- 105 focused Python tests passed: prop risk, dashboard runtime, performance risk,
  entry quality and compliance. One pre-existing dependency deprecation warning.
- Node dashboard tests passed: pause/recovery rendering, escaped reasons, stale
  telemetry, disabled entry under prop pause (including record-only compliance),
  positive/zero budgets and existing decision/bootstrap/chart checks.
- No live trades, account calls, configuration edits or service/task changes.
  Changes are saved locally; no running process was restarted. Backend deployment
  requires executors/dashboard to load the updated code under the project's normal
  deployment procedure. The deployed code may restore entry eligibility after
  successful verification, but every subsequent entry still requires all gates.

The cached consensus report was also read: six allowed symbols, desk-only votes,
and no confirm/breakout/analysis votes. It is not evidence of current account state.

### Subsequent user-authorized dashboard startup

On October 14 the user requested server startup. The existing prop-dashboard
scheduled task was started and the earlier stalled dashboard pair stopped.
The scheduled instance now listens on 127.0.0.1:8050 (PID 6128, forwarding parent
16708). Root HTML and the updated operations script both returned HTTP 200.
A console diagnostic instance exited after the scheduled instance acquired the
port; only one dashboard listener remains. Startup used the dashboard's normal
MT5 connection path. No trades were submitted, no risk configuration was edited,
and trading executors were not restarted by this startup request.
