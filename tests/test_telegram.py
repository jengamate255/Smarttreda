"""Tests for Telegram Bot with Groq integration."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mt5_mcp.telegram_bot import TradingTelegramBot, TelegramConfig, TradeContext


class TestTelegramConfig:

    def test_default_config(self):
        config = TelegramConfig()
        assert config.bot_token == ""
        assert config.enabled is False

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


class TestTradingTelegramBot:

    def test_initialization(self):
        bot = TradingTelegramBot()
        assert bot._initialized is False
        assert bot._groq_client is None

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

    def test_fallback_response_no_positions(self):
        bot = TradingTelegramBot()
        response = bot._fallback_response("show positions")
        assert "No open positions" in response

    def test_fallback_response_general(self):
        bot = TradingTelegramBot()
        response = bot._fallback_response("hello")
        assert "Trading Intelligence Bot" in response or "help" in response.lower()

    def test_handle_command_start(self):
        bot = TradingTelegramBot()
        response = bot.handle_command("/start")
        assert "Welcome" in response
        assert "/status" in response
        assert "/positions" in response

    def test_handle_command_status(self):
        bot = TradingTelegramBot()
        bot.update_context(account_balance=10000.0, account_equity=10500.0)
        response = bot.handle_command("/status")
        assert "10,000" in response
        assert "10,500" in response

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
        response = bot.handle_command("/analyze", "EURUSD")
        assert "EURUSD" in response

    def test_handle_command_analyze_no_symbol(self):
        bot = TradingTelegramBot()
        response = bot.handle_command("/analyze", "")
        assert "Usage" in response

    def test_handle_command_help(self):
        bot = TradingTelegramBot()
        response = bot.handle_command("/help")
        assert "Welcome" in response or "help" in response.lower()

    def test_handle_command_unknown(self):
        bot = TradingTelegramBot()
        response = bot.handle_command("/unknown")
        assert "Unknown command" in response

    def test_chat_without_groq(self):
        bot = TradingTelegramBot()
        response = bot.chat("hello")
        assert len(response) > 0

    def test_message_history(self):
        bot = TradingTelegramBot()
        bot._message_history = [{"role": "user", "content": "test"}]
        messages = bot._build_messages("new message")
        assert len(messages) == 3  # system + history + new

    def test_health_check(self):
        bot = TradingTelegramBot()
        result = bot.health_check()
        assert result["status"] == "success"
        assert result["groq_connected"] is False
        assert result["bot_configured"] is False

    def test_health_check_configured(self):
        config = TelegramConfig(bot_token="123:ABC")
        bot = TradingTelegramBot(config=config)
        result = bot.health_check()
        assert result["bot_configured"] is True
