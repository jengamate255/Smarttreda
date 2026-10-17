# Seven-day paper comparison — interim coverage

Generated: 2026-10-17T18:04:04.836190+00:00. Measurement: 2026-10-14T23:00:00.350608+00:00 through 2026-10-17T18:04:04.836190+00:00; scheduled end 2026-10-21T23:00:00.350608+00:00.
Saved status: running; saved run start: 2026-10-17T18:01:46.582093+00:00 (2.3 minutes old).

Estimated monetary results are in USD; frozen additional cost reserve: 0.05R per round trip.

## Caveats

- Interim PAPER research only. Independent strategy books must not be summed as portfolio returns; no edge or earnings forecast is established by this small sample.
- Only measurement observations are counted; initialization, prevalidation and warmup artifacts are excluded. Counts show logged evaluation rows and distinct signal bars, not five-minute scan attempts; the runner normally skips previously seen signal bars. Open-position, daily-cap and equity-stop skips are not logged observations; absent observations do not establish absence of signals.
- Bar snapshots overlap and include lookback history. Unique in-window bars use close time for window membership; reported timestamps are bar OPEN times. Calendar gaps include market closures and are not automatically feed failures. No exchange calendar or expected-session denominator is assumed.
- Tick files exist only for paper-position settlement requests. Zero tick files for a symbol does not imply a failed feed. Tick request unions and quote gaps cannot prove broker completeness or continuous prospective signal collection. Recovery history can settle an open trade but cannot recover missed entries during scanner interruptions.
- Per-strategy bar references identify the runner's initial signal timeframe; archives are shared across families and cannot prove each planner consumed every archived timeframe. Tick coverage is also shared by symbol, not attributable as independent coverage to every strategy.
- The saved error list is truncated to the last 100 on completed runs (partial runs may retain more). Error counts are retained evidence, not lifetime totals. The scan/settlement exception list includes feed failures and may include planner, sizing or conversion failures; exact messages are preserved. Plan errors are reported separately. Saved last_run marks run start, not proof a full scan completed; floating estimates may be stale and are omitted.
- Reads do not lock the active collector. Evidence hashes identify bytes read; files can represent different moments. Concurrent changes and unreadable files are disclosed; this is not a final integrity or settlement audit.
- Broker bar/tick timestamps are normalized by the frozen manifest offset; observation, entry and exit times are already UTC. Times in tables are UTC.
- Research may fail live admission; full desk, news, consensus, prop and portfolio gates are not replayed. Exits use stops/targets only; no early cuts, trailing or partials. Executable bid/ask includes spread; the frozen cost reserve is assumed, not measured future commission/swap.

## Read warnings

- Unreadable archive ticks\ETHUSD_1792269743490_1792270293516.parquet: Could not open Parquet input source '<Buffer>': Parquet file size is 4 bytes, smaller than the minimum file footer (8 bytes)

## By symbol

| Symbol | Observations | Retained errors | Entries | Exits | Open | Tick files | Unique quote triples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| USOIL | 156 | 27 | 1 | 1 | 0 | 3 | 2834 |
| AUDUSD | 138 | 23 | 3 | 2 | 1 | 281 | 68441 |
| BTCUSD | 236 | 0 | 0 | 0 | 0 | 0 | 0 |
| ETHUSD | 226 | 0 | 3 | 2 | 1 | 91 | 491739 |
| SPX500 | 171 | 27 | 0 | 0 | 0 | 0 | 0 |
| XAUUSD | 156 | 27 | 0 | 0 | 0 | 0 | 0 |

## By strategy

| Strategy | Observations | Retained errors | Entries | Exits | Open | Est. closed net |
| --- | --- | --- | --- | --- | --- | --- |
| trend_zone | 208 | 20 | 0 | 0 | 0 | +0.00 |
| mean_reversion | 208 | 20 | 0 | 0 | 0 | +0.00 |
| momentum_breakout | 178 | 12 | 4 | 2 | 2 | +13.55 |
| btc_trend_following | 76 | 16 | 0 | 0 | 0 | +0.00 |
| ema_crossover | 205 | 16 | 1 | 1 | 0 | -9.71 |
| bb_mr_fade | 208 | 20 | 2 | 2 | 0 | -20.36 |

## By strategy and symbol

Bar reference = shared archive symbol/timeframe; tick reference = shared symbol coverage below.

| Strategy | Symbol | Bar reference | Obs / unique bars | Retained errors | Entries / exits / open | Est. closed net |
| --- | --- | --- | --- | --- | --- | --- |
| trend_zone | USOIL | USOIL/H1 | 29 / 29 | 5 | 0 / 0 / 0 | +0.00 |
| trend_zone | AUDUSD | AUDUSD/H1 | 30 / 30 | 5 | 0 / 0 / 0 | +0.00 |
| trend_zone | BTCUSD | BTCUSD/H1 | 44 / 44 | 0 | 0 / 0 / 0 | +0.00 |
| trend_zone | ETHUSD | ETHUSD/H1 | 44 / 44 | 0 | 0 / 0 / 0 | +0.00 |
| trend_zone | SPX500 | SPX500/H1 | 32 / 29 | 5 | 0 / 0 / 0 | +0.00 |
| trend_zone | XAUUSD | XAUUSD/H1 | 29 / 29 | 5 | 0 / 0 / 0 | +0.00 |
| mean_reversion | USOIL | USOIL/H1 | 29 / 29 | 5 | 0 / 0 / 0 | +0.00 |
| mean_reversion | AUDUSD | AUDUSD/H1 | 30 / 30 | 5 | 0 / 0 / 0 | +0.00 |
| mean_reversion | BTCUSD | BTCUSD/H1 | 44 / 44 | 0 | 0 / 0 / 0 | +0.00 |
| mean_reversion | ETHUSD | ETHUSD/H1 | 44 / 44 | 0 | 0 / 0 / 0 | +0.00 |
| mean_reversion | SPX500 | SPX500/H1 | 32 / 29 | 5 | 0 / 0 / 0 | +0.00 |
| mean_reversion | XAUUSD | XAUUSD/H1 | 29 / 29 | 5 | 0 / 0 / 0 | +0.00 |
| momentum_breakout | USOIL | USOIL/H1 | 29 / 29 | 4 | 1 / 1 / 0 | +23.55 |
| momentum_breakout | AUDUSD | AUDUSD/H1 | 7 / 7 | 0 | 1 / 0 / 1 | +0.00 |
| momentum_breakout | BTCUSD | BTCUSD/H1 | 44 / 44 | 0 | 0 / 0 / 0 | +0.00 |
| momentum_breakout | ETHUSD | ETHUSD/H1 | 37 / 37 | 0 | 2 / 1 / 1 | -10.00 |
| momentum_breakout | SPX500 | SPX500/H1 | 32 / 29 | 4 | 0 / 0 / 0 | +0.00 |
| momentum_breakout | XAUUSD | XAUUSD/H1 | 29 / 29 | 4 | 0 / 0 / 0 | +0.00 |
| btc_trend_following | USOIL | USOIL/H4 | 11 / 11 | 4 | 0 / 0 / 0 | +0.00 |
| btc_trend_following | AUDUSD | AUDUSD/H4 | 11 / 11 | 4 | 0 / 0 / 0 | +0.00 |
| btc_trend_following | BTCUSD | BTCUSD/H4 | 16 / 16 | 0 | 0 / 0 / 0 | +0.00 |
| btc_trend_following | ETHUSD | ETHUSD/H4 | 16 / 16 | 0 | 0 / 0 / 0 | +0.00 |
| btc_trend_following | SPX500 | SPX500/H4 | 11 / 11 | 4 | 0 / 0 / 0 | +0.00 |
| btc_trend_following | XAUUSD | XAUUSD/H4 | 11 / 11 | 4 | 0 / 0 / 0 | +0.00 |
| ema_crossover | USOIL | USOIL/H1 | 29 / 29 | 4 | 0 / 0 / 0 | +0.00 |
| ema_crossover | AUDUSD | AUDUSD/H1 | 30 / 30 | 4 | 0 / 0 / 0 | +0.00 |
| ema_crossover | BTCUSD | BTCUSD/H1 | 44 / 44 | 0 | 0 / 0 / 0 | +0.00 |
| ema_crossover | ETHUSD | ETHUSD/H1 | 41 / 41 | 0 | 1 / 1 / 0 | -9.71 |
| ema_crossover | SPX500 | SPX500/H1 | 32 / 29 | 4 | 0 / 0 / 0 | +0.00 |
| ema_crossover | XAUUSD | XAUUSD/H1 | 29 / 29 | 4 | 0 / 0 / 0 | +0.00 |
| bb_mr_fade | USOIL | USOIL/H1 | 29 / 29 | 5 | 0 / 0 / 0 | +0.00 |
| bb_mr_fade | AUDUSD | AUDUSD/H1 | 30 / 30 | 5 | 2 / 2 / 0 | -20.36 |
| bb_mr_fade | BTCUSD | BTCUSD/H1 | 44 / 44 | 0 | 0 / 0 / 0 | +0.00 |
| bb_mr_fade | ETHUSD | ETHUSD/H1 | 44 / 44 | 0 | 0 / 0 / 0 | +0.00 |
| bb_mr_fade | SPX500 | SPX500/H1 | 32 / 29 | 5 | 0 / 0 / 0 | +0.00 |
| bb_mr_fade | XAUUSD | XAUUSD/H1 | 29 / 29 | 5 | 0 / 0 / 0 | +0.00 |

## Bar archive coverage

| Symbol / TF | Files | Rows with overlap | All-history unique | Window unique | First / last open UTC in window | Max open-time gap (s) | Calendar gaps > one bar |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AUDUSD/D1 | 2 | 1450 | 951 | 1 | 2026-10-14T21:00:00+00:00 / 2026-10-14T21:00:00+00:00 | None | 0 |
| AUDUSD/H1 | 32 | 18106 | 600 | 46 | 2026-10-14T23:00:00+00:00 / 2026-10-16T20:00:00+00:00 | 3600.0 | 0 |
| AUDUSD/H4 | 12 | 6527 | 559 | 12 | 2026-10-14T21:00:00+00:00 / 2026-10-16T17:00:00+00:00 | 14400.0 | 0 |
| AUDUSD/M5 | 2 | 1261 | 762 | 0 | None / None | None | 0 |
| BTCUSD/D1 | 3 | 2607 | 1055 | 2 | 2026-10-14T21:00:00+00:00 / 2026-10-15T21:00:00+00:00 | 86400.0 | 0 |
| BTCUSD/H1 | 45 | 34115 | 830 | 66 | 2026-10-14T23:00:00+00:00 / 2026-10-17T16:00:00+00:00 | 3600.0 | 0 |
| BTCUSD/H4 | 16 | 9619 | 624 | 17 | 2026-10-14T21:00:00+00:00 / 2026-10-17T13:00:00+00:00 | 14400.0 | 0 |
| BTCUSD/M5 | 2 | 1263 | 764 | 0 | None / None | None | 0 |
| ETHUSD/D1 | 3 | 3162 | 1056 | 2 | 2026-10-14T21:00:00+00:00 / 2026-10-15T21:00:00+00:00 | 86400.0 | 0 |
| ETHUSD/H1 | 45 | 34380 | 831 | 66 | 2026-10-14T23:00:00+00:00 / 2026-10-17T16:00:00+00:00 | 3600.0 | 0 |
| ETHUSD/H4 | 16 | 9728 | 625 | 17 | 2026-10-14T21:00:00+00:00 / 2026-10-17T13:00:00+00:00 | 14400.0 | 0 |
| SPX500/D1 | 2 | 1720 | 861 | 1 | 2026-10-14T21:00:00+00:00 / 2026-10-14T21:00:00+00:00 | None | 0 |
| SPX500/H1 | 31 | 16849 | 575 | 45 | 2026-10-14T23:00:00+00:00 / 2026-10-16T20:00:00+00:00 | 7200.0 | 1 |
| SPX500/H4 | 12 | 6540 | 557 | 12 | 2026-10-14T21:00:00+00:00 / 2026-10-16T17:00:00+00:00 | 14400.0 | 0 |
| USOIL/D1 | 2 | 961 | 481 | 1 | 2026-10-14T21:00:00+00:00 / 2026-10-14T21:00:00+00:00 | None | 0 |
| USOIL/H1 | 31 | 16829 | 575 | 45 | 2026-10-14T23:00:00+00:00 / 2026-10-16T20:00:00+00:00 | 7200.0 | 1 |
| USOIL/H4 | 12 | 6505 | 557 | 12 | 2026-10-14T21:00:00+00:00 / 2026-10-16T17:00:00+00:00 | 14400.0 | 0 |
| USOIL/M5 | 2 | 1227 | 728 | 0 | None / None | None | 0 |
| USOIL/W1 | 1 | 98 | 98 | 0 | None / None | None | 0 |
| XAUUSD/D1 | 2 | 1886 | 944 | 1 | 2026-10-14T21:00:00+00:00 / 2026-10-14T21:00:00+00:00 | None | 0 |
| XAUUSD/H1 | 31 | 16859 | 575 | 45 | 2026-10-14T23:00:00+00:00 / 2026-10-16T20:00:00+00:00 | 7200.0 | 1 |
| XAUUSD/H4 | 12 | 6552 | 558 | 12 | 2026-10-14T21:00:00+00:00 / 2026-10-16T17:00:00+00:00 | 14400.0 | 0 |

## Tick archive coverage

| Symbol | Files / rows with overlap | Unique valid quote triples | First / last quote UTC | Max quote gap (s) | Request union (s) | Request gaps | Invalid rows |
| --- | --- | --- | --- | --- | --- | --- | --- |
| USOIL | 3 / 2836 | 2834 | 2026-10-16T16:02:37.026000+00:00 / 2026-10-16T16:32:36.741000+00:00 | 7.306999921798706 | 1800.518 | 0 | 0 |
| AUDUSD | 281 / 69096 | 68441 | 2026-10-15T07:33:12.394000+00:00 / 2026-10-16T20:48:55.432000+00:00 | 227.00200009346008 | 209901.122 | 0 | 0 |
| BTCUSD | 0 / 0 | 0 | None / None | None | 0 | 0 | 0 |
| ETHUSD | 91 / 492266 | 491739 | 2026-10-15T03:02:26.054000+00:00 / 2026-10-17T17:42:23.490000+00:00 | 90610.1740000248 | 80314.263 | 2 | 0 |
| SPX500 | 0 / 0 | 0 | None / None | None | 0 | 0 | 0 |
| XAUUSD | 0 / 0 | 0 | None / None | None | 0 | 0 | 0 |

## Retained feed errors and observation gaps

- trend_zone / USOIL: observations 2026-10-14T23:02:16.848335+00:00 to 2026-10-16T20:13:30.308196+00:00; max successful-observation gap 35738.16462302208 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {}.
- trend_zone / AUDUSD: observations 2026-10-14T23:02:16.966595+00:00 to 2026-10-16T20:13:39.103138+00:00; max successful-observation gap 35737.8272588253 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {}.
- trend_zone / BTCUSD: observations 2026-10-14T23:02:19.438108+00:00 to 2026-10-17T17:02:39.972946+00:00; max successful-observation gap 35737.457802057266 seconds; retained feed errors {}; plan errors {}; rejections {}.
- trend_zone / ETHUSD: observations 2026-10-14T23:02:19.520658+00:00 to 2026-10-17T17:02:40.101940+00:00; max successful-observation gap 35737.307259082794 seconds; retained feed errors {}; plan errors {}; rejections {}.
- trend_zone / SPX500: observations 2026-10-14T23:02:19.608095+00:00 to 2026-10-16T20:47:58.800041+00:00; max successful-observation gap 35699.55103993416 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {}.
- trend_zone / XAUUSD: observations 2026-10-14T23:02:19.773928+00:00 to 2026-10-16T20:13:50.731094+00:00; max successful-observation gap 35737.2165119648 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {"need 19123.72, free 9393.87": 1}; rejections {}.
- mean_reversion / USOIL: observations 2026-10-14T23:02:13.716645+00:00 to 2026-10-16T20:13:27.667261+00:00; max successful-observation gap 35753.89939188957 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {}.
- mean_reversion / AUDUSD: observations 2026-10-14T23:02:13.761426+00:00 to 2026-10-16T20:13:27.870396+00:00; max successful-observation gap 35754.46624183655 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {}.
- mean_reversion / BTCUSD: observations 2026-10-14T23:02:15.234769+00:00 to 2026-10-17T17:02:38.063641+00:00; max successful-observation gap 35754.4474298954 seconds; retained feed errors {}; plan errors {}; rejections {}.
- mean_reversion / ETHUSD: observations 2026-10-14T23:02:15.282648+00:00 to 2026-10-17T17:02:38.236634+00:00; max successful-observation gap 35754.566067934036 seconds; retained feed errors {}; plan errors {}; rejections {}.
- mean_reversion / SPX500: observations 2026-10-14T23:02:15.319095+00:00 to 2026-10-16T20:47:56.001750+00:00; max successful-observation gap 35669.41760110855 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {}.
- mean_reversion / XAUUSD: observations 2026-10-14T23:02:15.353410+00:00 to 2026-10-16T20:13:28.668015+00:00; max successful-observation gap 35755.15602207184 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {}.
- momentum_breakout / USOIL: observations 2026-10-14T23:02:15.375679+00:00 to 2026-10-16T20:13:28.761752+00:00; max successful-observation gap 35740.80006599426 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {}.
- momentum_breakout / AUDUSD: observations 2026-10-14T23:02:15.410974+00:00 to 2026-10-15T07:33:09.024347+00:00; max successful-observation gap 13213.978980064392 seconds; retained feed errors {}; plan errors {}; rejections {}.
- momentum_breakout / BTCUSD: observations 2026-10-14T23:02:15.443930+00:00 to 2026-10-17T17:02:38.438632+00:00; max successful-observation gap 35740.95009803772 seconds; retained feed errors {}; plan errors {}; rejections {}.
- momentum_breakout / ETHUSD: observations 2026-10-14T23:02:15.461258+00:00 to 2026-10-17T17:02:38.485631+00:00; max successful-observation gap 60913.77896809578 seconds; retained feed errors {}; plan errors {}; rejections {}.
- momentum_breakout / SPX500: observations 2026-10-14T23:02:15.477431+00:00 to 2026-10-16T20:47:58.072656+00:00; max successful-observation gap 35698.57386612892 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {}.
- momentum_breakout / XAUUSD: observations 2026-10-14T23:02:15.493684+00:00 to 2026-10-16T20:13:28.914317+00:00; max successful-observation gap 35741.449873924255 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {"lot minimum exceeds frozen risk": 1}.
- btc_trend_following / USOIL: observations 2026-10-14T23:02:15.529688+00:00 to 2026-10-16T18:32:42.926080+00:00; max successful-observation gap 39419.32525706291 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {}.
- btc_trend_following / AUDUSD: observations 2026-10-14T23:02:15.549682+00:00 to 2026-10-16T18:32:43.019828+00:00; max successful-observation gap 39418.09210419655 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {}.
- btc_trend_following / BTCUSD: observations 2026-10-14T23:02:15.996326+00:00 to 2026-10-17T17:02:38.836820+00:00; max successful-observation gap 39417.53056192398 seconds; retained feed errors {}; plan errors {}; rejections {}.
- btc_trend_following / ETHUSD: observations 2026-10-14T23:02:16.017327+00:00 to 2026-10-17T17:02:38.952831+00:00; max successful-observation gap 39417.39220786095 seconds; retained feed errors {}; plan errors {}; rejections {}.
- btc_trend_following / SPX500: observations 2026-10-14T23:02:16.037328+00:00 to 2026-10-16T18:32:43.222965+00:00; max successful-observation gap 39417.333714962006 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {}.
- btc_trend_following / XAUUSD: observations 2026-10-14T23:02:16.056330+00:00 to 2026-10-16T18:32:43.254213+00:00; max successful-observation gap 39416.97302389145 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {}.
- ema_crossover / USOIL: observations 2026-10-14T23:02:16.079328+00:00 to 2026-10-16T20:13:29.225443+00:00; max successful-observation gap 35739.860276937485 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {}.
- ema_crossover / AUDUSD: observations 2026-10-14T23:02:16.108330+00:00 to 2026-10-16T20:13:29.334808+00:00; max successful-observation gap 35739.80586385727 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {}.
- ema_crossover / BTCUSD: observations 2026-10-14T23:02:16.151326+00:00 to 2026-10-17T17:02:39.184829+00:00; max successful-observation gap 35739.78031206131 seconds; retained feed errors {}; plan errors {}; rejections {}.
- ema_crossover / ETHUSD: observations 2026-10-14T23:02:16.204326+00:00 to 2026-10-17T17:02:39.297832+00:00; max successful-observation gap 35739.72048997879 seconds; retained feed errors {}; plan errors {}; rejections {}.
- ema_crossover / SPX500: observations 2026-10-14T23:02:16.275884+00:00 to 2026-10-16T20:47:58.480040+00:00; max successful-observation gap 35699.0863070488 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {}.
- ema_crossover / XAUUSD: observations 2026-10-14T23:02:16.315002+00:00 to 2026-10-16T20:13:29.607293+00:00; max successful-observation gap 35739.443742990494 seconds; retained feed errors {"scan: Stale/invalid quote": 4}; plan errors {}; rejections {}.
- bb_mr_fade / USOIL: observations 2026-10-14T23:02:16.377003+00:00 to 2026-10-16T20:13:29.685396+00:00; max successful-observation gap 35739.113432884216 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {"lot minimum exceeds frozen risk": 3}.
- bb_mr_fade / AUDUSD: observations 2026-10-14T23:02:16.469007+00:00 to 2026-10-16T20:13:29.747905+00:00; max successful-observation gap 33376.157500982285 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {}.
- bb_mr_fade / BTCUSD: observations 2026-10-14T23:02:16.584000+00:00 to 2026-10-17T17:02:39.524832+00:00; max successful-observation gap 35739.09276008606 seconds; retained feed errors {}; plan errors {}; rejections {}.
- bb_mr_fade / ETHUSD: observations 2026-10-14T23:02:16.704003+00:00 to 2026-10-17T17:02:39.584946+00:00; max successful-observation gap 35739.07948708534 seconds; retained feed errors {}; plan errors {}; rejections {}.
- bb_mr_fade / SPX500: observations 2026-10-14T23:02:16.752001+00:00 to 2026-10-16T20:47:58.535039+00:00; max successful-observation gap 35699.16635298729 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {}.
- bb_mr_fade / XAUUSD: observations 2026-10-14T23:02:16.771005+00:00 to 2026-10-16T20:13:29.911753+00:00; max successful-observation gap 35738.51277804375 seconds; retained feed errors {"scan: Stale/invalid quote": 5}; plan errors {}; rejections {}.

## Paper entries and exits

| Strategy | Symbol / side | Entry UTC | Exit UTC / reason | Entry / SL / TP | Lots | Est. net | Static screen |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bb_mr_fade | AUDUSD BUY | 2026-10-15T09:00:45.168172+00:00 | 2026-10-15T10:04:33.618000+00:00 / stop | 0.69295 / 0.69143 / 0.69498 | 0.06 | -9.575999999999999 | confidence below current floor; actual-fill RR below current floor; desk setup prefix blocked if executed through desk |
| bb_mr_fade | AUDUSD BUY | 2026-10-15T10:41:25.024758+00:00 | 2026-10-15T12:30:02.338000+00:00 / stop | 0.69171 / 0.69016 / 0.69378 | 0.06 | -10.785 | confidence below current floor; actual-fill RR below current floor; outside allowed hours; desk setup prefix blocked if executed through desk |
| ema_crossover | ETHUSD BUY | 2026-10-16T11:08:01.511762+00:00 | 2026-10-16T15:51:24.169000+00:00 / stop | 2469.73 / 2433.11 / 2542.96 | 0.25 | -9.70775 | confidence below current floor; actual-fill RR below current floor; outside allowed hours; desk setup prefix blocked if executed through desk |
| momentum_breakout | AUDUSD SELL | 2026-10-15T07:33:09.029446+00:00 | None / open | 0.69416 / 0.69717 / 0.68617 | 0.03 | unrealized omitted | confidence below current floor; desk setup prefix blocked if executed through desk |
| momentum_breakout | ETHUSD SELL | 2026-10-15T03:02:26.193226+00:00 | 2026-10-15T14:48:35.692000+00:00 / stop | 2365.86 / 2395.86 / 2285.86 | 0.31 | -10.004999999999999 | outside allowed hours; desk setup prefix blocked if executed through desk |
| momentum_breakout | ETHUSD SELL | 2026-10-17T17:02:38.486626+00:00 | None / open | 2402.89 / 2432.89 / 2322.89 | 0.31 | unrealized omitted | confidence below current floor; desk setup prefix blocked if executed through desk |
| momentum_breakout | USOIL BUY | 2026-10-16T16:02:37.238311+00:00 | 2026-10-16T16:29:09.313000+00:00 / target | 99.024 / 98.724 / 99.824 | 0.03 | 23.55 | confidence below current floor; outside allowed hours; desk setup prefix blocked if executed through desk |

Full request-gap intervals, evidence hashes and machine-readable counts are in summary.json when saved.
