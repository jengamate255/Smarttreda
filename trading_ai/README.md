# Forex Trading AI Platform

An autonomous recursive-improvement trading system for forex markets. The platform generates hypotheses, backtests strategy mutations, evaluates results through a gatekeeper, and promotes improvements — running continuously without human intervention.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        RECURSIVE IMPROVEMENT LOOP                       │
│                                                                         │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐            │
│  │  RESEARCH │──▶│ EVOLUTION │──▶│ BACKTEST │──▶│ EVALUATE │──┐         │
│  │  (hypo)   │   │ (mutate) │   │ (sim)    │   │(gatekeep)│  │         │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘  │         │
│       ▲                                                       │         │
│       └───────────────────────────────────────────────────────┘         │
│                                  │                                      │
│                     ┌────────────▼────────────┐                        │
│                     │      MARKET DATA        │                        │
│                     │  (MT5 / CSV / Stream)   │                        │
│                     └─────────────────────────┘                        │
├─────────────────────────────────────────────────────────────────────────┤
│  COMPONENTS                                                             │
│                                                                         │
│  data/          Market data normalization, validation, providers        │
│  strategies/    Genome-based strategy representation & mutation         │
│  backtesting/   Event-driven backtester with spread/slippage           │
│  risk/          Position sizing, drawdown tracking, kill switch         │
│  evaluation/    Gatekeeper scoring, robustness checks                   │
│  research/      Hypothesis generation from weakness detection           │
│  evolution/     Genetic algorithm for strategy population               │
│  features/      Technical indicator computation                         │
│  agents/        Autonomous agent lifecycle management                   │
│  memory/        Trade journal with natural language query               │
│  execution/     Paper & live broker adapters                            │
│  monitoring/    Performance dashboards & alerting                       │
│  api/           FastAPI REST interface                                   │
│  config/        Settings, events, session definitions                   │
│  models/        Shared schemas and data types                           │
└─────────────────────────────────────────────────────────────────────────┘
```

## Setup

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (for containerized deployment)
- PostgreSQL (or use Docker)

### Quick Start

```bash
# Clone and enter the project
cd trading_ai

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Generate sample data
python tools/generate_sample_data.py

# Run the example experiment
python tools/run_example_experiment.py

# Run tests
pytest tests/ -v
```

### Docker

```bash
# Build and start all services
docker-compose up -d

# Run tests inside the container
docker-compose exec app pytest tests/ -v

# View logs
docker-compose logs -f app
```

## Configuration

All settings are managed via environment variables with the prefix `TRADING_AI_`. Nested settings use `__` as delimiter.

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADING_AI__DATABASE__URL` | `sqlite+aiosqlite:///trading.db` | Database connection string |
| `TRADING_AI__REDIS__URL` | `None` | Redis URL for caching |
| `TRADING_AI__RISK_PER_TRADE` | `0.005` | Risk per trade as fraction of balance |
| `TRADING_AI__MIN_RISK_REWARD` | `3.0` | Minimum risk:reward ratio |
| `TRADING_AI__KILL_SWITCH__MAX_DRAWDOWN` | `0.10` | Max drawdown before kill switch |
| `TRADING_AI__KILL_SWITCH__DAILY_LOSS_LIMIT` | `0.03` | Daily loss limit before kill switch |
| `TRADING_AI__EXPERIMENT__MONTE_CARLO_ITERATIONS` | `1000` | Monte Carlo simulation count |

## Usage

### Strategy Genome

Strategies are encoded as JSON genomes — immutable data structures that describe the full strategy logic:

```python
from trading_ai.strategies.genome.schema import StrategyGenome

genome = StrategyGenome(
    strategy_id="my-strategy-001",
    name="Supply Demand v1",
    market=["EURUSD", "GBPUSD"],
    timeframes=["H4", "H1", "M15"],
    entry_rules=[
        {"type": "supply_demand", "params": {"lookback": 50, "min_strength": 0.6}},
    ],
    exit_rules=[
        {"type": "risk_reward", "params": {"min_rr": 3.0}},
    ],
    risk_rules=[
        {"type": "fixed_fraction", "params": {"risk_per_trade": 0.01}},
    ],
    filters=[],
)

# Mutate (creates a child genome, original unchanged)
child = genome.mutate({
    "filters": [{"type": "volatility", "params": {"min_atr": 0.001}}],
})
```

### Running Backtests

```python
from trading_ai.backtesting import Backtester
from trading_ai.data.normalizer import DataNormalizer

normalizer = DataNormalizer()
df = normalizer.normalize(raw_df)
backtester = Backtester(spread_pips=1.0, slippage_pips=0.5)
results = backtester.run(df, signals)
```

### Data Validation

```python
from trading_ai.data.validator import DataValidator

validator = DataValidator()
report = validator.validate(df, "EURUSD", "H1")
if report.has_errors:
    print(report.summary())
```

### Recursive Improvement Loop

The platform runs a continuous improvement loop:

1. **Research Agent** detects weaknesses in current strategies (e.g., high drawdown in ranging markets)
2. **Hypothesis Generation** proposes specific changes (e.g., "add volatility filter")
3. **Evolution Agent** mutates the strategy genome
4. **Backtester** runs the mutated strategy on historical data
5. **Walk-Forward Test** validates out-of-sample performance
6. **Monte Carlo Simulation** assesses robustness
7. **Gatekeeper** decides: PROMOTE / REJECT / HOLD
8. Loop repeats with improved strategies

### API

The FastAPI server provides REST endpoints for strategy management, backtesting, and monitoring:

```bash
# Start the API server
uvicorn trading_ai.api.main:app --reload --port 8000

# Available at http://localhost:8000/docs
```

## Safety Requirements

The platform includes multiple safety layers:

- **Kill Switch**: Automatically halts trading when drawdown or daily loss exceeds thresholds
- **Protected Genome Keys**: Risk limits, kill switch params, and broker credentials cannot be mutated
- **Minimum Risk:Reward**: All signals must meet minimum RR ratio (default 3:1)
- **Position Size Limits**: Maximum positions and per-trade risk are enforced
- **Data Validation**: Invalid data is caught before it reaches the backtester
- **Gatekeeper Evaluation**: No strategy goes live without passing robustness checks

### Kill Switch Thresholds

| Threshold | Default | Action |
|-----------|---------|--------|
| Max Drawdown | 10% | Halt all trading |
| Daily Loss Limit | 3% | Halt trading for the day |
| Min R:R | 3.0 | Reject any signal below threshold |

## Development Guide

### Project Structure

```
trading_ai/
├── agents/          Autonomous agent base classes
├── api/             FastAPI application and routes
├── backtesting/     Backtester engine
├── config/          Settings, events, session definitions
├── data/            Data providers, normalizer, validator
├── database/        SQLAlchemy async engine
├── evaluation/      Gatekeeper and scoring
├── evolution/       Genetic algorithm for strategies
├── features/        Technical indicators
├── memory/          Trade journal (SQL-based)
├── models/          Shared data schemas
├── monitoring/      Dashboards and alerting
├── research/        Hypothesis generation
├── risk/            Position sizing and risk management
├── strategies/      Strategy genome, base strategy, mutator
├── tests/           Test suite
│   ├── unit/        Unit tests
│   └── integration/ Integration tests
├── tools/           Utility scripts
├── Dockerfile       Container configuration
├── docker-compose.yml
├── requirements.txt
└── README.md
```

### Running Tests

```bash
# All tests
pytest tests/ -v

# Unit tests only
pytest tests/unit/ -v

# With coverage
pytest tests/ --cov=trading_ai --cov-report=term-missing

# Specific test file
pytest tests/unit/test_genome.py -v
```

### Adding a New Strategy Filter

1. Add filter type to `StrategyMutator` in `strategies/genome/mutator.py`
2. Implement filter logic in the backtester
3. Add unit tests in `tests/unit/`
4. Add integration test in `tests/integration/`

### Database Migrations

```bash
# Generate migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

### Sample Data

Generate realistic synthetic data for all supported symbols:

```bash
python tools/generate_sample_data.py
```

This creates CSV files in `data/{SYMBOL}/{SYMBOL}_{TIMEFRAME}.csv` with 2 years of M1, M5, M15, H1, H4, D1 data for EURUSD, GBPUSD, USDJPY, GBPJPY, and XAUUSD.
