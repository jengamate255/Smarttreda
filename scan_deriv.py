"""Check Deriv positions and scan for opportunities."""
from mt5_mcp.client import get_client

c = get_client('DERIV')
a = c.account_info()
print(f"Deriv Account: {a['login']} @ {a['server']}")
print(f"Balance: ${a['balance']:.2f}")
print(f"Equity: ${a['equity']:.2f}")
print(f"Free Margin: ${a['margin_free']:.2f}")
print()

positions = c.positions()
print(f"Open Positions ({len(positions)}):")
for p in positions:
    print(f"  {p['symbol']} {p['type']} {p['volume']} lots @ {p['price_open']}")
    print(f"    SL: {p.get('sl', 0)} TP: {p.get('tp', 0)}")
    print(f"    PnL: ${p.get('profit', 0):.2f}")
print()

# Scan allowlist symbols
from mt5_mcp.analysis import analyze_symbol
from mt5_mcp.consensus import publish

allowlist = ['AUDUSD', 'EURUSD', 'USDJPY', 'XAUUSD', 'EURJPY', 'JP225', 'SPX500', 'XAGUSD', 'ETHUSD']

print("Scanning allowlist symbols...")
print("-" * 80)

opportunities = []
for sym in allowlist:
    try:
        result = analyze_symbol(sym)
        if result:
            health = result.get('health', 0)
            direction = result.get('direction', 'NONE')
            score = result.get('score', 0)
            conf = result.get('confidence', 0)
            rr = result.get('rr_ratio')
            zone = result.get('zone_type', '')

            # Publish to consensus
            side = 'BUY' if direction == 'BUY' else 'SELL' if direction == 'SELL' else 'NONE'
            publish('desk', sym, side, score)

            status = '✓' if health >= 55 and score >= 70 and conf >= 75 else ' '
            print(f"{status} {sym:8s} health={health:5.1f} {direction:4s} score={score:5.1f} conf={conf:5.1f} RR={rr if rr else 'N/A':>5} zone={zone}")

            if health >= 55 and score >= 70 and conf >= 75 and rr and rr >= 3.0:
                opportunities.append({
                    'symbol': sym,
                    'direction': direction,
                    'score': score,
                    'confidence': conf,
                    'health': health,
                    'rr': rr,
                    'zone': zone,
                })
    except Exception as e:
        print(f"  {sym:8s} ERROR: {e}")

print()
print("=" * 80)
if opportunities:
    print(f"OPPORTUNITIES FOUND: {len(opportunities)}")
    for opp in opportunities:
        print(f"  {opp['symbol']} {opp['direction']} score={opp['score']:.1f} conf={opp['confidence']:.1f} health={opp['health']:.1f} RR={opp['rr']:.2f} zone={opp['zone']}")
else:
    print("No opportunities meeting floors (health≥55, score≥70, conf≥75, RR≥3.0)")
