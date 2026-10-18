# Expanded Deriv synthetic scan

Window (Nairobi): 2026-10-18T03:52:12.694070+03:00 to 2026-10-18T03:53:05.885540+03:00

Inventory: 100 synthetic instruments across 17 families; 91 have complete valid M15/H1/H4 analysis, including 76 additional markets. 8 broker-disabled, 1 unavailable or incomplete. Original fifteen were refreshed in the same scan where data permitted.

Verified saved history hashes: 273 files / 81,900 closed candles. No broker orders, account switching, trading configuration edits, services or tasks. Temporary Market Watch selections were restored; restoration errors: [].

Closed M15/H1/H4, up to 300 bars each. EMA20/50 ordering, Wilder RSI14/ATR14. Descriptive watchlist, no entry-quality gate or profitability validation. Terminal UTC tick agreement required within120s.

UP = closed price > EMA20 > EMA50; DOWN = closed price < EMA20 < EMA50; MIXED otherwise. Extended means at least one sampled timeframe has RSI below30/above70 or absolute EMA20 distance above2ATR. This is a descriptive flag, not a reversal prediction. Each live quote has its own timestamp in summary.json; the scan is sequential, not simultaneous. The inventory covers the synthetic groups listed below; real-market currency/gold baskets, forex, equities, crypto and commodities are excluded.

## Additional markets aligned across all three timeframes

| Market | Bias H4/H1/M15 | Bid / ask | H1 RSI | M15 EMA20 distance (ATR) | M15 spread / ATR | Extension | Live H1 agrees |
|---|---|---:|---:|---:|---:|---|---|
| Vol over Boom 400 | UP | 13,633.51000 / 13,634.74000 | 60.6 | 0.99 | 1.7% | No | Yes |
| High Frequency Vol 75 Index | UP | 45,472.14000 / 45,480.44000 | 54.1 | 0.19 | 2.9% | No | Yes |
| Volatility 15 Index | UP | 102,149.54000 / 102,154.32100 | 60.1 | 1.46 | 3.6% | No | Yes |
| DEX 600 UP Index | UP | 2,193.92000 / 2,194.33000 | 52.4 | 0.22 | 4.4% | No | Yes |
| Jump 75 Index | DOWN | 5,484.11000 / 5,485.96000 | 48.8 | -0.80 | 4.4% | No | Yes |
| DEX 1500 UP Index | UP | 4,315.68000 / 4,316.12000 | 59.0 | 1.30 | 4.7% | No | Yes |
| Boom 150 Index | UP | 9,655.29720 / 9,655.46770 | 69.2 | 0.29 | 4.8% | No | Yes |
| Skew Step Index 5 Up | DOWN | 12,202.40000 / 12,203.20000 | 37.0 | -0.58 | 5.7% | No | Yes |
| DEX 1500 DOWN Index | DOWN | 35,326.81000 / 35,330.92000 | 45.8 | -0.58 | 6.3% | No | Yes |
| Boom 99 Index | UP | 107,516.16000 / 107,523.99500 | 65.4 | 1.77 | 17.6% | No | Yes |
| Step Index 300 | DOWN | 13,282.80000 / 13,283.10000 | 25.9 | -0.84 | 2.0% | YES | Yes |
| High Frequency Vol 100 Index | UP | 47,033.98000 / 47,046.92000 | 65.8 | 2.25 | 3.3% | YES | Yes |
| Multi Step 2 Index | UP | 11,820.88000 / 11,821.13000 | 73.5 | 0.99 | 3.6% | YES | Yes |
| Volatility 30 Index | DOWN | 104,433.75800 / 104,443.78300 | 29.1 | -0.00 | 4.1% | YES | Yes |
| Vol over Boom 550 | DOWN | 5,009.95000 / 5,011.03000 | 27.8 | -0.50 | 4.5% | YES | Yes |
| Skew Step Index 4 Down | UP | 13,086.40000 / 13,087.00000 | 68.4 | 1.55 | 5.0% | YES | Yes |
| Boom 900 Index | UP | 8,689.81300 / 8,690.77600 | 68.3 | 0.96 | 7.8% | YES | Yes |
| Crash 100 Index | DOWN | 94,570.15800 / 94,577.59300 | 27.5 | -2.43 | 18.1% | YES | Yes |
| Boom 100 Index | DOWN | 94,557.39800 / 94,564.77300 | 32.9 | -0.37 | 22.2% | YES | Yes |
| Drift Switch Index 20 | DOWN | 2,719.61000 / 2,723.72000 | 31.0 | -0.81 | 60.4% | YES | Yes |

Cross-timeframe EMA agreement is not a confirmed pattern, fresh supply/demand zone, valid risk/reward plan, strategy edge or execution admission. Mixed instruments are included in the full table. Wide spreads are reported descriptively; no spread policy was changed. Existing synthetic admission blocks and learning quarantines remain unchanged.

## All successfully analyzed markets

| Market | New | H4 | H1 | M15 | Bid | H1 RSI | Recent M15 low | Recent M15 high | Live vs M15 range |
|---|---|---|---|---|---:|---:|---:|---:|---|
| Crash Boom Flip 1000 Index | Yes | UP | UP | MIXED | 10,115.74430 | 63.2 | 10,112.41420 | 10,196.58040 | INSIDE |
| Crash Boom Flip 150 Index | Yes | MIXED | MIXED | MIXED | 10,196.97540 | 48.4 | 10,188.14030 | 10,201.12150 | INSIDE |
| Crash Boom Flip 300 Index | Yes | MIXED | UP | UP | 6,857.79130 | 59.7 | 6,721.56690 | 6,952.67610 | INSIDE |
| Crash Boom Flip 500 Index | Yes | UP | UP | MIXED | 10,832.79430 | 56.5 | 10,775.42820 | 10,895.44450 | INSIDE |
| Boom 100 Index | Yes | DOWN | DOWN | DOWN | 94,557.39800 | 32.9 | 94,485.57800 | 94,619.92100 | INSIDE |
| Boom 1000 Index | Refresh | DOWN | DOWN | DOWN | 15,358.75420 | 49.8 | 15,315.35200 | 15,412.84500 | INSIDE |
| Boom 150 Index | Yes | UP | UP | UP | 9,655.29720 | 69.2 | 9,647.69280 | 9,664.89990 | INSIDE |
| Boom 200 Index | Yes | MIXED | DOWN | MIXED | 100,262.55900 | 38.1 | 100,135.70800 | 100,306.46100 | INSIDE |
| Boom 300 Index | Yes | DOWN | MIXED | UP | 470.19700 | 49.0 | 467.36000 | 476.47600 | INSIDE |
| Boom 50 Index | Yes | DOWN | MIXED | MIXED | 105,278.73400 | 48.9 | 105,168.66600 | 105,316.35000 | INSIDE |
| Boom 500 Index | Refresh | DOWN | DOWN | MIXED | 5,537.44000 | 40.3 | 5,485.68900 | 5,569.66800 | INSIDE |
| Boom 600 Index | Yes | UP | MIXED | DOWN | 5,225.07600 | 47.3 | 5,212.96200 | 5,285.57000 | INSIDE |
| Boom 900 Index | Yes | UP | UP | UP | 8,689.81300 | 68.3 | 8,623.74600 | 8,709.78900 | INSIDE |
| Boom 99 Index | Yes | UP | UP | UP | 107,516.16000 | 65.4 | 107,304.09500 | 107,526.40800 | INSIDE |
| Crash 100 Index | Yes | DOWN | DOWN | DOWN | 94,570.15800 | 27.5 | 94,524.16900 | 94,759.03500 | INSIDE |
| Crash 1000 Index | Refresh | UP | UP | MIXED | 6,233.30990 | 62.0 | 6,228.19800 | 6,285.33000 | INSIDE |
| Crash 150 Index | Yes | DOWN | MIXED | UP | 9,491.40180 | 53.9 | 9,485.31720 | 9,497.26570 | INSIDE |
| Crash 200 Index | Yes | UP | UP | MIXED | 101,862.57900 | 53.8 | 101,822.77800 | 102,012.81200 | INSIDE |
| Crash 300 Index | Yes | MIXED | UP | UP | 2,482.08700 | 52.7 | 2,415.16900 | 2,500.56700 | INSIDE |
| Crash 50 Index | Yes | MIXED | UP | UP | 98,752.81700 | 61.7 | 98,670.51800 | 98,777.32300 | INSIDE |
| Crash 500 Index | Refresh | DOWN | MIXED | UP | 2,983.98700 | 51.8 | 2,940.89000 | 2,981.21900 | ABOVE |
| Crash 600 Index | Yes | DOWN | DOWN | MIXED | 22,019.46700 | 47.4 | 21,954.14700 | 22,106.10800 | INSIDE |
| Crash 900 Index | Yes | MIXED | UP | UP | 16,663.47800 | 63.4 | 16,586.05200 | 16,688.35200 | INSIDE |
| Crash 99 Index | Yes | DOWN | DOWN | MIXED | 95,933.44700 | 45.1 | 95,833.09900 | 95,972.58100 | INSIDE |
| DEX 1500 DOWN Index | Yes | DOWN | DOWN | DOWN | 35,326.81000 | 45.8 | 35,164.43000 | 35,497.21000 | INSIDE |
| DEX 1500 UP Index | Yes | UP | UP | UP | 4,315.68000 | 59.0 | 4,275.77000 | 4,324.82000 | INSIDE |
| DEX 600 DOWN Index | Yes | MIXED | DOWN | DOWN | 1,733.27000 | 37.2 | 1,708.27000 | 1,752.80000 | INSIDE |
| DEX 600 UP Index | Yes | UP | UP | UP | 2,193.92000 | 52.4 | 2,157.02000 | 2,198.80000 | INSIDE |
| DEX 900 DOWN Index | Yes | MIXED | DOWN | DOWN | 36,831.36000 | 26.5 | 36,370.92000 | 37,245.59000 | INSIDE |
| DEX 900 UP Index | Yes | DOWN | DOWN | MIXED | 2,671.26000 | 46.5 | 2,590.99000 | 2,689.83000 | INSIDE |
| Drift Switch Index 10 | Yes | MIXED | DOWN | MIXED | 3,031.83000 | 49.0 | 3,026.12000 | 3,071.61000 | INSIDE |
| Drift Switch Index 20 | Yes | DOWN | DOWN | DOWN | 2,719.61000 | 31.0 | 2,707.02000 | 2,740.87000 | INSIDE |
| Drift Switch Index 30 | Yes | UP | MIXED | MIXED | 7,382.75000 | 48.3 | 7,339.73000 | 7,383.42000 | INSIDE |
| Exponential Growth Index 1 | Yes | MIXED | UP | MIXED | 49,151.13960 | 71.4 | 48,996.89730 | 49,780.72950 | INSIDE |
| Exponential Growth Index 2 | Yes | MIXED | DOWN | MIXED | 111,212.25160 | 44.2 | 109,435.49830 | 112,351.31010 | INSIDE |
| Vol over Boom 400 | Yes | UP | UP | UP | 13,633.51000 | 60.6 | 13,369.83000 | 13,766.09000 | INSIDE |
| Vol over Boom 550 | Yes | DOWN | DOWN | DOWN | 5,009.95000 | 27.8 | 4,969.46000 | 5,066.63000 | INSIDE |
| Vol over Boom 750 | Yes | UP | UP | DOWN | 10,287.41000 | 53.5 | 10,257.12000 | 10,389.14000 | INSIDE |
| Vol over Crash 400 | Yes | DOWN | DOWN | MIXED | 3,935.26000 | 41.0 | 3,868.18000 | 3,952.07000 | INSIDE |
| Vol over Crash 550 | Yes | DOWN | MIXED | UP | 882.85000 | 48.7 | 868.96000 | 889.54000 | INSIDE |
| Vol over Crash 750 | Yes | DOWN | MIXED | UP | 14,659.44000 | 52.5 | 14,351.61000 | 14,624.28000 | ABOVE |
| Jump 10 Index | Yes | DOWN | DOWN | MIXED | 93,075.49000 | 47.2 | 93,040.15000 | 93,498.13000 | INSIDE |
| Jump 100 Index | Yes | UP | MIXED | UP | 123.63000 | 50.7 | 118.30000 | 123.97000 | INSIDE |
| Jump 25 Index | Yes | UP | MIXED | MIXED | 132,549.68000 | 51.0 | 131,792.89000 | 133,745.59000 | INSIDE |
| Jump 50 Index | Yes | UP | MIXED | DOWN | 73,755.95000 | 50.1 | 72,760.50000 | 74,636.11000 | INSIDE |
| Jump 75 Index | Yes | DOWN | DOWN | DOWN | 5,484.11000 | 48.8 | 5,469.33000 | 5,640.43000 | INSIDE |
| Multi Step 2 Index | Yes | UP | UP | UP | 11,820.88000 | 73.5 | 11,800.68000 | 11,826.48000 | INSIDE |
| Multi Step 3 Index | Yes | MIXED | DOWN | DOWN | 10,999.50000 | 34.4 | 10,997.05000 | 11,047.15000 | INSIDE |
| Multi Step 4 Index | Yes | MIXED | UP | DOWN | 10,126.90000 | 51.8 | 10,117.60000 | 10,137.40000 | INSIDE |
| Range Break 100 Index | Yes | MIXED | UP | MIXED | 51,497.20000 | 56.1 | 51,481.00000 | 51,727.00000 | INSIDE |
| Range Break 200 Index | Yes | UP | UP | DOWN | 68,114.50000 | 56.4 | 68,075.00000 | 68,139.00000 | INSIDE |
| Skew Step Index 4 Down | Yes | UP | UP | UP | 13,086.40000 | 68.4 | 13,043.50000 | 13,093.50000 | INSIDE |
| Skew Step Index 4 Up | Yes | MIXED | DOWN | DOWN | 12,143.00000 | 38.6 | 12,133.80000 | 12,162.40000 | INSIDE |
| Skew Step Index 5 Down | Yes | MIXED | UP | MIXED | 12,021.00000 | 61.3 | 11,983.80000 | 12,042.00000 | INSIDE |
| Skew Step Index 5 Up | Yes | DOWN | DOWN | DOWN | 12,202.40000 | 37.0 | 12,184.60000 | 12,237.50000 | INSIDE |
| Spot Up - Volatility Up Index | Yes | UP | UP | MIXED | 8,191.89000 | 59.7 | 8,203.33000 | 8,415.27000 | BELOW |
| Step Index | Refresh | DOWN | MIXED | UP | 7,230.40000 | 50.4 | 7,205.40000 | 7,230.50000 | INSIDE |
| Step Index 200 | Yes | UP | MIXED | DOWN | 10,721.30000 | 43.6 | 10,717.50000 | 10,812.10000 | INSIDE |
| Step Index 300 | Yes | DOWN | DOWN | DOWN | 13,282.80000 | 25.9 | 13,265.10000 | 13,330.80000 | INSIDE |
| Step Index 400 | Yes | UP | UP | MIXED | 9,054.60000 | 57.9 | 9,007.00000 | 9,107.00000 | INSIDE |
| Step Index 500 | Yes | DOWN | DOWN | MIXED | 3,583.80000 | 42.7 | 3,540.80000 | 3,637.80000 | INSIDE |
| Trek Down Index | Yes | UP | UP | MIXED | 8,560.11000 | 52.1 | 8,494.20000 | 8,581.79000 | INSIDE |
| Trek Up Index | Yes | MIXED | UP | UP | 13,570.59000 | 60.3 | 13,460.86000 | 13,607.63000 | INSIDE |
| High Frequency Vol 10 Index | Yes | MIXED | MIXED | MIXED | 94,941.42000 | 48.4 | 94,692.94000 | 95,007.29000 | INSIDE |
| High Frequency Vol 100 Index | Yes | UP | UP | UP | 47,033.98000 | 65.8 | 44,888.29000 | 47,139.37000 | INSIDE |
| High Frequency Vol 25 Index | Yes | MIXED | MIXED | UP | 110,428.01000 | 62.3 | 108,871.27000 | 110,627.54000 | INSIDE |
| High Frequency Vol 50 Index | Yes | DOWN | DOWN | MIXED | 123,490.44000 | 42.8 | 121,736.00000 | 123,634.20000 | INSIDE |
| High Frequency Vol 75 Index | Yes | UP | UP | UP | 45,472.14000 | 54.1 | 44,727.48000 | 45,768.64000 | INSIDE |
| Volatility 10 (1s) Index | Refresh | DOWN | DOWN | MIXED | 9,301.14000 | 37.8 | 9,273.95000 | 9,308.91000 | INSIDE |
| Volatility 10 Index | Refresh | MIXED | UP | UP | 4,901.18900 | 60.7 | 4,883.27100 | 4,903.97300 | INSIDE |
| Volatility 100 (1s) Index | Refresh | UP | MIXED | MIXED | 1,383.93000 | 41.6 | 1,342.00000 | 1,385.42000 | INSIDE |
| Volatility 100 Index | Refresh | UP | UP | MIXED | 683.43000 | 55.3 | 668.97000 | 694.05000 | INSIDE |
| Volatility 15 (1s) Index | Yes | DOWN | DOWN | MIXED | 13,422.21100 | 47.1 | 13,377.98700 | 13,439.34700 | INSIDE |
| Volatility 15 Index | Yes | UP | UP | UP | 102,149.54000 | 60.1 | 101,567.63500 | 102,370.89800 | INSIDE |
| Volatility 150 (1s) Index | Yes | UP | UP | MIXED | 38.49000 | 54.7 | 38.48000 | 40.81000 | INSIDE |
| Volatility 25 (1s) Index | Refresh | DOWN | MIXED | UP | 865,522.77000 | 51.1 | 862,539.23000 | 868,441.78000 | INSIDE |
| Volatility 25 Index | Refresh | UP | MIXED | MIXED | 2,709.05600 | 49.3 | 2,694.78600 | 2,720.52800 | INSIDE |
| Volatility 250 (1s) Index | Yes | DOWN | MIXED | DOWN | 0.35002 | 35.9 | 0.34109 | 0.39550 | INSIDE |
| Volatility 30 (1s) Index | Yes | MIXED | MIXED | DOWN | 5,993.17600 | 48.1 | 5,974.32200 | 6,051.37900 | INSIDE |
| Volatility 30 Index | Yes | DOWN | DOWN | DOWN | 104,433.75800 | 29.1 | 103,886.45700 | 104,701.65000 | INSIDE |
| Volatility 5 (1s) Index | Yes | MIXED | MIXED | DOWN | 95,863.62000 | 49.0 | 95,820.85000 | 96,057.46000 | INSIDE |
| Volatility 5 Index | Yes | UP | MIXED | MIXED | 99,795.33500 | 45.7 | 99,603.20100 | 99,825.03500 | INSIDE |
| Volatility 50 (1s) Index | Refresh | MIXED | MIXED | DOWN | 186,450.91000 | 44.1 | 186,091.86000 | 191,958.77000 | INSIDE |
| Volatility 50 Index | Refresh | UP | UP | UP | 82.48280 | 56.5 | 81.17760 | 82.72730 | INSIDE |
| Volatility 75 (1s) Index | Refresh | UP | UP | MIXED | 5,248.73000 | 65.5 | 5,219.92000 | 5,367.34000 | INSIDE |
| Volatility 75 Index | Refresh | DOWN | MIXED | MIXED | 37,704.63000 | 49.8 | 37,385.84000 | 38,295.24000 | INSIDE |
| Volatility 90 (1s) Index | Yes | UP | MIXED | MIXED | 28,849.41200 | 44.6 | 28,133.47100 | 28,891.19600 | INSIDE |
| Volatility 90 Index | Yes | MIXED | MIXED | UP | 147,221.17300 | 54.1 | 141,466.37000 | 147,678.80600 | INSIDE |
| VolSwitch High Vol Index | Yes | DOWN | DOWN | MIXED | 617.40000 | 38.9 | 601.86000 | 628.69000 | INSIDE |
| VolSwitch Low Vol Index | Yes | MIXED | MIXED | UP | 10,654.08000 | 51.3 | 10,512.61000 | 10,652.17000 | ABOVE |
| VolSwitch Medium Vol Index | Yes | DOWN | DOWN | MIXED | 10,281.87000 | 44.4 | 10,204.56000 | 10,372.39000 | INSIDE |

## Unavailable or incomplete

- Spot Up - Volatility Down Index: Market data selection failed; []

## Broker-disabled symbols

- Volatility Crash 400 Index: Broker trade_mode disabled
- Volatility Crash 750 Index: Broker trade_mode disabled
- Volatility Boom 400 Index: Broker trade_mode disabled
- Volatility Boom 750 Index: Broker trade_mode disabled
- Volatility Boom 550 Index: Broker trade_mode disabled
- Volatility Crash 550 Index: Broker trade_mode disabled
- Ladder Volatility Up Index: Broker trade_mode disabled
- Ladder Volatility Down Index: Broker trade_mode disabled

## Synthetic family inventory

- Step Indices: 5
- Range Break: 2
- Crash Boom Indices: 20
- Volatility Indices: 25
- Jump Indices: 5
- DEX Indices: 6
- Drift Switching Indices: 3
- Multi Step Indices: 3
- Hybrid Indices: 6
- Skewed Step: 4
- Trek Indices: 2
- Volatility Switch Indices: 3
- Spot Volatility Indices: 2
- Exponential Growth Indices: 2
- Crash Boom Flip Indices: 4
- Volatility Crash/Boom Indices: 6
- Laddered Volatility Indices: 2

## Interpretation limits

Higher scan breadth increases the chance of observing attractive-looking charts by chance. No win probability or forecast profit is assigned. Spike/regime/step products need family-specific, tick-aware validation, costs and chronological forward results. Deriv warns that technical indicators may not suit most synthetic products: https://deriv.com/markets/derived-indices/synthetic-indices?tab=cfds

Files: inventory.json, summary.json, saved OHLC and evidence_sha256.json. Disabled/unavailable instruments were not assigned current directional recommendations.
