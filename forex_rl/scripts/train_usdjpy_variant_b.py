"""USDJPY Variant B only: spread=1.0, sl_atr=1.2, tp_atr=2.5, inaction=0.05"""
import sys
import time
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

from stable_baselines3.common.callbacks import BaseCallback
from forex_rl.src.market.indicators import FeatureEngine
from forex_rl.src.market.structure import MarketStructure
from forex_rl.src.market.supply_demand import SupplyDemandDetector
from forex_rl.src.market.regime import RegimeDetector
from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.policy import AgentConfig
from forex_rl.src.backtest.engine import BacktestEngine, BacktestConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("variant_b")


class StepLogCallback(BaseCallback):
    def __init__(self, total, interval=10000):
        super().__init__()
        self.total = total
        self.interval = interval
        self.t0 = time.time()

    def _on_step(self):
        if self.num_timesteps % self.interval == 0:
            logger.info("  Step %d/%d (%.0fs)", self.num_timesteps, self.total, time.time() - self.t0)
        return True


def select_top_features(features, feature_names, top_n=20):
    variances = np.nan_to_num(np.nanvar(features, axis=0), nan=0.0)
    top_idx = np.sort(np.argsort(variances)[-top_n:])
    return features[:, top_idx], [feature_names[i] for i in top_idx]


def prepare_features_fast(symbol, tf="H1", n_bars=5000):
    df = pd.read_parquet(f"forex_rl/data/raw/{symbol}_{tf}.parquet")
    if len(df) > n_bars:
        df = df.iloc[-n_bars:].copy()
    engine = FeatureEngine()
    df = engine.compute_all(df)
    ms = MarketStructure()
    s = ms.compute_structure_features(df)
    for c in s.columns:
        df[c] = s[c].values
    sd = SupplyDemandDetector()
    sf = sd.compute_sd_features(df)
    for c in sf.columns:
        df[c] = sf[c].values
    rd = RegimeDetector()
    rf = rd.compute_regime_features(df)
    for c in rf.columns:
        df[c] = rf[c].values
    h4_path = Path(f"forex_rl/data/raw/{symbol}_H4.parquet")
    if h4_path.exists():
        h4 = pd.read_parquet(h4_path)
        he = FeatureEngine()
        hf = he.compute_all(h4)
        hf.index = hf.index.tz_localize(None) if hf.index.tz else hf.index
        hs = hf[["ema_20", "ema_50", "rsi_14", "atr_14", "macd_histogram"]].copy()
        hs.columns = ["h4_ema20", "h4_ema50", "h4_rsi14", "h4_atr14", "h4_macd_hist"]
        ha = hs.reindex(df.index, method="ffill").fillna(0)
        for c in ha.columns:
            df[c] = ha[c].values
    df = df.reset_index(drop=True)
    exclude = {"time", "open", "high", "low", "close", "volume"}
    fcols = [c for c in df.columns if c not in exclude]
    features = np.nan_to_num(df[fcols].values.astype(np.float32), nan=0.0)
    if features.shape[1] > 20:
        features, fcols = select_top_features(features, fcols, 20)
    return df, features, fcols


def create_synth_env(spread, sl, tp, inact):
    np.random.seed(42)
    nb = 2000
    t2 = np.arange(nb)
    trend = np.concatenate([np.linspace(1.1, 1.12, nb // 2), np.linspace(1.12, 1.1, nb // 2)])
    cl = trend + np.random.normal(0, 0.0003, nb)
    hi = cl + np.abs(np.random.normal(0, 0.0002, nb))
    lo = cl - np.abs(np.random.normal(0, 0.0002, nb))
    op = cl + np.random.normal(0, 0.0001, nb)
    vol = np.random.randint(100, 1000, nb).astype(float)
    idx = pd.date_range("2024-01-01", periods=nb, freq="h")
    sdf = pd.DataFrame({"time": idx, "open": op, "high": hi, "low": lo, "close": cl, "volume": vol}, index=idx)
    se = FeatureEngine()
    sdf = se.compute_all(sdf)
    sm = MarketStructure()
    ss = sm.compute_structure_features(sdf)
    for c in ss.columns:
        sdf[c] = ss[c].values
    ssd = SupplyDemandDetector()
    ssf = ssd.compute_sd_features(sdf)
    for c in ssf.columns:
        sdf[c] = ssf[c].values
    srd = RegimeDetector()
    srf = srd.compute_regime_features(sdf)
    for c in srf.columns:
        sdf[c] = srf[c].values
    sdf = sdf.reset_index(drop=True)
    exc = {"time", "open", "high", "low", "close", "volume"}
    fc = [c for c in sdf.columns if c not in exc]
    feats = np.nan_to_num(sdf[fc].values.astype(np.float32), nan=0.0)
    feats, fc = select_top_features(feats, fc, 20)
    cfg = EnvConfig(lookback=64, spread_pips=spread, sl_atr_multiplier=sl, tp_atr_multiplier=tp,
                    inaction_penalty=inact, reward_type="simple", action_mode="discrete",
                    max_episode_steps=500, normalize_obs=True)
    return ForexTradingEnv(sdf, cfg, features=feats)


def main():
    t0 = time.time()
    symbol = "USDJPY"
    spread, sl, tp, inact = 1.0, 1.2, 2.5, 0.05

    logger.info("=" * 60)
    logger.info("VARIANT B: spread=%.1f sl=%.1f tp=%.1f inaction=%.2f", spread, sl, tp, inact)
    logger.info("=" * 60)

    df, features, fnames = prepare_features_fast(symbol)
    n = len(features)
    te = int(n * 0.70)
    ve = int(n * 0.85)
    tf, vf, testf = features[:te], features[te:ve], features[ve:]
    tdf = df.iloc[:te].reset_index(drop=True)
    vdf = df.iloc[te:ve].reset_index(drop=True)
    testdf = df.iloc[ve:].reset_index(drop=True)
    logger.info("Split: train=%d val=%d test=%d", len(tf), len(vf), len(testf))

    env_cfg = EnvConfig(lookback=64, spread_pips=spread, sl_atr_multiplier=sl, tp_atr_multiplier=tp,
                        inaction_penalty=inact, reward_type="simple", action_mode="discrete",
                        max_episode_steps=min(500, te - 64), normalize_obs=True)
    train_env = ForexTradingEnv(tdf, env_cfg, features=tf)
    val_env = ForexTradingEnv(vdf, env_cfg, features=vf)
    test_env = ForexTradingEnv(testdf, env_cfg, features=testf)

    logger.info("Phase 1: Synthetic warmup (5k steps)...")
    synth_env = create_synth_env(spread, sl, tp, inact)
    wa = PPOAgent(AgentConfig(algorithm="PPO", learning_rate=3e-4, n_steps=512, batch_size=64,
                              n_epochs=4, hidden_sizes=(64, 64), ent_coef=0.1, verbose=0, seed=42))
    wa.train(synth_env, total_timesteps=5000)
    logger.info("Warmup done.")

    logger.info("Phase 2: Fine-tuning on real data (100k steps)...")
    agent = PPOAgent(AgentConfig(algorithm="PPO", learning_rate=3e-4, n_steps=512, batch_size=64,
                                 n_epochs=4, hidden_sizes=(64, 64), ent_coef=0.15, gamma=0.995, verbose=0, seed=42))
    agent.model = agent._build_model(train_env)
    agent.model.policy.load_state_dict(wa.model.policy.state_dict(), strict=False)
    cb = StepLogCallback(100000)
    agent.model.learn(total_timesteps=100000, callback=cb, progress_bar=False)
    logger.info("Fine-tune done: %.0fs", time.time() - t0)

    logger.info("Evaluating on val set...")
    vr = agent.evaluate(val_env, n_episodes=10)
    logger.info("Val: reward=%.4f+-%.4f, sharpe=%.2f, dd=%.2f%%", vr.mean_reward, vr.std_reward, vr.mean_sharpe, vr.mean_max_dd)

    logger.info("Backtesting on test set...")
    bt = BacktestEngine(BacktestConfig())
    bt_result = bt.run(agent, test_env)
    m = bt_result.metrics

    logger.info("--- VARIANT B TEST RESULTS ---")
    trades = m.get("num_trades", m.get("total_trades", 0))
    net_pnl = m.get("net_profit", 0)
    sharpe = m.get("sharpe_ratio", 0)
    md = m.get("max_drawdown", 0)
    pf = m.get("profit_factor", 0)
    wr = m.get("win_rate", 0)
    logger.info("  Trades: %d", trades)
    logger.info("  Net P&L: $%.2f", net_pnl)
    logger.info("  Sharpe: %.3f", sharpe)
    logger.info("  Max DD: %.2f%%", md * 100 if md < 1 else md)
    logger.info("  PF: %.3f", pf)
    logger.info("  Win Rate: %.1f%%", wr * 100 if wr <= 1 else wr)

    print(f"\n=== VARIANT B RESULTS ===")
    print(f"Trades: {trades}")
    print(f"Net P&L: ${net_pnl:.2f}")
    print(f"Sharpe: {sharpe:.3f}")
    print(f"MaxDD: {md * 100:.2f}%" if md < 1 else f"MaxDD: {md:.2f}%")
    print(f"PF: {pf:.3f}")
    print(f"WR: {wr * 100:.1f}%" if wr <= 1 else f"WR: {wr:.1f}%")
    print(f"Elapsed: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
