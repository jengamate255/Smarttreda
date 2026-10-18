import os
import sys
import io

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

from mt5_mcp.telegram_bot import TradingTelegramBot

bot = TradingTelegramBot()

bot.update_context(
    account_balance=10000.0,
    account_equity=10350.0,
    positions=[
        {"symbol": "EURUSD", "side": "BUY", "volume": 0.1, "entry_price": 1.1000, "pnl": 250.0},
        {"symbol": "GBPUSD", "side": "SELL", "volume": 0.05, "entry_price": 1.2750, "pnl": -50.0},
        {"symbol": "USDJPY", "side": "BUY", "volume": 0.08, "entry_price": 149.00, "pnl": 150.0},
    ],
    recent_signals=[
        {"symbol": "EURUSD", "direction": "LONG", "strength": 0.75},
        {"symbol": "GBPUSD", "direction": "SHORT", "strength": 0.60},
        {"symbol": "USDJPY", "direction": "LONG", "strength": 0.80},
    ]
)

print("=" * 70)
print("TELEGRAM BOT + GROQ LLM TEST")
print("=" * 70)

print("\n1. ACCOUNT STATUS")
print("-" * 70)
print(bot.handle_command("/status"))

print("\n2. OPEN POSITIONS")
print("-" * 70)
print(bot.handle_command("/positions"))

print("\n3. RISK ANALYSIS")
print("-" * 70)
r = bot.chat("What is my total risk exposure?")
print(r[:800])

print("\n4. TRADE IDEA")
print("-" * 70)
r = bot.chat("Give me a trade idea for EURGBP")
print(r[:800])

print("\n5. EXIT STRATEGY")
print("-" * 70)
r = bot.chat("What TP and SL for GBPUSD?")
print(r[:800])

print("\n6. PORTFOLIO REVIEW")
print("-" * 70)
r = bot.chat("Review my portfolio")
print(r[:800])

print("\n" + "=" * 70)
print("TEST COMPLETE")
print("=" * 70)

h = bot.health_check()
print("\nBot Status:", h["status"])
print("Groq Connected:", h["groq_connected"])
print("Messages:", h["message_history"])
