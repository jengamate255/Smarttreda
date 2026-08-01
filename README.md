# MT5 Institutional AI Trading Assistant

A production-ready **Model Context Protocol (MCP) server** + **Web Dashboard** connecting AI agents to MetaTrader 5. Implements a complete institutional trading desk:

- **Market Intelligence Agent** — Market Health Score (0-100), regime detection, session awareness
- **Supply & Demand Agent** — Zone detection, scoring, freshness tracking
- **Price Action Agent** — 10+ patterns with confidence scores
- **Trend Agent** — EMA50/200 + market structure (HH/HL/LH/LL)
- **Strategy Engine** — Multi-timeframe rules, risk-managed trade plans
- **Risk/Compliance Agent** — Veto gate, daily/weekly limits, correlation exposure
- **Execution Agent** — Market/pending orders, SL/TP modify, trailing, break-even
- **Pattern Memory (Journal)** — SQLite trade journal, auto-sync from MT5
- **Statistical Learning Agent** — Win rate, expectancy, profit factor, Sharpe, best setups/pairs/sessions
- **Research Agent** — Weekly reports with data-driven suggestions
- **AI Coach** — Post-trade reviews: evidence for/against, similar trades, improvements
- **Web Dashboard** — Real-time charts, positions, backtesting, journal, news

---

## Quickstart (Native Windows — Required for MT5)

The **MCP server must run natively on Windows** because the MetaTrader 5 Python package connects to a locally running MT5 terminal via named pipes. Docker (Linux containers) cannot access the host's MT5 terminal.

### 1. Install Dependencies

```bash
pip install -e ".[dev]"
```

### 2. Configure Credentials

```bash
copy .env.example .env
# Edit .env with your MT5 login details
```

### 3. Run Manually

**Terminal 1 — MCP Server (for AI agents):**
```bash
python server.py
```

**Terminal 2 — Web Dashboard:**
```bash
python web_dashboard.py
# Open http://localhost:8050
```

---

## Auto-Start at Windows Login (Recommended)

Run the MCP server + web dashboard automatically when you log in:

### Option A: PowerShell Script + Task Scheduler (Included)

1. **Register the startup task:**
   ```powershell
   # Run as Administrator
   $taskXml = Get-Content "E:\Project\Mt5 Ea\MT5_Trading_Assistant_Startup.xml" -Raw
   Register-ScheduledTask -Xml $taskXml -TaskName "MT5 Trading Assistant" -Force
   ```

2. **Or manually via Task Scheduler:**
   - Open **Task Scheduler** → **Import Task** → select `MT5_Trading_Assistant_Startup.xml`
   - Trigger: **At log on** (30s delay)
   - Action: `powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File "E:\Project\Mt5 Ea\start_services.ps1"`

### Option B: Simple Startup Folder Shortcut

1. Create a shortcut to `start_services.ps1`
2. Press `Win+R` → `shell:startup` → paste shortcut
3. Right-click shortcut → Properties → Advanced → **Run as administrator**

---

## Usage with AI Assistants (MCP Clients)

### Claude Desktop (`claude_desktop_config.json`)

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

### OpenCode (`.opencode.json`)

```json
{
  "mcp": {
    "servers": {
      "mt5": {
        "command": "python",
        "args": ["E:\\Project\\Mt5 Ea\\mt5_mcp\\server.py"],
        "env": { "MT5_MCP_MT5_LOGIN": "12345678", "MT5_MCP_MT5_PASSWORD": "xxx", "MT5_MCP_MT5_SERVER": "Broker-Server" }
      }
    }
  }
}
```

---

## Web Dashboard (Optional Docker)

The web dashboard *can* run in Docker since it only needs HTTP access to the MCP server:

```bash
# Build
docker compose build

# Run dashboard only (connects to host MT5 via MCP server)
docker compose up -d web-dashboard

# Access at http://localhost:8050
```

**docker-compose.yml** is included for this. The MCP server still runs natively on Windows.

---

## MCP Tools Available (55 tools)

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
│   ├── tools/                   # MCP tool modules (15 domains)
│   ├── memory/                  # SQLite journal, learning, statistics
│   ├── tests/                   # 197 tests (no MT5 required)
│   └── examples/                # MCP client configs
└── static/                      # Web dashboard UI
```

---

## Testing

```bash
# All tests (no MT5 terminal required - uses fakes)
python -m pytest mt5_mcp/tests -q

# Web dashboard tests
python -m pytest test_web_dashboard.py test_server_tools.py test_server.py -q
```

---

## License

MIT