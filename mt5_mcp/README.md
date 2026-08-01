# MT5 MCP Server - Price Action Trading System

A production-ready Model Context Protocol (MCP) server connecting AI agents
(OpenCode, Claude Desktop, ChatGPT, any MCP client) to a running MetaTrader 5
terminal. It implements a complete price action trading workflow: trend
analysis, supply/demand zones, confirmation patterns, risk-managed execution,
position management, history analytics, and an AI memory layer.

## Features

| Area | What it does |
|---|---|
| **MT5 connection** | Auto-connect to a running terminal, reconnect with retries, health checks, graceful shutdown |
| **Account** | Balance, equity, margin, margin level, daily P/L, floating profit, drawdown, currency, leverage |
| **Market** | Symbols, search, bid/ask, spread, point, tick value, pip value, session, open status |
| **Candles** | OHLCV for M1/M5/M15/M30/H1/H4/D1/W1/MN1, any count |
| **Indicators** | EMA, SMA, ATR, RSI, MACD, Bollinger, VWAP, volume, swing highs/lows (pure pandas, no TA-Lib needed) |
| **Supply & Demand** | Zone detection (drop-base-rally / rally-base-drop), strength 1-100, touches, fresh/broken/retested |
| **Price action** | Engulfing, pin bar, hammer, shooting star, morning/evening star, inside/outside bar, BOS, CHOCH, liquidity sweep, fake breakout - all with confidence scores |
| **Trend** | EMA 50/200 + market structure (HH/HL/LH/LL) -> bullish/bearish/sideways |
| **Strategy** | Full rules engine: trend filter -> zone revisit -> confirmation -> 0.5% risk -> 80-100 pip target -> 20-30 pip SL -> min RR 1:3, scored 0-100 |
| **Trades** | Market orders, pending orders, close, partial close, SL/TP modify, trailing stop, break-even, risk-based lot sizing |
| **Positions** | Floating P/L, pips, risk, duration, R:R, health rating |
| **History** | Closed trades, win rate, avg win/loss, avg RR, profit factor, monthly/daily profits |
| **Risk manager** | Lot size, daily/weekly loss limits, open risk, exposure, warnings, blocks trading on limit breach |
| **Market intelligence** | Market Health Score (0-100), regime detection (trending/ranging/volatile), abnormal volatility (ATR percentile), session awareness, spread/news/liquidity scoring |
| **Research Agent** | Weekly/periodic analysis: win rate, expectancy, profit factor, avg R, max DD, best/worst symbols and setups, sessions, mistakes, data-driven suggestions |
| **AI Coach** | Post-trade reviews: why taken, evidence for/against, improvements, how similar historical trades performed |
| **AI memory** | SQLite journal auto-synced from MT5 history, notes (reason/mistakes/lessons/screenshots), learning queries, statistics dashboard |
| **Logging** | Every tool call logged with time, user, tool, inputs, outputs, duration, errors (file + SQLite audit table) |
| **Safety** | Preflight checks on every order: symbol exists, market open, spread, SL/TP vs stop level, lot bounds, margin, max positions |

## Project layout

```
mt5_mcp/
├── server.py            # FastMCP entry point, tool registration, audit middleware
├── config.py            # settings.json + env/.env overrides, logging setup
├── settings.json        # all tunable parameters
├── common.py            # shared pydantic models, timeframes, pip helpers
├── mt5_client.py        # MT5 connection manager (reconnect, health, thread-safe)
├── client.py            # shared client singleton (multi-account ready)
├── indicators.py        # pandas indicator library
├── supply_demand.py     # zone detection + scoring
├── price_action.py      # candle patterns + structure events
├── strategy.py          # the trading rules engine
├── market_intel.py      # Market Intelligence Agent (regime, health score)
├── research.py          # Research Agent (periodic reports, suggestions)
├── coach.py             # AI Coach (post-trade reviews)
├── tools/               # one MCP tool module per domain
├── memory/              # trade journal (SQLite), learning, statistics
├── logs/                # rotating log files
├── tests/               # pytest suite (no MT5 terminal required)
└── examples/            # MCP client configs and a demo client
```

## Quickstart

```bash
cd mt5_mcp
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt   # for tests

copy .env.example to .env    # or configure settings.json / env vars

python server.py             # starts the stdio MCP server
```

The terminal must be running and logged in. Connection settings (in order of
precedence): environment variables `MT5_MCP_MT5_TERMINAL_PATH`,
`MT5_MCP_MT5_LOGIN`, `MT5_MCP_MT5_PASSWORD`, `MT5_MCP_MT5_SERVER` >
`settings.json` > `.env`.

> **Security note**: prefer environment variables over `settings.json` for
> login credentials - `settings.json` may be committed to version control.

## Example MCP client configs

**Claude Desktop** (`examples/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mt5": {
      "command": "python",
      "args": ["E:\\Project\\Mt5 Ea\\mt5_mcp\\server.py"],
      "env": {
        "MT5_MCP_MT5_LOGIN": "12345678",
        "MT5_MCP_MT5_PASSWORD": "your_password",
        "MT5_MCP_MT5_SERVER": "YourBroker-Server"
      }
    }
  }
}
```

**OpenCode** (`examples/opencode_config.json`): add the server to your
`opencode.json` MCP servers section, same command/args.

**Demo client** (`examples/mcp_client_demo.py`): a standalone async client
that connects over stdio and shows the tool list + a strategy evaluation.

## Strategy tool output

`evaluate_strategy("EURUSD")` returns a scored trade plan:

```json
{
  "action": "BUY",
  "valid": true,
  "confidence": 96.4,
  "trade_score": 97.4,
  "reason": "HTF trend bullish (EMA50/200, conf 100%); demand zone 1.30398-1.30420 strength 100/100 touches 2; confirmation: BOS (bullish, 90%); RR 4.16 (min 3.0), risk 0.5% = 50.00 USD",
  "entry": 1.30586, "sl": 1.30370, "tp": 1.31486,
  "sl_pips": 21.6, "tp_pips": 90.0, "risk_reward": 4.16,
  "htf_trend": "bullish",
  "confirmation": "BOS (bullish, conf 0.90)",
  "zone_strength": 99.7,
  "lots": 0.23,
  "risk_amount": 50.0,
  "warnings": [], "errors": []
}
```

## Safety

Every order goes through `tools/trades.py::_preflight`:

1. symbol exists
2. market is open (trading mode, weekday, fresh quotes)
3. spread within configured limit
4. SL/TP on the correct side and beyond the broker stop level
5. lot size within broker min/max/step
6. free margin covers the order (`order_calc_margin`)
7. max open positions respected

Risk limits are enforced by `risk_report`: if the daily or weekly loss limit
is hit, `trading_allowed` becomes `false` and the agent is told to stop.

## Market Intelligence Agent tools

`market_intelligence("EURUSD")` returns a Market Health Score (0-100) with
fully explainable components:

```json
{
  "symbol": "EURUSD",
  "health_score": 82.3,
  "verdict": "tradeable",
  "regime": "trending_bullish",
  "trend": "bullish",
  "trend_confidence": 87.5,
  "components": [
    {"name": "trend", "score": 87.5, "weight": 0.20, "note": "EMA50/200 + structure: bullish (HH 3, HL 2, LH 1, LL 0)"},
    {"name": "volatility", "score": 92.0, "weight": 0.15, "note": "ATR 0.00084 at 54th percentile (z -0.12)"},
    {"name": "regime", "score": 100.0, "weight": 0.20, "note": "trending bullish - EMA separation 1.34 ATRs"},
    {"name": "spread", "score": 87.5, "weight": 0.10, "note": "spread 5.0 points (limit 40.0)"},
    {"name": "session", "score": 70.0, "weight": 0.10, "note": "new_york active (new_york, london)"},
    {"name": "news", "score": 100.0, "weight": 0.15, "note": "no high-impact events imminent"},
    {"name": "liquidity", "score": 100.0, "weight": 0.10, "note": "market open (market open)"}
  ],
  "evidence": [
    "trend bullish with 88% clarity",
    "regime: trending_bullish",
    "spread near or above the configured limit"
  ]
}
```

`market_scan()` ranks multiple symbols by health score - best conditions first.

## Research Agent tools

`research_report(days=7)` produces the weekly research report with stats
and data-driven suggestions:

```json
{
  "window_days": 7,
  "trades": 12,
  "win_rate": 58.3,
  "expectancy": 42.1,
  "profit_factor": 1.8,
  "average_rr": 2.9,
  "net_pnl": 505.0,
  "max_drawdown": {"max_drawdown": 680.0, "max_drawdown_percent": 6.8},
  "best_symbols": [{"name": "EURUSD", "trades": 5, "win_rate": 60.0, "net_pnl": 320.0}],
  "worst_symbols": [{"name": "XAUUSD", "trades": 3, "win_rate": 33.3, "net_pnl": -180.0}],
  "suggestions": [
    {"severity": "medium", "suggestion": "avoid XAUUSD until conditions improve", "support": "3 trades, -180.00 net, 33% win rate", "action": "remove the symbol from the watchlist..."},
    {"severity": "low", "suggestion": "winners are being cut short", "support": "average realised R multiple 2.90 (target 3.0)", "action": "let winners reach the full TP or trail stops..."}
  ]
}
```

Reports are saved as Markdown + JSON to `memory/reports/`.

## AI Coach tool

`coach_review(ticket)` (or no ticket for the latest trade) returns a full
post-trade review:

```json
{
  "ticket": 42,
  "outcome": "loss",
  "why_taken": "demand retest with engulfing confirmation",
  "verdict": "the trade lost - separate bad luck from bad process using the evidence below",
  "evidence_for": [
    "trade score 85 passes the 70 bar",
    "zone strength 92 passes the 70 bar",
    "planned R:R 3.50 meets the 3.0 minimum",
    "entry reason journaled - the trade was a decision, not a guess"
  ],
  "evidence_against": [
    "confidence 82% below the A+ floor (90%)",
    "no session recorded"
  ],
  "improvement": [
    "mistake to avoid next time: entered_before_confirmation",
    "lesson learned: wait for candle close",
    "similar trades average -12.00 per trade - this setup family is currently a losing proposition"
  ],
  "similar_trades": {
    "count": 4,
    "win_rate": 25.0,
    "avg_pnl": -12.0,
    "trades": [...]
  }
}
```

## AI memory

- `sync_journal` pulls closed trades from MT5 history into SQLite
  (`memory/journal.db`).
- `journal_update_notes` attaches reason, mistakes, lessons, screenshot path,
  market conditions to a ticket.
- Learning queries: `repeated_mistakes` ("what am I repeating?"),
  `best_setups`, `best_pairs`, `best_sessions`.
- `statistics_dashboard`: win rate, expectancy, average RR, profit factor,
  Sharpe, max drawdown, equity curve, monthly profits, best setups/pairs/sessions.
- `audit_log`: full trace of every tool call.

## Testing

```bash
cd mt5_mcp
python -m pytest tests -q
```

All tests run without an MT5 terminal using fakes (`tests/conftest.py`).

## Configuration knobs (`settings.json`)

| Section | Key knobs |
|---|---|
| `risk` | default/max risk %, daily/weekly loss %, max open risk %, min RR, TP/SL pips, max spread, max positions |
| `strategy` | trend TF + EMAs, zone TFs, confirmation TF, min zone/trade score, min confidence |
| `mt5` | terminal path, login, retries, timeouts |
| `memory` | journal db path, auto-sync window, report dir |
| `logging` | level, log dir, rotation |

## Extending (future modules)

The architecture is ready for: Smart Money Concepts (SMC), ICT, economic
calendar (implement `NewsProvider`), multi-account trading (swap
`client.py` for a registry), portfolio management, reinforcement learning
(feed from the journal), AI coaching (learning queries), Telegram/Discord
alerts (subscribe to audit), web dashboard / FastAPI / WebSocket
(any HTTP server can import `strategy.py` directly), Docker
(wrap `python server.py` with the terminal sidecar).
