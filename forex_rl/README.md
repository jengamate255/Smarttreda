# Forex RL Trading System

Reinforcement Learning-based forex trading system with PPO/SAC agents,
sophisticated risk management, and a full backtesting pipeline.

## Architecture

```
forex_rl/
├── config.yaml              # All hyperparameters and risk limits
├── src/
│   ├── agents/              # RL agent implementations
│   │   ├── ppo_agent.py     # PPO (Stable-Baselines3)
│   │   ├── sac_agent.py     # SAC (Stable-Baselines3)
│   │   ├── policy.py        # BaseAgent ABC + config
│   │   └── factory.py       # AgentFactory
│   ├── data/                # Data loading and preprocessing
│   │   ├── loader.py        # MT5 / synthetic data loader
│   │   ├── cleaner.py       # Gap filling, outlier removal
│   │   └── splitter.py      # Chronological train/val/test split
│   ├── environment/         # Gymnasium RL environment
│   │   ├── forex_env.py     # ForexTradingEnv (47 features, discrete/continuous)
│   │   └── reward.py        # 4 reward functions (simple, risk-adj, sharpe, pareto)
│   ├── market/              # Market analysis
│   │   ├── indicators.py    # FeatureEngine (28 technical features)
│   │   ├── structure.py     # HH/HL/LH/LL, BOS, CHoCH
│   │   ├── supply_demand.py # S/D zone detection + strength scoring
│   │   └── regime.py        # 6-class regime classification
│   ├── risk/                # Risk management
│   │   ├── risk_manager.py  # Trade approval (DD, positions, R:R, cooldown)
│   │   └── position_sizing.py  # Fixed-fractional, Kelly, volatility-adjusted
│   ├── backtest/            # Backtesting
│   │   ├── engine.py        # BacktestEngine + walk-forward
│   │   ├── metrics.py       # Sharpe, Sortino, PF, max DD, etc.
│   │   └── monte_carlo.py   # Monte Carlo ruin probability
│   ├── training/            # Training pipeline
│   │   ├── train.py         # Full pipeline + baseline comparison
│   │   ├── evaluation.py    # Evaluator
│   │   ├── walk_forward.py  # Walk-forward validation
│   │   └── hyperparameter_search.py  # Optuna HPO
│   ├── execution/           # Live trading
│   │   ├── mt5_connector.py # MT5 API wrapper
│   │   ├── order_manager.py # Order lifecycle
│   │   └── live_trader.py   # Live orchestration
│   ├── dashboard/           # Monitoring UI
│   │   └── app.py           # Dash dashboard (equity, DD, positions)
│   └── utils/               # Shared utilities
│       ├── config.py        # YAML + .env config loader
│       ├── database.py      # SQLite trade journal
│       └── logging.py       # Structured logging
├── tests/                   # Test suite
│   ├── conftest.py          # Shared fixtures
│   ├── test_features.py     # Feature engineering tests
│   ├── test_risk.py         # Risk management tests
│   ├── test_reward.py       # Reward function tests
│   ├── test_environment.py  # Environment tests
│   ├── test_backtest.py     # Backtesting tests
│   └── test_pipeline.py     # Integration test
├── scripts/                 # CLI entry points
│   ├── train.py             # Training script
│   ├── backtest.py          # Backtest script
│   ├── evaluate.py          # Evaluation script
│   ├── download_data.py     # Data download
│   └── live.py              # Live trading
├── models/                  # Saved models
├── reports/                 # Generated reports
├── data/                    # Raw/processed data
├── experiments/             # Experiment tracking
└── logs/                    # Training logs
```

## Installation

```bash
# Clone the repository
cd E:\Project\Mt5 Ea
pip install -e forex_rl

# Or install dependencies directly
pip install -r forex_rl/requirements.txt

# For training
pip install stable-baselines3 torch tensorboard

# For dashboard
pip install dash plotly

# For testing
pip install pytest
```

## Quick Start

### 1. Train an agent

```bash
cd forex_rl
python scripts/train.py --symbol EURUSD --timeframe H1 --algorithm PPO --timesteps 200000
```

### 2. Backtest

```bash
python scripts/backtest.py --model models/EURUSD_H1_PPO --symbol EURUSD --timeframe H1
```

### 3. Run the dashboard

```bash
python -m forex_rl.src.dashboard.app
# Open http://127.0.0.1:8056
```

### 4. Run tests

```bash
pytest forex_rl/tests/ -v
```

## Configuration

All parameters are in `config.yaml`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `risk_per_trade` | 0.005 | 0.5% risk per trade |
| `max_account_drawdown` | 0.10 | 10% max drawdown limit |
| `max_daily_drawdown` | 0.02 | 2% daily loss limit |
| `maximum_positions` | 3 | Max concurrent positions |
| `minimum_rr` | 3.0 | Minimum reward-to-risk ratio |
| `spread_pips` | 1.5 | Simulated spread |
| `slippage_pips` | 0.5 | Simulated slippage |
| `lookback` | 64 | Observation window (bars) |
| `algorithm` | PPO | RL algorithm (PPO/SAC) |

## Module Descriptions

### Feature Engineering (47 features)
- **Price** (6): returns, log returns, candle body/wicks/range
- **Trend** (6): EMA 20/50/100/200, slope, distance from EMA50
- **Momentum** (5): RSI, MACD (line/signal/histogram), ROC
- **Volatility** (3): ATR, rolling vol, volatility percentile
- **Session** (4): Asian, London, NY, overlap binary flags
- **Time** (4): Sin/cos hour-of-day, day-of-week
- **Structure** (4): HH/HL/LH/LL encoding, BOS, CHoCH, trend strength
- **Supply/Demand** (6): Zone distances, strengths, in-zone flags
- **Regime** (9): 6 regime one-hot + strength, ADX, trend direction

### Risk Management
- Position sizing: fixed-fractional, Kelly, volatility-adjusted
- Drawdown guards: account DD, daily DD, open risk cap
- Trade validation: min R:R, spread sanity, SL/TP direction, cooldown
- Correlation guard: max correlated exposure

### Reward Functions
- `simple`: P&L proportional, clipped
- `risk_adjusted`: P&L - DD penalty - overtrading - revenge + regime bonus
- `sharpe`: Rolling Sharpe ratio based
- `pareto`: Multi-objective (profit + Sharpe - DD)

## Safety Warnings

> **This system is for educational and research purposes only.**
>
> - Never trade with money you cannot afford to lose.
> - Past performance does not guarantee future results.
> - Always paper-trade extensively before live deployment.
> - The risk manager enforces limits but cannot prevent all losses.
> - Slippage and spread estimates may differ from real market conditions.
> - Always verify risk parameters before going live.
