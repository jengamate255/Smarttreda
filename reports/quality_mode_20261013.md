# Quality Mode Deployment

## Entry Policy

Quality mode is enabled in production settings. Compliance now rejects violations;
an environment-level record-only flag cannot override quality mode. Existing risk
caps, sessions, symbol restrictions and the six-symbol consensus allowlist were
preserved. Desk setup-family blocks remain active, so desk entries are paper-only.
The new learning process never overrides an explicit compliance block.

All stack entry paths require closed-candle evidence, H1/H4 agreement, a confirmed
H1 setup, matching entry-candle confirmation, and a fresh, unbroken zone. Execution
rechecks current bid/ask, quote freshness, price drift, stop/target geometry and RR.
Permitted drift is 0.25 entry-timeframe ATR; zone distance is at most 0.5 ATR.
Exits, protective-stop changes and cancellations bypass entry-quality checks.

No hard spread cap is imposed: both spread limits are zero, per operating policy.
Existing spread-aware position sizing remains. Actual bid/ask still affects entry
geometry and achievable reward/risk. ML scores are advisory, not a hard entry veto;
no model was promoted and no risk limit was increased.

## Learning and Counterfactual Outcomes

`memory/entry_shadow.db` is separate from the real trade journal. Repeated signals
are deduplicated; one active paper position per symbol/setup/side prevents
overlapping observations from inflating the recovery sample. Rejection reasons
are merged without replacing the original price/evidence snapshot.

Paper entries use the next full future candle open, not an earlier candle's
favorable close. They recheck the same ATR drift bound and configured RR floor.
Bid/ask spread and an assumed additional 0.05R cost are included. Stop gaps use
the adverse opening price. Same-bar stop/target ambiguity is reported as a loss
and excluded from recovery evidence. Missing entry/history is marked missed,
never a fabricated winner. Open paper trades time out after 72 entry bars.

Real losing cohorts with at least eight attributable completed outcomes are
persistently quarantined. A timer alone cannot reopen them. Recovery needs at
least 20 new, qualified, non-overlapping paper outcomes after quarantine began,
PF >=1.2, mean net R >=0.1, and an approximate one-sided mean lower bound above
zero. Old, future-dated, ambiguous and otherwise unqualified outcomes cannot
release a cohort. A fresh losing real outcome can start another quarantine.
An additional real loss during an existing quarantine also resets its evidence
window; paper results collected before that new loss cannot reopen the cohort.
Session/confidence/health failures and stale/unavailable news evidence do not
count toward recovery. Paper evidence never trains on or populates the real journal.

## Data and Operations

The broker's epochs encode UTC+3. History fetches previously ended at UTC now,
leaving the newest candles roughly three hours behind. Fetches now anchor to the
broker tick epoch; quality quote-age checks use an explicit server-offset map.
Read-only verification found M5 bars about 106 seconds behind the quote.
Unknown/mismatched clocks fail closed rather than silently accepting stale data.
Higher-timeframe broker requests now use MT5's native timeframe constants rather
than minute counts. Window durations still use minutes. This avoids unnecessary
large M5 aggregation downloads for supported H1/H4/D1/W1 histories.

Restarted only the existing active Gateway, Trade Desk and Prop Dashboard through
their existing scheduled tasks. Disabled Analysis/Confirm/Watch/Watchdog/Telegram
tasks remain disabled. No test order was sent. Production settings were compared
against a fingerprint excluding only the authorized quality/spread/enforcement
fields; all unrelated settings, including credential fields, were unchanged.
The settings guard's known-good copy and hash were rebaselined for strict mode.
The guard is still run by analysis_agent when that disabled service is resumed.

A transient journal read error during service shutdown cleared on subsequent
inspection. SQLite quick_check returned ok; no WAL/SHM files were removed or
trade rows modified for recovery. Two losing cohorts were persisted as paper-only.

The dashboard exposes sanitized `/api/diagnostic/quality` telemetry and an
expandable Entry quality section visible on desktop and mobile. Paper feed
timestamps are stored in SQLite so the dashboard can observe the desk worker.
Paper replay runs in a non-overlapping background worker, also started before
full scans. Slow W1/D1 trend caches retain their established TTLs; only H1/H4
receive the tighter quality-mode cache interval. Font CSS now loads without
blocking the parser. Service-worker cache is v8.

## Verification and Limits

180 focused Python tests passed across quality, runtime, websocket, learning,
ranker validation, strategy, broker data, compliance, journal, log mode, pipeline
and desk management; one broker test was skipped. Updated quality tests were
rerun after the paper ATR/gap fixes. Operations JS escaping/freshness checks and
syntax/diff checks passed. Live API verified connected=True, enforced quality,
zero hard spread limits, advisory ML and preserved desk-family restrictions.
Live desktop and 390px mobile quality-panel checks passed: enforced mode,
paper-only status, no JS errors and no horizontal overflow. Screenshots:
`output/playwright/quality_desktop_20261013.png` and
`output/playwright/quality_mobile_20261013.png`. The independent paper worker
completed replay with no error; invalid earlier snapshots were marked missed.

This is an abstention and evidence-collection policy, not demonstrated improved
accuracy or profitability. Prospective outcomes have not yet qualified a cohort.
The additional paper cost is an explicit assumption, not measured broker fees;
OHLC replay is approximate, and the statistical lower bound is not a guarantee.
All explicit symbol/setup restrictions remain authoritative after paper recovery.
