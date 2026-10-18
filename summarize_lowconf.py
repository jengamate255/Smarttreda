import json
from pathlib import Path

OUT = Path(r"E:\Project\Mt5 Ea\mt5_mcp\backtest\results\lowconf_exp")
TAGS = ["A_baseline", "B_mid_small", "C_low_micro", "D_vlow_micro"]
DESC = {
    "A_baseline": "conf>=75 score>=70 risk=0.50%",
    "B_mid_small": "conf 55-75 score>=55 risk=0.20%",
    "C_low_micro": "conf 40-75 score>=40 risk=0.10%",
    "D_vlow_micro": "conf 30-75 score>=35 risk=0.05%",
}


def load_tag(tag: str) -> list[dict]:
    rows = []
    for f in sorted((OUT / tag).glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        trades = d.get("trades") or []
        confs = [t.get("confidence", 0) for t in trades if t.get("confidence")]
        rows.append({
            "symbol": d["params"]["symbol"],
            "n": d["n_trades"],
            "wins": d["wins"],
            "pnl": d["net_pnl"],
            "pf": d["profit_factor"],
            "wr": d["win_rate"],
            "dd": d["max_drawdown_pct"],
            "exp": d["expectancy"],
            "avg_conf": round(sum(confs) / len(confs), 1) if confs else None,
            "start": d.get("start"),
            "end": d.get("end"),
        })
    return rows


def agg(rows: list[dict]) -> dict:
    n = sum(r["n"] for r in rows)
    wins = sum(r["wins"] for r in rows)
    pnl = sum(r["pnl"] for r in rows)
    gp = sum(r["pnl"] for r in rows if r["pnl"] > 0)
    gl = abs(sum(r["pnl"] for r in rows if r["pnl"] < 0))
    wconf = [(r["avg_conf"], r["n"]) for r in rows if r["n"] and r["avg_conf"]]
    avg_conf = (sum(c * n for c, n in wconf) / sum(n for _, n in wconf)) if wconf else None
    return {
        "trades": n,
        "wins": wins,
        "net_pnl": round(pnl, 2),
        "win_rate": round(wins / n * 100, 1) if n else 0.0,
        "profit_factor": round(gp / gl, 3) if gl else (999.0 if gp else 0.0),
        "avg_dd": round(sum(r["dd"] for r in rows) / len(rows), 2) if rows else 0,
        "max_dd": round(max((r["dd"] for r in rows), default=0), 2),
        "expectancy": round(pnl / n, 2) if n else 0.0,
        "avg_conf": round(avg_conf, 1) if avg_conf else None,
        "symbols_with_trades": sum(1 for r in rows if r["n"]),
        "start": min((r["start"] for r in rows if r["start"]), default=""),
        "end": max((r["end"] for r in rows if r["end"]), default=""),
    }


all_rows = {t: load_tag(t) for t in TAGS}
aggs = {t: agg(all_rows[t]) for t in TAGS}

print("=" * 90)
print("LOW-CONFIDENCE SMALL-LOT EXPERIMENT (8 symbols, H4/H1+H4, z>=40, RR>=3, ~6 mo)")
print("=" * 90)
print(f"{'variant':14} {'desc':36} {'tr':>3} {'wr%':>5} {'PF':>6} {'PnL':>9} {'exp':>7} {'maxDD':>6} {'avgDD':>6} {'conf':>5}")
print("-" * 90)
for t in TAGS:
    a = aggs[t]
    conf = f"{a['avg_conf']:.1f}" if a["avg_conf"] is not None else "-"
    print(
        f"{t:14} {DESC[t]:36} {a['trades']:3d} {a['win_rate']:5.1f} {a['profit_factor']:6.2f} "
        f"{a['net_pnl']:+9.2f} {a['expectancy']:7.2f} {a['max_dd']:6.2f} {a['avg_dd']:6.2f} {conf:>5}"
    )

base = aggs["A_baseline"]
print("\nCOMBINED: baseline full-size + disjoint low band at small size")
print("-" * 90)
for t in TAGS[1:]:
    b = aggs[t]
    comb_n = base["trades"] + b["trades"]
    comb_pnl = base["net_pnl"] + b["net_pnl"]
    comb_wins = base["wins"] + b["wins"]
    print(
        f"  A+{t:12} tr={comb_n:3d}  PnL={comb_pnl:+9.2f} "
        f"(base {base['net_pnl']:+8.2f} + band {b['net_pnl']:+8.2f})  "
        f"WR={comb_wins/comb_n*100:.1f}%  bandDD={b['avg_dd']:.2f}%  "
        f"{'HURTS' if b['net_pnl'] < 0 else 'HELPS'}"
    )

# Risk-normalized: $ per 1% risk unit
print("\nRISK-NORMALIZED (net_pnl / sum(risk% * trades) as $ per risk-unit proxy)")
print("-" * 90)
RISK = {"A_baseline": 0.50, "B_mid_small": 0.20, "C_low_micro": 0.10, "D_vlow_micro": 0.05}
for t in TAGS:
    a = aggs[t]
    risk_units = RISK[t] * a["trades"]  # % risk deployed
    per_unit = a["net_pnl"] / risk_units if risk_units else 0
    print(f"  {t:14} risk%*trades={risk_units:6.2f}  PnL={a['net_pnl']:+9.2f}  PnL per risk-unit={per_unit:+7.2f}")

print("\nPER-SYMBOL net_pnl")
print("-" * 90)
syms = sorted({r["symbol"] for r in all_rows["A_baseline"]})
print(f"{'symbol':8}", end="")
for t in TAGS:
    print(f" {t[:8]:>9}", end="")
print()
for s in syms:
    print(f"{s:8}", end="")
    for t in TAGS:
        row = next(r for r in all_rows[t] if r["symbol"] == s)
        print(f" {row['pnl']:+9.2f}", end="")
    print()

report = {
    "variants": {t: {"desc": DESC[t], "agg": aggs[t], "risk_pct": RISK[t]} for t in TAGS},
    "combined": {
        t: {
            "trades": base["trades"] + aggs[t]["trades"],
            "net_pnl": round(base["net_pnl"] + aggs[t]["net_pnl"], 2),
            "verdict": "hurts" if aggs[t]["net_pnl"] < 0 else "helps",
        }
        for t in TAGS[1:]
    },
    "per_symbol": all_rows,
}
(OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(f"\nWrote {OUT / 'report.json'}")
