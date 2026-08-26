#!/usr/bin/env python
import sys
sys.path.insert(0, '.')
from mt5_mcp.research_lab import generate_variants, run_variant_backtests
from mt5_mcp.common import BOOM_CRASH_SYMBOLS

# Generate variants
v = generate_variants(symbols=['BOOM100'], per_symbol=2, seed=42)
print('Generated variants:')
for x in v:
    p = x.params
    print(f'  {x.name}: max_hold_bars={p["max_hold_bars"]}, sl_atr_buffer={p["sl_atr_buffer"]}, reward_risk_ratio={p["reward_risk_ratio"]}')

# Run backtests
rows = run_variant_backtests(v, days=5)
print()
print('Backtest results:')
for r in rows:
    s = r['stats']
    print(f'  {r["name"]:25s} trades={s.get("trades",0):3d} WR={s.get("win_rate",0):5.1f}% PF={s.get("profit_factor",0):5.2f} net=${s.get("net_usd",0):+8.2f} avgR={s.get("avg_r",0):+6.3f}')