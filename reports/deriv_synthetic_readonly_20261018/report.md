# Deriv synthetic market snapshot

Captured Nairobi: 2026-10-18T03:44:23.989905+03:00

Existing Deriv demo terminal identity verified. Fifteen markets, 300 closed candles each on M15/H1/H4: 45 histories, 13,500 bars. Final capture had no freshness/continuity errors; saved OHLC geometry checked and SHA256 evidence recorded. Initial history reads were incomplete; subsequent reads populated continuous histories. Tick timestamps agreed with UTC within approximately two seconds; no broker clock adjustment applied. Public WebSocket endpoints still returned HTTP 520.

Bias means closed price > EMA20 > EMA50 (UP), the reverse (DOWN), or MIXED. RSI/ATR use Wilder smoothing, period14. Levels are observed twenty-bar extrema, not independently confirmed demand/supply zones. Live quote and closed-bar readings are distinguished.

| Market | Bid / ask | H4 | H1 | M15 | H1 RSI | M15 low–high (20 bars) |
|---|---:|---|---|---|---:|---:|
| Step Index | 7,227.20000 / 7,227.30000 | DOWN | MIXED | MIXED | 50.4 | 7,205.40000–7,228.10000 |
| Boom 1000 Index | 15,367.09520 / 15,368.81770 | DOWN | DOWN | MIXED | 49.8 | 15,315.35200–15,412.84500 |
| Crash 1000 Index | 6,230.02090 / 6,230.56310 | UP | UP | MIXED | 62.0 | 6,242.84600–6,285.33000 |
| Boom 500 Index | 5,537.28000 / 5,537.80600 | DOWN | DOWN | MIXED | 40.3 | 5,485.68900–5,569.66800 |
| Crash 500 Index | 2,980.87500 / 2,981.20100 | DOWN | MIXED | UP | 51.8 | 2,940.89000–2,977.88300 |
| Volatility 10 Index | 4,903.63000 / 4,903.81800 | MIXED | UP | UP | 60.7 | 4,880.16900–4,900.50000 |
| Volatility 25 Index | 2,713.38900 / 2,713.72000 | UP | MIXED | MIXED | 49.3 | 2,694.78600–2,720.52800 |
| Volatility 50 Index | 82.26920 / 82.29220 | UP | UP | UP | 56.5 | 81.11300–82.72730 |
| Volatility 75 Index | 37,587.35000 / 37,602.86000 | DOWN | MIXED | MIXED | 49.8 | 37,385.84000–38,295.24000 |
| Volatility 100 Index | 677.92000 / 678.21000 | UP | UP | MIXED | 55.3 | 668.97000–694.05000 |
| Volatility 10 (1s) Index | 9,302.02000 / 9,302.40000 | DOWN | DOWN | MIXED | 37.8 | 9,273.95000–9,308.91000 |
| Volatility 25 (1s) Index | 866,623.14000 / 866,693.19000 | DOWN | MIXED | UP | 51.1 | 862,539.23000–868,441.78000 |
| Volatility 50 (1s) Index | 186,600.77000 / 186,652.52000 | MIXED | MIXED | DOWN | 44.1 | 186,091.86000–191,958.77000 |
| Volatility 75 (1s) Index | 5,242.85000 / 5,244.36000 | UP | UP | MIXED | 65.5 | 5,219.92000–5,367.34000 |
| Volatility 100 (1s) Index | 1,379.06000 / 1,379.64000 | UP | MIXED | MIXED | 41.6 | 1,342.00000–1,385.42000 |

## Interpretation

- Volatility 50 (standard, not 1s) has the clearest bullish ordering across all three sampled timeframes. H1 RSI56.5/H4 RSI62.3. Bid82.2692, ask82.2922; watch the recent M15 high82.7273 and H1 high83.2818. M15 EMA20 is approximately82.0212, a pullback reference rather than a confirmed support zone. No entry is confirmed by these indicators alone.
- Volatility 100 standard has bullish H1/H4 but mixed M15. Recent M15 range668.97–694.05. Await lower-timeframe confirmation rather than treating higher-timeframe bias as an entry.
- Volatility 75 (1s) has bullish H1/H4 but mixed M15. H4 RSI71.4 and last H4 close2.67ATR above EMA20 show extension; do not infer an imminent reversal from RSI. Recent M15 range5219.92–5367.34.
- Boom500 has bearish H1/H4 but mixed M15. Recent M15 high5569.668 and H1 high5574.841 are nearby upside references; M15 low5485.689 and H1 low5432.824 are downside references. A short would face upward-spike exposure; no rejection/entry is established here.
- Crash1000 closed H1/H4 readings are bullish, but the live bid6230.0209 is already below the previous twenty closed M15 bars low6242.846; the older bullish readings do not establish a current buy. Closed M15 RSI38.6 also shows weakening short-term momentum.
- Volatility75 standard and Step have bearish H4 but mixed H1/M15. Volatility10 (1s) has bearish H1/H4 but mixed M15 and H4 RSI27.9, so momentum is extended downward without a new confirmed entry.
- Crash500 has bullish M15 but mixed H1/bearish H4; Volatility50 (1s) bearish M15 has RSI24.2 with mixed higher timeframes. Neither has full directional alignment.

These are descriptive chart conditions and a research watchlist, not probabilities of success, validated strategies, or execution-approved orders. No full pattern/zone/quality-gate or tick-level strategy replay was performed. Existing synthetic restrictions/quarantines remain unchanged. No orders, notifications, account switching, settings changes or service/task actions.

Deriv product mechanics: https://deriv.com/derived-indices/crash-boom-indices

Evidence: summary.json, 45 saved OHLC JSON files and evidence_sha256.json.
