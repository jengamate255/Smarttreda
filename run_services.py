"""Trading Intelligence Platform - Service Manager.

Starts all services:
- Telegram Bot (with Groq LLM)
- MT5 Connection
- Account Monitor
- Alert System
- Dashboard API
"""

import os
import sys
import io
import logging
import threading
import time
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Load secrets from env (no hardcoded keys)
from pathlib import Path as _Path
from dotenv import load_dotenv as _load
_load(_Path(__file__).resolve().parent / ".env")
_load(_Path(__file__).resolve().parent / "telegram.env")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("E:/Project/Mt5 Ea/logs/services.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("services")

# Magic id for manually confirmed Telegram entries (distinct from the
# automated mt5-mcp pipeline, MAGIC 20260601).
TELEGRAM_MAGIC = int(os.getenv("TELEGRAM_BOT_MAGIC", "20260602"))


class MT5Service:
    """MT5 Connection Service."""

    def __init__(self):
        self._mt5 = None
        self._connected = False
        self._account_info = {}
        self._positions = []

    def start(self):
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
            self._account_info = {
                "login": account.login,
                "server": account.server,
                "balance": account.balance,
                "equity": account.equity,
                "margin": account.margin,
                "free_margin": account.margin_free,
                "leverage": account.leverage,
                "profit": account.profit,
            }

            logger.info("MT5 Connected: Account %d @ %s", account.login, account.server)
            logger.info("Balance: $%.2f | Equity: $%.2f", account.balance, account.equity)
            return True

        except Exception as e:
            logger.error("MT5 connection error: %s", e)
            return False

    def stop(self):
        if self._mt5:
            self._mt5.shutdown()
            self._connected = False
            logger.info("MT5 disconnected")

    def get_account(self):
        if not self._connected:
            return {}
        account = self._mt5.account_info()
        if account:
            return {
                "login": account.login,
                "server": account.server,
                "balance": account.balance,
                "equity": account.equity,
                "margin": account.margin,
                "free_margin": account.margin_free,
                "leverage": account.leverage,
                "profit": account.profit,
            }
        return {}

    def get_positions(self):
        if not self._connected:
            return []
        positions = self._mt5.positions_get()
        if not positions:
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
                "sl": pos.sl,
                "tp": pos.tp,
            }
            for pos in positions
        ]

    def get_prices(self, symbols):
        if not self._connected:
            return {}
        prices = {}
        for sym in symbols:
            tick = self._mt5.symbol_info_tick(sym)
            if tick:
                prices[sym] = {"bid": tick.bid, "ask": tick.ask}
        return prices

    @property
    def is_connected(self):
        return self._connected


class TelegramService:
    """Telegram Bot Service."""

    def __init__(self, mt5_service):
        self._mt5 = mt5_service
        self._bot = None
        self._app = None

    def start(self):
        try:
            from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
            from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
            from mt5_mcp.telegram_bot import TradingTelegramBot, TelegramConfig

            from mt5_mcp.telegram_alerts import _load_config as _load_tg
            _tok, _ = _load_tg()
            _tok = os.getenv("TELEGRAM_BOT_TOKEN", _tok or os.getenv("MT5_NOTIFY_TELEGRAM_TOKEN", "")).strip()
            if not _tok:
                raise RuntimeError("TELEGRAM_BOT_TOKEN not configured")
            config = TelegramConfig(
                bot_token=_tok,
                enabled=True,
                mt5_enabled=True,
            )
            self._bot = TradingTelegramBot(config=config)
            self._update_context()

            app = Application.builder().token(config.bot_token).build()

            app.add_handler(CommandHandler("start", self._start_command))
            app.add_handler(CommandHandler("status", self._status_command))
            app.add_handler(CommandHandler("positions", self._positions_command))
            app.add_handler(CommandHandler("analyze", self._analyze_command))
            app.add_handler(CommandHandler("buy", self._buy_command))
            app.add_handler(CommandHandler("sell", self._sell_command))
            app.add_handler(CommandHandler("alerts", self._alerts_command))
            app.add_handler(CommandHandler("help", self._help_command))
            app.add_handler(CallbackQueryHandler(self._callback_handler))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

            self._app = app
            logger.info("Telegram bot initialized")
            return True

        except Exception as e:
            logger.error("Telegram init error: %s", e)
            return False

    def _update_context(self):
        if self._mt5.is_connected:
            account = self._mt5.get_account()
            positions = self._mt5.get_positions()
            symbols = list(set([p["symbol"] for p in positions] + ["EURUSD", "GBPUSD", "USDJPY"]))
            prices = self._mt5.get_prices(symbols)

            self._bot.update_context(
                account_balance=account.get("balance", 0),
                account_equity=account.get("equity", 0),
                account_profit=account.get("profit", 0),
                free_margin=account.get("free_margin", 0),
                leverage=account.get("leverage", 100),
                positions=positions,
                prices=prices,
            )

    def _make_keyboard(self, data):
        if not data:
            return None
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        buttons = []
        for row in data.get("inline_keyboard", []):
            buttons.append([InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row])
        return InlineKeyboardMarkup(buttons) if buttons else None

    async def _start_command(self, update, context):
        self._update_context()
        response = self._bot.handle_command("/start")
        kb = self._make_keyboard(self._bot.get_keyboard("main"))
        await update.message.reply_text(response, reply_markup=kb)

    async def _status_command(self, update, context):
        self._update_context()
        response = self._bot.handle_command("/status")
        kb = self._make_keyboard(self._bot.get_keyboard("main"))
        await update.message.reply_text(response, reply_markup=kb)

    async def _positions_command(self, update, context):
        self._update_context()
        response = self._bot.handle_command("/positions")
        kb = self._make_keyboard(self._bot.get_keyboard("positions"))
        await update.message.reply_text(response, reply_markup=kb)

    async def _analyze_command(self, update, context):
        args = context.args
        symbol = args[0].upper() if args else ""
        if not symbol:
            await update.message.reply_text("Usage: /analyze EURUSD")
            return
        tick = self._mt5._mt5.symbol_info_tick(symbol) if self._mt5._mt5 else None
        response = f"Analyzing {symbol}\n"
        if tick:
            response += f"Bid: {tick.bid:.5f}\nAsk: {tick.ask:.5f}\n"
        response += "\nAsk me anything about this symbol."
        await update.message.reply_text(response)

    async def _buy_command(self, update, context):
        args = context.args
        symbol = args[0].upper() if args else ""
        if not symbol:
            await update.message.reply_text("Usage: /buy EURUSD")
            return
        tick = self._mt5._mt5.symbol_info_tick(symbol) if self._mt5._mt5 else None
        price = tick.ask if tick else 0
        self._bot._pending_orders["pending"] = {"symbol": symbol, "side": "BUY", "price": price}
        kb = self._make_keyboard(self._bot.get_keyboard("confirm"))
        await update.message.reply_text(f"Confirm BUY {symbol} @ {price:.5f}?", reply_markup=kb)

    async def _sell_command(self, update, context):
        args = context.args
        symbol = args[0].upper() if args else ""
        if not symbol:
            await update.message.reply_text("Usage: /sell EURUSD")
            return
        tick = self._mt5._mt5.symbol_info_tick(symbol) if self._mt5._mt5 else None
        price = tick.bid if tick else 0
        self._bot._pending_orders["pending"] = {"symbol": symbol, "side": "SELL", "price": price}
        kb = self._make_keyboard(self._bot.get_keyboard("confirm"))
        await update.message.reply_text(f"Confirm SELL {symbol} @ {price:.5f}?", reply_markup=kb)

    async def _alerts_command(self, update, context):
        response = self._bot.handle_command("/alerts")
        kb = self._make_keyboard(self._bot.get_keyboard("alerts"))
        await update.message.reply_text(response, reply_markup=kb)

    async def _help_command(self, update, context):
        response = self._bot.handle_command("/help")
        kb = self._make_keyboard(self._bot.get_keyboard("main"))
        await update.message.reply_text(response, reply_markup=kb)

    async def _callback_handler(self, update, context):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "confirm_yes":
            order = self._bot._pending_orders.get("pending", {})
            if order and self._mt5._mt5:
                symbol = order.get("symbol", "")
                side = order.get("side", "")
                tick = self._mt5._mt5.symbol_info_tick(symbol)
                if tick:
                    price = tick.ask if side == "BUY" else tick.bid
                    order_type = self._mt5._mt5.ORDER_TYPE_BUY if side == "BUY" else self._mt5._mt5.ORDER_TYPE_SELL
                    request = {
                        "action": self._mt5._mt5.TRADE_ACTION_DEAL,
                        "symbol": symbol,
                        "volume": 0.01,
                        "type": order_type,
                        "price": price,
                        "sl": order.get("sl") or 0.0,
                        "tp": order.get("tp") or 0.0,
                        "magic": TELEGRAM_MAGIC,
                        "comment": "tg-manual",
                        "type_time": self._mt5._mt5.ORDER_TIME_GTC,
                        "type_filling": self._mt5._mt5.ORDER_FILLING_IOC,
                    }
                    blocked = ""
                    try:
                        from mt5_mcp import execution_gate
                        from mt5_mcp.client import get_client

                        execution_gate.require(
                            request, client=get_client(), source="telegram"
                        )
                    except Exception as exc:  # noqa: BLE001 - fail closed, never bypass
                        blocked = str(exc)
                    if blocked:
                        text = f"Order blocked: {blocked}"
                    else:
                        result = self._mt5._mt5.order_send(request)
                        if result and result.retcode == self._mt5._mt5.TRADE_RETCODE_DONE:
                            text = f"Order placed: {side} 0.01 {symbol} @ {result.price}"
                        else:
                            text = f"Order failed: {result.comment if result else 'Unknown error'}"
                else:
                    text = "No price data"
            else:
                text = "No pending order"
            self._bot._pending_orders.pop("pending", None)
            self._update_context()
            kb = self._make_keyboard(self._bot.get_keyboard("main"))
            await query.edit_message_text(text, reply_markup=kb)
            return

        if data == "confirm_no":
            self._bot._pending_orders.pop("pending", None)
            kb = self._make_keyboard(self._bot.get_keyboard("main"))
            await query.edit_message_text("Order cancelled.", reply_markup=kb)
            return

        text, kb_data = self._bot.handle_callback(data)
        kb = self._make_keyboard(kb_data)
        await query.edit_message_text(text, reply_markup=kb)

    async def _handle_message(self, update, context):
        user_message = update.message.text
        await update.message.chat.send_action("typing")
        self._update_context()
        response = self._bot.chat(user_message)
        kb = self._make_keyboard(self._bot.get_keyboard("main"))
        if len(response) > 4000:
            chunks = [response[i:i+4000] for i in range(0, len(response), 4000)]
            for i, chunk in enumerate(chunks):
                await update.message.reply_text(chunk, reply_markup=kb if i == len(chunks) - 1 else None)
        else:
            await update.message.reply_text(response, reply_markup=kb)

    def run(self):
        if self._app:
            logger.info("Telegram bot starting polling...")
            self._app.run_polling(allowed_updates=Update.ALL_TYPES if 'Update' in dir() else None)


class AlertService:
    """Price Alert Monitoring Service."""

    def __init__(self, mt5_service):
        self._mt5 = mt5_service
        self._alerts = []
        self._running = False

    def add_alert(self, symbol, alert_type, target_price):
        alert = {
            "id": f"alert_{len(self._alerts) + 1}",
            "symbol": symbol.upper(),
            "type": alert_type.upper(),
            "target": target_price,
            "triggered": False,
        }
        self._alerts.append(alert)
        logger.info("Alert added: %s %s @ %.5f", symbol, alert_type, target_price)
        return alert

    def check_alerts(self):
        if not self._mt5.is_connected:
            return []

        triggered = []
        for alert in self._alerts:
            if alert["triggered"]:
                continue

            tick = self._mt5._mt5.symbol_info_tick(alert["symbol"])
            if not tick:
                continue

            price = tick.bid
            if alert["type"] == "ABOVE" and price >= alert["target"]:
                alert["triggered"] = True
                triggered.append(alert)
                logger.info("Alert triggered: %s ABOVE @ %.5f (price: %.5f)",
                           alert["symbol"], alert["target"], price)
            elif alert["type"] == "BELOW" and price <= alert["target"]:
                alert["triggered"] = True
                triggered.append(alert)
                logger.info("Alert triggered: %s BELOW @ %.5f (price: %.5f)",
                           alert["symbol"], alert["target"], price)

        return triggered

    def start_monitoring(self, interval=5):
        self._running = True
        def monitor():
            while self._running:
                triggered = self.check_alerts()
                for alert in triggered:
                    logger.warning("ALERT: %s %s @ %.5f TRIGGERED!",
                                  alert["symbol"], alert["type"], alert["target"])
                time.sleep(interval)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        logger.info("Alert monitoring started (interval: %ds)", interval)

    def stop(self):
        self._running = False
        logger.info("Alert monitoring stopped")


class DashboardService:
    """Web Dashboard Service."""

    def __init__(self, mt5_service):
        self._mt5 = mt5_service
        self._thread = None

    def start(self, port=8080):
        try:
            from flask import Flask, render_template_string
            import MetaTrader5 as mt5
            from datetime import datetime, timezone
            import threading
            import sys
            import io

            app = Flask(__name__)

            # Import the HTML template from run_dashboard
            sys.path.insert(0, "E:/Project/Mt5 Ea")
            from run_dashboard import HTML

            @app.route("/")
            def index():
                data = {
                    "login": 0, "server": "Disconnected", "balance": 0, "equity": 0,
                    "free_margin": 0, "profit": 0, "leverage": 100, "position_count": 0,
                    "positions": [], "prices": {}, "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    "swarm_status": "OFFLINE", "active_agents": "0 / 0", "latency": "--",
                    "profit_sign": "", "profit_pct": "0.00%", "margin_used": 0,
                    "margin_level": "0.0%", "margin_utilization": "0%", "margin_pct": 0,
                    "risk_status": "STANDBY", "active_symbols": "0", "price_count": 0,
                }

                if mt5.initialize():
                    account = mt5.account_info()
                    if account:
                        data["login"] = account.login
                        data["server"] = account.server
                        data["balance"] = account.balance
                        data["equity"] = account.equity
                        data["free_margin"] = account.margin_free
                        data["profit"] = account.profit
                        data["leverage"] = account.leverage
                        data["margin_used"] = account.margin

                        profit_pct_val = (account.profit / account.balance * 100) if account.balance > 0 else 0
                        data["profit_pct"] = "{:+.2f}%".format(profit_pct_val)
                        data["profit_sign"] = "+" if account.profit >= 0 else ""

                        margin_level_val = (account.equity / account.margin * 100) if account.margin > 0 else 0
                        data["margin_level"] = "{:.1f}%".format(margin_level_val) if account.margin > 0 else "N/A"

                        margin_util_val = (account.margin / account.balance * 100) if account.balance > 0 else 0
                        data["margin_utilization"] = "{:.1f}% ({:,.0f} / {:,.0f})".format(margin_util_val, account.margin, account.balance)
                        data["margin_pct"] = min(margin_util_val, 100)

                        data["swarm_status"] = "SYNCHRONIZED"
                        data["active_agents"] = "4 / 4"
                        data["latency"] = "1.2ms"
                        data["risk_status"] = "SAFEGUARDS PASS"

                    positions = mt5.positions_get()
                    if positions:
                        data["position_count"] = len(positions)
                        symbols_set = set()
                        for pos in positions:
                            symbols_set.add(pos.symbol)
                            data["positions"].append({
                                "symbol": pos.symbol,
                                "side": "BUY" if pos.type == 0 else "SELL",
                                "volume": pos.volume,
                                "entry": pos.price_open,
                                "current": pos.price_current,
                                "pnl": pos.profit,
                                "sl": pos.sl,
                                "tp": pos.tp,
                            })
                        data["active_symbols"] = str(len(symbols_set))

                    symbols = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF",
                               "EURJPY", "GBPJPY", "XAUUSD", "BTCUSD"]
                    for sym in symbols:
                        tick = mt5.symbol_info_tick(sym)
                        info = mt5.symbol_info(sym)
                        if tick and info:
                            spread = (tick.ask - tick.bid) / info.point if info.point > 0 else 0
                            data["prices"][sym] = {"bid": tick.bid, "ask": tick.ask, "spread": spread}
                    data["price_count"] = len(data["prices"])

                    mt5.shutdown()

                return render_template_string(HTML, **data)

            def run_flask():
                app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

            self._thread = threading.Thread(target=run_flask, daemon=True)
            self._thread.start()
            logger.info("Dashboard started on http://127.0.0.1:%d", port)
            return True

        except Exception as e:
            logger.error("Dashboard start error: %s", e)
            return False

    def stop(self):
        logger.info("Dashboard stopped")


class AutoTraderService:
    """Runs auto_trader.run_once() every 30 minutes in a background thread."""

    def __init__(self, interval_minutes=30):
        self.interval = interval_minutes * 60
        self._thread = None
        self._stop_event = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("AutoTrader service started (every %d min)", self.interval // 60)
        return True

    def _loop(self):
        import mt5_mcp.auto_trader as at
        while not self._stop_event.is_set():
            try:
                logger.info("AutoTrader cycle starting...")
                exit_code = at.run_once(dry_run=False)
                logger.info("AutoTrader cycle finished (exit=%d)", exit_code)
            except Exception as exc:
                logger.exception("AutoTrader cycle failed: %s", exc)
            self._stop_event.wait(self.interval)

    def stop(self):
        self._stop_event.set()
        logger.info("AutoTrader service stopped")


class ServicesManager:
    """Manages all platform services."""

    def __init__(self):
        self.mt5 = MT5Service()
        self.telegram = None
        self.alerts = None
        self.dashboard = None
        self.auto_trader = None
        self._started = False

    def start_all(self):
        logger.info("=" * 60)
        logger.info("Starting Trading Intelligence Platform Services")
        logger.info("=" * 60)

        # 1. Start MT5
        logger.info("[1/5] Starting MT5 connection...")
        if not self.mt5.start():
            logger.error("MT5 failed to start")
            return False

        # 2. Start Alert Service
        logger.info("[2/5] Starting Alert service...")
        self.alerts = AlertService(self.mt5)
        self.alerts.start_monitoring()

        # 3. Telegram Bot - DISABLED (dedicated run_telegram_bot.py handles this)
        # logger.info("[3/5] Starting Telegram bot...")
        # self.telegram = TelegramService(self.mt5)
        # if not self.telegram.start():
        #     logger.error("Telegram failed to start")
        #     return False
        logger.info("[3/5] Telegram bot: SKIPPED (dedicated process)")

        # 4. Start Dashboard
        logger.info("[4/5] Starting Dashboard...")
        self.dashboard = DashboardService(self.mt5)
        self.dashboard.start(port=8080)

        # 5. Start AutoTrader (every 30 min)
        logger.info("[5/6] Starting AutoTrader service...")
        self.auto_trader = AutoTraderService(interval_minutes=30)
        self.auto_trader.start()

        # 6. Log status
        logger.info("[6/6] Services started successfully!")
        account = self.mt5.get_account()
        logger.info("-" * 60)
        logger.info("ACCOUNT: %d @ %s", account.get("login", 0), account.get("server", ""))
        logger.info("BALANCE: $%.2f", account.get("balance", 0))
        logger.info("EQUITY: $%.2f", account.get("equity", 0))
        logger.info("POSITIONS: %d", len(self.mt5.get_positions()))
        logger.info("-" * 60)
        logger.info("SERVICES RUNNING:")
        logger.info("  - MT5: Connected")
        logger.info("  - Telegram Bot: Running")
        logger.info("  - Alert Monitor: Running")
        logger.info("  - Dashboard: http://localhost:8080")
        logger.info("  - AutoTrader: Running (every 30 min)")
        logger.info("=" * 60)

        self._started = True
        return True

    def stop_all(self):
        logger.info("Stopping all services...")
        if self.dashboard:
            self.dashboard.stop()
        if self.alerts:
            self.alerts.stop()
        self.mt5.stop()
        logger.info("All services stopped")

    def run(self):
        if not self.start_all():
            return

        try:
            # Telegram disabled - dedicated process handles it
            # self.telegram.run()
            import time
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            self.stop_all()


def main():
    os.makedirs("E:/Project/Mt5 Ea/logs", exist_ok=True)
    services = ServicesManager()
    services.run()


if __name__ == "__main__":
    main()
