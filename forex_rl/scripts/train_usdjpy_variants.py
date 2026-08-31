"""USDJPY variant comparison: two EnvConfig variants using train_v2 pipeline."""
import sys
import time
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

from forex_rl.src.data.loader import DataLoader
from forex_rl.src.data.cleaner import DataCleaner
from forex_rl.src.market.indicators import FeatureEngine
from forex_rl.src.market.structure import MarketStructure
from forex_rl.src.market.supply_demand import SupplyDemandDetector
from forex_rl.src.market.regime import RegimeDetector
from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig
from stable_baselines3.common.callbacks import BaseCallback
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.policy import AgentConfig
from forex_rl.src.backtest.engine import BacktestEngine, BacktestConfig
from forex_rl.src.backtest.metrics import BacktestMetrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("usdjpy_variants")

# Reuse logic from train_v2.py
def select_top_features(features: np.ndarray, feature_names: list, top_n: int = 20) -> tuple:
    variances = np.nanvar(features, axis=0)
    variances = np.nan_to_num(variances, nan=0.0)
    top_indices = np.argsort(variances)[-top_n:]
    top_indices = np.sort(top_indices)
    selected_names = [feature_names[i] for i in top_indices]
    selected_features = features[:, top_indices]
    return selected_features, selected_names


def prepare_features_fast(symbol: str, tf: str = "H1", n_bars: int = 5000, top_n_features: int = 20) -> tuple:
    data_path = Path(f"forex_rl/data/raw/{symbol}_{tf}.parquet")
    df = pd.read_parquet(data_path)
    if len(df) > n_bars:
        df = df.iloc[-n_bars:].copy()
    logger.info("Data: %d bars", len(df))
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
        logger.info("Added 5 H4 MTF features")
    df = df.reset_index(drop=True)
    exclude = {"time", "open", "high", "low", "close", "volume"}
    feature_cols = [c for c in df.columns if c not in exclude]
    features = df[feature_cols].values.astype(np.float32)
    features = np.nan_to_num(features, nan=0.0)
    logger.info("Raw features: %d bars x %d columns", *features.shape)
    if features.shape[1] > top_n_features:
        features, feature_cols = select_top_features(features, feature_cols, top_n_features)
        logger.info("Selected top %d features: %s", top_n_features, feature_cols)
    return df, features, feature_cols


def create_synthetic_trending_env(n_bars: int = 2000, env_config: EnvConfig = None) -> ForexTradingEnv:
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
    config = env_config or EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type="simple",
        max_episode_steps=500,
        normalize_obs=True,
    )
    return ForexTradingEnv(df, config, features=features)


class StepLogCallback(BaseCallback):
    """Log progress every N steps."""
    def __init__(self, log_interval: int = 10000):
        super().__init__()
        self.log_interval = log_interval
        self.t0 = time.time()

    def _on_step(self) -> bool:
        if self.num_timesteps % self.log_interval == 0:
            elapsed = time.time() - self.t0
            logger.info("  Step %d/%d (%.0fs elapsed)", self.num_timesteps, self.n_steps_total, elapsed)
        return True

    def set_total(self, total: int):
        self.n_steps_total = total


def run_variant(symbol: str, variant_name: str, spread_pips: float, sl_atr: float, tp_atr: float, inaction_penalty: float,
                n_bars: int = 5000, n_steps: int = 100000, seed: int = 42) -> dict:
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("VARIANT %s: spread=%.1f sl=%.1f tp=%.1f inaction=%.2f", variant_name, spread_pips, sl_atr, tp_atr, inaction_penalty)
    logger.info("=" * 60)

    df, features, feature_names = prepare_features_fast(symbol, n_bars=n_bars, top_n_features=20)

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

    env_config = EnvConfig(
        lookback=64,
        spread_pips=spread_pips,
        sl_atr_multiplier=sl_atr,
        tp_atr_multiplier=tp_atr,
        inaction_penalty=inaction_penalty,
        reward_type="simple",
        action_mode="discrete",
        max_episode_steps=min(500, train_end - 64),
        normalize_obs=True,
    )
    logger.info("EnvConfig: %s", env_config)

    train_env = ForexTradingEnv(train_df, env_config, features=train_feat)
    val_env = ForexTradingEnv(val_df, env_config, features=val_feat)
    test_env = ForexTradingEnv(test_df, env_config, features=test_feat)
    logger.info("Obs shape: %s", train_env.observation_space.shape)

    # Phase 1: synthetic warmup 5k steps with ent_coef 0.1
    logger.info("Phase 1: Synthetic warmup (5k steps)...")
    synth_config = EnvConfig(
        lookback=64,
        spread_pips=spread_pips,
        sl_atr_multiplier=sl_atr,
        tp_atr_multiplier=tp_atr,
        inaction_penalty=inaction_penalty,
        reward_type="simple",
        action_mode="discrete",
        max_episode_steps=500,
        normalize_obs=True,
    )
    synth_env = create_synthetic_trending_env(2000, env_config=synth_config)
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
    logger.info("Warmup done.")

    # Phase 2: fine-tune on real data with ent_coef 0.15, lr 3e-4, n_steps 512, batch 64, hidden 64,64
    logger.info("Phase 2: Fine-tuning on real data (%d steps)...", n_steps)
    agent_config = AgentConfig(
        algorithm="PPO",
        learning_rate=3e-4,
        n_steps=512,
        batch_size=64,
        n_epochs=4,
        hidden_sizes=(64, 64),
        ent_coef=0.15,
        gamma=0.995,
        verbose=0,
        seed=seed,
    )
    agent = PPOAgent(agent_config)
    agent.model = agent._build_model(train_env)
    try:
        agent.model.policy.load_state_dict(warmup_agent.model.policy.state_dict(), strict=False)
        logger.info("Copied warmup weights.")
    except Exception as e:
        logger.warning("Could not copy warmup weights: %s", e)

    step_cb = StepLogCallback(log_interval=10000)
    step_cb.set_total(n_steps)
    agent.model.learn(total_timesteps=n_steps, callback=step_cb, progress_bar=False)
    logger.info("Fine-tune done: %.1fs", time.time() - t0)

    # Evaluate (use train_env directly to avoid redundant agent.train internal eval)
    logger.info("Evaluating on val set...")
    val_result = agent.evaluate(val_env, n_episodes=10)
    logger.info("Val: reward=%.4f±%.4f, sharpe=%.2f, dd=%.2f%%", val_result.mean_reward, val_result.std_reward, val_result.mean_sharpe, val_result.mean_max_dd)

    logger.info("Evaluating on val set...")
    val_result = agent.evaluate(val_env, n_episodes=10)
    logger.info("Val: reward=%.4f±%.4f, sharpe=%.2f, dd=%.2f%%", val_result.mean_reward, val_result.std_reward, val_result.mean_sharpe, val_result.mean_max_dd)

    logger.info("Backtesting on test set...")
    bt_config = BacktestConfig()
    bt_engine = BacktestEngine(bt_config)
    bt_result = bt_engine.run(agent, test_env)
    m = bt_result.metrics

    logger.info("--- TEST RESULTS %s ---", variant_name)
    logger.info("  Trades: %d", m.get("num_trades", m.get("total_trades", 0)))
    logger.info("  Net P&L: $%.2f", m.get("net_profit", 0))
    logger.info("  Sharpe: %.3f", m.get("sharpe_ratio", 0))
    logger.info("  Max DD: %.2f%%", m.get("max_drawdown", 0) * 100 if m.get("max_drawdown", 0) < 1 else m.get("max_drawdown", 0))
    # Normalize MaxDD display: metrics returns 0-1 fraction
    logger.info("  PF: %.3f", m.get("profit_factor", 0))
    logger.info("  Win Rate: %.1f%%", m.get("win_rate", 0) * 100 if m.get("win_rate", 0) <= 1 else m.get("win_rate", 0))

    elapsed = time.time() - t0

    # Normalize for return dict
    trades = int(m.get("num_trades", m.get("total_trades", 0)))
    net_pnl = float(m.get("net_profit", 0))
    sharpe = float(m.get("sharpe_ratio", 0))
    pf = float(m.get("profit_factor", 0))
    max_dd = float(m.get("max_drawdown", 0))
    # Convert fraction to percent for consistent display but keep raw
    wr = float(m.get("win_rate", 0))
    if wr <= 1:
        wr_percent = wr * 100
    else:
        wr_percent = wr

    # Save model
    out = Path("forex_rl/models/checkpoints")
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / f"ppo_usdjpy_h1_{variant_name.lower()}"
    try:
        agent.save(str(model_path))
    except Exception as e:
        logger.warning("Save failed: %s", e)

    result = {
        "variant": variant_name,
        "spread_pips": spread_pips,
        "sl_atr": sl_atr,
        "tp_atr": tp_atr,
        "inaction_penalty": inaction_penalty,
        "trades": trades,
        "net_pnl": net_pnl,
        "sharpe": sharpe,
        "pf": pf,
        "max_dd": max_dd,
        "win_rate": wr_percent,
        "elapsed": elapsed,
        "metrics": m,
    }
    return result


def main():
    symbol = "USDJPY"
    variants = [
        {"name": "A", "spread_pips": 1.0, "sl_atr": 1.5, "tp_atr": 3.0, "inaction_penalty": 0.05},
        {"name": "B", "spread_pips": 1.0, "sl_atr": 1.2, "tp_atr": 2.5, "inaction_penalty": 0.05},
    ]

    results = []
    for v in variants:
        r = run_variant(symbol, v["name"], v["spread_pips"], v["sl_atr"], v["tp_atr"], v["inaction_penalty"],
                        n_bars=5000, n_steps=100000, seed=42)
        results.append(r)
        print(f"\n{'='*60}")
        print(f"Variant {r['variant']}: spread={r['spread_pips']} sl={r['sl_atr']} tp={r['tp_atr']}")
        print(f"  Trades: {r['trades']}")
        print(f"  Net P&L: ${r['net_pnl']:.2f}")
        print(f"  Sharpe: {r['sharpe']:.3f}")
        print(f"  PF: {r['pf']:.3f}" if r['pf'] != float('inf') else "  PF: inf")
        print(f"  MaxDD: {r['max_dd']*100:.2f}%" if r['max_dd'] < 1 else f"  MaxDD: {r['max_dd']:.2f}%")
        print(f"  WR: {r['win_rate']:.1f}%")
        print(f"{'='*60}\n")

    print("\n" + "="*60)
    print("SUMMARY - USDJPY variants")
    print("="*60)
    for r in results:
        pf_str = f"{r['pf']:.3f}" if r['pf'] != float('inf') else "inf"
        print(f"Variant {r['variant']} (sl={r['sl_atr']} tp={r['tp_atr']} spread={r['spread_pips']}): Trades={r['trades']}, Net=${r['net_pnl']:.2f}, Sharpe={r['sharpe']:.3f}, PF={pf_str}, MaxDD={r['max_dd']*100:.2f}%, WR={r['win_rate']:.1f}%")

    # Recommend best variant: prioritize PF > 2.0, then net PnL, then Sharpe
    def score(r):
        pf = r['pf'] if r['pf'] != float('inf') else 10
        # prefer PF >2, higher PF wins, then net_pnl, then sharpe
        return (pf, r['net_pnl'], r['sharpe'])

    best = max(results, key=score)
    # Also check if any has PF>2
    pf_ok = [r for r in results if r['pf'] > 2.0 and r['pf'] != float('inf')]
    if pf_ok:
        best_pf_ok = max(pf_ok, key=score)
        best = best_pf_ok

    print(f"\nRECOMMENDATION: Variant {best['variant']} is best (PF={best['pf']:.3f}, Net=${best['net_pnl']:.2f}, Sharpe={best['sharpe']:.3f}, WR={best['win_rate']:.1f}%)")
    if best['pf'] > 2.0:
        print(f"  -> Variant {best['variant']} achieves PF > 2.0 target!")
    else:
        print(f"  -> Neither variant achieves PF > 2.0; Variant {best['variant']} is closest.")
    print("="*60)

    # Save json
    out_path = Path("forex_rl/reports/usdjpy_variants.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        # make serializable
        ser = []
        for r in results:
            d = {k: v for k, v in r.items() if k != "metrics"}
            # handle inf
            if d['pf'] == float('inf'):
                d['pf'] = "inf"
            ser.append(d)
        json.dump({"results": ser, "best": best['variant']}, f, indent=2, default=str)
    print(f"Saved report to {out_path}")


if __name__ == "__main__":
    main()
