# Send-everything forward-test review

Review scope: local source changes only. No live service, task, MT5 account,
settings.json, .env, guard baseline, or known-good copy action was performed.
Existing Oct 13 quality, learning, performance-risk and prop-risk changes remain.

## Evidence and findings

- Oct 9 comments requested record-only gates and a later flip back to strict.
  There was no deadline or stop control independent of cached settings.
- Oct 12 atlas: 216 rows, **0 PROMOTE / 10 PAPER / 206 FAIL**. Routing proposals
  all KEEP. Small live-positive samples and selected winning hours do not prove
  prospective edge. This review does not widen symbols, promote models, alter
  routing, relax floors, or retune risk.
- The current tree is newer than the Oct 9 test: strict quality/performance/prop
  policies already override log mode. Keep them. Consensus continues to return
  its raw rule verdict; compliance exposes both rules_allowed and effective
  allowed. Existing consensus defaults-on-config-read-failure behavior remains.
- Old execution_gate logs recorded violations only. Their sent=true was gate
  permission, before the broker call. They cannot establish a complete attempt
  denominator, fills, profit factor, or rule-level profitability.

## Implemented path

- Optional forward_test configuration: test_id, starts_at and expires_at as UTC
  epochs. risk.reject_orders=false only permits log mode inside an identified
  window of at most seven days. Missing/expired/future/invalid bounds enforce.
  Current strict policy overrides still enforce; nothing is activated here.
- memory/forward_test.stop is checked on every rejections_enabled call, even
  with cached settings. It restores existing enforcement; it does not stop
  management, close positions, cancel orders itself, or shut down services.
- Every opening attempt reaching require is now logged, including clean ones.
  Schema 2 includes unique attempt_id, test window/id, setup comment, magic,
  source, symbol/side/volume, UTC timestamp, mode, violations, rules_allowed,
  and send_permitted. sent is unknown for permitted requests, false for blocks.
  Log-mode audit failure rejects the entry. Existing learning/quality vetoes stay.
- Offline report groups by symbol/setup/source/UTC hour/mode/verdict, counts
  attempts and violations, deduplicates attempt IDs, and explicitly separates
  legacy and malformed rows. No journal or broker connection is opened.
- Offline rollback-candidate preserves every other field (including credentials),
  sets risk.reject_orders=true and expires_at=0. Output must be a separate new
  *.candidate.json; it never applies the change or rebaselines the guard.
- Guard refuses unsafe first baselines and known-good copies failing the current
  critical policy, so a historical log-mode backup cannot roll back strict policy.

## Remaining manual steps

Validation: 50 focused window/log-mode/guard/execution tests passed; scoped git
diff whitespace checks passed. Offline Oct 9 inclusive–Oct 13 exclusive review
found 1,812 legacy rows and no schema2 attempt denominator. These are historical
gate events, not 1,812 fills. Tests used temporary files and fake clients.

1. Review/deploy this code deliberately. Running processes keep imported code;
   no restart was performed. Do not disable current quality/prop/performance
   safeguards merely to revive the Oct 9 experiment. The strict guard currently
   pins those policies, so a permissive test also conflicts with its criticals.
2. Before a future separately authorized test, specify the account and test ID,
   UTC start/end (<=7 days), sample target, maximum acceptable loss/drawdown,
   review cadence and early-stop criteria. Prefer paper/demo evidence. Snapshot
   effective executor policy and ensure one independent outcome per position.
3. Run the offline report with explicit UTC epoch bounds; test-id filtering is
   for new schema rows, so omit it when reviewing legacy evidence:

   ```powershell
   $py = 'C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe'
   $since = [DateTimeOffset]::Parse('2026-10-09T00:00:00Z').ToUnixTimeSeconds()
   $until = [DateTimeOffset]::Parse('2026-10-13T00:00:00Z').ToUnixTimeSeconds()
   & $py -m mt5_mcp.forward_test_review report --log mt5_mcp/memory/execution_gate.jsonl --since $since --until $until
   ```

4. Reconcile broker export/journal tickets to attempts on the intended account:
   accepted, rejected, pending, expired, open, fully closed, partial closes and
   duplicates. Gate logs have no broker result/ticket/account link; unmatched
   outcomes remain unknown. Include commission, swap, fees and spread/slippage.
   Report net P&L, PF, expectancy, drawdown and sample counts by the same cohorts,
   including permitted-with-violations vs clean. Do not claim causal benefit or
   count repeated partial exits as independent wins. Upstream rejected signals
   are outside this final-gate denominator. Outcome linking remains manual.
5. After deployment, emergency enforcement can be requested locally by creating
   `mt5_mcp/memory/forward_test.stop`. This takes effect at the next gate call;
   requests already admitted/in-flight cannot be recalled. Leave the marker in
   place until a separately authorized test. Prepare a durable rollback with:

   ```powershell
   & $py -m mt5_mcp.forward_test_review rollback-candidate --settings mt5_mcp/settings.json --output reports/rollback.candidate.json
   ```

   The candidate contains credentials; keep it local and never commit/share it.
   Review its two field changes. Applying it, resolving environment overrides,
   validating/rebaselining a trusted guard copy and reloading/restarting executors
   are manual deployment actions, not performed here. Do not restore an entire
   historical settings file or use git restore over current user changes.
