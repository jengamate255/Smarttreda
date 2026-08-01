"""Local web dashboard for the price-action strategy.

Pages:
    /           overview - recent backtest results, trades, compliance status
    /backtest   dedicated backtesting page (run a backtest, see trades + equity)
    /forward    forward-testing page (windowed run on recent data + comparison)

Run from the package directory:

    python dashboard.py --port 8000
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

from backtest import (
    Backtester,
    BacktestParams,
    BacktestResult,
    load_frames,
    rebuild_summary,
    save_result,
)

BASE = Path(__file__).resolve().parent
RESULTS = BASE / "backtest" / "results"
DEFAULT_OUT = RESULTS / "month"

DEFAULT_SYMBOLS = ["EURUSD", "USDJPY", "AUDUSD", "SPX500", "XAGUSD", "XAUUSD"]

app = FastAPI(title="Strategy Dashboard", docs_url=None, redoc_url=None)

_CSS = """
body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}
header{background:#1e293b;padding:14px 24px;display:flex;gap:24px;align-items:center}
header a{color:#93c5fd;text-decoration:none;font-weight:600}
header a:hover{text-decoration:underline}
main{padding:24px;max-width:1200px;margin:0 auto}
h1{font-size:20px;margin:0 0 16px}h2{font-size:16px;margin:24px 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.card{background:#1e293b;border-radius:8px;padding:12px 16px}
.card .k{color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.card .v{font-size:20px;font-weight:700;margin-top:4px}
.pos{color:#4ade80}.neg{color:#f87171}.warn{color:#fbbf24}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
th,td{padding:6px 10px;border-bottom:1px solid #334155;text-align:right;white-space:nowrap}
th{color:#94a3b8;font-weight:600}td:first-child,th:first-child{text-align:left}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;background:#1e293b;border-radius:8px;padding:16px}
label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:#94a3b8}
input,select{padding:8px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#e2e8f0;font-size:13px}
button{padding:10px 20px;border:0;border-radius:6px;background:#2563eb;color:white;font-weight:700;font-size:14px;cursor:pointer;margin-top:8px}
button:hover{background:#1d4ed8}
.badge{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700}
.badge.green{background:#14532d;color:#4ade80}.badge.red{background:#7f1d1d;color:#f87171}
.badge.amber{background:#78350f;color:#fbbf24}
.muted{color:#64748b;font-size:12px}
a.link{color:#93c5fd}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)} - Strategy Dashboard</title>"
        f"<style>{_CSS}</style></head>"
        f"<body><header><a href='/'>Dashboard</a>"
        f"<a href='/backtest'>Backtesting</a>"
        f"<a href='/forward'>Forward Testing</a></header>"
        f"<main>{body}</main></body></html>"
    )


def _cls(value: float) -> str:
    if value > 0:
        return "pos"
    if value < 0:
        return "neg"
    return ""


def _fmt(value: float, digits: int = 2) -> str:
    if isinstance(value, str):
        return value
    return f"{value:.{digits}f}"


def _symbol_info(symbol: str) -> dict[str, Any]:
    try:
        from client import get_client

        info = get_client().symbol_info(symbol)
        return info if isinstance(info, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _run_backtest(
    params: BacktestParams,
    out_dir: Path,
    since: str | None = None,
) -> tuple[BacktestResult, Path | None]:
    tfs = sorted({params.trend_timeframe, params.confirmation_timeframe, *params.zone_timeframes})
    frames = load_frames(params.symbol, tfs, params.bars)
    if since:
        since_ts = pd.Timestamp(since)
        sliced: dict[str, pd.DataFrame] = {}
        for tf, f in frames.items():
            col = f["time"]
            if col.dt.tz is not None:
                col = col.dt.tz_localize(None)
            f = f.assign(time=col)
            sliced[tf] = f[f["time"] >= since_ts]
        frames = sliced
    info = _symbol_info(params.symbol)
    bt = Backtester(params, frames, info)
    result = bt.run()
    saved: Path | None = None
    if not result.errors or result.n_trades:
        try:
            saved = save_result(result, out_dir)
            rebuild_summary(out_dir)
        except Exception:  # noqa: BLE001
            saved = None
    return result, saved


def _equity_points(result: BacktestResult) -> list[dict[str, Any]]:
    equity = result.params.get("start_balance", 10000.0)
    points = [{"label": "start", "value": equity}]
    for t in result.trades:
        equity += t["pnl"]
        points.append({"label": t["exit_time"][:10], "value": equity})
    return points


def _svg_curve(points: list[dict[str, Any]], width: int = 940, height: int = 220) -> str:
    if len(points) < 2:
        return "<p class='muted'>No equity curve (no trades).</p>"
    values = [p["value"] for p in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = 10

    def x(i: int) -> float:
        return pad + i * (width - 2 * pad) / (len(points) - 1)

    def y(v: float) -> float:
        return height - pad - (v - lo) / span * (height - 2 * pad)
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    base_y = y(lo)
    area = f"{pad},{height - pad} " + " ".join(
        f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values)
    ) + f" {width - pad},{height - pad}"
    last = values[-1]
    color = "#4ade80" if last >= values[0] else "#f87171"
    return (
        f"<svg viewBox='0 0 {width} {height}' style='width:100%;background:#0b1220;"
        f"border-radius:8px;margin-top:8px'>"
        f"<polygon points='{area}' fill='{color}' opacity='0.15'/>"
        f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='2'/>"
        f"<line x1='{pad}' y1='{base_y:.1f}' x2='{width - pad}' y2='{base_y:.1f}' "
        f"stroke='#475569' stroke-dasharray='4 4'/>"
        f"</svg>"
    )


def _trades_table(result: BacktestResult) -> str:
    trades = result.trades
    if not trades:
        return "<p class='muted'>No trades in this run.</p>"
    rows = []
    for t in trades:
        rows.append(
            f"<tr><td>{html.escape(t['entry_time'][:16])}</td>"
            f"<td>{t['action']}</td><td>{_fmt(t['entry'], 4)}</td>"
            f"<td>{_fmt(t['sl'], 4)}</td><td>{_fmt(t['tp'], 4)}</td>"
            f"<td>{_fmt(t['exit_price'], 4)}</td>"
            f"<td>{t['lots']}</td><td>{t['rr']:.1f}</td>"
            f"<td class='{_cls(t['pnl'])}'>{_fmt(t['pnl'])}</td>"
            f"<td class='muted'>{html.escape(t.get('reason', ''))}</td></tr>"
        )
    return (
        "<table><tr><th>Entry</th><th>Side</th><th>Entry</th><th>SL</th><th>TP</th>"
        "<th>Exit</th><th>Lots</th><th>R</th><th>PnL</th><th>Reason</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def _stats_cards(result: BacktestResult) -> str:
    cards = [
        ("Trades", str(result.n_trades), ""),
        ("Win rate", f"{result.win_rate:.1f}%", "pos" if result.win_rate >= 50 else "neg"),
        ("Profit factor", _fmt(result.profit_factor), "pos" if result.profit_factor >= 1 else "neg"),
        ("Net PnL", f"{result.net_pnl:+.2f}", _cls(result.net_pnl)),
        ("Return", f"{result.total_return_pct:+.2f}%", _cls(result.total_return_pct)),
        ("Max DD", f"{result.max_drawdown_pct:.2f}%", "neg"),
        ("Expectancy", f"{result.expectancy:+.2f}", _cls(result.expectancy)),
        ("Avg R", f"{result.avg_rr:.2f}", ""),
    ]
    return "<div class='grid'>" + "".join(
        f"<div class='card'><div class='k'>{k}</div>"
        f"<div class='v {c}'>{v}</div></div>" for k, v, c in cards
    ) + "</div>"


def _run_view(result: BacktestResult, saved: Path | None, since: str | None) -> str:
    window = f"<p class='muted'>Evaluation window: {result.start} &rarr; {result.end}"
    if since:
        window += f" (windowed from {html.escape(since)})"
    window += "</p>"
    saved_html = ""
    if saved:
        saved_html = f"<p class='muted'>Saved to <code>{html.escape(str(saved))}</code></p>"
    errors = ""
    if result.errors:
        errors = "<p class='warn'>" + html.escape("; ".join(result.errors)) + "</p>"
    return (
        f"{_stats_cards(result)}{window}{saved_html}{errors}"
        f"{_svg_curve(_equity_points(result))}"
        f"<h2>Trades</h2>{_trades_table(result)}"
    )


def _summary_table(out_dir: Path) -> str:
    path = out_dir / "summary.csv"
    if not path.exists():
        return f"<p class='muted'>No summary.csv in {html.escape(str(out_dir))}</p>"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if not rows:
        return "<p class='muted'>Empty summary.</p>"
    head = "<tr>" + "".join(f"<th>{html.escape(k)}</th>" for k in rows[0]) + "</tr>"
    body = []
    for r in rows:
        cls = ""
        try:
            net = float(r.get("net_pnl", 0) or 0)
            cls = _cls(net)
        except ValueError:
            pass
        body.append(
            "<tr>" + "".join(
                f"<td class='{cls if k == 'net_pnl' else ''}'>{html.escape(v)}</td>"
                for k, v in r.items()
            ) + "</tr>"
        )
    return f"<table>{head}{''.join(body)}</table>"


def _runs_with_trades() -> list[tuple[str, BacktestResult]]:
    found: list[tuple[str, BacktestResult]] = []
    for path in sorted(RESULTS.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if data.get("n_trades", 0) > 0:
            try:
                found.append((str(path.relative_to(RESULTS)), BacktestResult(**data)))
            except TypeError:
                continue
    return found[-12:]


def _compliance_card() -> str:
    try:
        from compliance import evaluate

        s = evaluate()
    except Exception as exc:  # noqa: BLE001
        return f"<p class='muted'>Compliance unavailable: {html.escape(str(exc))}</p>"
    mode = s.get("mode", "unknown")
    badge = {
        "normal": ("green", "NORMAL"),
        "conservative": ("amber", "CONSERVATIVE"),
        "halted": ("red", "HALTED"),
    }.get(mode, ("amber", str(mode).upper()))
    cards = [
        ("Mode", f"<span class='badge {badge[0]}'>{badge[1]}</span>", ""),
        ("Balance", f"{s.get('balance', 0):,.2f}", ""),
        ("Daily buffer", f"{s.get('remaining_daily_buffer', 0):.2f}", ""),
        ("Drawdown buffer", f"{s.get('remaining_drawdown_buffer', 0):.2f}", ""),
        ("Open risk", f"{s.get('open_risk', 0):.2f}%", ""),
        ("Trades today", str(s.get('trades_today', 0)), ""),
        ("News blackout", "YES" if s.get("news_blocked") else "no", "warn" if s.get("news_blocked") else ""),
        ("Trading allowed", "YES" if s.get("trading_allowed") else "NO", "pos" if s.get("trading_allowed") else "neg"),
    ]
    body = "<div class='grid'>" + "".join(
        f"<div class='card'><div class='k'>{k}</div>"
        f"<div class='v {c}'>{v}</div></div>" for k, v, c in cards
    ) + "</div>"
    if s.get("halt_reason"):
        body += f"<p class='warn'>Halt reason: {html.escape(s['halt_reason'])}</p>"
    return body


@app.get("/", response_class=HTMLResponse)
def overview() -> HTMLResponse:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        rel = str(DEFAULT_OUT.relative_to(BASE))
    except ValueError:
        rel = str(DEFAULT_OUT)
    body = f"<h1>Overview <span class='muted'>({now})</span></h1>"
    body += "<h2>Compliance status</h2>" + _compliance_card()
    body += f"<h2>Latest run - {html.escape(rel)}</h2>"
    body += _summary_table(DEFAULT_OUT)
    runs = _runs_with_trades()
    if runs:
        body += "<h2>Trades from recent backtests</h2>"
        for name, result in runs:
            body += (
                f"<h3><a class='link' href='/run?name={html.escape(name)}'>{html.escape(name)}</a>"
                f" <span class='muted'>{result.n_trades} trades, "
                f"net {result.net_pnl:+.2f}</span></h3>"
            )
            body += _trades_table(result)
    return _page("Overview", body)


@app.get("/run", response_class=HTMLResponse)
def run_detail(name: str) -> HTMLResponse:
    path = RESULTS / name
    if not path.is_file():
        return _page("Not found", f"<p>No result file <code>{html.escape(name)}</code></p>")
    data = json.loads(path.read_text(encoding="utf-8"))
    return _page(name, _run_view(BacktestResult(**data), path, None))


@app.get("/backtest", response_class=HTMLResponse)
def backtest_form() -> HTMLResponse:
    body = (
        "<h1>Backtesting</h1>"
        "<form method='post' action='/backtest'>"
        "<label>Symbol"
        "<select name='symbol'>"
        + "".join(f"<option value='{s}'>{s}</option>" for s in DEFAULT_SYMBOLS)
        + "</select></label>"
        "<label>Trend timeframe <select name='trend_tf'><option>H4</option><option>H1</option></select></label>"
        "<label>Zone timeframes <input name='zone_tfs' value='H1,H4'></label>"
        "<label>Min zone score <input type='number' step='1' name='min_zone_score' value='40'></label>"
        "<label>Min RR <input type='number' step='0.5' name='min_rr' value='3'></label>"
        "<label>TP min (pips) <input type='number' name='tp_min' value='80'></label>"
        "<label>TP max (pips) <input type='number' name='tp_max' value='100'></label>"
        "<label>SL min (pips) <input type='number' name='sl_min' value='20'></label>"
        "<label>Risk % <input type='number' step='0.1' name='risk' value='0.5'></label>"
        "<label>Bars (H1) <input type='number' name='bars' value='1500'></label>"
        "<label>Start balance <input type='number' name='start_balance' value='10000'></label>"
        "<label>Output dir <input name='out' value='backtest/results/month'></label>"
        "<div><button type='submit'>Run backtest</button>"
        "<p class='muted'>Data is fetched live from MT5 (terminal must be running). "
        "~460 bars of warm-up are consumed, so bars=1500 covers roughly 6 weeks.</p></div>"
        "</form>"
    )
    return _page("Backtesting", body)


@app.post("/backtest", response_class=HTMLResponse)
def backtest_run(
    symbol: str = Form(...),
    trend_tf: str = Form("H4"),
    zone_tfs: str = Form("H1,H4"),
    min_zone_score: float = Form(40.0),
    min_rr: float = Form(3.0),
    tp_min: float = Form(80.0),
    tp_max: float = Form(100.0),
    sl_min: float = Form(20.0),
    risk: float = Form(0.5),
    bars: int = Form(1500),
    start_balance: float = Form(10000.0),
    out: str = Form("backtest/results/month"),
) -> HTMLResponse:
    params = BacktestParams(
        symbol=symbol.upper(),
        trend_timeframe=trend_tf.upper(),
        zone_timeframes=[tf.upper() for tf in zone_tfs.split(",") if tf.strip()],
        min_zone_score=min_zone_score,
        min_rr=min_rr,
        tp_min=tp_min,
        tp_max=tp_max,
        sl_min=sl_min,
        risk_percent=risk,
        bars=bars,
        start_balance=start_balance,
    )
    out_dir = Path(out)
    if not out_dir.is_absolute():
        out_dir = BASE / out_dir
    started = time.perf_counter()
    result, saved = _run_backtest(params, out_dir)
    elapsed = time.perf_counter() - started
    body = (
        f"<h1>Backtest - {html.escape(params.symbol)} "
        f"<span class='muted'>({elapsed:.1f}s)</span></h1>"
        f"{_run_view(result, saved, None)}"
        "<p><a class='link' href='/backtest'>Run another</a></p>"
    )
    return _page(f"Backtest {params.symbol}", body)


@app.get("/forward", response_class=HTMLResponse)
def forward_form() -> HTMLResponse:
    body = (
        "<h1>Forward Testing</h1>"
        "<p class='muted'>Runs the strategy on the most recent window only "
        "(out-of-sample, no retraining) and compares it with the full backtest "
        "on the same symbol.</p>"
        "<form method='post' action='/forward'>"
        "<label>Symbol <select name='symbol'>"
        + "".join(f"<option value='{s}'>{s}</option>" for s in DEFAULT_SYMBOLS)
        + "</select></label>"
        "<label>Start date (YYYY-MM-DD) <input name='since' value='2026-07-01'></label>"
        "<label>Trend timeframe <select name='trend_tf'><option>H4</option><option>H1</option></select></label>"
        "<label>Zone timeframes <input name='zone_tfs' value='H1,H4'></label>"
        "<label>Min zone score <input type='number' step='1' name='min_zone_score' value='40'></label>"
        "<label>Min RR <input type='number' step='0.5' name='min_rr' value='3'></label>"
        "<label>TP min (pips) <input type='number' name='tp_min' value='80'></label>"
        "<label>TP max (pips) <input type='number' name='tp_max' value='100'></label>"
        "<label>Risk % <input type='number' step='0.1' name='risk' value='0.5'></label>"
        "<label>Bars (H1) <input type='number' name='bars' value='1500'></label>"
        "<div><button type='submit'>Run forward test</button>"
        "<p class='muted'>Runs twice (windowed + full comparison), so allow 1-3 minutes.</p></div>"
        "</form>"
    )
    return _page("Forward Testing", body)


@app.post("/forward", response_class=HTMLResponse)
def forward_run(
    symbol: str = Form(...),
    since: str = Form("2026-07-01"),
    trend_tf: str = Form("H4"),
    zone_tfs: str = Form("H1,H4"),
    min_zone_score: float = Form(40.0),
    min_rr: float = Form(3.0),
    tp_min: float = Form(80.0),
    tp_max: float = Form(100.0),
    risk: float = Form(0.5),
    bars: int = Form(1500),
) -> HTMLResponse:
    symbol = symbol.upper()
    common = dict(
        symbol=symbol,
        trend_timeframe=trend_tf.upper(),
        zone_timeframes=[tf.upper() for tf in zone_tfs.split(",") if tf.strip()],
        min_zone_score=min_zone_score,
        min_rr=min_rr,
        tp_min=tp_min,
        tp_max=tp_max,
        risk_percent=risk,
        bars=bars,
    )
    out_dir = RESULTS / "forward"
    started = time.perf_counter()
    full, _ = _run_backtest(BacktestParams(**common), out_dir)
    windowed, _ = _run_backtest(BacktestParams(**common), out_dir, since=since)
    elapsed = time.perf_counter() - started
    body = (
        f"<h1>Forward Test - {html.escape(symbol)} <span class='muted'>({elapsed:.1f}s)</span></h1>"
        "<h2>Forward window (since " + html.escape(since) + ")</h2>"
        + _run_view(windowed, None, since)
        + "<h2>Full backtest (comparison)</h2>"
        + _run_view(full, None, None)
        + "<p><a class='link' href='/forward'>Run another</a></p>"
    )
    return _page(f"Forward {symbol}", body)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Strategy dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
