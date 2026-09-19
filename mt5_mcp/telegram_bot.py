"""Telegram Bot for Trading Intelligence Platform.

Connects to Telegram and uses Groq LLM to answer trade questions.
"""

from __future__ import annotations

import logging
import json
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("mt5_mcp.telegram")


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""
    webhook_url: str = ""
    enabled: bool = False


@dataclass
class TradeContext:
    symbol: str = ""
    current_price: float = 0.0
    positions: list[dict[str, Any]] = field(default_factory=list)
    recent_signals: list[dict[str, Any]] = field(default_factory=list)
    account_balance: float = 0.0
    account_equity: float = 0.0


class TradingTelegramBot:
    """Telegram bot for trade Q&A using Groq LLM."""

    def __init__(self, config: TelegramConfig | None = None):
        self.config = config or TelegramConfig()
        self._groq_client = None
        self._trade_context = TradeContext()
        self._message_history: list[dict[str, Any]] = []
        self._initialized = False

    def _init_groq(self):
        """Initialize Groq client."""
        if self._initialized:
            return

        try:
            from groq import Groq
            import os
            api_key = os.environ.get("GROQ_API_KEY", "")
            if api_key:
                self._groq_client = Groq(api_key=api_key)
                self._initialized = True
                logger.info("Groq client initialized")
            else:
                logger.warning("GROQ_API_KEY not set")
        except ImportError:
            logger.warning("groq package not installed")

    def update_context(self, **kwargs):
        """Update trade context with current market data."""
        for key, value in kwargs.items():
            if hasattr(self._trade_context, key):
                setattr(self._trade_context, key, value)

    def _build_system_prompt(self) -> str:
        ctx = self._trade_context
        positions_text = "None"
        if ctx.positions:
            positions_text = "\n".join([
                f"  - {p.get('symbol')} {p.get('side')} {p.get('volume')} lots @ {p.get('entry_price')} PnL: ${p.get('pnl', 0):.2f}"
                for p in ctx.positions
            ])

        signals_text = "None"
        if ctx.recent_signals:
            signals_text = "\n".join([
                f"  - {s.get('symbol')} {s.get('direction')} (strength: {s.get('strength', 0):.2f})"
                for s in ctx.recent_signals[-5:]
            ])

        return f"""You are a professional forex trading assistant. You help traders analyze markets, manage positions, and make informed decisions.

CURRENT MARKET CONTEXT:
- Account Balance: ${ctx.account_balance:,.2f}
- Account Equity: ${ctx.account_equity:,.2f}
- Open Positions:
{positions_text}
- Recent Signals:
{signals_text}

RULES:
1. Always provide risk-aware advice
2. Never guarantee profits
3. Suggest stop losses for all trades
4. Consider current market conditions
5. Be concise and actionable
6. If asked about a specific symbol, check positions first
7. For trade ideas, include entry, SL, and TP levels"""

    def _build_messages(self, user_message: str) -> list[dict[str, str]]:
        """Build message array for Groq API."""
        messages = [
            {"role": "system", "content": self._build_system_prompt()},
        ]

        # Add recent history (last 10 messages)
        for msg in self._message_history[-10:]:
            messages.append(msg)

        messages.append({"role": "user", "content": user_message})
        return messages

    def chat(self, user_message: str) -> str:
        """Process a chat message and return response."""
        self._init_groq()

        if not self._groq_client:
            return self._fallback_response(user_message)

        try:
            messages = self._build_messages(user_message)

            response = self._groq_client.chat.completions.create(
                model="qwen/qwen3-8b",
                messages=messages,
                temperature=0.7,
                max_tokens=1024,
            )

            assistant_message = response.choices[0].message.content

            # Update history
            self._message_history.append({"role": "user", "content": user_message})
            self._message_history.append({"role": "assistant", "content": assistant_message})

            # Keep history manageable
            if len(self._message_history) > 20:
                self._message_history = self._message_history[-20:]

            return assistant_message

        except Exception as e:
            logger.error("Groq API error: %s", e)
            return f"Error: {str(e)}"

    def _fallback_response(self, message: str) -> str:
        """Fallback when Groq is not available."""
        msg_lower = message.lower()

        if any(w in msg_lower for w in ["position", "open", "trade"]):
            if self._trade_context.positions:
                pos_list = "\n".join([
                    f"• {p.get('symbol')} {p.get('side')} {p.get('volume')} lots"
                    for p in self._trade_context.positions
                ])
                return f"Open positions:\n{pos_list}"
            return "No open positions."

        if any(w in msg_lower for w in ["balance", "equity", "account"]):
            return (f"Account:\n"
                   f"Balance: ${self._trade_context.account_balance:,.2f}\n"
                   f"Equity: ${self._trade_context.account_equity:,.2f}")

        if any(w in msg_lower for w in ["price", "market", "eurusd", "gbpusd"]):
            return f"Current price data not available. Use /refresh to update."

        return ("I can help with:\n"
                "• Position analysis\n"
                "• Trade ideas\n"
                "• Risk management\n"
                "• Market analysis\n\n"
                "Groq LLM not connected. Set GROQ_API_KEY environment variable.")

    def handle_command(self, command: str, args: str = "") -> str:
        """Handle bot commands."""
        if command == "/start":
            return ("Welcome to Trading Intelligence Bot!\n\n"
                   "Commands:\n"
                   "/status - Account status\n"
                   "/positions - Open positions\n"
                   "/analyze SYMBOL - Analyze a symbol\n"
                   "/help - Show this help\n\n"
                   "Or just ask me anything about trading!")

        elif command == "/status":
            ctx = self._trade_context
            return (f"Account Status:\n"
                   f"Balance: ${ctx.account_balance:,.2f}\n"
                   f"Equity: ${ctx.account_equity:,.2f}\n"
                   f"Positions: {len(ctx.positions)}")

        elif command == "/positions":
            if not self._trade_context.positions:
                return "No open positions."
            lines = []
            for p in self._trade_context.positions:
                pnl = p.get('pnl', 0)
                emoji = "+" if pnl >= 0 else ""
                lines.append(f"• {p.get('symbol')} {p.get('side')} {p.get('volume')} lots "
                           f"@ {p.get('entry_price')} → {emoji}${pnl:.2f}")
            return "Open Positions:\n" + "\n".join(lines)

        elif command == "/analyze":
            symbol = args.upper().strip()
            if not symbol:
                return "Usage: /analyze EURUSD"
            return f"Analyzing {symbol}...\n(Use natural language for detailed analysis)"

        elif command == "/help":
            return self.handle_command("/start")

        return f"Unknown command: {command}"

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "success",
            "bot_configured": bool(self.config.bot_token),
            "groq_connected": self._initialized,
            "context": {
                "symbol": self._trade_context.symbol,
                "positions": len(self._trade_context.positions),
                "balance": self._trade_context.account_balance,
            },
            "message_history": len(self._message_history),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
