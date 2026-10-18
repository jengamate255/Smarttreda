# Deriv synthetic markets analysis — 18 October 2026

## Finding
Current directional setups could not be verified. The unauthenticated public WebSocket endpoints ws.derivws.com and ws.binaryws.com both rejected handshakes with HTTP 520. Attaching through MetaTrader5.initialize to the existing Deriv VU terminal returned (-6, Terminal: Authorization failed). No credentialed login or account switch was attempted. No fresh quotes/candles were obtained; no current entry, stop or target is asserted.

## Market mechanics and research implications
- Volatility indices: compare each instrument separately using percentage ATR, spread relative to ATR and closed-bar H4/H1/M15 structure. The volatility number describes the product's volatility design, not probability of profit or current directional strength.
- Boom/Crash: abrupt directional spikes make average-indicator readings and candle-only stop simulations particularly fragile. A quoted average tick interval between spikes is not a countdown. Measure drift and spike exposure separately, including adverse fills through stops.
- Step: evaluate separately from spike products; apparent chart structure alone does not establish a repeatable edge.
- Range Break: a separate candidate for range/breakout research. Deriv explicitly cautions that technical indicators may be unsuitable for synthetic indices other than Range Break; this exception does not prove profitability.
- These are RNG-generated products, not a basis for macroeconomic or news-driven forecasts. Deriv describes synthetic indices as available 24/7 and independent of real-world events.

## Existing project evidence
The 13 October outcome-learning handoff records a chronological replay of 73 trades: 30 wins / 43 losses, net -877.43 in the report's account currency, profit factor 0.710. It records quarantined cohorts Boom 100 Index SELL (8 trades, -468.50, PF 0.160) and Crash 99 Index SELL (8 trades, -660.54, PF 0). These figures are historical evidence from that review, not a new replay or today's performance.

A saved legacy v18_market_age6_20 backtest reports pooled PF 1.201 over 11,009 trades. Its data integrity, leakage, execution costs, selection bias and relevance to the present executor were not independently audited here. It cannot establish a deployable edge or supersede observed losing cohorts. Samples and methods differ, so those PF values are not a like-for-like comparison.

The local consensus report has a six-symbol allowlist (AUDUSD, BTCUSD, ETHUSD, SPX500, USOIL, XAUUSD), no synthetic indices, and only stale desk votes. No fresh engine agreement or synthetic admission is established. This is the board/configuration in this checkout, not verification of every running executor's cached configuration.

## Practical assessment
A useful first comparison is Volatility 25/50/75, Boom/Crash 500/1000 and Step, with separate books. Capture fresh broker bid/ask and closed M15/H1/H4 candles before ranking trend alignment, extension, support/resistance and spread cost. These are proposed research candidates, not buy recommendations.

Require chronological held-out evidence and forward paper results net of spread, slippage and fees. For Boom/Crash use tick-aware exits and adverse stop-gap fills. Keep existing quarantine and admission rules intact.

## Sources
- https://deriv.com/markets/derived-indices/synthetic-indices?tab=cfds
- https://deriv.com/derived-indices/crash-boom-indices
- https://traders-academy.deriv.com/trading-guides/guide-to-understanding-volatility-indices
- Local AGENTS.md, Outcome learning (2026-10-13).
- Local mt5_mcp/backtest/results/deriv_synth/v18_market_age6_20.json (unaudited legacy summary).

No orders, notifications, settings edits, account switching, service restarts or scheduled-task changes were made. Only analysis scripts/report artifacts and the handoff note were written.
