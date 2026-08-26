# MT5 Institutional AI Trading Assistant — Project Overview

## What It Is

A Python-based **Model Context Protocol (MCP) server** that connects AI agents (Claude, ChatGPT, etc.) to MetaTrader 5 for automated and semi-automated trading. It implements a complete institutional trading desk with 55 MCP tools across 10+ AI agent modules.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  AI Agent (Claude / OpenCode / ChatGPT)             │
│  Connects via MCP protocol (stdio or HTTP)          │
└──────────────────────┬──────────────────────────────┘
                       │  55 MCP tools
┌──────────────────────▼──────────────────────────────┐
│  MCP Server (server.py)                             │
│  FastMCP framework, middleware logging, audit trail  │
├─────────────────────────────────────────────────────┤
│  AI Agents (each a module):                         │
│  • Market Intelligence — Health Score, regime, session│
│  • Supply & Demand — Zone detection, scoring         │
│  • Price Action — 10+ patterns with confidence       │
│  • Trend — EMA50/200, market structure (HH/HL/LH/LL)│
│  • Strategy Engine — Multi-timeframe rules           │
│  • Risk/Compliance — Veto gate, limits, correlation  │
│  • Execution — Orders, SL/TP, trailing, break-even   │
│  • Statistical Learning — Win rate, expectancy, Sharpe│
│  • Research Agent — Weekly reports                   │
│  • AI Coach — Post-trade reviews                     │
├─────────────────────────────────────────────────────┤
│  SQLite Memory — Trade journal, learning, statistics │
└──────────────────────┬──────────────────────────────┘
                       │  MetaTrader5 Python package
┌──────────────────────▼──────────────────────────────┐
│  MT5 Terminal(s) — Native Windows only               │
│  • FundingPips2-SIM (prop firm, account 11911448)   │
│  • Deriv-Demo (synthetic indices, account 41204968)  │
└─────────────────────────────────────────────────────┘
```

---

## Key Files

| File | Purpose |
|------|---------|
| `mt5_mcp/server.py` | MCP server entry point, tool registration, audit logging |
| `mt5_mcp/config.py` | Settings from `settings.json` + env overrides |
| `mt5_mcp/client.py` | MT5 connection singleton |
| `mt5_mcp/strategy.py` | Trading rules engine |
| `mt5_mcp/supply_demand.py` | Zone detection + scoring |
| `mt5_mcp/price_action.py` | Pattern recognition |
| `mt5_mcp/market_intel.py` | Market Health Score (0-100), regime detection |
| `mt5_mcp/compliance.py` | Prop firm compliance gate |
| `mt5_mcp/coach.py` | AI Coach post-trade reviews |
| `mt5_mcp/research.py` | Research Agent weekly reports |
| `mt5_mcp/backtest.py` | Backtesting engine |
| `mt5_mcp/ml_ranker.py` | ML trade ranking |
| `mt5_mcp/monte_carlo.py` | Monte Carlo simulation |
| `mt5_mcp/tools/` | 24 MCP tool modules (account, trades, indicators, etc.) |
| `mt5_mcp/memory/` | SQLite journal, learning, statistics |
| `mt5_mcp/ea/` | MQL5 Expert Advisors (compiled .ex5 + source .mq5) |
| `web_dashboard.py` | FastAPI web dashboard (port 8050) |
| `run_dual_accounts.py` | Dual-account supervisor (prop + synthetic) |
| `mt5_mcp/auto_trader.py` | Unattended SIM auto-trader |

---

## Tech Stack

- **Language:** Python 3.10+, MQL5 (for EA source)
- **Framework:** FastMCP (MCP server), FastAPI (web dashboard)
- **Data:** pandas, numpy, matplotlib, SQLite
- **MT5 Integration:** MetaTrader5 Python package (Windows-only, named pipe IPC)
- **Deployment:** Native Windows (required for MT5), optional Docker for dashboard only
- **Config:** `settings.json` + `.env` files
- **Testing:** 197 tests in `mt5_mcp/tests/` (no MT5 required, uses fakes)

---

## MCP Tools (55 total)

| Category | Tools |
|----------|-------|
| **Account** | `get_account_info` |
| **Market** | `get_symbol_info`, `get_market_status`, `get_market_symbols`, `get_market_tick`, `search_symbols` |
| **Candles/Indicators** | `get_rates`, `get_tick_info`, `indicator_ema/sma/atr/rsi/macd/bollinger/vwap/volume/swings` |
| **Supply/Demand** | `detect_zones` |
| **Price Action** | `detect_patterns` |
| **Strategy** | `evaluate_strategy`, `trend_analysis` |
| **Trades** | `place_order`, `close_position`, `modify_sl_tp`, `trail_stop`, `break_even`, `calc_lots`, `assess_trade`, `pending_order` |
| **Positions/History** | `get_positions`, `get_orders`, `get_history_deals`, `get_history_orders` |
| **Risk/Compliance** | `risk_report`, `compliance_check`, `compliance_status` |
| **News** | `news_filter` |
| **Journal/Memory** | `sync_journal`, `journal_record`, `journal_update_notes`, `journal_query` |
| **Learning** | `repeated_mistakes`, `best_setups`, `best_pairs`, `best_sessions`, `learning_report`, `statistics_dashboard` |
| **Market Intelligence** | `market_intelligence`, `market_scan`, `market_health_note` |
| **Research** | `research_report`, `weekly_research_report` |
| **AI Coach** | `coach_review` |
| **System** | `health`, `system_info`, `audit_log` |

---

## Dual Account Setup

- **Prop Firm:** FundingPips2-SIM on `terminal64.exe` (FundingPips terminal), account `11911448`
- **Synthetic Indices:** Deriv-Demo on separate `terminal64.exe` (Deriv terminal), account `41204968`
- Supervised by `run_dual_accounts.py` with PID locks and graceful shutdown
- Separate PowerShell scripts for start/stop: `start_dual_live.ps1`, `stop_dual_all.ps1`

---

## Trading Strategies (EA Source Files)

The EA source files in `mt5_mcp/ea/` implement Price Action + Supply/Demand strategies:

| EA File | Market | Logic |
|---------|--------|-------|
| `PriceAction_SD_Trader_Med.mq5` | EURUSD | H1 zones, strength ≥70, 15-30 pip SL, 80-100 pip TP |
| `PriceAction_SD_Trader_JP225.mq5` | JP225 | H1+H4 zones, strength ≥40, 20-30 pip SL, 80-100 pip TP |
| `PriceAction_SD_Trader_SPX.mq5` | SPX500 | US index variant |
| `PriceAction_SD_Trader_HF.mq5` | Various | High frequency / shorter timeframes |
| `PriceAction_SD_Trader_BTC_Monitor.mq5` | BTCUSD | Crypto monitoring variant |
| `PriceAction_SD_Trader.mq5` | Base | Base template |

---

## Safety Gates (Auto Trader)

The unattended auto-trader enforces strict safety rules:

- **Account restriction:** SIM server only (`FundingPips2-SIM`), refuses live accounts
- **Minimum RR:** 1:3 risk-reward ratio
- **Confidence threshold:** 90%+ required
- **Compliance gate:** News, liquidity, spread, daily-loss, drawdown, position-count, correlation checks
- **Confirmation:** Completed H1 candle required
- **Fail-closed:** Trading disabled if news provider is unavailable
- **Kill switch:** `manage_auto_trader.ps1 -Action Disable` creates kill-switch file

---

## Running

```bash
# Install
pip install -e ".[dev]"

# MCP Server (for AI agents)
python server.py

# Web Dashboard
python web_dashboard.py    # http://localhost:8050

# Dual live
.\start_dual_live.ps1

# Tests
python -m pytest mt5_mcp/tests -q
```

---

## AI Agent Modules

### Market Intelligence Agent
- Market Health Score (0-100) combining volatility, trend strength, session quality
- Regime detection (trending, ranging, volatile)
- Session awareness (London, New York, Asian, overlap)

### Supply & Demand Agent
- Zone detection from candle structure
- Zone scoring (strength, freshness, mitigation status)
- Zone tracking across timeframes

### Price Action Agent
- 10+ candlestick patterns with confidence scores
- Market structure events (break of structure, change of character)

### Trend Agent
- EMA 50/200 crossover analysis
- Market structure: Higher Highs, Higher Lows, Lower Highs, Lower Lows

### Strategy Engine
- Multi-timeframe rule evaluation
- Risk-managed trade plan generation
- Entry/exit logic with position sizing

### Risk/Compliance Agent
- Veto gate — can block trades that fail risk criteria
- Daily/weekly loss limits
- Correlation exposure tracking
- Prop firm rule enforcement

### Execution Agent
- Market and pending orders
- SL/TP modification
- Trailing stop and break-even logic
- Lot size calculation

### Statistical Learning Agent
- Win rate, expectancy, profit factor, Sharpe ratio
- Best setups, pairs, sessions analysis
- Repeated mistake detection

### Research Agent
- Weekly performance reports
- Data-driven strategy suggestions

### AI Coach
- Post-trade reviews with evidence for/against
- Similar trade lookup
- Improvement suggestions

---

## Project Structure

```
Mt5 Ea/
├── server.py                    # Root MCP server (stdio transport)
├── web_dashboard.py             # FastAPI web dashboard (port 8050)
├── start_services.ps1           # Auto-start PowerShell script
├── MT5_Trading_Assistant_Startup.xml  # Task Scheduler import
├── docker-compose.yml           # Web dashboard container
├── Dockerfile                   # Build image
├── .env.example                 # Template for credentials
├── pyproject.toml               # Python project config
├── mt5_mcp/
│   ├── server.py                # MCP server entry point
│   ├── config.py                # Settings (settings.json + env overrides)
│   ├── settings.json            # All tunable parameters
│   ├── client.py                # MT5 client singleton
│   ├── mt5_client.py            # MT5 connection manager
│   ├── common.py                # Shared models, helpers
│   ├── indicators.py            # pandas TA (EMA, ATR, RSI, MACD, BB, VWAP, swings)
│   ├── supply_demand.py         # Zone detection + scoring
│   ├── price_action.py          # Patterns + structure events
│   ├── strategy.py              # Trading rules engine
│   ├── market_intel.py          # Market Intelligence Agent
│   ├── research.py              # Research Agent
│   ├── coach.py                 # AI Coach
│   ├── compliance.py            # Prop firm compliance gate
│   ├── backtest.py              # Backtesting engine
│   ├── monte_carlo.py           # Monte Carlo simulation
│   ├── ml_ranker.py             # ML trade ranking
│   ├── tools/                   # MCP tool modules (24 files)
│   ├── memory/                  # SQLite journal, learning, statistics
│   ├── tests/                   # 197 tests (no MT5 required)
│   ├── ea/                      # MQL5 Expert Advisors
│   └── examples/                # MCP client configs
├── static/                      # Web dashboard UI assets
├── backtest/                    # Backtest results and edge lab
├── colab/                       # Google Colab notebooks
└── logs/                        # Runtime logs
```
