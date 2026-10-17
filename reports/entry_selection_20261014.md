# Entry selection improvements — 2026-10-14

The user selected better trade selection and fewer weak entries. This change
repairs inconsistencies in the existing quality policy and adds evidence for
future reviews. It does not establish a profitable strategy or promote a model.

## Findings

- Desk conviction previously included scores from analysts opposing the selected
  direction. A stronger opposing opinion could therefore increase conviction.
- Portfolio selection checked confirmed status and zone freshness but did not
  require the selected pattern and selected zone to agree with the entry side.
  A different zone found by the final gate could mask that inconsistent selection.
- Patterns were ranked by raw score before learned trust was applied to the winner.
  A historically penalized winner could hide a better remaining pattern.
- Pattern confirmation did not establish that executable entry was still between
  structural invalidation and target. The desk used absolute invalidation distance
  and generated a fresh target from a fixed R multiple, even if the pattern's
  own target had already been reached.

## Implemented behavior

Under the existing enabled quality profile:

1. Only confirmed, usable patterns compete. BUY uses executable ask and SELL
   uses bid; the pattern must have matching direction and finite positive
   invalidation/target prices surrounding the entry. Learned trust is applied
   before ranking. Emerging or exhausted candidates cannot rescue the selection.
2. Zone ranking excludes broken and nonfresh zones. Portfolio selection requires
   the selected pattern, selected zone and H1/H4 trends to agree with the entry.
3. Opposing analyst scores contribute no support to the winning direction.
   Existing weights, agreement penalty, health floor and conviction floor remain.
4. The desk uses measured ATR, preserves configured stop floors and wider
   structural invalidations, and caps TP at the usable pattern target. If the
   resulting trade cannot meet the existing reward-to-risk minimum it is skipped.
   Missing measured ATR cannot become a synthetic fallback entry plan.
5. The final shared quality gate independently rechecks pattern geometry using the
   fresh executable market quote or pending entry price. Remaining reward is
   measured against the proposed stop. A generated target beyond the supporting
   pattern target is rejected. Desk comments must match the supporting setup
   family; an unrelated healthy pattern cannot rescue an exhausted selected setup.

No numeric risk/selection thresholds, spread cap, routing, allowlist, model,
settings, .env, settings-guard baseline or known-good copy were changed. These
checks apply only to opening entries; existing exit/protective modification paths
remain intact. Legacy behavior when quality is disabled is retained.

## Evidence and measurement limits

The reproducible offline audit is
`python scripts/review_entry_selection.py --output reports/entry_selection_20261014/audit.json`.
It uses no runtime imports, account queries, notifications or order APIs. SQLite
inputs are opened with `mode=ro` and consistent read transactions.

The saved audit inspected 619 scans and 3,095 top-five observations. It found 39
fixed-pattern direction mismatches, none also recorded as portfolio picks. The
paper database contained 700 candidates with 61 fixed-pattern direction
mismatches and zero qualified candidates. These counts are repeated observations,
not independent trades, fills, or prevented losses. Dynamic triangle/breakout
direction is deliberately not inferred from the pattern's name.

Historical scan rows contain no full analyst snapshots, structural prices,
executable quotes or contemporaneous policy/state. An honest complete
before/after selection or profit replay is therefore unavailable. The prior
October 13 review also found zero promoted strategies and unvalidated model
performance; no thresholds or model were tuned on these observations.

Each future scan now stores a versioned `selection` snapshot containing all scanned
candidates, selected analyst evidence, executable quote, measured plan, selection
floor values and rejection reasons. Eligible lower-ranked correlation candidates
are distinguished from candidates failing evidence checks. The snapshot uses
explicit field allowlists and contains no full candles or account credentials.
It supports later selection review; an actual profit comparison still needs linked
outcomes and prospective validation.

## Validation and rollout state

183 focused tests passed across desk selection, shared entry quality, prop risk,
outcome learning, performance risk, final execution gates and compliance. The
tests use synthetic evidence and isolated state; no broker trades or account
queries were made. Cases cover both directions, ask crossing target, exhausted
or invalidated structure, residual reward, target capping without shrinking stops,
learned-score ranking, opposing-score monotonicity, coherent portfolio selection,
explicit audit reasons, correlation ranking and bounded audit fields.

Changes are saved locally. No services or trading executors were restarted in this
improvement session. Long-running processes need to load the updated code under
the usual deployment procedure; restoring entry eligibility still requires all
existing prop, outcome, quality, consensus and compliance gates.
