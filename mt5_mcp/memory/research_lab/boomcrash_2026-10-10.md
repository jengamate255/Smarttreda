# Boom/Crash Spike+Drift — robust backtest

Generated 2026-10-10T22:03:07.934837+00:00 | 35 days M1 | spread-adjusted (round trip = 1x median bar spread) | grid 1 configs x 20 symbols

## Baseline (live watcher params: thr 2.5 ATR, SL buf 2.0, age 15)

**Verdict: PROMOTE** — n=4213 PF=1.427 WR=54.0% avgR=0.205 net=$19118.97 thirds=[1.37, 1.48, 1.43] holdout=1.473 (n=1264) bootP=1.0

| symbol | verdict | n | PF | WR% | avg R | net $ |
|---|---|---|---|---|---|---|
| BOOM100 | PROMOTE | 249 | 1.554 | 58.6 | 0.217 | 2332.74 |
| BOOM99 | PROMOTE | 243 | 1.47 | 56.0 | 0.182 | 2327.9 |
| BOOM50 | PROMOTE | 240 | 1.589 | 58.3 | 0.246 | 2261.3 |
| CRASH99 | PROMOTE | 228 | 1.412 | 55.3 | 0.179 | 1804.31 |
| CRASH200 | PROMOTE | 230 | 1.27 | 51.7 | 0.116 | 1581.32 |
| CRASH50 | PAPER | 275 | 1.346 | 55.3 | 0.147 | 1544.23 |
| BOOM500 | PROMOTE | 303 | 1.863 | 58.4 | 0.358 | 1263.45 |
| BOOM1000 | PROMOTE | 201 | 1.559 | 50.2 | 0.244 | 1248.8 |
| CRASH600 | PROMOTE | 170 | 1.293 | 50.6 | 0.137 | 1101.99 |
| BOOM900 | PROMOTE | 143 | 1.656 | 52.4 | 0.278 | 641.62 |
| CRASH900 | PAPER | 149 | 1.293 | 46.3 | 0.169 | 576.65 |
| BOOM150 | PROMOTE | 396 | 1.748 | 60.6 | 0.296 | 536.69 |
| CRASH300 | PAPER | 219 | 1.19 | 49.3 | 0.075 | 372.84 |
| BOOM600 | PROMOTE | 165 | 1.359 | 50.3 | 0.154 | 305.78 |
| CRASH500 | PAPER | 301 | 1.271 | 49.8 | 0.163 | 291.04 |
| CRASH1000 | PROMOTE | 205 | 1.285 | 46.3 | 0.19 | 284.24 |
| CRASH150 | PROMOTE | 245 | 1.408 | 55.5 | 0.197 | 206.5 |
| BOOM300 | PROMOTE | 236 | 1.502 | 54.2 | 0.215 | 173.85 |
| CRASH100 | FAIL | 8 | 2.097 | 62.5 | 0.298 | 141.93 |
| BOOM200 | FAIL | 7 | 2.027 | 57.1 | 0.331 | 121.8 |

## $500 account (fixed-fractional, min-lot 0.2 floor, live sizing mirror)

Sizing mirrors trade_watch: budget = equity x risk%, lots floored at
volume_min (0.2) — min-lot risk exceeding 1.5x budget counted as over-risk.
All signals taken (max 2 open concurrent is not simulated).

| profile | risk/trade | final | return | max DD | over-risk trades | worst trade |
|---|---|---|---|---|---|---|
| conservative_0.5pct | 0.5% | $43954.08 | 8690.8% | $1912.54 (4.3%) | 213 | $-215.22 |
| live_mirror_0.8pct | 0.8% | $434084.86 | 86717.0% | $27654.5 (6.4%) | 40 | $-3348.03 |

## Monte Carlo — sequence risk ($500 account, 0.8% risk, 1000 shuffles)

Same trades, random order. Shows how much outcome depends on sequence.

| metric | value |
|---|---|
| median final equity | **$431992.14** |
| 5th percentile (worst case) | $431463.28 |
| 95th percentile (best case) | $434362.83 |
| worst draw | $430862.25 |
| best draw | $435142.59 |
| median max drawdown | 6.2% |
| 95th percentile max DD | 6.4% |
| probability of doubling ($1000+) | **100.0%** |
| probability of halving ($250 or less) | **0.0%** |


## Configs ranked by avg R

