"""Run the Telegram Bot with REAL MT5 Data.

Connects to live MetaTrader 5 terminal for real:
- Account balance, equity, margin
- Open positions with live PnL
- Live bid/ask prices
- Trade execution
- Price alerts
"""

import os
import sys
import io
import logging
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Load secrets from env files (production-ready: no hardcoded keys)
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(Path(__file__).resolve().parent / "telegram.env")
# Groq and Telegram tokens must be set via env; fail fast if missing
from mt5_mcp.telegram_alerts import _load_config as _load_tg_config
_tg_token, _tg_chat = _load_tg_config()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", _tg_token or os.getenv("MT5_NOTIFY_TELEGRAM_TOKEN", "")).strip()
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
if not TELEGRAM_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN not configured (set telegram.env or .env)")
if not GROQ_KEY:
    import logging as _lg
    _lg.getLogger(__name__).warning("GROQ_API_KEY not set - LLM fallback will be used")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from mt5_mcp.telegram_bot import TradingTelegramBot, TelegramConfig

# production logging to file + console
from pathlib import Path as _PLOG
_PLOG("logs").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler("logs/telegram_bot.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# rate limiting
_RATE_LIMIT = {}
_RATE_WINDOW = 2.0  # seconds per user
_RATE_MAX = 5  # burst

def _rate_limited(chat_id: int) -> bool:
    import time as _t
    now = _t.time()
    rec = _RATE_LIMIT.get(chat_id)
    if not rec:
        _RATE_LIMIT[chat_id] = [now, 1]
        return False
    last, count = rec
    if now - last > _RATE_WINDOW:
        _RATE_LIMIT[chat_id] = [now, 1]
        return False
    if count >= _RATE_MAX:
        return True
    _RATE_LIMIT[chat_id][1] += 1
    return False

# --- production: single-instance lock + auth ---
from pathlib import Path as _P
import time as _time
from contextlib import contextmanager as _cm

_LOCK_PATH = _P(__file__).resolve().parent / "mt5_mcp" / "memory" / "telegram_bot.lock"

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
            if not h:
                return False
            st = ctypes.windll.kernel32.WaitForSingleObject(h, 0)
            ctypes.windll.kernel32.CloseHandle(h)
            return st == 0x00000102
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True

@_cm
def _single_instance():
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                pid = int(_LOCK_PATH.read_text(encoding="utf-8").strip())
            except Exception:
                pid = 0
            if pid and _pid_alive(pid):
                raise SystemExit(f"another telegram bot is running (pid {pid}) - exiting to avoid 409 Conflict")
            try:
                _LOCK_PATH.unlink()
            except FileNotFoundError:
                pass
    else:
        raise SystemExit("could not acquire telegram lock")
    _time.sleep(1.5)
    try:
        owner = int(_LOCK_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        owner = 0
    if owner != os.getpid():
        raise SystemExit(f"lost telegram lock race to pid {owner} - exiting to avoid 409 Conflict")
    try:
        yield
    finally:
        try:
            _LOCK_PATH.unlink()
        except FileNotFoundError:
            pass

def _is_authorized(chat_id: int | None) -> bool:
    if chat_id is None:
        return False
    raw = []
    for k in ("TELEGRAM_CHAT_ID", "MT5_NOTIFY_TELEGRAM_CHAT_ID", "TELEGRAM_AUTHORIZED_IDS", "TELEGRAM_CHAT_IDS"):
        v = os.getenv(k, "").strip()
        if v:
            raw.extend(v.replace(",", " ").split())
    allowed = set()
    for p in raw:
        try:
            allowed.add(int(p.strip()))
        except ValueError:
            continue
    if not allowed:
        return True  # open if not configured
    return chat_id in allowed

def _start_health_server(bot: TradingTelegramBot, port: int = 8082):
    import threading, json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/health", "/ready"):
                data = bot.health_check() if self.path == "/health" else bot.ready_check()
                body = json.dumps(data, indent=2).encode()
                self.send_response(200 if data.get("status") != "error" else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404); self.end_headers()
        def log_message(self, fmt, *args):
            logger.debug("health %s", fmt % args)
    def run():
        try:
            srv = HTTPServer(("127.0.0.1", port), H)
            logger.info("Health server listening on http://127.0.0.1:%d/health", port)
            srv.serve_forever()
        except Exception as e:
            logger.warning("health server failed: %s", e)
    threading.Thread(target=run, daemon=True).start()

def _chat_id(update) -> int | None:
    try:
        if hasattr(update, "effective_chat") and update.effective_chat:
            return int(update.effective_chat.id)
        if hasattr(update, "callback_query") and update.callback_query and update.callback_query.message:
            return int(update.callback_query.message.chat.id)
    except Exception:
        pass
    return None

config = TelegramConfig(bot_token=TELEGRAM_TOKEN, enabled=True, mt5_enabled=True)
trading_bot = TradingTelegramBot(config=config)

# Distinct magic id so manually confirmed Telegram entries are attributed
# separately from the automated mt5-mcp pipeline (MAGIC 20260601).
TELEGRAM_MAGIC = int(os.getenv("TELEGRAM_BOT_MAGIC", "20260602"))


class MT5LiveConnector:
    """Connects to real MT5 terminal for live data."""

    def __init__(self):
        self._mt5 = None
        self._connected = False

    def connect(self) -> bool:
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5

            if not mt5.initialize():
                logger.error("MT5 initialize failed: %s", mt5.last_error())
                return False

            account = mt5.account_info()
            if account is None:
                logger.error("Failed to get account info")
                return False

            self._connected = True
            logger.info("Connected to MT5: Account %d, Server: %s", account.login, account.server)
            return True

        except ImportError:
            logger.error("MetaTrader5 not installed")
            return False
        except Exception as e:
            logger.error("MT5 connection error: %s", e)
            return False

    def disconnect(self):
        if self._mt5:
            self._mt5.shutdown()
            self._connected = False

    def get_account_info(self) -> dict:
        if not self._connected:
            return {}
        account = self._mt5.account_info()
        if account is None:
            return {}
        return {
            "balance": account.balance,
            "equity": account.equity,
            "margin": account.margin,
            "free_margin": account.margin_free,
            "leverage": account.leverage,
            "currency": account.currency,
            "profit": account.profit,
            "login": account.login,
            "server": account.server,
        }

    def get_positions(self) -> list[dict]:
        if not self._connected:
            return []
        positions = self._mt5.positions_get()
        if positions is None:
            return []
        return [
            {
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "side": "BUY" if pos.type == 0 else "SELL",
                "volume": pos.volume,
                "entry_price": pos.price_open,
                "current_price": pos.price_current,
                "pnl": pos.profit,
                "swap": pos.swap,
                "commission": pos.comment if hasattr(pos, 'comment') else "",
                "sl": pos.sl,
                "tp": pos.tp,
                "time": datetime.fromtimestamp(pos.time, tz=timezone.utc).isoformat(),
            }
            for pos in positions
        ]

    def get_prices(self, symbols: list[str]) -> dict:
        if not self._connected:
            return {}
        prices = {}
        for symbol in symbols:
            tick = self._mt5.symbol_info_tick(symbol)
            if tick:
                prices[symbol] = {"bid": tick.bid, "ask": tick.ask}
        return prices

    def get_tick(self, symbol: str) -> dict:
        if not self._connected:
            return {"bid": 0, "ask": 0}
        tick = self._mt5.symbol_info_tick(symbol)
        if tick is None:
            return {"bid": 0, "ask": 0}
        return {"bid": tick.bid, "ask": tick.ask}

    def place_order(self, symbol: str, side: str, volume: float,
                    sl: float = 0, tp: float = 0, comment: str = "") -> dict:
        if not self._connected:
            return {"success": False, "error": "Not connected to MT5"}

        tick = self._mt5.symbol_info_tick(symbol)
        if tick is None:
            return {"success": False, "error": f"No tick data for {symbol}"}

        price = tick.ask if side == "BUY" else tick.bid

        order_type = self._mt5.ORDER_TYPE_BUY if side == "BUY" else self._mt5.ORDER_TYPE_SELL

        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "magic": TELEGRAM_MAGIC,
            "comment": (comment or "tg-manual")[:30],
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }

        # Manual opens go through the same execution gate as the bot:
        # allowlist + compliance (SL required, risk cap, daily limits,
        # spread, session/news). Closes are never gated.
        try:
            from mt5_mcp import execution_gate
            from mt5_mcp.client import get_client

            execution_gate.require(
                request, client=get_client(), source="telegram"
            )
        except Exception as exc:  # noqa: BLE001 - fail closed, never bypass
            return {"success": False, "error": f"execution gate: {exc}"}

        result = self._mt5.order_send(request)
        if result is None:
            return {"success": False, "error": "Order send failed"}

        if result.retcode != self._mt5.TRADE_RETCODE_DONE:
            return {"success": False, "error": f"Order failed: {result.comment}"}

        return {
            "success": True,
            "ticket": result.order,
            "price": result.price,
            "volume": result.volume,
            "message": f"Order placed: {side} {volume} {symbol} @ {result.price}",
        }

    def close_position(self, ticket: int) -> dict:
        if not self._connected:
            return {"success": False, "error": "Not connected to MT5"}

        positions = self._mt5.positions_get(ticket=ticket)
        if not positions:
            return {"success": False, "error": f"Position {ticket} not found"}

        pos = positions[0]
        side = "SELL" if pos.type == 0 else "BUY"
        close_type = self._mt5.ORDER_TYPE_SELL if pos.type == 0 else self._mt5.ORDER_TYPE_BUY

        tick = self._mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return {"success": False, "error": f"No tick data for {pos.symbol}"}

        price = tick.bid if pos.type == 0 else tick.ask

        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "comment": "Telegram Close",
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }

        result = self._mt5.order_send(request)
        if result is None:
            return {"success": False, "error": "Close failed"}

        if result.retcode != self._mt5.TRADE_RETCODE_DONE:
            return {"success": False, "error": f"Close failed: {result.comment}"}

        return {"success": True, "message": f"Position {ticket} closed"}


mt5_connector = MT5LiveConnector()
mt5_connected = mt5_connector.connect()

if mt5_connected:
    account = mt5_connector.get_account_info()
    positions = mt5_connector.get_positions()
    symbols = list(set([p["symbol"] for p in positions] + ["EURUSD", "GBPUSD", "USDJPY"]))
    prices = mt5_connector.get_prices(symbols)

    trading_bot.update_context(
        account_balance=account.get("balance", 0),
        account_equity=account.get("equity", 0),
        account_profit=account.get("profit", 0),
        free_margin=account.get("free_margin", 0),
        leverage=account.get("leverage", 100),
        positions=positions,
        prices=prices,
    )
    logger.info("MT5 data loaded: Balance=%.2f, Equity=%.2f, Positions=%d",
                account.get("balance", 0), account.get("equity", 0), len(positions))
else:
    logger.warning("MT5 not connected, using demo data")
    trading_bot.update_context(
        account_balance=10000.0,
        account_equity=10350.0,
        account_profit=350.0,
        free_margin=8350.0,
        leverage=100,
        positions=[],
        prices={},
    )


def make_keyboard(data: dict | None) -> InlineKeyboardMarkup | None:
    if not data:
        return None
    buttons = []
    for row in data.get("inline_keyboard", []):
        buttons.append([InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row])
    return InlineKeyboardMarkup(buttons) if buttons else None


def refresh_mt5_data():
    """Refresh live data from MT5."""
    if not mt5_connected:
        return
    account = mt5_connector.get_account_info()
    positions = mt5_connector.get_positions()
    symbols = list(set([p["symbol"] for p in positions] + ["EURUSD", "GBPUSD", "USDJPY"]))
    prices = mt5_connector.get_prices(symbols)

    trading_bot.update_context(
        account_balance=account.get("balance", 0),
        account_equity=account.get("equity", 0),
        account_profit=account.get("profit", 0),
        free_margin=account.get("free_margin", 0),
        leverage=account.get("leverage", 100),
        positions=positions,
        prices=prices,
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        try:
            await update.message.reply_text("Unauthorized.")
        except Exception:
            pass
        return
    refresh_mt5_data()
    response = trading_bot.handle_command("/start")
    if mt5_connected:
        response += "\n\nLIVE MODE: Connected to MT5"
    kb = make_keyboard(trading_bot.get_keyboard("main"))
    await update.message.reply_text(response, reply_markup=kb)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    refresh_mt5_data()
    response = trading_bot.handle_command("/status")
    kb = make_keyboard(trading_bot.get_keyboard("main"))
    await update.message.reply_text(response, reply_markup=kb)


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    refresh_mt5_data()
    response = trading_bot.handle_command("/positions")
    kb = make_keyboard(trading_bot.get_keyboard("positions"))
    await update.message.reply_text(response, reply_markup=kb)


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    args = context.args
    symbol = args[0].upper() if args else ""
    if not symbol:
        await update.message.reply_text("Usage: /analyze EURUSD")
        return
    tick = mt5_connector.get_tick(symbol)
    response = f"Analyzing {symbol}\n"
    if tick["bid"] > 0:
        response += f"Bid: {tick['bid']:.5f}\nAsk: {tick['ask']:.5f}\n"
    response += "\nAsk me anything about this symbol for detailed analysis."
    await update.message.reply_text(response)


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    args = context.args
    symbol = args[0].upper() if args else ""
    if not symbol:
        await update.message.reply_text("Usage: /buy EURUSD")
        return
    tick = mt5_connector.get_tick(symbol)
    trading_bot._pending_orders["pending"] = {"symbol": symbol, "side": "BUY", "price": tick["ask"]}
    kb = make_keyboard(trading_bot.get_keyboard("confirm"))
    await update.message.reply_text(f"Confirm BUY {symbol} @ {tick['ask']:.5f}?", reply_markup=kb)


async def sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    args = context.args
    symbol = args[0].upper() if args else ""
    if not symbol:
        await update.message.reply_text("Usage: /sell EURUSD")
        return
    tick = mt5_connector.get_tick(symbol)
    trading_bot._pending_orders["pending"] = {"symbol": symbol, "side": "SELL", "price": tick["bid"]}
    kb = make_keyboard(trading_bot.get_keyboard("confirm"))
    await update.message.reply_text(f"Confirm SELL {symbol} @ {tick['bid']:.5f}?", reply_markup=kb)


async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.handle_command("/alerts")
    kb = make_keyboard(trading_bot.get_keyboard("alerts"))
    await update.message.reply_text(response, reply_markup=kb)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.handle_command("/help")
    kb = make_keyboard(trading_bot.get_keyboard("main"))
    await update.message.reply_text(response, reply_markup=kb)


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    args = context.args
    symbol = args[0].upper() if args else ""
    timeframe = args[1] if len(args) > 1 else "M1"
    if not symbol:
        await update.message.reply_text("Usage: /chart EURUSD [M1|M5|M15|H1|H4|D1]")
        return
    refresh_mt5_data()
    caption, png = trading_bot.cmd_chart(symbol, timeframe)
    if png:
        await update.message.reply_photo(photo=png, caption=caption)
    else:
        await update.message.reply_text(caption)


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    refresh_mt5_data()
    response = trading_bot.cmd_scan()
    await update.message.reply_text(response)


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    args = context.args
    try:
        days = int(args[0]) if args else 7
    except ValueError:
        days = 7
    response = trading_bot.cmd_summary(min(days, 90))
    await update.message.reply_text(response)


async def at_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_at()
    await update.message.reply_text(response)


async def atpause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_atpause()
    await update.message.reply_text(response)


async def atresume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_atresume()
    await update.message.reply_text(response)


async def research_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_research()
    await update.message.reply_text(response)


async def heatmap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_heatmap()
    await update.message.reply_text(response)


async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    args = context.args
    try:
        ticket = int(args[0]) if args else 0
    except (ValueError, IndexError):
        await update.message.reply_text("Usage: /close TICKET")
        return
    response = trading_bot.cmd_close(ticket)
    await update.message.reply_text(response)


async def halt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_halt()
    await update.message.reply_text(response)


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_resume()
    await update.message.reply_text(response)


async def desk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_desk()
    await update.message.reply_text(response, parse_mode="HTML")


async def consensus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_consensus()
    await update.message.reply_text(response, parse_mode="HTML")


async def challenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_challenge()
    await update.message.reply_text(response, parse_mode="HTML")


async def journal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    args = " ".join(context.args) if context.args else ""
    response = trading_bot.cmd_journal.__func__(trading_bot, int(args) if args.isdigit() else 10)
    await update.message.reply_text(response, parse_mode="HTML")


async def services_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    response = trading_bot.cmd_services()
    await update.message.reply_text(response, parse_mode="HTML")


async def strategy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        return
    args = " ".join(context.args) if context.args else ""
    response = trading_bot.cmd_strategy.__func__(trading_bot, args)
    await update.message.reply_text(response, parse_mode="HTML")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(_chat_id(update)):
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        return
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("close_"):
        ref = data.replace("close_", "")
        try:
            ticket = int(ref)
            result = mt5_connector.close_position(ticket)
            text = result.get("message", result.get("error", "Unknown error"))
            refresh_mt5_data()
        except ValueError:
            text = f"Invalid ticket: {ref}"
        kb = make_keyboard(trading_bot.get_keyboard("main"))
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data.startswith("buy_"):
        symbol = data.replace("buy_", "")
        tick = mt5_connector.get_tick(symbol)
        trading_bot._pending_orders["pending"] = {"symbol": symbol, "side": "BUY", "price": tick["ask"]}
        text, kb_data = trading_bot.handle_callback(data)
        kb = make_keyboard(kb_data)
        await query.edit_message_text(f"{text}\n@ {tick['ask']:.5f}", reply_markup=kb)
        return

    if data.startswith("sell_"):
        symbol = data.replace("sell_", "")
        tick = mt5_connector.get_tick(symbol)
        trading_bot._pending_orders["pending"] = {"symbol": symbol, "side": "SELL", "price": tick["bid"]}
        text, kb_data = trading_bot.handle_callback(data)
        kb = make_keyboard(kb_data)
        await query.edit_message_text(f"{text}\n@ {tick['bid']:.5f}", reply_markup=kb)
        return

    if data == "confirm_yes":
        order = trading_bot._pending_orders.get("pending", {})
        if order:
            result = mt5_connector.place_order(
                order.get("symbol", ""),
                order.get("side", ""),
                0.01,
                comment="Telegram Bot",
            )
            text = result.get("message", result.get("error", "Unknown error"))
            refresh_mt5_data()
        else:
            text = "No pending order."
        kb = make_keyboard(trading_bot.get_keyboard("main"))
        await query.edit_message_text(text, reply_markup=kb)
        return

    text, kb_data = trading_bot.handle_callback(data)
    kb = make_keyboard(kb_data)
    await query.edit_message_text(text, reply_markup=kb)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = _chat_id(update)
    if not _is_authorized(cid):
        return
    if cid and _rate_limited(cid):
        try:
            await update.message.reply_text("Rate limited. Please wait a moment.")
        except Exception:
            pass
        return
    user_message = update.message.text

    confirm_result = trading_bot.handle_confirm_message(user_message)
    if confirm_result:
        order = trading_bot._pending_orders.get("pending", {})
        if order:
            result = mt5_connector.place_order(
                order.get("symbol", ""),
                order.get("side", ""),
                0.01,
                comment="Telegram Bot",
            )
            confirm_result = result.get("message", result.get("error", confirm_result))
            refresh_mt5_data()
        kb = make_keyboard(trading_bot.get_keyboard("main"))
        await update.message.reply_text(confirm_result, reply_markup=kb)
        return

    alert_result = trading_bot.handle_alert_message(user_message)
    if alert_result:
        kb = make_keyboard(trading_bot.get_keyboard("alerts"))
        await update.message.reply_text(alert_result, reply_markup=kb)
        return

    await update.message.chat.send_action("typing")
    refresh_mt5_data()
    response = trading_bot.chat(user_message)
    kb = make_keyboard(trading_bot.get_keyboard("main"))

    if len(response) > 4000:
        chunks = [response[i:i+4000] for i in range(0, len(response), 4000)]
        for i, chunk in enumerate(chunks):
            await update.message.reply_text(chunk, reply_markup=kb if i == len(chunks) - 1 else None)
    else:
        await update.message.reply_text(response, reply_markup=kb)


def main():
    print("=" * 60)
    print("Trading Intelligence Telegram Bot v2.0 - LIVE MODE")
    print("=" * 60)
    print(f"Token: {TELEGRAM_TOKEN[:10]}...")
    print(f"Groq API: {'Configured' if os.getenv('GROQ_API_KEY') else 'Fallback'}")
    print(f"MT5: {'Connected' if mt5_connected else 'Not Connected'}")
    if mt5_connected:
        account = mt5_connector.get_account_info()
        print(f"Account: {account.get('login')} @ {account.get('server')}")
        print(f"Balance: ${account.get('balance', 0):,.2f}")
        print(f"Equity: ${account.get('equity', 0):,.2f}")
    # authorized chat
    _tok2, _chat = _load_tg_config()
    chat_disp = os.getenv("TELEGRAM_CHAT_ID", _chat or "?")
    print(f"Authorized chat: {chat_disp}")
    print("=" * 60)

    with _single_instance():
        _start_health_server(trading_bot, port=int(os.getenv("TELEGRAM_HEALTH_PORT", "8082")))
        application = Application.builder().token(TELEGRAM_TOKEN).build()

        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(CommandHandler("positions", positions_command))
        application.add_handler(CommandHandler("analyze", analyze_command))
        application.add_handler(CommandHandler("buy", buy_command))
        application.add_handler(CommandHandler("sell", sell_command))
        application.add_handler(CommandHandler("chart", chart_command))
        application.add_handler(CommandHandler("scan", scan_command))
        application.add_handler(CommandHandler("summary", summary_command))
        application.add_handler(CommandHandler("at", at_command))
        application.add_handler(CommandHandler("atpause", atpause_command))
        application.add_handler(CommandHandler("atresume", atresume_command))
        application.add_handler(CommandHandler("research", research_command))
        application.add_handler(CommandHandler("heatmap", heatmap_command))
        application.add_handler(CommandHandler("close", close_command))
        application.add_handler(CommandHandler("halt", halt_command))
        application.add_handler(CommandHandler("resume", resume_command))
        application.add_handler(CommandHandler("alerts", alerts_command))
        application.add_handler(CommandHandler("desk", desk_command))
        application.add_handler(CommandHandler("consensus", consensus_command))
        application.add_handler(CommandHandler("challenge", challenge_command))
        application.add_handler(CommandHandler("journal", journal_command))
        application.add_handler(CommandHandler("services", services_command))
        application.add_handler(CommandHandler("strategy", strategy_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CallbackQueryHandler(callback_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        print("Bot is running with LIVE MT5 data! (single-instance lock active)")
        print("Press Ctrl+C to stop.")
        # drop_pending_updates avoids replay on restart; handle Conflict gracefully
        try:
            application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, close_loop=False)
        except Exception as e:
            if "Conflict" in str(e) or "409" in str(e):
                logger.error("Telegram Conflict: another instance running. Exiting. %s", e)
                raise SystemExit(1)
            raise


if __name__ == "__main__":
    main()
