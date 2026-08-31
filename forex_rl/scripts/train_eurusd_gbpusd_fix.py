"""Fix EURUSD/GBPUSD inaction collapse: retrain with inaction_penalty=0.05 and 20k steps.

Copies train_v2.py logic inline but patches EnvConfig to use inaction_penalty=0.05
and fine-tunes for 20k steps (not 100k) to prevent convergence to inaction.
"""
import sys
import os
import time
import json
import logging
from pathlib import Path

# Ensure project root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

from forex_rl.src.data.loader import DataLoader  # noqa: F401
from forex_rl.src.data.cleaner import DataCleaner  # noqa: F401
from forex_rl.src.market.indicators import FeatureEngine
from forex_rl.src.market.structure import MarketStructure
from forex_rl.src.market.supply_demand import SupplyDemandDetector
from forex_rl.src.market.regime import RegimeDetector
from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.policy import AgentConfig
from forex_rl.src.backtest.engine import BacktestEngine, BacktestConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fix_train")

# Reuse helpers from train_v2 but copied inline to allow patching
def select_top_features(features: np.ndarray, feature_names: list, top_n: int = 20) -> tuple:
    variances = np.nanvar(features, axis=0)
    variances = np.nan_to_num(variances, nan=0.0)
    top_indices = np.argsort(variances)[-top_n:]
    top_indices = np.sort(top_indices)
    selected_names = [feature_names[i] for i in top_indices]
    selected_features = features[:, top_indices]
    return selected_features, selected_names


def prepare_features_fast(symbol: str, tf: str = "H1", n_bars: int = 5000,
                           top_n_features: int = 20) -> tuple:
    data_path = Path(f"forex_rl/data/raw/{symbol}_{tf}.parquet")
    df = pd.read_parquet(data_path)
    if len(df) > n_bars:
        df = df.iloc[-n_bars:].copy()
    logger.info("Data %s: %d bars", symbol, len(df))
    engine = FeatureEngine()
    df = engine.compute_all(df)
    ms = MarketStructure()
    struct = ms.compute_structure_features(df)
    for col in struct.columns:
        df[col] = struct[col].values
    sd = SupplyDemandDetector()
    sd_feats = sd.compute_sd_features(df)
    for col in sd_feats.columns:
        df[col] = sd_feats[col].values
    rd = RegimeDetector()
    regime_feats = rd.compute_regime_features(df)
    for col in regime_feats.columns:
        df[col] = regime_feats[col].values

    h4_path = Path(f"forex_rl/data/raw/{symbol}_H4.parquet")
    if h4_path.exists():
        h4_df = pd.read_parquet(h4_path)
        h4_engine = FeatureEngine()
        h4_feats = h4_engine.compute_all(h4_df)
        h4_feats.index = h4_feats.index.tz_localize(None) if h4_feats.index.tz else h4_feats.index
        h4_sub = h4_feats[["ema_20", "ema_50", "rsi_14", "atr_14", "macd_histogram"]].copy()
        h4_sub.columns = ["h4_ema20", "h4_ema50", "h4_rsi14", "h4_atr14", "h4_macd_hist"]
        h4_aligned = h4_sub.reindex(df.index, method="ffill").fillna(0)
        for col in h4_aligned.columns:
            df[col] = h4_aligned[col].values
        logger.info("Added 5 H4 MTF features for %s", symbol)

    df = df.reset_index(drop=True)
    exclude = {"time", "open", "high", "low", "close", "volume"}
    feature_cols = [c for c in df.columns if c not in exclude]
    features = df[feature_cols].values.astype(np.float32)
    features = np.nan_to_num(features, nan=0.0)
    logger.info("Raw features %s: %d bars x %d columns", symbol, *features.shape)
    if features.shape[1] > top_n_features:
        features, feature_cols = select_top_features(features, feature_cols, top_n_features)
        logger.info("Selected top %d features for %s: %s", top_n_features, symbol, feature_cols)
    return df, features, feature_cols


def create_synthetic_trending_env(n_bars: int = 2000) -> ForexTradingEnv:
    np.random.seed(42)
    t = np.arange(n_bars)
    trend = np.concatenate([
        np.linspace(1.1000, 1.1200, n_bars // 2),
        np.linspace(1.1200, 1.1000, n_bars // 2),
    ])
    noise = np.random.normal(0, 0.0003, n_bars)
    close = trend + noise
    high = close + np.abs(np.random.normal(0, 0.0002, n_bars))
    low = close - np.abs(np.random.normal(0, 0.0002, n_bars))
    open_ = close + np.random.normal(0, 0.0001, n_bars)
    volume = np.random.randint(100, 1000, n_bars).astype(float)
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="h")
    df = pd.DataFrame({
        "time": idx, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }, index=idx)
    engine = FeatureEngine()
    df = engine.compute_all(df)
    ms = MarketStructure()
    struct = ms.compute_structure_features(df)
    for col in struct.columns:
        df[col] = struct[col].values
    sd = SupplyDemandDetector()
    sd_feats = sd.compute_sd_features(df)
    for col in sd_feats.columns:
        df[col] = sd_feats[col].values
    rd = RegimeDetector()
    regime_feats = rd.compute_regime_features(df)
    for col in regime_feats.columns:
        df[col] = regime_feats[col].values
    df = df.reset_index(drop=True)
    exclude = {"time", "open", "high", "low", "close", "volume"}
    feature_cols = [c for c in df.columns if c not in exclude]
    features = df[feature_cols].values.astype(np.float32)
    features = np.nan_to_num(features, nan=0.0)
    features, feature_cols = select_top_features(features, feature_cols, 20)
    config = EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type="simple",
        max_episode_steps=500,
        normalize_obs=True,
        inaction_penalty=0.05,
    )
    return ForexTradingEnv(df, config, features=features)


def train_single_symbol(symbol: str, inaction_penalty: float = 0.05, n_steps: int = 20000, seed: int = 42) -> dict:
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("FIX TRAINING %s (inaction_penalty=%.2f, n_steps=%d)", symbol, inaction_penalty, n_steps)
    logger.info("=" * 60)

    df, features, feature_names = prepare_features_fast(symbol, n_bars=5000, top_n_features=20)

    n = len(features)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)
    train_feat = features[:train_end]
    val_feat = features[train_end:val_end]
    test_feat = features[val_end:]
    train_df = df.iloc[:train_end].reset_index(drop=True)
    val_df = df.iloc[train_end:val_end].reset_index(drop=True)
    test_df = df.iloc[val_end:].reset_index(drop=True)
    logger.info("Split: train=%d val=%d test=%d", len(train_feat), len(val_feat), len(test_feat))

    # EnvConfig with inaction_penalty=0.05, reward_type=simple, lookback=64
    env_config = EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type="simple",
        max_episode_steps=min(500, train_end - 64),
        normalize_obs=True,
        inaction_penalty=inaction_penalty,
    )
    logger.info("EnvConfig: inaction_penalty=%.3f reward=%s lookback=%d max_steps=%d",
                env_config.inaction_penalty, env_config.reward_type, env_config.lookback, env_config.max_episode_steps)

    train_env = ForexTradingEnv(train_df, env_config, features=train_feat)
    val_env = ForexTradingEnv(val_df, env_config, features=val_feat)
    test_env = ForexTradingEnv(test_df, env_config, features=test_feat)
    logger.info("Obs shape: %s", train_env.observation_space.shape)

    # Phase 1: Synthetic warmup 5k steps
    logger.info("Phase 1: Synthetic warmup (5k steps)...")
    synth_env = create_synthetic_trending_env(2000)
    # Ensure synth_env also uses patched penalty (already set in create_synthetic_trending_env)
    # If inaction_penalty differs from 0.05, patch it
    if inaction_penalty != 0.05:
        synth_env.cfg.inaction_penalty = inaction_penalty

    agent_config_warmup = AgentConfig(
        algorithm="PPO",
        learning_rate=3e-4,
        n_steps=512,
        batch_size=64,
        n_epochs=4,
        hidden_sizes=(64, 64),
        ent_coef=0.1,
        verbose=0,
        seed=seed,
    )
    warmup_agent = PPOAgent(agent_config_warmup)
    warmup_agent.train(synth_env, total_timesteps=5000)
    logger.info("Warmup done. Agent explored 5k steps on synthetic data.")

    # Phase 2: Fine-tune on real data 20k steps
    logger.info("Phase 2: Fine-tuning on real data (%d steps)...", n_steps)
    agent_config = AgentConfig(
        algorithm="PPO",
        learning_rate=3e-4,
        n_steps=min(512, len(train_feat) // 4),
        batch_size=64,
        n_epochs=4,
        hidden_sizes=(64, 64),
        ent_coef=0.15,
        gamma=0.995,
        verbose=0,
        seed=seed,
    )
    logger.info("AgentConfig: ent_coef=%.2f lr=%.1e n_steps=%d batch=%d hidden=(64,64) gamma=%.3f",
                agent_config.ent_coef, agent_config.learning_rate, agent_config.n_steps, agent_config.batch_size, agent_config.gamma)

    agent = PPOAgent(agent_config)
    # Initialize with warmup weights
    agent.model = agent._build_model(train_env)
    try:
        agent.model.policy.load_state_dict(warmup_agent.model.policy.state_dict(), strict=False)
        logger.info("Loaded warmup weights into fine-tune agent.")
    except Exception as e:
        logger.warning("Failed to copy warmup weights: %s", e)

    train_result = agent.train(train_env, total_timesteps=n_steps)
    logger.info("Training done: %.1fs, reward=%.4f", train_result.training_time, train_result.mean_reward)

    # Evaluate val
    logger.info("Evaluating on val set...")
    val_result = agent.evaluate(val_env, n_episodes=10)
    logger.info("Val: reward=%.4f +/- %.4f, sharpe=%.2f, dd=%.2f%%",
                val_result.mean_reward, val_result.std_reward,
                val_result.mean_sharpe, val_result.mean_max_dd)

    # Backtest test
    logger.info("Backtesting on test set...")
    bt_config = BacktestConfig()
    bt_engine = BacktestEngine(bt_config)
    bt_result = bt_engine.run(agent, test_env)
    m = bt_result.metrics

    # Handle both key styles
    total_trades = m.get("total_trades", m.get("num_trades", m.get("n_trades", 0)))
    net_profit = m.get("net_profit", m.get("net_pnl", 0))
    sharpe = m.get("sharpe_ratio", m.get("sharpe", 0))
    pf = m.get("profit_factor", m.get("pf", 0))
    win_rate = m.get("win_rate", 0)
    max_dd = m.get("max_drawdown", m.get("max_dd", 0))

    # Convert win_rate to percent if needed (0-1 vs 0-100)
    win_rate_pct = win_rate * 100 if win_rate <= 1 else win_rate
    max_dd_pct = max_dd * 100 if max_dd <= 1 and max_dd != 0 else max_dd
    # If metrics already as percent >1, handle
    # In train_v2 they log max_dd as %, but metrics gives fraction. We'll print both.

    logger.info("--- TEST RESULTS ---")
    logger.info("  Trades: %d", total_trades)
    logger.info("  Net P&L: $%.2f", net_profit)
    logger.info("  Sharpe: %.3f", sharpe)
    logger.info("  Sortino: %.3f", m.get("sortino_ratio", 0))
    logger.info("  Max DD: %.2f%%", max_dd_pct if max_dd <= 1 else max_dd)
    logger.info("  PF: %.3f", pf)
    logger.info("  Win Rate: %.1f%%", win_rate_pct)

    # Also print to stdout explicitly for task reporter
    print(f"\n=== TEST RESULTS {symbol} ===")
    print(f"  Trades: {total_trades}")
    print(f"  Net P&L: ${net_profit:.2f}")
    print(f"  Sharpe: {sharpe:.3f}")
    print(f"  PF: {pf:.3f}" if pf != float('inf') else "  PF: inf")
    print(f"  Win Rate: {win_rate_pct:.1f}%")
    print(f"  Max DD: {max_dd_pct:.2f}%")
    print(f"  Val reward: {val_result.mean_reward:.4f} +/- {val_result.std_reward:.4f}")
    print(f"  Train reward: {train_result.mean_reward:.4f}")
    print(f"====================\n")

    # Save model
    out = Path("forex_rl/models/checkpoints")
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / f"ppo_{symbol.lower()}_h1_v2_fix"
    agent.save(str(model_path))
    logger.info("Saved model to %s.zip", model_path)

    results = {
        "symbol": symbol,
        "inaction_penalty": inaction_penalty,
        "n_steps": n_steps,
        "features": len(feature_names),
        "selected_features": feature_names[:20] if len(feature_names) > 20 else feature_names,
        "train_bars": len(train_feat),
        "reward_type": "simple",
        "ent_coef": 0.15,
        "val_reward": val_result.mean_reward,
        "val_sharpe": val_result.mean_sharpe,
        "val_max_dd": val_result.mean_max_dd,
        "test_metrics": {k: (float(v) if isinstance(v, float) and v != float('inf') else (None if v == float('inf') else v)) for k, v in m.items() if isinstance(v, (int, float))},
        "test_metrics_raw": m,
        "trades": int(total_trades),
        "elapsed": time.time() - t0,
    }
    results_path = out / f"ppo_{symbol.lower()}_h1_v2_fix_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("Results saved to %s", results_path)

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info("DONE: %s — Sharpe=%.2f, PF=%.2f, DD=%.2f%%, Trades=%d — %.0fs",
                symbol, sharpe, pf if pf != float('inf') else 999, max_dd_pct, total_trades, elapsed)
    logger.info("=" * 60)

    return results


def main():
    symbols = ["EURUSD", "GBPUSD"]
    all_results = {}
    for sym in symbols:
        try:
            r = train_single_symbol(sym, inaction_penalty=0.05, n_steps=20000, seed=42)
            all_results[sym] = r
            trades = r.get("trades", 0)
            if trades == 0:
                logger.warning("%s still 0 trades with penalty 0.05 — no auto retry in this script, but task says to try 0.10 as second attempt manually.", sym)
        except Exception as e:
            import traceback
            logger.error("ERROR training %s: %s", sym, e)
            traceback.print_exc()
            all_results[sym] = {"error": str(e)}

    print("\n" + "=" * 60)
    print("FIX TRAINING SUMMARY (penalty=0.05, steps=20k)")
    print("=" * 60)
    for sym, r in all_results.items():
        if "error" in r:
            print(f"  {sym}: ERROR - {r['error']}")
        else:
            m = r.get("test_metrics", {})
            trades = r.get("trades", m.get("total_trades", m.get("num_trades", 0)))
            pf = m.get("profit_factor")
            pf_str = f"{pf:6.3f}" if isinstance(pf, (int, float)) else "   inf"
            sharpe = m.get("sharpe_ratio", 0) or 0
            dd = m.get("max_drawdown", 0) or 0
            wr = m.get("win_rate", 0) or 0
            pnl = m.get("net_profit", 0) or 0
            print(f"  {sym}: Trades={trades:>3d}  Sharpe={sharpe:>7.3f}  PF={pf_str}  DD={dd:>6.2%}  WR={wr:>5.1%}  P&L=${pnl:>8.2f}")

    out = Path("forex_rl/models/checkpoints")
    with open(out / "fix_all_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print("\nResults saved to forex_rl/models/checkpoints/fix_all_results.json")

    # Report whether Trades > 0
    for sym in symbols:
        r = all_results.get(sym, {})
        trades = r.get("trades", 0)
        status = "PASS: Trades > 0" if trades > 0 else "FAIL: Trades == 0 (needs penalty 0.10 retry)"
        print(f"{sym}: {status} (Trades={trades})")


if __name__ == "__main__":
    main()
