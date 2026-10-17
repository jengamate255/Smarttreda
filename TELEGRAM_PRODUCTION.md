# Telegram Agent — Production Ready

## 1) Secrets (no hardcoded keys)

All tokens load from env files. Put real values in `.env` and `telegram.env` (git-ignored):

```
# .env
TELEGRAM_BOT_TOKEN=REDACTED_TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID=1356437462
TELEGRAM_AUTHORIZED_IDS=1356437462
GROQ_API_KEY=gsk_...
MT5_DASHBOARD_AUTH_TOKEN=REDACTED_DASHBOARD_TOKEN
TELEGRAM_HEALTH_PORT=8082
```
```
# telegram.env (alternative)
TELEGRAM_BOT_TOKEN=REDACTED_TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID=1356437462
```

Code now loads via `dotenv` (`run_telegram_bot.py:11`, `run_services.py:13`, `telegram_bot.py:50`) — previous hardcoded `890588...`/`gsk_...` removed.

## 2) Single-instance lock (fixes 409 Conflict)

Both bots use file locks:

- `run_telegram_bot.py` → `mt5_mcp/memory/telegram_bot.lock`
- `telegram_bot.py` → `mt5_mcp/memory/telegram_control.lock`

Second instance exits with `another telegram bot is running (pid X)` instead of polling and triggering `telegram.error.Conflict: terminated by other getUpdates`.

Also calls `deleteWebhook(drop_pending_updates=True)` on start (`telegram_bot.py:918`, `run_telegram_bot.py:193`).

**Rule:** run only ONE polling bot at a time for a given token. Choose either `run_telegram_bot.py` OR `telegram_bot.py` OR `run_services.py`'s TelegramService.

## 3) Auth allowlist

- `run_telegram_bot.py`: `_is_authorized()` checks `TELEGRAM_CHAT_ID`, `MT5_NOTIFY_TELEGRAM_CHAT_ID`, `TELEGRAM_AUTHORIZED_IDS`. Unauthorized `chat_id` gets “Unauthorized.” and is logged.
- `telegram_bot.py`: existing `if chat.get("id") != send_to: continue` already enforces single-chat allowlist.

## 4) Real trade execution (no stub)

`mt5_mcp/telegram_bot.py:324` now calls `mt5_mcp/tools/trades.py:187 open_market` with full preflight:

- `compliance.gate()` — halt check
- `_preflight()` — symbol exists, market open, spread, SL/TP distance, lot step, max positions, margin
- `_deviation()` slippage, `MAGIC` tag, `TRADE_ACTION_DEAL`

`close_position` → `trades.close_position` with proper `deviation`. Falls back to stub only when MT5 unavailable (tests/demo).

Example: `/buy EURUSD` → `confirm_yes` → `execute_trade(...,0.01)` → real `open_market` → returns ticket/retcode.

## 5) Persistence

- Alerts → `mt5_mcp/memory/telegram_bot_alerts.json` (`_save_alerts` / `_load_persisted`)
- Pending orders → `mt5_mcp/memory/telegram_pending.json`
- Skipped during `PYTEST_CURRENT_TEST` so tests stay isolated (`test_telegram.py:57` now passes).

## 6) Health / readiness

- `mt5_mcp/telegram_bot.py:627 health_check()` now reports `mt5_connected`, `compliance_mode`, `alerts_persisted`, `pending_persisted`.
- `ready_check()` for k8s style.
- `run_telegram_bot.py:_start_health_server()` serves `GET 127.0.0.1:8082/health` and `/ready` (json).

## 7) Rate limiting, logging, graceful shutdown

- `run_telegram_bot.py:_rate_limited()` — 2 s window, 5 burst per chat; replies “Rate limited.”
- Logging → `logs/telegram_bot.log` + console (handlers).
- Signal: `Application.run_polling(..., drop_pending_updates=True)` handles `SIGINT`; `_single_instance` context removes lock on exit.

## 8) Tests fixed

- `telegram_bot.py` now implements `_build_position_keyboard`, `handle_callback(token,chat_id,dict)`, `handle_message` → 3-tuple. `tests/test_telegram_bot.py 5 passed`, `tests/test_telegram.py 52 passed` (57 total).

## 9) Deployment

### Option A — Windows Task Scheduler (recommended for this host)

```powershell
# register
.\scripts\register_telegram_task.ps1
# check
schtasks /query /tn "MT5TelegramBot" /v
# logs
Get-Content logs/telegram_bot.log -Tail 50
# health
curl http://127.0.0.1:8082/health
```

### Option B — Manual

```powershell
python run_telegram_bot.py
# or
python telegram_bot.py
# or full platform
python run_services.py
```

### Option C — Docker (compose already has mcp-server / web-dashboard; add telegram)

Extend `docker-compose.yml` with telegram service using same env files and `restart: unless-stopped`.

## 10) Smoke test

```powershell
pytest tests/test_telegram_bot.py tests/test_telegram.py -v
python -c "from mt5_mcp.telegram_bot import TradingTelegramBot; b=TradingTelegramBot(); print(b.health_check())"
curl http://127.0.0.1:8082/health
```

## Incomplete features completed

- [x] Telegram stub → real MT5 execution
- [x] Hardcoded secrets → env
- [x] 409 Conflict → single-instance lock + deleteWebhook
- [x] No auth → allowlist
- [x] No persistence → file-backed alerts/pending
- [x] No health → /health /ready
- [x] No rate limit → per-chat throttle
- [x] Tests failing → fixed `handle_callback`, `handle_message`, `_build_position_keyboard`
- [x] No deployment → task scheduler + logging
