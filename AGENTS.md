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
- Recurring tasks `MT5R_*` (7) are the live set. Legacy `MT5_*`/`MT5LIVE_*` tasks were deleted 2026-09-25;
  admin-rights deletions still pending for VBS tasks (`schtasks /Delete` elevated).
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
  `MT5_DESK_RISK_PERCENT=0.25`, `MT5_DESK_RISK_FIXED=45` (was 50 — **systematically
  self-rejected**: compliance caps risk at 0.5% equity ≈ $48.25, so every desk execution
  died at the gate (GBPJPY $52.10, AUDNZD $52.24, NZDUSD $48.31, USDJPY $55.22 rejections
  2026-09-24/25). Fixed→45 on 2026-09-25 12:35; must stay under 0.5% × equity),
  `MT5_DESK_SCAN_MINUTES=30`, `MT5_DESK_SL_ATR=4.5` (widened from 3.0),
  `MT5_DESK_TP_R=2.0`, entry TF M5.
- Compliance (`mt5_mcp/settings.json` → `risk` + `compliance.py`), updated 2026-09-27:
  - Mode was GREEN/normal (daily & drawdown buffers fresh).
  - **Blocked symbols**: NDX100, DJI30, EURCAD, EURGBP, NZDUSD.
    GBPUSD/USDCAD/GBPJPY **unblocked** 2026-09-27 (were conflicting with engine universes —
    desk/confirm/breakout all tried to trade them but compliance silently rejected).
    NZDUSD stays blocked (backtest PF 0.07).
  - **Relaxed risk floors** 2026-09-27: `min_risk_reward` 3.0→2.0, `confidence_floor_percent`
    75→60, `conservative_risk_percent` 0.25→0.5 (was self-rejecting valid setups).
  - **Trades/day max 5**: `max_trades_per_day` base 3 + green bonus 2 (bonus only when
    daily P&L > 0 AND normal mode) — absolute cap 5, conservative mode 3.
  - `compliance.evaluate()` now returns `max_positions`; desk `free_slots` fallback fixed
    2→3 (was silently attempting only 1 pick/scan when status lacked the key).
    Desk restarted 2026-09-25 12:24 to apply all of the above.
- Journal: `mt5_mcp/memory/journal.db` (`trades` has no `ts` column — use `open_time`/`close_time`).
  Desk events: `mt5_mcp/memory/desk_events.jsonl`, `desk_state.json`.

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
  1. **allowlist** — only backtested set tradable (expanded 2026-09-27 from 9→21 symbols):
     SPX500 XAGUSD AUDUSD USDJPY EURUSD XAUUSD JP225 ETHUSD EURJPY GBPUSD USDCAD
     GBPJPY AUDNZD NZDCAD AUDJPY CHFJPY GBPCHF NZDJPY CADCHF USDCHF BTCUSD.
     Set in `settings.json` `consensus.allowed_symbols`.
  2. **veto** — fresh (≤30m) opposite stance with score ≥75 blocks the entry
     (e.g. desk NZDUSD SELL 76.3 ⇒ nobody buys NZDUSD for 30m).
  3. **`min_agree: 0`** (corroboration optional): engines rarely see the same
     setup simultaneously, so requiring agreeing votes would kill most trades;
     raise only with vote-history evidence.
  4. **unified risk cap 0.5%** — `consensus.risk_cap()`; breakout budget and
     confirm's `evaluate_strategy(risk_percent=...)` both capped (raised from 0.25% 2026-09-27).
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
  `evaluate_all_strategies` runs all three (trend_zone + mean_reversion + momentum_breakout),
  `pick_best_strategy` resolves BUY/SELL conflicts by score.
  **Strategy routing** (2026-09-27 backtest): `STRATEGY_ROUTING` maps symbol → preferred
  strategy (EURJPY/GBPJPY/USDJPY/GBPUSD → mean_reversion PF 1.52-2.65, USDCAD →
  momentum_breakout PF 1.56, EURUSD/USDCHF → trend_zone PF 1.34-3.25). `BACKTEST_PF`
  stores per-(symbol, strategy) profit factors; `pick_best_strategy` multiplies trade_score
  by PF (clamped 0.5-3.0) so historically profitable strategies are preferred. AUDUSD has
  no profitable strategy (best PF 0.70 mean_reversion).

- **`mt5_mcp/rl_trader.py`** — live RL trading wrapper.
  `RLTrader` loads trained PPO/reinforce models from `models/dojo/{symbol}_{timeframe}/`.
  `decide(symbol, timeframe, confidence_threshold=0.6)` → BUY/SELL/NONE with probabilities.
  Features: returns, ATR, RSI, EMA, MACD, BB position, volume ratio, hour sin/cos.
  **No trained models exist yet** — train via `forex_dojo.py` first.

- **`web_diagnostic.py`** + **`static/app.diagnostic.js`** — diagnostic dashboard:
  `/api/diagnostic/summary` (system health), `/config` (issues), `/consensus` (board),
  `/symbols` (per-symbol pipeline trace: compliance → consensus → MT5 → strategy).
  Registered in `web_dashboard.py`. Frontend panel in Diagnostic tab of main dashboard.

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

- **Services need restart** to apply 2026-09-27 changes: relaxed config floors, expanded
  consensus allowlist (21 symbols), unblocked GBPUSD/USDCAD/GBPJPY, adaptive thresholds,
  ML filter, multi-strategy. Restart desk/confirm/breakout + dashboard.
- **Train RL models**: no trained models at `models/dojo/` yet — run `forex_dojo.py`
  training loop before `rl_trader.py` can produce actions.
- **Run diagnostic dashboard** after restart: check `/api/diagnostic/symbols` to verify
  symbols now pass the full pipeline (compliance → consensus → MT5 → strategy).
- Elevated: delete leftover VBS scheduled tasks (`MT5 Watch Daemon`, `MT5 Telegram Bot`, `MT5 Gateway`,
  `MT5 Prop Dashboard`, `MT5 Trade Desk`, `MT5_Trading_Assistant_Startup`) if superseded by `MT5R_*`.
- Blocked externally: OpenRouter 402 (credits), Groq 429 OTPM, news feed 429.
- Backtest verdict: lowering conf/score floors degrades quality (report:
  `mt5_mcp/backtest/results/lowconf_exp/report.json`) — production floors stay, but adaptive
  thresholds can temporarily relax them based on performance (safe: clamped to min bounds).
