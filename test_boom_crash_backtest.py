from mt5_mcp.backtest import BacktestParams, Backtester, load_frames, _variant_tfs, FRAME_TTL_SECONDS
from mt5_mcp.client import get_client
import time

symbol = 'CRASH500'
params = BacktestParams(
    symbol=symbol,
    bars=3000,
    risk_percent=0.5,
    min_rr=3.0,
    min_zone_score=40.0,
    tp_min=80.0,
    tp_max=100.0,
    sl_min=20.0,
    sl_max=30.0,
    use_trailing_stop=True,
    use_partial_tp=True,
    use_adaptive_timeout=True,
    use_vol_spread=True,
)

print('Loading frames...')
tfs = _variant_tfs(params)
frames = load_frames(symbol, sorted(tfs), 3000, use_cache=True, allow_mt5=True, ttl=FRAME_TTL_SECONDS)
info = get_client().symbol_info(symbol) or {}

print('Running backtest...')
t0 = time.time()
engine = Backtester(params, frames, info)
report = engine.run()
elapsed = time.time() - t0

print('Trades:', report.n_trades)
print('Win Rate:', round(report.win_rate, 1), '%')
print('PF:', round(report.profit_factor, 2))
print('Net PnL: $' + str(round(report.net_pnl, 2)))
print('Return:', str(round(report.total_return_pct, 2)) + '%')
print('Max DD:', str(round(report.max_drawdown_pct, 2)) + '%')
print('Expectancy: $' + str(round(report.expectancy, 2)) + '/trade')
print('Time:', str(round(elapsed, 1)) + 's')