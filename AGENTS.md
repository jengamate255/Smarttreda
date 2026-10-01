# AGENTS.md — Read This First

Project: MT5 trading stack (`E:\Project\Mt5 Ea`) — EA/dashboard/backtest/Telegram research.
This file is the AI handoff context. Update it whenever state changes (services, config, decisions).

## Hard-won rules (do not violate)

- **Commit before risky git operations.** `git checkout`/`git restore` on working-tree files has already
  destroyed uncommitted work once (`mt5_mcp/telegram_bot.py`, recovered only via opencode snapshot store
  at `C:\Users\Dave\.local\share\opencode\snapshot\` — a git object DB). Commit early, commit often.
- **Shell is Windows PowerShell 5.1**: no `&&`, `||`, `grep`, `head`; use `;`, `Select-String`.
  Quote paths with spaces: `cd "E:\Project\Mt5 Ea"` (bare `cd E:\Project\Mt5 Ea` fails).
  Avoid nested double quotes in `python -c "..."` — write a temp script instead.
- **Preferred Python**: `C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`
  (uv-python duplicates spawn and must be killed). `.venv\Scripts\pythonw.exe` is broken → renamed `.disabled`.
- **Process survival**: tool-shell child trees die at command timeout (Job Object kill).
  Long-running services use `Register-ScheduledTask` (`RepetitionInterval 1 min`,
  `ExecutionTimeLimit Zero`, `MultipleInstances IgnoreNew`); paths with spaces need `cmd /c` in schtasks.
- **Disk**: C: has been at ~0.3 GB free (140 GB used) — breaks caches/copies. E: is the work drive.
- **Only one Telegram bot may poll per token** (409 Conflict otherwise).

## Services / scheduled tasks

- VBS services (start via `service.vbs` / `desk_service.vbs`): MT5 Gateway :8060, MT5 Prop Dashboard :8050,
  MT5 Trade Desk, MT5 Telegram Bot, MT5 Watch Daemon, research service.
- Recurring tasks `MT5R_*` (9) are the live set. **`MT5R_LiveTrades`** (added 2026-10-01):
  every **30 min** runs `live_trade_updates.py` (pythonw, InteractiveToken) → one Telegram
  HTML card with ALL open positions + balance/equity/floating via `notify(force=True)`,
  silent when flat, logs to `logs/live_trades_update.log`. **Expires ~2026-12-30**
  (inherits P90D/StopAtDurationEnd from MT5R_Analysis template) — re-create with
  `schtasks /Create /TN "MT5R_LiveTrades" /XML` if it stops. Legacy `MT5_*`/`MT5LIVE_*` tasks were deleted 2026-09-25;
  admin-rights deletions still pending for VBS tasks (`schtasks /Delete` elevated).
  **`MT5R_Atlas`** (added 2026-10-01): weekly strategy sweep, Mon 06:00 — see
  Strategy atlas section (no 90-day expiry; recreate cmd documented there).
- Dashboards: 8080, 8082 (telegram health), 8050/8051, 8060. 8050/8060 have flapped (watchdog respawn vs MT5 IPC).
- Accounts: Prop FundingPips2-SIM login 11911448 (`C:\Program Files\MetaTrader 5 Terminal\terminal64.exe`);
  Deriv-Demo 41204968 (`MT5_MCP_ACCOUNT=DERIV` routes here). Balance ~9642.

## Trading config (source of truth: `.env`, never commit it)

- Floors (strategy): conf ≥75, score ≥70, health ≥55, RR ≥3.0 (`analysis_agent.py`).
  Auto-trader: `MIN_TRADE_SCORE=70`, `MIN_CONFIDENCE=90`. Backtest CLI supports `--min-conf/--max-score`.
- **Adaptive thresholds (since 2026-09-27):** `mt5_mcp/adaptive_thresholds.py` auto-adjusts
  strategy floors based on recent journal performance (win rate, consecutive losses, time since
  last trade). Relaxes 15-20% after 48h+ inactivity or tightens after 3+ consecutive losses.
  State saved to `mt5_mcp/memory/adaptive_state.json`. Integrated into `strategy.py`
  `evaluate_strategy(adaptive=True)` — overrides static config floors when adaptive data available.
  Effective floors with adaptive: zone ≥60, score ≥60, conf ≥50, RR ≥2.0 (relaxed from static 70/70/60/3.0).
- Desk (`trade_desk.py`, magic 20260601): `MT5_DESK_ENABLED=true`, `MT5_DESK_MODE=SIM`,
  `MT5_DESK_RISK_PERCENT=1.0`, `MT5_DESK_RISK_FIXED=90` (updated 2026-09-27 for FundingPips
  challenge: 1% of ~$9642 = $96.42, capped at $90 for safety margin),
  `MT5_DESK_SCAN_MINUTES=30`, `MT5_DESK_SL_ATR=4.5` (widened from 3.0),
  `MT5_DESK_TP_R=2.0`, entry TF M5.
- **Consistency floor 0.25 (2026-10-01):** `challenge_tracker.get_consistency_multiplier`
  no longer returns 0.0 (full desk pause) when best day > 40% of total — that hard pause
  was a deadlock (trade_desk is the sole consumer, so total_pnl could never grow to dilute
  the ratio). Floor 0.25 = desk trades quarter size until ratio < 40%. Desk restarted via
  `schtasks /Run "MT5 Trade Desk"` (VBS task `desk_service.vbs` — no MT5R_* task exists
  for desk; watch out: killing desk can transiently respawn, count python procs = pair only).
  First scan after fix: EURJPY BUY 0.14 #400849873 + ETHUSD BUY 1.0 #400849878 filled at
  25% size (daily limit then 2/2).
- Compliance (`mt5_mcp/settings.json` → `risk` + `compliance.py`), updated 2026-09-27:
  - Mode was GREEN/normal (daily & drawdown buffers fresh).
  - **Risk raised to 1%** for FundingPips challenge (was 0.5%). Conservative mode stays 0.5%.
  - **Daily loss raised to 4%** (was 3%, FundingPips hard limit is 5% — 1% safety buffer).
  - **Max trades/day: 2** base + 1 green bonus = 3 absolute (was 3+2=5, reduced for quality).
  - **Blocked symbols**: DJI30, EURCAD, EURGBP, NZDUSD.
    NDX100 **unblocked** 2026-09-27 (ema_crossover PF 1.92).
    GBPUSD/USDCAD/GBPJPY **unblocked** 2026-09-27.
    NZDUSD stays blocked (backtest PF 0.07).
  - **Relaxed risk floors** 2026-09-27: `min_risk_reward` 3.0→2.0, `conservative_risk_percent`
    0.25→0.5 (was self-rejecting valid setups). `confidence_floor_percent` now **80**
    (values 60/75 seen in older notes were interim).
  - **Trades/day max 3**: `max_trades_per_day` base 2 + green bonus 1 (bonus only when
    daily P&L > 0 AND normal mode) — absolute cap 3.
  - **Spread never blocks (2026-10-01, user directive):** `risk.max_spread_points` 60→**0**.
    Falsy → compliance skips the points check; absent `max_spread_relative` (default 0)
    → relative check passes too, so both `compliance.gate` and `tools/trades.py` allow
    any spread (BTCUSD 3222 pts verified passing; only remaining violation was the probe's
    missing SL). Re-enable: set `max_spread_points` > 0 **and restart executors**
    (`get_settings()` lru_cache — desk/confirm/watchers/telegram read it at import).
  - `compliance.evaluate()` now returns `max_positions`; desk `free_slots` fallback fixed
    2→3 (was silently attempting only 1 pick/scan when status lacked the key).
    Desk restarted 2026-09-25 12:24 to apply all of the above.
- **settings.json clobber incident (2026-09-30 10:39 restore):** an unknown writer
  re-serialized `mt5_mcp/settings.json` at 2026-09-29 16:02 (4-space `ConvertTo-Json`
  style) and silently reverted the allowlist to 21, floor to 60, trades to 3, re-blocked
  NDX100, and **corrupted `deriv.api_token` (68→69 chars)**. Restored from
  `C:\Users\Dave\AppData\Local\Temp\opencode\settings.working.json`; re-verify the file
  after any bulk settings edit, and keep `config.get_settings()` lru_cache in mind:
  long-running processes only pick up risk-block changes on restart (consensus
  allowlist is re-read per call and is hot).
- Journal: `mt5_mcp/memory/journal.db` (`trades` has no `ts` column — use `open_time`/`close_time`).
  Desk events: `mt5_mcp/memory/desk_events.jsonl`, `desk_state.json`.

## News feed (fixed 2026-09-30)

- **Bug:** `ForexFactoryNewsProvider._fetch()` hit the network first and only fell
  back to disk after failure; feed 429s → `FAILURE_TTL=180s` retry storm →
  `news_cache.json` never refreshed → `compliance._in_news_blackout()` failed open
  (silent blindness) while `auto_trader._news_guard()` failed closed (inconsistent).
- **Fix (`mt5_mcp/tools/news.py`):** disk-cache-first reads (STALE_GRACE_TTL 7d),
  network only past `FAILURE_TTL`, exponential backoff 180s→3600s ±20% jitter honoring
  `Retry-After`, atomic cache writes (tmp + `os.replace`), and `news_status()` →
  `{news_source, news_data_age_sec, news_stale, news_retry_in_sec, news_provider_error}`
  (spread into `news_filter()` results and `compliance.evaluate()` return).
- **Staleness policy:** `risk.news_stale_max_minutes` (default **0** = warn only,
  fail-open — set >0 to hard-blackout trading when the calendar is older than N minutes).
  `gate()` reports `news_blocked_reason`; synthetic symbols stay exempt.
- Verified 2026-09-30: disk-first fetch 0.00s/41 events, cache fresh, `analysis_agent
  --once` cycle 1141 exit 0 with **no 429**; `tests/test_news.py` 7 pass.

## Telegram (merged — status 2026-09-25)

- `mt5_mcp/telegram_bot.py` (1276 lines, `TradingTelegramBot`) is the single implementation:
  Groq Q&A + alerts + inline keyboards + trade execution + dashboards control +
  `/chart /scan /summary /at /atpause /atresume /research /heatmap /close /halt /resume`.
- `run_telegram_bot.py` is the runner (python-telegram-bot handlers; `/chart` replies with photo).
- Root `telegram_bot.py` is the legacy standalone bot — source of merged features; do not run both.
- Health endpoint port 8082; locks in `mt5_mcp/memory/telegram_bot.lock`.

## Trading consensus layer (since 2026-09-26)

- **Module `mt5_mcp/consensus.py`** + config `settings.json -> "consensus"`.
  Board file `mt5_mcp/memory/consensus.json` (atomic writes, multi-process).
- **All 4 readable engines publish stances** (BUY/SELL/NONE + score, TTL 30m):
  `desk` (top-sheet of every scan), `confirm` (every evaluation),
  `breakout` (qualifying setups only), `analysis` (read-only, every cycle).
- **Pre-trade `check()` rules** (wired into trade_desk `execute_pick`,
  confirm `scan_symbol`, forex_breakout `scan_once`):
  1. **allowlist** — only backtested set tradable (reset 2026-09-30 to the 11
     PF-backed symbols; replaces the 21-symbol list, which included 10 symbols
     with no profitable strategy — `diagnose_pipeline.py` reports the other
     engine symbols as "missing" by design, not a blocker):
     EURUSD EURJPY GBPJPY USDJPY SPX500 XAUUSD ETHUSD USDCAD GBPUSD USDCHF BTCUSD.
     Set in `settings.json` `consensus.allowed_symbols` (read per-call — hot, no restart).
  2. **veto** — fresh (≤30m) opposite stance with score ≥75 blocks the entry
     (e.g. desk NZDUSD SELL 76.3 ⇒ nobody buys NZDUSD for 30m).
  3. **`min_agree: 0`** (corroboration optional): engines rarely see the same
     setup simultaneously, so requiring agreeing votes would kill most trades;
     raise only with vote-history evidence.
  4. **unified risk cap 1.0%** — `consensus.risk_cap()` (= `consensus.risk_percent`
     in settings, raised 0.5→1.0 in the 2026-09-29 config retune); breakout budget and
     confirm's `evaluate_strategy(risk_percent=...)` both capped.
- **Fail-open by design**: consensus errors log + allow — `compliance.gate()`
  stays the hard veto. Consensus is an *additive* restriction (never unblocks
  a compliance-blocked symbol).
- **Session routine (part B)**: at session start run
  `python -X utf8 mt5_mcp/consensus.py` (report) and present the board to the
  user — who agrees with whom, stale votes, any blocked attempts
  (`check SYMBOL SIDE --source x --score n` for an on-demand verdict).

## Shared trading infrastructure (since 2026-09-27)

Three shared modules eliminate duplication across execution paths and watchers:

- **`mt5_mcp/pipeline.py`** — shared pre-trade gate chain + execution.
  - `run_gates(symbol, side, lots, sl_price, confidence, source, score)` → `GateVerdict`
    chains consensus check (fail-open) + compliance gate (hard veto).
  - `execute_trade(...)` → `ExecResult` runs gates → open_market → journal → notify.
  - `publish_stance(source, symbol, side, score)` — fail-open consensus publish.
  - `size_position(symbol, sl_distance, risk_amount)` — tick-value lot sizing.
  - **Used by**: `trade_desk.py` (`execute_pick` uses `run_gates`, `scan_cycle` uses
    `publish_stance`), `confirm_execute_daemon.py` (`scan_symbol` uses both).

- **`mt5_mcp/notifier.py`** — unified notification dispatcher.
  - `trade_filled(...)`, `trade_closed(...)`, `signal_detected(...)`,
    `position_update(...)`, `daily_summary(...)`.
  - Each dispatches to Telegram (rich HTML), desktop popup, and JSONL event log.
  - **Used by**: all execution paths and watchers (replaces direct telegram/popup/emit calls).

- **`mt5_mcp/watcher_base.py`** — `BaseWatcher` ABC for watcher processes.
  - Shared: logger setup, client management, lot sizing, exposure check,
    daily loss breaker, main loop, CLI arg parser.
  - Subclasses implement: `symbols()`, `client_key()`, `detect_signal()`,
    `compliance_gate()`, `build_order()`.
  - Override hooks: `exit_management()`, `pre_scan_cycle()`, `on_order_accepted()`,
    `account_label()`, `volume_defaults()`.
  - **Used by**: `trade_watch.py` (BoomCrashWatcher), `forex_breakout_watch.py`
    (ForexBreakoutWatcher), `btc_trend_watch.py` (BtcTrendWatcher).

## Pipeline improvements (since 2026-09-27)

Modules added to fix zero-trade diagnosis and improve signal quality:

- **`mt5_mcp/adaptive_thresholds.py`** — performance-based threshold adjustment.
  Reads last 7 days of journal trades, calculates win rate / consecutive losses / hours
  since last trade. Relaxes floors 15-20% after 48h+ inactivity; tightens after 3+ losses.
  State persisted to `mt5_mcp/memory/adaptive_state.json`.

- **`mt5_mcp/ml_ranker.py`** — ML signal ranker (logistic regression, pure numpy).
  Trained on 623 trades (91 journal + 532 backtest), AUC 0.602, win rate above 0.5 = 83.3%.
  Model at `mt5_mcp/models/signal_ranker.json`. Retrain: `python ml_ranker.py --train --journal`.
  Integrated into `strategy.py`: rejects signals with `ml_score < 0.40`.

- **`mt5_mcp/supply_demand.py`** — enhanced zone detection (2026-09-27):
  `base_bars` 4→3, `impulse_mult` 1.5→1.3 (detects more zones).
  Added proximity boost (up to +10 strength within 2 ATR of price), fresh zone bonus (+5),
  relaxed broken penalty (0.5 was 0.3). `strategy.py` passes `current_price` for proximity.

- **`mt5_mcp/multi_strategy.py`** — multi-strategy system:
  `mean_reversion_strategy` (RSI < 30 + lower BB in ranging markets, ADX < 25),
  `momentum_breakout_strategy` (ADX > 25 + price breaking 20-bar high/low),
  `btc_trend_following_strategy` (EMA 50/200 cross + RSI pullback completion,
  2.5x ATR SL / 4x ATR TP — designed for BTC's $462 ATR, 3.3% pullbacks),
  `ema_crossover_strategy` (EMA20/50 cross + ADX > 20 filter, 2x ATR SL / 4x ATR TP —
  optimized for stock indices),
  `evaluate_all_strategies` runs all five (trend_zone + mean_reversion + momentum_breakout + btc_trend + ema_crossover),
  `pick_best_strategy` resolves BUY/SELL conflicts by score × backtest PF multiplier.
  Strategy routing: EURJPY/GBPJPY/USDJPY/GBPUSD→mean_reversion, USDCAD→momentum_breakout,
  EURUSD/USDCHF/ETHUSD/XAUUSD→trend_zone, BTCUSD→btc_trend_following (PF 1.17),
  SPX500/NDX100→ema_crossover (PF 1.78-1.92).
  **Strategy routing** (2026-09-27 backtest): `STRATEGY_ROUTING` maps symbol → preferred
  strategy (EURJPY/GBPJPY/USDJPY/GBPUSD → mean_reversion PF 1.52-2.65, USDCAD →
  momentum_breakout PF 1.56, EURUSD/USDCHF/ETHUSD/XAUUSD → trend_zone PF 1.34-3.25).
  `BACKTEST_PF` stores per-(symbol, strategy) profit factors; `pick_best_strategy`
  multiplies trade_score by PF (clamped 0.5-3.0) so historically profitable strategies
  are preferred. AUDUSD and BTCUSD have no profitable strategy (best PF 0.70/0.90).

- **`mt5_mcp/rl_trader.py`** — live RL trading wrapper.
  `RLTrader` loads trained PPO/reinforce models from `models/dojo/{symbol}_{timeframe}/`.
  `decide(symbol, timeframe, confidence_threshold=0.6)` → BUY/SELL/NONE with probabilities.
  Features: returns, ATR, RSI, EMA, MACD, BB position, volume ratio, hour sin/cos.
  **No trained models exist yet** — train via `forex_dojo.py` first.

- **`web_diagnostic.py`** + **`static/app.diagnostic.js`** — diagnostic dashboard:
  `/api/diagnostic/summary` (system health), `/config` (issues), `/consensus` (board),
  `/symbols` (per-symbol pipeline trace: compliance → consensus → MT5 → strategy).
  Registered in `web_dashboard.py`. Frontend panel in Diagnostic tab of main dashboard.

- **`mt5_mcp/challenge_tracker.py`** + **`web_challenge.py`** — FundingPips challenge tracker:
  `ChallengeTracker` monitors progress toward 8% profit target, tracks daily loss (4% internal
  cap vs 5% FundingPips limit), max drawdown (8%), consistency rule (no day > 40% of total profit),
  and min trading days (5). State persisted to `memory/challenge_state.json`.
  API: `/api/challenge/status` (progress + risk alerts), `/api/challenge/init?balance=N`
  (set starting balance), `/api/challenge/daily` (day-by-day P&L breakdown).
  Registered in `web_dashboard.py`.

## Strategy atlas (since 2026-10-01)

- **`mt5_mcp/research/atlas.py`** — per-pair strategy sweep: every family × every
  symbol, gated selection, weekly re-run + drift check.
  - Universe **24 symbols** (analysis majors/crosses + XAUUSD/XAGUSD/USOIL/JP225 +
    USDJPY/BTCUSD/ETHUSD/SPX500/NDX100); variants = 5 routing families
    (mean_reversion, momentum_breakout, ema_crossover, bb_mr_fade,
    btc_trend_following@H4) + 4 candidates (donchian/pullback/squeeze/daybreak).
  - Data: `copy_rates_range` from **2021** (bypasses the terminal MaxBars ~5k cap
    that `client.rates` hits; 10k–46k H1 bars/symbol). Zero-spread history bars
    (~57% pre-2024) backfilled with the symbol's median non-zero spread.
  - Costs: gated on **realistic** (per-bar spread + 1pt slip); **canonical**
    (15pt fixed × pip, vol-adjusted, 1 pip slip — backtest.py style) reported
    alongside as `canonical.pf`. Most edges die under canonical → cost-sensitive.
  - Robustness: full window + 70/30 chronological holdout, trade-thirds stability,
    1000-draw bootstrap P(PF>1). Verdicts: **PROMOTE** (n≥50, PF≥1.25, thirds all
    ≥1.0, boot ≥0.80, holdout n≥15 & PF≥1.0) / **PAPER** (n≥40, PF≥1.10, boot
    ≥0.60, ≥2/3 thirds) / FAIL. Routing `CHANGE` proposals only from PROMOTE rows
    beating current `BACKTEST_PF` by ≥0.1 (otherwise KEEP).
  - Outputs: `mt5_mcp/memory/research_lab/atlas_<date>.json/.md` + one Telegram
    summary (`notify("research_atlas")`); drift vs previous atlas reported
    (PF ≥1.25→<1.10, or PROMOTE→FAIL = alert).
  - Harness loaded **by file path** (`importlib`): `mt5_mcp/research.py` module
    shadows the `research/` package (adding `__init__.py` would break
    web_dashboard/web_research). Full run ~115 min under load; frames per symbol.
- **Weekly task `MT5R_Atlas`** (Mon 06:00, InteractiveToken, **no 90-day expiry**;
  action = `C:\Users\Dave\run_atlas_weekly.bat`, a no-space launcher copy — schtasks
  `/TR` quoting chokes on the space in `Mt5 Ea`; the repo `run_atlas.bat` redirects
  to `logs/atlas_weekly.log`). Recreate:
  `schtasks /Create /TN "MT5R_Atlas" /TR "C:\Users\Dave\run_atlas_weekly.bat" /SC WEEKLY /D MON /ST 06:00 /IT /F`
- **First sweep (2026-10-01): 216 runs = 205 FAIL / 11 PAPER / 0 PROMOTE ⇒
  STRATEGY_ROUTING UNCHANGED.** PAPER: SPX500 btc_trend 1.65 (holdout 0.805),
  EURCHF mean_reversion 1.51, AUDUSD btc_trend 1.29, SPX500 ema 1.21, GBPJPY ema
  1.21, NDX100 ema 1.15, GBPCHF MR 1.15, NDX100 btc 1.14, USDCAD momentum 1.14
  (its routed family), USDCHF MR 1.13, USDJPY squeeze 1.10. **EURCHF bb_mr_fade
  FAILs full history (PF 0.90, n=711)** vs the research-window 1.25 → treat the
  ~10-20 allowlist review skeptically. 0 errors across all symbols.

## Live state (2026-09-26 12:45)

- **Consensus layer deployed 12:42**: desk/confirm/breakout restarted on
  consensus-wired code (analysis picks it up each 1-min run). Board live with
  4 sources; zero errors in first cycles. Weekend = market closed, no fills.
- **Process "pairs" are one logical instance**: pythonw→python and
  hermes→uv-python are parent→child forwarding chains (verified via
  ParentProcessId) — do NOT kill just one PID, kill both or the twin survives.

- **User operating policy:** desk auto-fills within caps; user audits after the
  fact (no per-trade confirmation needed for desk executions inside compliance caps).
- **Open positions (1/3):** AUDNZD BUY 0.18 ticket 396986574 (confirm_exec relaxed,
  entry 1.2392, SL 1.23456, TP 1.24857, +$7ish).
- **Today's USDJPY round trips (both losses, journaled):**
  - Desk BUY 0.11 #397132112: 13:06→14:52 SL 157.529 → **−$27.93** (SL trailed
    157.325→157.529 by manage). Journal signal + trades row backfilled.
  - **BUY 0.15 #397151807: 15:41→16:00 SL 157.427 → −$35.17 — magic 0, empty comment
    = no stack engine claims it** (all live engines use magic 20260601 or non-empty
    comments) → treated as manual/unknown origin; journaled as `setup=manual`.
    MCP audit log is stale (capped 2026-09-11) so MCP `open_trade` can't be ruled
    out from logs alone.
- **Desk `_log_event` crash fixed:** `manage_cycle` passed a `list` to
  `_log_event` (`**payload` needs a mapping) → TypeError killed manage on any
  position action (the 15:20:03 cycle error, position_closed recorded with
  `pnl 0.0 / details unavailable`). `_log_event` now wraps lists as
  `{"actions": [...]}`; trade_desk.py restarts via logon task
  `MT5_Trading_Assistant_Startup` → `start_services.ps1` → `run_services.py`.
- **Second live engine exists:** `forex_breakout_watch.py` (range-breakout, floor
  conf 65, risk 0.5%, magic 20260601, `logs/forex_breakout.log`) — today: USDCAD
  blocked-by-compliance spam (~13 rejections), NZDUSD risk-rejected, USDCHF
  buy_stop #397207862 expired unfilled 23:48 (no exposure). Also
  `confirm_execute_daemon`, `run_synthetic_watcher`, `btc_trend_watch` trade.
- **Post-fill crash fixed (13:06 incident):** `trade_journal.log_signal` INSERT
  had `zone_strength` twice → error AFTER fill; INSERT rewritten, backfilled.
- **Fresh-deal blackout fixed:** server history timestamps are wall-clock-as-epoch
  (UTC+3 baked in), `mt5_client.deals_since` `date_to` now +86400.
- Desk `cycle error` log line now includes traceback (`exc_info=True`).
- Correlation gate verified: existing + new combined vs 0.5% cap (`jpy 84.00`
  message = 39 existing + 45 new > 48.24 — working as designed).

## Current / next steps

- **2026-10-01 atlas session:** strategy atlas built + first full sweep (section above;
  0 PROMOTE ⇒ routing unchanged), analysis router **generalized to all 5 planners**
  (`analysis_agent.py` `planners` map + `strategy_source` field; live-verified —
  GBPUSD emits mean-reversion reasons, USDCAD momentum-breakout), multi_strategy
  B2/B3 fixed, `test_analysis_agent` 5/5, weekly `MT5R_Atlas` created. Telegram
  409 incident: legacy root `telegram_bot.py` pair was running alongside
  `run_telegram_bot.py` (conflict killed polling at 22:37) — killed legacy,
  restarted `MT5R_Telegram`; getUpdates 200 OK, 8082 /health 200, pidfile tracks
  run_telegram. All services verified up (ports 8010/8050/8055/8060/8080/8082;
  MT5R_* running; watchdog had also respawned the 3 watchers + 8055 after a
  22:57 incident — stable since 23:02).
- **2026-09-30 session:** news feed fixed (disk-first + backoff, section above),
  consensus allowlist reset to the 11 PF-backed symbols, settings.json restored after
  the 16:02 clobber (incl. corrupted deriv token), tests: `test_news` 7/7,
  `test_compliance` 26/26 after extending the `pinned_risk_settings` fixture
  (`max_daily_loss_percent` 3.0, `max_risk_percent` 0.5 — the live 4.0/1.0 retunes
  had drifted 6 gate tests) + stubbing `symbol_info` for the unknown-symbol test.
  `analysis_agent --once`, `diagnose_pipeline.py`, `consensus.py` board all verified.
- **EURCHF research (2026-09-30, research-only, no config edits):** new bbmr
  mean-reversion idea (H1: BB(20,2.0) touch + RSI<30, SL 1.5×ATR, TP 2.0×ATR)
  qualifies at the *minimum* bar — PF 1.25 realistic (69 tr, halves 1.46/1.08,
  thirds 1.26/1.25/1.24, plateau median 1.15 over 28 cells, bootstrap P(PF>1)=0.80) —
  proposed `STRATEGY_ROUTING["EURCHF"]="bb_mr_fade"`, `BACKTEST_PF=1.25`, NOT applied.
  Report + all 50+ tested variants: `mt5_mcp/memory/research_lab/report_2026-09-30.md`,
  harness in `mt5_mcp/research/eurchf/`. **4 multi_strategy bugs found** (B1
  `rsi(frame,14)` DataFrame TypeError kills MR/EMAC/BTC strategies if ever called;
  B2 momentum_breakout close>high impossible; B3 MR validity gate unreachable;
  B4 `evaluate_all_strategies`/`pick_best_strategy` have zero production call sites).
  **B1/B2/B3 all fixed 2026-10-01** (B2 now compares close vs prior-20-bar high/low;
  B3 score uses `zone_strength` so the gate is reachable at conf ≥60); **B4 still open**.
- **EURCHF paper-trade (since 2026-10-01):** research verdict `bb_mr_fade` (H1 BB(20,2.0)
  + RSI<30 fade, SL 1.5ATR/TP 2.0ATR, PF 1.25 / thin edge — report at
  `mt5_mcp/memory/research_lab/report_2026-09-30.md`) wired READ-ONLY into
  `analysis_agent` (router hook in `_analyze_symbol`, `STRATEGY_ROUTING["EURCHF"]` +
  `BACKTEST_PF=1.25`, confirmation contains `bb_mr_fade`). **EURCHF stays OFF the
  allowlist** — stack cannot fill it; review ~2026-10-20 and allowlist only if live
  paper signals match the backtest. **Atlas caveat (2026-10-01): bb_mr_fade FAILs
  full 5.75y history (PF 0.90, n=711) vs the research-window 1.25** — the edge may
  be window-specific; re-check before any allowlist change. Structural RR 1.33 <
  floors ⇒ `avail=False` by design (track via plan line + consensus stance). B1
  `rsi(frame,...)` crash bug fixed this session; B2 (momentum `close>high`
  impossible) and B3 (mean_reversion gate unreachable) also fixed 2026-10-01;
  none have production call sites (B4, open).
- **Pre-existing test failures (21, fail at HEAD too — not from this session):**
  `test_coach`/`test_memory`/`test_research` sqlite `separation_atr` (test-schema
  drift; live `journal.db` already has the column), `test_ml_ranker::test_build_dataset`
  (feature shape), `test_strategy` ×3, `test_trade_guard` ×2.
- **Services restarted 2026-09-30** after the settings restore (lru_cache holds the
  pre-restore values until restart; consensus allowlist is hot). Wave A = watchdog/1-min
  task respawn, wave B = batch-started (desk/gateway/8050/watch_daemon ×2/run_services)
  need `schtasks /Run` or VBS relaunch — verify ports 8050/8055/8060/8080/8082/8010 after.
  **Watchdog telegram trap — FIXED 2026-10-02:** `service_watchdog`'s `telegram_bot` cfg
  now targets `run_telegram_bot.py` (cmd+pattern) **and** `.env` carries
  `START_TELEGRAM_BOT=0` → the watchdog skips telegram entirely; `MT5R_Telegram`
  (interval PT1M, IgnoreNew) is the sole spawner (~1 min recovery). Also hardened
  `run_telegram_bot._single_instance`: after acquiring the lock it sleeps 1.5 s and
  re-reads the owner pid (the old stale-lock race let task + watchdog both pass and
  double-start → 409). Still: never two pollers; if legacy root `telegram_bot.py`
  reappears, kill it.
- **Initialize challenge tracker**: POST `/api/challenge/init?balance=9642` to set starting
  balance. Current status: +$210 (2.18%), 27.3% toward 8% target. Consistency risk flagged
  (best day = 117% of total profit — need more steady gains).
- **Update .env**: set `MT5_DESK_RISK_FIXED=90` (was 45) to match new 1% risk setting.
- **Train RL models**: no trained models at `models/dojo/` yet — run `forex_dojo.py`
  training loop before `rl_trader.py` can produce actions.
- **Run diagnostic dashboard** after restart: check `/api/diagnostic/symbols` to verify
  symbols now pass the full pipeline (compliance → consensus → MT5 → strategy).
- Elevated: delete leftover VBS scheduled tasks (`MT5 Watch Daemon`, `MT5 Telegram Bot`, `MT5 Gateway`,
  `MT5 Prop Dashboard`, `MT5 Trade Desk`, `MT5_Trading_Assistant_Startup`) if superseded by `MT5R_*`.
- Blocked externally: OpenRouter 402 (credits), Groq 429 OTPM.
  News feed 429 **resolved 2026-09-30** (disk-first + backoff — see News feed section).
- Backtest verdict: lowering conf/score floors degrades quality (report:
  `mt5_mcp/backtest/results/lowconf_exp/report.json`) — production floors stay, but adaptive
  thresholds can temporarily relax them based on performance (safe: clamped to min bounds).
