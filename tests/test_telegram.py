"""Tests for Telegram Bot v2.0 with Keyboards, Trade Execution, Alerts."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def _can_connect_mt5():
    """Check if MT5 can actually initialize (skip tests if it can)."""
    try:
        import MetaTrader5 as mt5
        return mt5.initialize(timeout=1000)
    except Exception:
        return False

_HAS_MT5 = _can_connect_mt5()

from mt5_mcp.telegram_bot import TradingTelegramBot, TelegramConfig, TradeContext, Alert


class TestTelegramConfig:

    def test_default_config(self):
        config = TelegramConfig()
        assert config.bot_token == ""
        assert config.enabled is False
        assert config.mt5_enabled is True
        assert config.alerts_enabled is True

    def test_custom_config(self):
        config = TelegramConfig(bot_token="123:ABC", chat_id="12345", enabled=True)
        assert config.bot_token == "123:ABC"
        assert config.chat_id == "12345"
        assert config.enabled is True


class TestTradeContext:

    def test_default_context(self):
        ctx = TradeContext()
        assert ctx.symbol == ""
        assert ctx.positions == []
        assert ctx.account_balance == 0.0
        assert ctx.prices == {}

    def test_prices_context(self):
        ctx = TradeContext(prices={"EURUSD": {"bid": 1.1, "ask": 1.1002}})
        assert ctx.prices["EURUSD"]["bid"] == 1.1


class TestAlert:

    def test_alert_creation(self):
        from datetime import datetime, timezone
        alert = Alert(
            alert_id="alert_1",
            symbol="EURUSD",
            alert_type="ABOVE",
            target_price=1.1200,
            current_price=0.0,
            created_at=datetime.now(timezone.utc),
        )
        assert alert.symbol == "EURUSD"
        assert alert.triggered is False


@pytest.mark.skipif(_HAS_MT5, reason="tests require MT5 unavailable (mock mode)")
class TestTradingTelegramBot:

    def test_initialization(self):
        bot = TradingTelegramBot()
        assert bot._initialized is False
        assert bot._groq_client is None
        assert bot._alerts == []
        assert bot._pending_orders == {}

    def test_update_context(self):
        bot = TradingTelegramBot()
        bot.update_context(
            symbol="EURUSD",
            account_balance=10000.0,
            positions=[{"symbol": "EURUSD", "side": "BUY", "pnl": 50.0}],
        )
        assert bot._trade_context.symbol == "EURUSD"
        assert bot._trade_context.account_balance == 10000.0
        assert len(bot._trade_context.positions) == 1

    def test_system_prompt(self):
        bot = TradingTelegramBot()
        bot.update_context(account_balance=10000.0, account_equity=10200.0)
        prompt = bot._build_system_prompt()
        assert "10,000.00" in prompt
        assert "10,200.00" in prompt
        assert "trading assistant" in prompt.lower()

    def test_system_prompt_with_prices(self):
        bot = TradingTelegramBot()
        bot.update_context(
            account_balance=10000.0,
            prices={"EURUSD": {"bid": 1.10250, "ask": 1.10270}},
        )
        prompt = bot._build_system_prompt()
        assert "1.10250" in prompt
        assert "1.10270" in prompt

    def test_fallback_response_positions(self):
        bot = TradingTelegramBot()
        bot.update_context(positions=[
            {"symbol": "EURUSD", "side": "BUY", "volume": 0.1},
        ])
        response = bot._fallback_response("show me my positions")
        assert "EURUSD" in response
        assert "BUY" in response

    def test_fallback_response_balance(self):
        bot = TradingTelegramBot()
        bot.update_context(account_balance=50000.0, account_equity=51000.0)
        response = bot._fallback_response("what is my balance")
        assert "50,000" in response
        assert "51,000" in response

    def test_handle_command_start(self):
        bot = TradingTelegramBot()
        response = bot.handle_command("/start")
        assert "Welcome" in response
        assert "/status" in response
        assert "/buy" in response
        assert "/sell" in response
        assert "/alerts" in response

    def test_handle_command_status(self):
        bot = TradingTelegramBot()
        bot.update_context(account_balance=10000.0, account_equity=10500.0, account_profit=500.0)
        response = bot.handle_command("/status")
        assert "10,000" in response
        assert "10,500" in response
        assert "+$500" in response

    def test_handle_command_positions_empty(self):
        bot = TradingTelegramBot()
        response = bot.handle_command("/positions")
        assert "No open positions" in response

    def test_handle_command_positions_with_trades(self):
        bot = TradingTelegramBot()
        bot.update_context(positions=[
            {"symbol": "EURUSD", "side": "BUY", "volume": 0.1, "entry_price": 1.1, "pnl": 50.0},
            {"symbol": "GBPUSD", "side": "SELL", "volume": 0.05, "entry_price": 1.27, "pnl": -20.0},
        ])
        response = bot.handle_command("/positions")
        assert "EURUSD" in response
        assert "GBPUSD" in response
        assert "+$50" in response

    def test_handle_command_analyze(self):
        bot = TradingTelegramBot()
        bot.update_context(prices={"EURUSD": {"bid": 1.10250, "ask": 1.10270}})
        response = bot.handle_command("/analyze", "EURUSD")
        assert "EURUSD" in response
        assert "1.10250" in response

    def test_handle_command_buy(self):
        bot = TradingTelegramBot()
        response = bot.handle_command("/buy", "EURUSD")
        assert "BUY" in response or "buy" in response.lower()

    def test_handle_command_sell(self):
        bot = TradingTelegramBot()
        response = bot.handle_command("/sell", "EURUSD")
        assert "SELL" in response or "sell" in response.lower()

    def test_handle_command_alerts_empty(self):
        bot = TradingTelegramBot()
        response = bot.handle_command("/alerts")
        assert "No active alerts" in response

    def test_execute_trade(self):
        bot = TradingTelegramBot()
        result = bot.execute_trade("EURUSD", "BUY", 0.1, sl=1.0950, tp=1.1100)
        assert result["success"] is True
        assert result["symbol"] == "EURUSD"
        assert result["side"] == "BUY"
        assert result["volume"] == 0.1
        assert result["sl"] == 1.0950
        assert result["tp"] == 1.1100

    def test_close_position(self):
        bot = TradingTelegramBot()
        result = bot.close_position(1001)
        assert result["success"] is True
        assert result["ticket"] == 1001

    def test_add_alert(self):
        bot = TradingTelegramBot()
        alert = bot.add_alert("EURUSD", "ABOVE", 1.1200)
        assert alert.symbol == "EURUSD"
        assert alert.alert_type == "ABOVE"
        assert alert.target_price == 1.1200
        assert len(bot._alerts) == 1

    def test_check_alerts_triggered(self):
        bot = TradingTelegramBot()
        bot.add_alert("EURUSD", "ABOVE", 1.1200)
        triggered = bot.check_alerts("EURUSD", 1.1250)
        assert len(triggered) == 1
        assert triggered[0].triggered is True

    def test_check_alerts_not_triggered(self):
        bot = TradingTelegramBot()
        bot.add_alert("EURUSD", "ABOVE", 1.1200)
        triggered = bot.check_alerts("EURUSD", 1.1150)
        assert len(triggered) == 0

    def test_check_alerts_below(self):
        bot = TradingTelegramBot()
        bot.add_alert("EURUSD", "BELOW", 1.1000)
        triggered = bot.check_alerts("EURUSD", 1.0950)
        assert len(triggered) == 1

    def test_get_keyboard_main(self):
        bot = TradingTelegramBot()
        kb = bot.get_keyboard("main")
        assert "inline_keyboard" in kb
        assert len(kb["inline_keyboard"]) == 3

    def test_get_keyboard_trade(self):
        bot = TradingTelegramBot()
        kb = bot.get_keyboard("trade", symbol="EURUSD")
        assert "inline_keyboard" in kb
        assert "Buy EURUSD" in str(kb)

    def test_get_keyboard_confirm(self):
        bot = TradingTelegramBot()
        kb = bot.get_keyboard("confirm")
        assert "inline_keyboard" in kb
        assert "CONFIRM" in str(kb)
        assert "CANCEL" in str(kb)

    def test_get_keyboard_positions(self):
        bot = TradingTelegramBot()
        bot.update_context(positions=[
            {"symbol": "EURUSD", "side": "BUY", "pnl": 50.0},
        ])
        kb = bot.get_keyboard("positions")
        assert "inline_keyboard" in kb

    def test_get_keyboard_alerts(self):
        bot = TradingTelegramBot()
        bot.add_alert("EURUSD", "ABOVE", 1.1200)
        kb = bot.get_keyboard("alerts")
        assert "inline_keyboard" in kb

    def test_handle_callback_account(self):
        bot = TradingTelegramBot()
        bot.update_context(account_balance=10000.0)
        text, kb = bot.handle_callback("account")
        assert "10,000" in text
        assert kb is not None

    def test_handle_callback_positions(self):
        bot = TradingTelegramBot()
        bot.update_context(positions=[
            {"symbol": "EURUSD", "side": "BUY", "pnl": 50.0, "ticket": 1001},
        ])
        text, kb = bot.handle_callback("positions")
        assert "EURUSD" in text

    def test_handle_callback_trade(self):
        bot = TradingTelegramBot()
        text, kb = bot.handle_callback("trade")
        assert "Select symbol" in text

    def test_handle_callback_buy(self):
        bot = TradingTelegramBot()
        text, kb = bot.handle_callback("buy_EURUSD")
        assert "BUY" in text
        assert bot._pending_orders["pending"]["symbol"] == "EURUSD"

    def test_handle_callback_sell(self):
        bot = TradingTelegramBot()
        text, kb = bot.handle_callback("sell_EURUSD")
        assert "SELL" in text

    def test_handle_callback_confirm_yes(self):
        bot = TradingTelegramBot()
        bot._pending_orders["pending"] = {"symbol": "EURUSD", "side": "BUY"}
        text, kb = bot.handle_callback("confirm_yes")
        assert "Order placed" in text or "BUY" in text

    def test_handle_callback_confirm_no(self):
        bot = TradingTelegramBot()
        text, kb = bot.handle_callback("confirm_no")
        assert "cancelled" in text.lower()

    def test_handle_callback_addalert(self):
        bot = TradingTelegramBot()
        text, kb = bot.handle_callback("addalert")
        assert "ALERT" in text
        assert "ABOVE" in text

    def test_handle_callback_delalert(self):
        bot = TradingTelegramBot()
        bot.add_alert("EURUSD", "ABOVE", 1.1200)
        assert len(bot._alerts) == 1
        text, kb = bot.handle_callback("delalert_alert_1")
        assert len(bot._alerts) == 0
        assert "deleted" in text.lower()

    def test_handle_alert_message_valid(self):
        bot = TradingTelegramBot()
        result = bot.handle_alert_message("ALERT EURUSD ABOVE 1.12000")
        assert result is not None
        assert "EURUSD" in result
        assert len(bot._alerts) == 1

    def test_handle_alert_message_invalid_price(self):
        bot = TradingTelegramBot()
        result = bot.handle_alert_message("ALERT EURUSD ABOVE abc")
        assert result is not None
        assert "Invalid" in result

    def test_handle_alert_message_invalid_type(self):
        bot = TradingTelegramBot()
        result = bot.handle_alert_message("ALERT EURUSD SIDEWAYS 1.12000")
        assert result is not None
        assert "ABOVE or BELOW" in result

    def test_handle_alert_message_not_alert(self):
        bot = TradingTelegramBot()
        result = bot.handle_alert_message("hello world")
        assert result is None

    def test_handle_confirm_message_confirm(self):
        bot = TradingTelegramBot()
        bot._pending_orders["pending"] = {"symbol": "EURUSD", "side": "BUY"}
        result = bot.handle_confirm_message("CONFIRM")
        assert result is not None
        assert "Order placed" in result or "BUY" in result

    def test_handle_confirm_message_cancel(self):
        bot = TradingTelegramBot()
        result = bot.handle_confirm_message("CANCEL")
        assert result is not None
        assert "cancelled" in result.lower()

    def test_handle_confirm_message_other(self):
        bot = TradingTelegramBot()
        result = bot.handle_confirm_message("something else")
        assert result is None

    def test_health_check(self):
        bot = TradingTelegramBot()
        result = bot.health_check()
        assert result["status"] == "success"
        assert result["groq_connected"] is False
        assert result["bot_configured"] is False
        assert result["alerts"] == 0

    def test_health_check_with_alerts(self):
        bot = TradingTelegramBot()
        bot.add_alert("EURUSD", "ABOVE", 1.1200)
        result = bot.health_check()
        assert result["alerts"] == 1

    def test_chat_without_groq(self):
        bot = TradingTelegramBot()
        response = bot.chat("hello")
        assert len(response) > 0

    def test_message_history(self):
        bot = TradingTelegramBot()
        bot._message_history = [{"role": "user", "content": "test"}]
        messages = bot._build_messages("new message")
        assert len(messages) == 3

    def test_get_price(self):
        bot = TradingTelegramBot()
        bot.update_context(prices={"EURUSD": {"bid": 1.1025, "ask": 1.1027}})
        price = bot._get_price("EURUSD")
        assert price["bid"] == 1.1025
        assert price["ask"] == 1.1027

    def test_get_price_unknown(self):
        bot = TradingTelegramBot()
        price = bot._get_price("XYZXYZ")
        assert price["bid"] == 0.0
