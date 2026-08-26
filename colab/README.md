# Google Colab Integration

Run the **Deriv API**, **backtesting**, **ML training**, and **technical analysis** components of this project in Google Colab.

## Quick Start

1. **Open the notebook in Colab:**
   - Go to [Google Colab](https://colab.research.google.com)
   - Click **File → Upload notebook**
   - Select `colab/MT5_EA_Colab.ipynb`

   Or use the direct link:
   ```
   https://colab.research.google.com/github/jengamate255/Superbot-/blob/main/colab/MT5_EA_Colab.ipynb
   ```

2. **Run cells in order** — the notebook handles:
   - Installing dependencies
   - Cloning the repository
   - Mounting Google Drive (optional)
   - Connecting to the Deriv WebSocket API

## What Works in Colab

| Feature | Status | Notes |
|---------|--------|-------|
| **Deriv API** | ✅ | WebSocket connection to synthetic indices, Boom/Crash, forex |
| **Technical Analysis** | ✅ | EMA, ATR, RSI, MACD, Bollinger, Stochastic, ADX |
| **Boom/Crash Spike Detection** | ✅ | Full strategy engine |
| **Backtesting** | ✅ | Boom/Crash, Gold/NDX, walk-forward |
| **ML Training** | ✅ | LightGBM, XGBoost, Random Forest, PyTorch |
| **Monte Carlo Simulation** | ✅ | Risk analysis |
| **Market Intelligence** | ✅ | Health scores, trend analysis |
| **Research Reports** | ✅ | Automated market summaries |
| **MT5 Live Trading** | ❌ | Requires Windows + MT5 terminal |

## Configuration

Set these environment variables in Colab (via `os.environ` or Colab Secrets):

| Variable | Default | Description |
|----------|---------|-------------|
| `MT5_REPO` | `https://github.com/jengamate255/Superbot-.git` | Repository URL |
| `MT5_REPO_DIR` | `/content/mt5-ea` | Clone directory |
| `MT5_MOUNT_DRIVE` | `1` | Mount Google Drive (`0` to skip) |
| `DERIV_APP_ID` | `1089` | Deriv app ID (shared default) |
| `DERIV_API_TOKEN` | `` | Your Deriv API token (optional) |
| `DERIV_WS_URL` | `wss://ws.derivws.com/websockets/v3` | Deriv WebSocket URL |
| `MT5_SYMBOL` | `R_100` | Default symbol for analysis |
| `MT5_TIMEFRAME` | `M1` | Default timeframe |
| `MT5_BACKTEST_SYMBOL` | `BOOM1000` | Symbol for backtesting |
| `MT5_BACKTEST_BARS` | `1000` | Bars for backtesting |
| `MT5_ML_SYMBOL` | `R_100` | Symbol for ML training |
| `MT5_ML_BARS` | `2000` | Bars for ML training |

## Syncing with Local Machine

### Option A: Google Drive
1. Mount Drive in the notebook (cell 3)
2. Results are saved to `MyDrive/mt5_ea/`
3. Access from Windows via Google Drive desktop app

### Option B: GitHub
1. Push changes from Colab to GitHub
2. Pull on Windows machine

### Option C: Direct Download
Use cell 16 to download files directly to your local machine.

## Important Notes

- **MT5 components** (live trading, account management) **cannot** run in Colab because the MetaTrader 5 Python package requires a locally running MT5 terminal via named pipes.
- The **Deriv API** works perfectly in Colab and provides access to synthetic indices, Boom/Crash, and other markets.
- The **shared app_id (1089)** only provides access to synthetic indices. For forex/commodities, register your own app_id at [Deriv API](https://api.deriv.com/).
- All analysis and backtesting code is pure Python and runs identically in Colab and on your Windows machine.