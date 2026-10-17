"""
Deriv AutoTrader - Scans 52 synthetic symbols for qualified setups.
LONG: Trend=BULL, EMA=BULL, RSI 30-65, Vol<50%
SHORT: Trend=BEAR, EMA=BEAR, RSI 35-70, Vol<50%
RISK: 0.5% equity, max 3 positions, max 5 trades/day
"""
import os, json, csv, time
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

# Config
DERIV_PATH = r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe"
RISK_PCT = 0.005
MAX_POS = 3
MAX_DAILY = 5
MAX_DAILY_LOSS = 0.03
SL_PCT = 0.02
TP_PCT = 0.04
SESSION_HOURS = (5, 19)  # Extended: 5:00-19:00 UTC (covers London open + NY close)
JOURNAL = Path(__file__).parent / "mt5_mcp" / "memory" / "autotrade_journal.csv"

def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")

def connect():
    if mt5.initialize(path=DERIV_PATH, timeout=15000):
        acct = mt5.account_info()
        log(f"Connected: {acct.login} | Balance: ${acct.balance:.2f} | Equity: ${acct.equity:.2f}")
        return True
    log(f"MT5 init FAILED: {mt5.last_error()}")
    return False

def get_daily_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    count, pnl = 0, 0.0
    if JOURNAL.exists():
        with open(JOURNAL) as f:
            for row in csv.DictReader(f):
                if row.get("date","").startswith(today):
                    count += 1
                    pnl += float(row.get("pnl", 0))
    return count, pnl

def get_open_positions():
    pos = mt5.positions_get()
    if not pos:
        return []
    return [p for p in pos if any(x in p.symbol for x in ["Boom","Crash","Volatility"])]

def analyze(sym):
    info = mt5.symbol_info(sym)
    if not info or not info.visible:
        mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
    if not info:
        return None
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 200)
    if rates is None or len(rates) < 50:
        return None
    df = pd.DataFrame(rates)
    df["sma10"] = df["close"].rolling(10).mean()
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["ema12"] = df["close"].ewm(span=12).mean()
    df["ema26"] = df["close"].ewm(span=26).mean()
    d = df["close"].diff()
    g = d.clip(lower=0).rolling(14).mean()
    l = (-d.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + g / l.replace(0, np.nan))
    df["tr"] = pd.concat([df["high"]-df["low"], abs(df["high"]-df["close"].shift()), abs(df["low"]-df["close"].shift())], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(14).mean()
    df["lr"] = np.log(df["close"]/df["close"].shift(1))
    vol = df["lr"].std() * np.sqrt(24*260) * 100
    r = df.iloc[-1]
    p = df.iloc[-2]
    chg24 = (r["close"] - df.iloc[-25]["close"]) / df.iloc[-25]["close"] * 100 if len(df) > 25 else 0
    trend = ("STRONG_BULL" if r.sma10>r.sma20>r.sma50 else "BULL" if r.sma10>r.sma20 else "STRONG_BEAR" if r.sma10<r.sma20<r.sma50 else "BEAR" if r.sma10<r.sma20 else "FLAT")
    ema = "BULL" if r.ema12>r.ema26 else "BEAR"
    return {"symbol": sym, "close": r["close"], "trend": trend, "ema": ema, "rsi": r["rsi"],
            "vol": vol, "atr": r["atr"], "chg24": chg24, "info": info}

def qualify(sig):
    t, e = sig["trend"], sig["ema"]
    rsi, vol, chg = sig["rsi"], sig["vol"], sig["chg24"]
    if "BULL" in t and e == "BULL" and 30 <= rsi <= 65 and vol < 50 and chg > -2:
        return "BUY"
    if "BEAR" in t and e == "BEAR" and 35 <= rsi <= 70 and vol < 50 and chg < 2:
        return "SELL"
    return None

def calc_lots(equity, sl_pts, info):
    risk = equity * RISK_PCT
    tv = info.trade_tick_value
    ts = info.trade_tick_size
    if tv == 0 or ts == 0 or sl_pts <= 0:
        return info.volume_min
    lots = risk / (sl_pts / ts * tv)
    step = info.volume_step
    lots = max(info.volume_min, min(info.volume_max, lots))
    return round(round(lots / step) * step, 2)

def execute_trade(sig, action):
    info = sig["info"]
    price = sig["close"]
    atr = sig["atr"]
    equity = mt5.account_info().equity
    sl_pts = max(atr * 1.5, price * SL_PCT)
    tp_pts = sl_pts * 2
    lots = calc_lots(equity, sl_pts, info)
    if action == "BUY":
        sl = price - sl_pts
        tp = price + tp_pts
        order_type = mt5.ORDER_TYPE_BUY
        filled = mt5.symbol_info_tick(sig["symbol"]).ask
    else:
        sl = price + sl_pts
        tp = price - tp_pts
        order_type = mt5.ORDER_TYPE_SELL
        filled = mt5.symbol_info_tick(sig["symbol"]).bid
    # Detect correct filling mode
    fill = mt5.ORDER_FILLING_IOC
    if info.filling_mode & 1:
        fill = mt5.ORDER_FILLING_FOK
    elif info.filling_mode & 2:
        fill = mt5.ORDER_FILLING_RETURN
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": sig["symbol"],
        "volume": lots, "type": order_type, "price": filled,
        "sl": round(sl, info.digits), "tp": round(tp, info.digits),
        "deviation": 10, "magic": 202609, "comment": f"Auto{action[:3]}",
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": fill
    }
    try:
        from mt5_mcp import execution_gate
        execution_gate.require(
            req,
            client=execution_gate.RawMt5Adapter(mt5),
            source="deriv_autotrade",
        )
    except Exception as exc:
        log(f"BLOCKED {sig['symbol']}: {exc}")
        return False
    result = mt5.order_send(req)
    # Try other filling modes if first fails
    if not result or result.retcode != mt5.TRADE_RETCODE_DONE:
        for alt_fill in [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]:
            if alt_fill == fill:
                continue
            req["type_filling"] = alt_fill
            result = mt5.order_send(req)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                break
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"EXECUTED {action} {sig['symbol']} {lots} lots @ {filled} SL={sl:.5f} TP={tp:.5f}")
        log_journal(sig, action, lots, filled, sl, tp)
        return True
    else:
        log(f"FAILED {sig['symbol']}: {result.comment if result else 'No result'}")
        return False

def log_journal(sig, action, lots, entry, sl, tp):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    write_header = not JOURNAL.exists()
    with open(JOURNAL, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["date","time","symbol","action","lots","entry","sl","tp","pnl","reason"])
        w.writerow([datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%H:%M:%S"),
                     sig["symbol"], action, lots, f"{entry:.5f}", f"{sl:.5f}", f"{tp:.5f}", 0,
                     f"RSI={sig['rsi']:.1f} Vol={sig['vol']:.1f}% Trend={sig['trend']} EMA={sig['ema']}"])

def scan():
    log("=" * 60)
    log("DERIV AUTOTRADER - SCAN CYCLE")
    log("=" * 60)
    # Compliance checks
    daily_count, daily_pnl = get_daily_stats()
    open_pos = get_open_positions()
    equity = mt5.account_info().equity
    balance = mt5.account_info().balance
    log(f"Equity: ${equity:.2f} | Open: {len(open_pos)}/{MAX_POS} | Today: {daily_count}/{MAX_DAILY} trades | Daily P&L: ${daily_pnl:.2f}")
    if len(open_pos) >= MAX_POS:
        log("MAX POSITIONS reached - skipping")
        return
    if daily_count >= MAX_DAILY:
        log("MAX DAILY TRADES reached - skipping")
        return
    if daily_pnl < -(balance * MAX_DAILY_LOSS):
        log(f"DAILY LOSS LIMIT hit (${daily_pnl:.2f}) - halting")
        return
    hour = datetime.utcnow().hour
    if not (SESSION_HOURS[0] <= hour < SESSION_HOURS[1]):
        log(f"Outside session ({hour}:00 UTC) - skipping")
        return
    # Get symbols
    syms = mt5.symbols_get()
    deriv = [s.name for s in syms if any(x in s.name for x in ["Boom","Crash","Volatility"])]
    log(f"Scanning {len(deriv)} symbols...")
    qualified = []
    for sym in deriv:
        try:
            sig = analyze(sym)
            if not sig:
                continue
            action = qualify(sig)
            if action:
                sig["action"] = action
                qualified.append(sig)
                log(f"  QUALIFIED: {sym:30s} {action:4s} RSI={sig['rsi']:.1f} Vol={sig['vol']:.1f}% Trend={sig['trend']} EMA={sig['ema']}")
        except Exception as e:
            pass  # Skip errors silently
    if not qualified:
        log("No qualified setups found")
        return
    # Sort by RSI (best setups first)
    qualified.sort(key=lambda s: abs(s["rsi"] - 50))
    # Execute best setup
    best = qualified[0]
    log(f"\nBEST SETUP: {best['symbol']} {best['action']} RSI={best['rsi']:.1f} Vol={best['vol']:.1f}%")
    execute_trade(best, best["action"])
    names = [f"{s['symbol']}({s['action'][:3]})" for s in qualified]
    log(f"\nAll qualified: {', '.join(names)}")

def main():
    if not connect():
        return
    scan()
    mt5.shutdown()

if __name__ == "__main__":
    main()
