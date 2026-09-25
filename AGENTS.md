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
- Desk (`trade_desk.py`, magic 20260601): `MT5_DESK_ENABLED=true`, `MT5_DESK_MODE=SIM`,
  `MT5_DESK_RISK_PERCENT=0.25`, `MT5_DESK_RISK_FIXED=50`, `MT5_DESK_SCAN_MINUTES=30`,
  `MT5_DESK_SL_ATR=4.5` (widened 2026-09-24 from 3.0 — restart desk to apply),
  `MT5_DESK_TP_R=2.0`, entry TF M5.
- Compliance: drawdown-based halt currently blocks trading (`compliance.py`); USDJPY/USDCAD on blocked list.
- Journal: `mt5_mcp/memory/journal.db` (`trades` has no `ts` column — use `open_time`/`close_time`).
  Desk events: `mt5_mcp/memory/desk_events.jsonl`, `desk_state.json`.

## Telegram (merged — status 2026-09-25)

- `mt5_mcp/telegram_bot.py` (1276 lines, `TradingTelegramBot`) is the single implementation:
  Groq Q&A + alerts + inline keyboards + trade execution + dashboards control +
  `/chart /scan /summary /at /atpause /atresume /research /heatmap /close /halt /resume`.
- `run_telegram_bot.py` is the runner (python-telegram-bot handlers; `/chart` replies with photo).
- Root `telegram_bot.py` is the legacy standalone bot — source of merged features; do not run both.
- Health endpoint port 8082; locks in `mt5_mcp/memory/telegram_bot.lock`.

## Current / next steps

- Restart Trade Desk service so `MT5_DESK_SL_ATR=4.5` applies.
- Elevated: delete leftover VBS scheduled tasks (`MT5 Watch Daemon`, `MT5 Telegram Bot`, `MT5 Gateway`,
  `MT5 Prop Dashboard`, `MT5 Trade Desk`, `MT5_Trading_Assistant_Startup`) if superseded by `MT5R_*`.
- Blocked externally: OpenRouter 402 (credits), Groq 429 OTPM, news feed 429.
- Backtest verdict: lowering conf/score floors degrades quality (report:
  `mt5_mcp/backtest/results/lowconf_exp/report.json`) — production floors stay.
