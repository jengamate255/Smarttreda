import sys
import pandas as pd
import numpy as np
from datetime import datetime, timezone

sys.path.insert(0, '.')

from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import MT5Config
from mt5_mcp.indicators import atr, rsi, sma, ema, macd

# Connect to Prop terminal
config = MT5Config(
    terminal_path=r'C:\Program Files\MetaTrader 5 Terminal\terminal64.exe',
    login=None,
    password='',
    server=''
)
client = MT5Client(config)
if not client.connect():
    print('Failed to connect to Prop terminal')
    sys.exit(1)

print('Connected to Prop terminal - analyzing volatility across symbols\n')

# Get all symbols
all_symbols = client.symbols()
print(f'Total symbols: {len(all_symbols)}\n')

# Focus on non-synthetic symbols we can trade
focus_symbols = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'XAUUSD', 'XAGUSD', 
                 'DJI30', 'SPX500', 'GER40', 'JP225',
                 'UKOIL', 'USOIL']

# Filter to existing symbols
focus_symbols = [s for s in focus_symbols if s in all_symbols]

# Analyze each symbol
results = []

for symbol in focus_symbols:
    try:
        # Get H1 rates (200 bars = ~200 hours ~ 8+ days history)
        raw = client.rates(symbol, 'H1', 200)
        if raw is None or len(raw) < 100:
            continue
        
        # Convert to proper DataFrame with float values
        df = pd.DataFrame(raw)
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['tick_volume'] = df['tick_volume'].astype(float)
        
        close = df['close']
        high = df['high']
        low = df['low']
        
        # ATR (14 period)
        atr_val = atr(df, period=14).iloc[-1]
        atr_pct = atr_val / close.iloc[-1] * 100
        
        # RSI (14 period)
        rsi_val = rsi(close, period=14).iloc[-1]
        
        # MACD - returns DataFrame with columns macd, signal, histogram
        macd_df = macd(close, fast=12, slow=26, signal=9)
        macd_val = macd_df['macd'].iloc[-1]
        signal_val = macd_df['signal'].iloc[-1]
        hist_val = macd_df['histogram'].iloc[-1]
        
        # Bollinger Bands (manual calculation using sma and std)
        period = 20
        middle = sma(close, period).iloc[-1]
        std = close.rolling(window=period).std().iloc[-1]
        upper = middle + 2 * std
        lower = middle - 2 * std
        bb_width = (upper - lower) / middle * 100  # % bollinger width
        
        # Price change % over last 20 bars
        price_change_20 = (close.iloc[-1] / close.iloc[-20] - 1) * 100 if len(close) > 20 else 0
        
        # Annualized volatility (20-bar std dev * sqrt(252))
        returns = close.pct_change().dropna()
        vol_20d = returns.rolling(20).std().iloc[-1] * np.sqrt(252) * 100  # annualized vol %
        
        # Volatility regime: compare current ATR% to recent average
        atr_series = atr(df, period=14)
        atr_recent = atr_series.iloc[-20:].mean()
        atr_ratio = atr_val / atr_recent if atr_recent > 0 else 0
        
        results.append({
            'symbol': symbol,
            'price': round(close.iloc[-1], 5),
            'atr_pct': round(atr_pct, 2),
            'rsi': round(rsi_val, 1),
            'macd': round(macd_val, 5),
            'bb_width': round(bb_width, 2),
            'price_change_20d': round(price_change_20, 2),
            'vol_annual': round(vol_20d, 1),
            'atr_ratio': round(atr_ratio, 2),
        })
        
    except Exception as e:
        print(f'Error analyzing {symbol}: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
        continue

# Sort by volatility (ATR % descending)
results.sort(key=lambda x: x['atr_pct'], reverse=True)

print('=== VOLATILITY RANKING (by ATR % - H1 timeframe) ===')
print(f'{"Symbol":<10} {"Price":>8} {"ATR%":>6} {"RSI":>5} {"MACD":>8} {"BB%":>6} {"Chg20d%":>8} {"AnnVol%":>9} {"ATR/Avg":>7}')
print('-' * 80)
for r in results:
    print(f"{r['symbol']:<10} {r['price']:>8.5f} {r['atr_pct']:>6.2f} {r['rsi']:>5.1f} {r['macd']:>8.5f} {r['bb_width']:>6.2f} {r['price_change_20d']:>8.2f} {r['vol_annual']:>9.1f} {r['atr_ratio']:>7.2f}")

print('\n=== TRADING INTERPRETATION ===')
print('1. HIGH ATR% (>1%) = High volatility pairs - good for spike/breakout strategies')
print('   Examples: XAUUSD, XAGUSD, crude oil')
print()
print('2. LOW ATR% (<0.5%) = Quiet pairs - good for range/reversion strategies')  
print('   Examples: EURUSD, GBPUSD, USDJPY')
print()
print('3. RSI: >70 overbought, <30 oversold - combine with ATR for entries')
print('4. BB Width contracting = Low vol, impending big move (trade the breakout)')
print('5. Annual Vol %: Higher = more risk per unit time')
print('6. ATR/Ratio > 1.0 = Volatility elevated vs recent average - good for mean reversion')
print('7. Price change 20d: |%| > 5% = strong momentum, < -5% = strong mean reversion')