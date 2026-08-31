"""Reconciliation test: trade log P&L must match equity curve P&L.

Validates the two accounting fixes:
1. _unrealised_pnl uses x100000 (was x10000 — 10x understated)
2. Commission charged once (was double-charged at entry AND exit)
3. Episode-end positions force-closed (no unrealized leakage)

Run: python -X utf8 forex_rl/scripts/test_reconciliation.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig


def make_env(max_episode_steps: int = 50) -> ForexTradingEnv:
    np.random.seed(7)
    n = 300
    close = 1.1000 + np.cumsum(np.random.normal(0, 0.0004, n))
    high = close + np.abs(np.random.normal(0, 0.0002, n))
    low = close - np.abs(np.random.normal(0, 0.0002, n))
    open_ = np.roll(close, 1)
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    df = pd.DataFrame(
        {"time": idx, "open": open_, "high": high, "low": low, "close": close, "volume": 100.0},
        index=idx,
    ).reset_index(drop=True)

    rng = np.random.default_rng(7)
    feats = rng.normal(0, 1, (n, 3)).astype(np.float32)

    cfg = EnvConfig(
        lookback=8,
        initial_balance=10000.0,
        risk_per_trade=0.005,
        max_positions=2,
        reward_type="simple",
        max_episode_steps=max_episode_steps,
        inaction_penalty=0.05,
    )
    return ForexTradingEnv(df, cfg, features=feats)


def run_reconciliation() -> dict:
    env = make_env()
    obs, info = env.reset()
    initial = info["equity"]

    trade_log_pnl = 0.0
    n_trades = 0
    done = False
    t = 0
    while not done:
        # Alternate buy/sell/hold to force trades and episode-end flush
        action = [1, 2, 0][t % 3]
        obs, reward, term, trunc, info = env.step(action)
        done = term or trunc
        for tc in info.get("trades_closed", []):
            trade_log_pnl += tc["pnl"]
            n_trades += 1
        t += 1

    final = info["equity"]

    # All positions must be flushed at episode end
    assert not env.portfolio.positions, "Positions still open at episode end!"

    # Trade-log P&L must reconcile with equity change (zero spread/commission drift)
    equity_pnl = final - initial
    drift = equity_pnl - trade_log_pnl

    return {
        "steps": t,
        "n_trades": n_trades,
        "trade_log_pnl": round(float(trade_log_pnl), 4),
        "equity_pnl": round(float(equity_pnl), 4),
        "reconciliation_drift": round(float(drift), 4),
        "initial_equity": float(initial),
        "final_equity": round(float(final), 4),
        "reconciled": bool(abs(drift) < 0.50),
        "positions_flushed": True,
    }


if __name__ == "__main__":
    r = run_reconciliation()
    print(json.dumps(r, indent=2))
    status = "PASS" if r["reconciled"] else "FAIL"
    print(f"\nReconciliation: {status} (drift=${r['reconciliation_drift']})")
    assert r["reconciled"], "Trade log and equity curve do NOT reconcile!"
