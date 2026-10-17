# Outcome Learning Review - 2026-10-13

The bot now uses completed outcomes to decide when to abstain. These are risk
controls, not a demonstrated increase in predictive accuracy or a guarantee
that the next trade wins.

## Entry Policy

- Evidence is scoped to symbol, setup and direction over the last 30 days.
- At least eight completed, attributable trades are required for a pause.
- Three consecutive losses in that sample trigger a six-hour cooldown.
- Negative net profit and profit factor below 0.8 trigger a 24-hour cooldown.
- Both timers start at the last close, so repeated scans cannot extend a pause.
- Pauses reject new entries even in forward-test log mode. Exits, SL/TP changes
  and cancellations remain available. Failed journal reads abstain on entries.
- Unknown/manual/reconciled setups, unfinished trades, duplicate tickets and
  future outcomes cannot supply strategy evidence. Too little evidence leaves
  existing entry gates in charge; it is not a prediction of profitability.

## Historical Check

Read-only chronological replay of 73 attributable trades in the current
30-day window saw only outcomes closed before each proposed entry. Baseline:
30 wins, 43 losses, net -877.43, profit factor 0.710. The final rules skipped
zero historical entries, so this replay does not show an accuracy or profit
improvement. A preliminary rule pausing after only three losses skipped two
winners and one loser; that version was rejected in favor of a minimum sample.

At inspection, the completed evidence pauses Boom 100 Index SELL
(8 trades, net -468.50, PF 0.160) and Crash 99 Index SELL
(8 trades, net -660.54, PF 0.000), both for boom_crash_spike_drift. Other setups,
symbols and directions are assessed separately. Pause expiry is automatic.

## Model Validation

The ranker previously named in-sample training metrics CV metrics. Evaluation
now uses ordered forward splits and excludes training trades whose outcomes
were unknown at the first test entry. Duplicate training examples are removed.
Model promotion requires at least 20 held-out outcomes, both classes, and
held-out AUC >= 0.55 plus the validated baseline check. Tolerance cannot bypass
this floor. Publications are atomic and inference reloads changed model files.

Read-only evaluation: 622 usable trades, 515 held-out trades, AUC 0.474,
held-out accuracy 0.683. This candidate was not published. The existing model
file remains unchanged; legacy model scores are labelled unvalidated.

The ordering principle follows official
[time-series validation guidance](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
The ranker's implementation also purges outcomes unavailable at test entry.

## Feedback Repairs

Desk setup prefixes are normalized, and pattern factors are scoped by symbol
and cannot boost a net-losing setup. Inactivity no longer lowers entry floors.
Open trades no longer acquire invented close timestamps; journal updates are
committed. Partial exits are not treated as completed positions. Desk closures
record an explicit outcome and net P&L across all deal legs and costs; missing
broker evidence is retried rather than labelled as a flat or losing trade.

Learning refreshes on SQLite/WAL changes. Decisions are available from
`/api/diagnostic/learning` and existing execution/desk logs. No orders were
submitted for validation, and risk limits, spread policy and allowlists were
not retuned.
