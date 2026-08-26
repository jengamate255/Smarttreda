import sys
sys.path.insert(0, r'E:\Project\Mt5 Ea')
from mt5_mcp.backtest import Backtester, BacktestParams
import urllib.request, json, pandas as pd

# Get M15 data from dashboard
d = json.loads(urllib.request.urlopen('http://127.0.0.1:8050/api/rates?symbol=Boom%20900%20Index&timeframe=M15&count=200', timeout=15).read())

times = d.get('times', [])
opens = d.get('open', [])
highs = d.get('high', [])
lows = d.get('low', [])
closes = d.get('close', [])
volumes = d.get('volume', [])

if len(times) >= 200:
    df = pd.DataFrame({
        'timestamp': [pd.Timestamp(ts, unit='s') for ts in times[-200:]],
        'open': [float(o) for o in opens[-200:]],
        'high': [float(h) for h in highs[-200:]],
        'low': [float(l) for l in lows[-200:]],
        'close': [float(c) for c in closes[-200:]],
        'volume': [int(v) for v in volumes[-200:]]
    })
    df = df.set_index('timestamp')
    
    params = BacktestParams(
        symbol='BOOM900',
        bars=200,
        trend_timeframe='H4',
        zone_timeframes=['H1'],
        min_zone_score=70.0,
        min_trade_score=70.0,
        min_confidence_percent=90.0,
        risk_percent=0.5,
    )
    
    frames = {'M15': df}
    info = {'symbol': 'BOOM900'}
    
    try:
        bt = Backtester(params, frames, info)
        result = bt.run()
        print('Backtest result:', result)
    except Exception as e:
        print('Backtest error type:', type(e).__name__)
        print('Error:', e)
else:
    print('Not enough bars:', len(times))