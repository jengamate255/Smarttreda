"""Detailed analysis of backtest results."""
import json, sys
from pathlib import Path
from datetime import datetime

def analyze(trades):
    if not trades:
        return {"error": "no trades"}
    total = len(trades)
    winners = [t for t in trades if t.get("pnl_usd", 0) > 0]
    losers = [t for t in trades if t.get("pnl_usd", 0) < 0]
    wins, losses = len(winners), len(losers)
    wr = wins / total * 100
    gp = sum(t.get("pnl_usd", 0) for t in winners)
    gl = abs(sum(t.get("pnl_usd", 0) for t in losers))
    net = gp - gl
    aw = gp / wins if wins else 0
    al = gl / losses if losses else 0
    pf = gp / gl if gl > 0 else float("inf")
    pr = aw / al if al > 0 else float("inf")
    exp_r = (wr/100 * pr) - ((1 - wr/100))
    exp_usd = (wr/100 * aw) - ((1 - wr/100) * al)
    rs = [t.get("r_multiple", 0) for t in trades]
    avg_r = sum(rs) / total
    rw = [r for r in rs if r > 0]
    rl = [r for r in rs if r < 0]
    avg_rw = sum(rw) / len(rw) if rw else 0
    avg_rl = sum(rl) / len(rl) if rl else 0
    run, peak, mdd = 0, 0, 0
    for t in trades:
        run += t.get("pnl_usd", 0)
        if run > peak: peak = run
        dd = peak - run
        if dd > mdd: mdd = dd
    mws, mls, cs, st = 0, 0, 0, None
    for t in trades:
        p = t.get("pnl_usd", 0)
        if p > 0:
            cs = cs + 1 if st == "w" else 1; st = "w"
            mws = max(mws, cs)
        elif p < 0:
            cs = cs + 1 if st == "l" else 1; st = "l"
            mls = max(mls, cs)
        else:
            cs = 0; st = None
    syms = {}
    for t in trades:
        s = t.get("symbol", "?")
        if s not in syms:
            syms[s] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0, "rs": []}
        syms[s]["trades"] += 1
        p = t.get("pnl_usd", 0)
        syms[s]["pnl"] += p
        syms[s]["rs"].append(t.get("r_multiple", 0))
        if p > 0: syms[s]["wins"] += 1
        elif p < 0: syms[s]["losses"] += 1
    exits = {}
    for t in trades:
        r = t.get("exit_reason", "?")
        if r not in exits:
            exits[r] = {"n": 0, "pnl": 0, "w": 0, "l": 0}
        exits[r]["n"] += 1
        p = t.get("pnl_usd", 0)
        exits[r]["pnl"] += p
        if p > 0: exits[r]["w"] += 1
        elif p < 0: exits[r]["l"] += 1
    return {
        "total": total, "wins": wins, "losses": losses, "wr": wr,
        "gp": gp, "gl": gl, "net": net, "aw": aw, "al": al,
        "pf": pf, "pr": pr, "exp_r": exp_r, "exp_usd": exp_usd,
        "avg_r": avg_r, "avg_rw": avg_rw, "avg_rl": avg_rl,
        "mdd": mdd, "mws": mws, "mls": mls, "syms": syms, "exits": exits,
    }

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else str(
        sorted(Path(r"E:\Project\Mt5 Ea\backtest").glob("top6_5day_*.json"))[-1])
    with open(path, encoding="utf-8") as f:
        trades = json.load(f).get("trades", [])
    s = analyze(trades)
    if "error" in s:
        print(s["error"]); return
    print("=" * 70)
    print("           DETAILED 5-DAY BACKTEST PERFORMANCE REPORT")
    print("=" * 70)
    print()
    print("TRADE SUMMARY")
    print("-" * 40)
    print(f"  Total trades     : {s['total']}")
    print(f"  Wins             : {s['wins']}")
    print(f"  Losses           : {s['losses']}")
    print(f"  Win rate         : {s['wr']:.1f}%")
    print()
    print("P&L BREAKDOWN")
    print("-" * 40)
    print(f"  Gross profit     : ${s['gp']:>+12.2f}")
    print(f"  Gross loss       : ${s['gl']:>12.2f}")
    print(f"  NET P&L          : ${s['net']:>+12.2f}")
    print()
    print("AVERAGES")
    print("-" * 40)
    print(f"  Avg win          : ${s['aw']:>+12.2f}")
    print(f"  Avg loss         : ${s['al']:>12.2f}")
    print(f"  Avg trade (all)  : ${s['net']/s['total']:>+12.2f}")
    print()
    print("EFFICIENCY METRICS")
    print("-" * 40)
    print(f"  Profit factor    : {s['pf']:>12.2f}")
    print(f"  Payoff ratio     : {s['pr']:>12.2f}  (avg win / avg loss)")
    print(f"  Expectancy (R)   : {s['exp_r']:>+12.3f} R per trade")
    print(f"  Expectancy ($)   : ${s['exp_usd']:>+12.2f} per trade")
    print()
    print("R-MULTIPLE ANALYSIS")
    print("-" * 40)
    print(f"  Avg R (all)      : {s['avg_r']:>+12.3f}")
    print(f"  Avg R (wins)     : {s['avg_rw']:>+12.3f}")
    print(f"  Avg R (losses)   : {s['avg_rl']:>+12.3f}")
    print()
    print("RISK")
    print("-" * 40)
    print(f"  Max drawdown     : ${s['mdd']:>12.2f}")
    mddp = s['mdd'] / (s['net'] + s['mdd']) * 100 if (s['net'] + s['mdd']) > 0 else 0
    print(f"  Max DD from peak : {mddp:.1f}%")
    print()
    print("STREAKS")
    print("-" * 40)
    print(f"  Max win streak   : {s['mws']}")
    print(f"  Max loss streak  : {s['mls']}")
    print()
    print("EXIT REASON BREAKDOWN")
    print("-" * 60)
    print(f"  {'Reason':<12} {'Count':>6} {'Wins':>6} {'Losses':>6} {'P&L':>10} {'WR':>6}")
    for r, d in sorted(s["exits"].items(), key=lambda x: -x[1]["n"]):
        wr = d["w"] / d["n"] * 100 if d["n"] else 0
        print(f"  {r:<12} {d['n']:>6} {d['w']:>6} {d['l']:>6} ${d['pnl']:>+9.2f} {wr:>5.0f}%")
    print()
    print("BY SYMBOL (ranked by net)")
    print("-" * 70)
    print(f"  {'Symbol':<12} {'Trades':>7} {'Wins':>6} {'WR':>7} {'Net P&L':>10} {'Avg R':>8}")
    for sym, d in sorted(s["syms"].items(), key=lambda x: -x[1]["pnl"]):
        wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
        ar = sum(d["rs"]) / len(d["rs"]) if d["rs"] else 0
        print(f"  {sym:<12} {d['trades']:>7} {d['wins']:>6} {wr:>6.1f}% ${d['pnl']:>+9.2f} {ar:>+7.3f}")
    print()
    print("VERDICT")
    print("-" * 40)
    grade = "A+" if s["pf"] > 2 and s["wr"] > 55 else "A" if s["pf"] > 1.8 else "B+" if s["pf"] > 1.5 else "B" if s["pf"] > 1.2 else "C"
    print(f"  Strategy grade   : {grade}")
    print(f"  Edge quality     : {'STRONG' if s['exp_r'] > 0.2 else 'MODERATE' if s['exp_r'] > 0.1 else 'WEAK'} ({s['exp_r']:+.3f} R/trade)")
    print(f"  Risk efficiency  : {'GOOD' if s['mdd'] < abs(s['net']) * 0.3 else 'ACCEPTABLE' if s['mdd'] < abs(s['net']) * 0.5 else 'POOR'} (DD/Net = {s['mdd']/abs(s['net'])*100:.0f}%)" if s['net'] != 0 else "  Risk efficiency  : N/A")
    daily = s['net'] / 5
    print(f"  Daily avg P&L    : ${daily:>+8.2f}")
    monthly = daily * 30
    print(f"  Projected monthly: ${monthly:>+10.2f} (30 days, same pace)")
    print()

if __name__ == "__main__":
    main()
