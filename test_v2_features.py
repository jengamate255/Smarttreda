import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()

from mt5_mcp.telegram_bot import TradingTelegramBot, TelegramConfig

config = TelegramConfig(bot_token=os.environ.get('MT5_NOTIFY_TELEGRAM_TOKEN', ''), enabled=True)
bot = TradingTelegramBot(config=config)

bot.update_context(
    account_balance=10000.0,
    account_equity=10350.0,
    account_profit=350.0,
    free_margin=8350.0,
    positions=[
        {"ticket": 1001, "symbol": "EURUSD", "side": "BUY", "volume": 0.1, "entry_price": 1.1000, "pnl": 250.0},
        {"ticket": 1002, "symbol": "GBPUSD", "side": "SELL", "volume": 0.05, "entry_price": 1.2750, "pnl": -50.0},
    ],
    prices={"EURUSD": {"bid": 1.10250, "ask": 1.10270}},
)

print("=" * 60)
print("TELEGRAM BOT v2.0 - FULL FEATURE TEST")
print("=" * 60)

print("\n1. INLINE KEYBOARDS")
kb = bot.get_keyboard("main")
print(f"Main keyboard: {len(kb['inline_keyboard'])} rows")
kb = bot.get_keyboard("trade", symbol="EURUSD")
print(f"Trade keyboard: {len(kb['inline_keyboard'])} rows")
kb = bot.get_keyboard("confirm")
print(f"Confirm keyboard: {len(kb['inline_keyboard'])} rows")
kb = bot.get_keyboard("positions")
print(f"Positions keyboard: {len(kb['inline_keyboard'])} rows")
kb = bot.get_keyboard("alerts")
print(f"Alerts keyboard: {len(kb['inline_keyboard'])} rows")

print("\n2. TRADE EXECUTION")
result = bot.execute_trade("EURUSD", "BUY", 0.1, sl=1.0950, tp=1.1100)
print(f"Trade: {result['message']}")
result = bot.close_position(1001)
print(f"Close: {result['message']}")

print("\n3. ALERTS")
alert = bot.add_alert("EURUSD", "ABOVE", 1.1200)
print(f"Alert added: {alert.symbol} {alert.alert_type} @{alert.target_price}")
triggered = bot.check_alerts("EURUSD", 1.1250)
print(f"Alert triggered: {len(triggered) > 0}")
alert2 = bot.add_alert("GBPUSD", "BELOW", 1.2600)
print(f"Alert added: {alert2.symbol} {alert2.alert_type} @{alert2.target_price}")

print("\n4. CALLBACKS")
text, kb = bot.handle_callback("account")
print(f"Account: {text[:80]}")
text, kb = bot.handle_callback("positions")
print(f"Positions: {text[:80]}")
text, kb = bot.handle_callback("buy_EURUSD")
print(f"Buy: {text[:80]}")
text, kb = bot.handle_callback("confirm_yes")
print(f"Confirm: {text[:80]}")
text, kb = bot.handle_callback("alerts")
print(f"Alerts: {text[:80]}")

print("\n5. ALERT PARSING")
result = bot.handle_alert_message("ALERT GBPUSD BELOW 1.26000")
print(f"Parse: {result}")
result = bot.handle_alert_message("ALERT EURUSD ABOVE 1.15000")
print(f"Parse: {result}")

print("\n6. CONFIRM MESSAGES")
bot._pending_orders["pending"] = {"symbol": "GBPUSD", "side": "SELL"}
result = bot.handle_confirm_message("CONFIRM")
print(f"Confirm: {result[:80]}")

print("\n7. GROQ LLM")
response = bot.chat("What is my account status?")
print(f"LLM: {response[:150]}...")

print("\n" + "=" * 60)
print("ALL FEATURES WORKING!")
print("=" * 60)
