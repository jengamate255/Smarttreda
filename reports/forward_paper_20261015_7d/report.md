# Prospective strategy paper test

UTC window: 2026-10-14T23:00:00.350608+00:00 to 2026-10-21T23:00:00.350608+00:00.
Status: **running**. Latest run: 2026-10-17T22:07:35.969711+00:00.
Reference balance: 9393.87 USD; fixed risk 0.1% per paper entry.

These are independent research accounts, not additive portfolio returns or predicted earnings. No broker orders are sent.
All six families are evaluated on all six allowed symbols; candidate routes are not promoted. Closed bars only; fresh tick entries and bid/ask tick exits.
Strategy signals are tested even when static live eligibility fails. Eligibility reasons are logged. Full desk, news, consensus, prop and portfolio admission are not reproduced.
Base strategy thresholds and low performance-risk budget are frozen; adaptive updates are disabled. Optional W1 advisory is omitted. Source/model changes pause the test. Stops/targets only; no desk early cuts, partials or trailing.
Spread is included via executable bid/ask; additional cost reserve 0.05R per round trip is an assumption, not measured commission/swap. Overnight costs and execution latency can differ.
A missing tick-history request flags an error; an unobserved outage cannot be guaranteed complete by the broker. Results require coverage review.

| Strategy | Closed | Wins | Estimated net | Open | Floating estimate | Paper entries satisfying static screen |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| trend_zone | 0 | 0 | +0.00 | 0 | +0.00 | 0 |
| mean_reversion | 0 | 0 | +0.00 | 0 | +0.00 | 0 |
| momentum_breakout | 2 | 1 | +13.55 | 2 | +2.91 | 0 |
| btc_trend_following | 0 | 0 | +0.00 | 0 | +0.00 | 0 |
| ema_crossover | 1 | 0 | -9.71 | 0 | +0.00 | 0 |
| bb_mr_fade | 2 | 0 | -20.36 | 0 | +0.00 | 0 |

Initial numbers are zero until prospective signals complete. No earnings estimate is established by zero or a few trades.
Each strategy has at most three open paper positions, three entries/day, and one position per symbol. A 4% daily or 8% total equity loss stops new paper entries.
At expiry, open paper positions are marked and reported, not recorded as closed wins/losses. Do not combine strategy books as if the same capital funded them concurrently.

## Recent errors

[
  {
    "utc": "2026-10-17T22:07:35.969711+00:00",
    "family": "ema_crossover",
    "symbol": "SPX500",
    "phase": "scan",
    "error": "Stale/invalid quote"
  },
  {
    "utc": "2026-10-17T22:07:35.969711+00:00",
    "family": "ema_crossover",
    "symbol": "XAUUSD",
    "phase": "scan",
    "error": "Stale/invalid quote"
  },
  {
    "utc": "2026-10-17T22:07:35.969711+00:00",
    "family": "bb_mr_fade",
    "symbol": "USOIL",
    "phase": "scan",
    "error": "Stale/invalid quote"
  },
  {
    "utc": "2026-10-17T22:07:35.969711+00:00",
    "family": "bb_mr_fade",
    "symbol": "AUDUSD",
    "phase": "scan",
    "error": "Stale/invalid quote"
  },
  {
    "utc": "2026-10-17T22:07:35.969711+00:00",
    "family": "bb_mr_fade",
    "symbol": "SPX500",
    "phase": "scan",
    "error": "Stale/invalid quote"
  },
  {
    "utc": "2026-10-17T22:07:35.969711+00:00",
    "family": "bb_mr_fade",
    "symbol": "XAUUSD",
    "phase": "scan",
    "error": "Stale/invalid quote"
  },
  {
    "utc": "2026-10-17T22:07:35.969711+00:00",
    "family": "trend_zone",
    "symbol": "USOIL",
    "phase": "scan",
    "error": "Stale/invalid quote"
  },
  {
    "utc": "2026-10-17T22:07:35.969711+00:00",
    "family": "trend_zone",
    "symbol": "AUDUSD",
    "phase": "scan",
    "error": "Stale/invalid quote"
  },
  {
    "utc": "2026-10-17T22:07:35.969711+00:00",
    "family": "trend_zone",
    "symbol": "SPX500",
    "phase": "scan",
    "error": "Stale/invalid quote"
  },
  {
    "utc": "2026-10-17T22:07:35.969711+00:00",
    "family": "trend_zone",
    "symbol": "XAUUSD",
    "phase": "scan",
    "error": "Stale/invalid quote"
  }
]
