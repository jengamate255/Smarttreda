# Last ten completed FundingPips trades — October 15, 2026

Broker-verified account 11911448, FundingPips2-SIM; currency USD. Latest ten fully closed positions, sorted by final exit; partial exits are aggregated. Deriv trades are excluded. Capture: 2026-10-14T22:40:02.083684+00:00.

**2 wins / 8 losses; net -78.79 USD; winning trades +135.72; losing trades -214.51; profit factor 0.633.** Average winner 67.86, average loser 26.81. Price P&L -49.67; commission/swap/fees -29.12. Even before these costs this sample lost money.

| Final close (EAT) | Ticket | Symbol / side | Net USD | Recorded cause / observation |
| --- | ---: | --- | ---: | --- |
| 2026-10-14 15:16:40 | 403463489 | GBPJPY SELL | -7.89 | Stop-out; SELL ascending-triangle label; reconstructed H1 bullish / H4 bearish. |
| 2026-10-13 10:16:37 | 406692180 | AUDNZD BUY | -54.17 | Early cut at -0.50 price R; score 78; actual fill RR 1.77. |
| 2026-10-13 06:34:39 | 406680536 | BTCUSD SELL | -47.48 | Early cut at -0.60 price R; score 71.7; reconstructed H1 sideways / H4 bullish against SELL. |
| 2026-10-13 00:00:28 | 406643231 | AUDNZD BUY | -30.57 | Early cut at -0.51 price R; score 70.1; reconstructed H1 sideways; actual fill RR 1.78. |
| 2026-10-12 16:25:13 | 406618147 | BTCUSD BUY | -45.00 | Stop-out at -1.00 price R; score 73.3; reconstructed H1 sideways; actual fill RR 1.78. |
| 2026-10-12 09:54:32 | 403652923 | EURCHF SELL | +68.24 | Winner; partial close plus later desktop-client close, not a TP fill. |
| 2026-10-12 09:29:00 | 403651225 | USDCAD BUY | +67.48 | Winner; confirm_exec relaxed; desktop-client close, not a TP fill. |
| 2026-10-07 04:58:16 | 403369520 | ETHUSD BUY | -11.92 | Stop-out; BUY descending-triangle label; direction mismatch requires original pattern evidence to confirm. |
| 2026-10-06 18:58:22 | 403326859 | BTCUSD BUY | -5.41 | Early cut at -0.53 price R; reconstructed H1/H4 bullish; alignment alone did not prevent loss. |
| 2026-10-05 07:32:27 | 403147846 | ETHUSD BUY | -12.07 | Stop-out at -1.05 price R; exit 1.53 below SL; actual fill RR 1.78. |

## Findings supported by records

1. Four stop-outs total -76.88; four expert closes matched to early-loss-cut events total -137.63. Early cuts occur near -0.5R after the holding threshold. They explain realized exits, not why the entries lacked follow-through. No hold-to-target counterfactual was run, so this review cannot say the early-cut rule caused avoidable losses.
2. The latest four desk entries scored 70.1, 71.7, 73.3 and 78.0, all below today's configured 80 confidence floor. Their net is -177.22. These are logged confluence values also stored as journal confidence, not calibrated probabilities. Historical policy/code snapshots are unavailable: record-only behavior, cached settings or an older executor are plausible explanations, not proven bypass attribution. The Oct 14 forward-test review documents the older record-only experiment.
3. Recomputing current trend logic on closed pre-entry H1 (400 bars) and H4 (250 bars) gives conflicting or sideways trends for GBPJPY SELL, BTCUSD SELL, BTCUSD BUY and one AUDNZD BUY. Several aligned entries also lost. These are reconstructions using current code, not recovered live indicator snapshots, and do not prove what the executor saw.
4. GBPJPY SELL carries an ascending-triangle label; ETHUSD BUY carries a descending-triangle label. These are suspicious direction combinations, not proof that the original detected pattern direction was opposite: those original pattern geometry/status snapshots are missing.
5. Recorded 2R plans often became less than 2R at actual broker fill: AUDNZD entries 1.77/1.78R, BTCUSD BUY 1.78R, ETHUSD BUY 1.78R, before fees. This uses entry-order SL/TP and volume-weighted broker entry. Quote-to-fill differences can include spread, latency and price movement; tick evidence is missing, so do not classify all differences as slippage.
6. Losses were concentrated: BTCUSD three losses -97.89; AUDNZD two losses -84.74; ETHUSD two losses -23.99; GBPJPY one loss -7.89. This is a ten-trade descriptive sample, not statistical proof of a symbol or pattern edge.
7. Journal prices sometimes represent the pre-send quote rather than actual fills; some legacy close events report zero or missing details. The journal also mixes accounts and has incomplete closure rows. Broker position aggregation resolves this sample without modifying the journal. EURCHF partial exits are aggregated; using only its final exit would understate realized P&L.

## Implications

The strongest supported explanation is a low hit rate combined with weak entry selection and inconsistent planned-versus-filled reward/risk. Direction conflicts and marginal entry scores identify validation priorities. Stops and early cuts realized the losses; they do not establish that wider stops or removing cuts would improve results.
Validate deployed enforcement and effective policy snapshots, actual-fill RR, pattern direction, and pre-entry confirmation in an offline replay before considering changes. The recent three-month replay produced zero entries under current stricter rules; it does not validate the older live execution policy or prove current profitability. No trading/config/service changes were made.

## Evidence and limitations

broker_snapshot.json: read-only current-account position legs and historical orders; analysis_evidence.json: linked journal and desk events; entry_context.json and *_entry.parquet: closed pre-entry bar reconstruction. Broker timestamps have UTC+3 encoded; displayed close times are EAT. Net P&L includes commissions, swap and fees on all position legs. Open position IDs were excluded, and entry/exit volumes reconciled.
No original full chart snapshots, news state, historical tick quotes or effective imported executor policy are available. No causal claim about fakeouts, news or spread magnitude is made. Synthetic-account journal sample remains in journal_snapshot.json for transparency but is not combined with FundingPips results. No live journal writes, notifications, orders or service actions.
