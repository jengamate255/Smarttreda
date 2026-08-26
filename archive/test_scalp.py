import sys
sys.path.insert(0, '.')
from mt5_mcp.research_lab import generate_variants, run_variant_backtests, _row_stats, BASELINE
from mt5_mcp.common import BOOM_CRASH_SYMBOLS
from dataclasses import dataclass

scalping_variants = generate_variants(symbols=['BOOM100','CRASH100'], per_symbol=4, seed=20260824)
rows = run_variant_backtests(scalping_variants, days=3, verbose=True)
print('\n--- Summary rows ---')
for r in rows:
    s = r['stats']
    base = r.get('baseline', {})
    beat = s.get('avg_r',0) > base.get('avg_r',0) + 0.05 if base else False
    print(f"{r['name']:25s} trades={s.get('trades',0):3d} WR={s.get('win_rate',0):5.1f}% PF={s.get('profit_factor',0):5.2f} net=${s.get('net_usd',0):+8.2f} avgR={s.get('avg_r',0):+6.3f} beats={beat}")