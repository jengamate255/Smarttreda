"""Optuna for XAUUSD - 10 trials, 5k steps each (fastest)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import json, time, numpy as np
from forex_rl.scripts.train_v2 import prepare_features_fast
from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.policy import AgentConfig
from forex_rl.src.backtest.engine import BacktestEngine, BacktestConfig

def safe_pf(m):
    pf = m.get("profit_factor", 0)
    if pf == float("inf") or pf > 100:
        return pf if pf != float("inf") else 999.0
    return float(pf)

def fmt_pf(pf):
    if pf == float("inf") or pf >= 999:
        return "inf"
    return f"{pf:.3f}"

t_start = time.time()

df, features, feature_names = prepare_features_fast("XAUUSD", n_bars=5000, top_n_features=20)
print(f"[DATA] Bars={len(df)} Features={features.shape[1]}")

import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

n = len(features)
train_feat = features[0:3500]
val_feat = features[3500:4250]
train_df = df.iloc[0:3500].reset_index(drop=True)
val_df = df.iloc[3500:4250].reset_index(drop=True)
print(f"[DATA] Train 0:3500 ({len(train_feat)}) Val 3500:4250 ({len(val_feat)})")

bt_config = BacktestConfig()

def objective(trial):
    ent_coef = trial.suggest_float("ent_coef", 0.08, 0.20)
    lr = trial.suggest_float("learning_rate", 1e-4, 5e-4, log=True)
    sl_atr = trial.suggest_float("sl_atr", 1.2, 2.5)
    inaction_penalty = trial.suggest_float("inaction_penalty", 0.03, 0.08)

    env_cfg_train = EnvConfig(
        lookback=64, action_mode="discrete", reward_type="simple",
        max_episode_steps=min(500, len(train_feat) - 64),
        normalize_obs=True, inaction_penalty=inaction_penalty,
        sl_atr_multiplier=sl_atr,
    )
    env_cfg_val = EnvConfig(
        lookback=64, action_mode="discrete", reward_type="simple",
        max_episode_steps=min(500, len(val_feat) - 64),
        normalize_obs=True, inaction_penalty=inaction_penalty,
        sl_atr_multiplier=sl_atr,
    )
    train_env = ForexTradingEnv(train_df, env_cfg_train, features=train_feat)
    val_env = ForexTradingEnv(val_df, env_cfg_val, features=val_feat)

    agent_cfg = AgentConfig(
        algorithm="PPO", learning_rate=lr, n_steps=512, batch_size=64,
        n_epochs=4, hidden_sizes=(64, 64), ent_coef=ent_coef,
        gamma=0.995, verbose=0, seed=42 + trial.number,
    )
    agent = PPOAgent(agent_cfg)
    t0 = time.time()
    agent.train(train_env, total_timesteps=5000)
    train_time = time.time() - t0

    t1 = time.time()
    bt_engine = BacktestEngine(bt_config)
    bt_result = bt_engine.run(agent, val_env)
    bt_time = time.time() - t1
    m = bt_result.metrics
    sharpe = float(m.get("sharpe_ratio", 0))
    maxdd = float(m.get("max_drawdown", 0))
    obj = sharpe - 0.5 * maxdd
    trades = int(m.get("num_trades", 0))
    pf = safe_pf(m)
    wr = float(m.get("win_rate", 0)) * 100

    print(f"  Trial {trial.number:2d}: ent={ent_coef:.3f} lr={lr:.1e} sl_atr={sl_atr:.2f} inact={inaction_penalty:.3f} "
          f"=> Sharpe={sharpe:.3f} MaxDD={maxdd*100:.2f}% Obj={obj:.3f} Trades={trades} PF={fmt_pf(pf)} WR={wr:.1f}% "
          f"[train={train_time:.0f}s bt={bt_time:.0f}s]")

    trial.set_user_attr("sharpe", sharpe)
    trial.set_user_attr("maxdd", maxdd)
    trial.set_user_attr("trades", trades)
    trial.set_user_attr("pf", pf)
    trial.set_user_attr("win_rate", wr)
    trial.set_user_attr("net_profit", float(m.get("net_profit", 0)))

    del train_env, val_env, agent
    return obj

study = optuna.create_study(direction="maximize")
study.optimize(objective, n_trials=10)

print("\n" + "-" * 70)
print("OPTUNA RESULTS (10 trials, 5k steps)")
print("-" * 70)
trials_sorted = sorted(study.trials, key=lambda t: t.value if t.value is not None else -999, reverse=True)
print(f"{'Trial':<6} {'Obj':<8} {'Sharpe':<8} {'MaxDD%':<8} {'PF':<8} {'WR%':<8} {'Trades':<8} {'ent':<6} {'lr':<10} {'sl_atr':<7} {'inact':<6}")
print("-" * 95)
for t in trials_sorted:
    ent = t.params.get("ent_coef", 0)
    lr = t.params.get("learning_rate", 0)
    sl = t.params.get("sl_atr", 0)
    ina = t.params.get("inaction_penalty", 0)
    sharpe = t.user_attrs.get("sharpe", 0)
    maxdd = t.user_attrs.get("maxdd", 0) * 100
    pf_s = fmt_pf(t.user_attrs.get("pf", 0))
    wr = t.user_attrs.get("win_rate", 0)
    tr = t.user_attrs.get("trades", 0)
    val = t.value if t.value else 0
    print(f"{t.number:<6} {val:<8.3f} {sharpe:<8.3f} {maxdd:<8.2f} {pf_s:<8} {wr:<8.1f} {tr:<8} {ent:<6.3f} {lr:<10.1e} {sl:<7.2f} {ina:<6.3f}")

best = study.best_trial
print("-" * 95)
print(f"BEST TRIAL #{best.number}: objective={best.value:.4f}  Sharpe={best.user_attrs.get('sharpe', 0):.3f}  MaxDD={best.user_attrs.get('maxdd', 0)*100:.2f}%")
print(f"  Params: ent_coef={best.params['ent_coef']:.4f}  lr={best.params['learning_rate']:.2e}  sl_atr={best.params['sl_atr']:.3f}  inaction_penalty={best.params['inaction_penalty']:.4f}")
print(f"  Trades={best.user_attrs.get('trades')} PF={fmt_pf(best.user_attrs.get('pf', 0))} WR={best.user_attrs.get('win_rate'):.1f}% Net=${best.user_attrs.get('net_profit'):.2f}")

best_params = {
    "trial": best.number,
    "objective": float(best.value),
    "sharpe": float(best.user_attrs.get("sharpe", 0)),
    "max_drawdown": float(best.user_attrs.get("maxdd", 0)),
    "max_drawdown_pct": float(best.user_attrs.get("maxdd", 0) * 100),
    "profit_factor": float(best.user_attrs.get("pf", 0)),
    "win_rate": float(best.user_attrs.get("win_rate", 0)),
    "win_rate_frac": float(best.user_attrs.get("win_rate", 0) / 100),
    "trades": int(best.user_attrs.get("trades", 0)),
    "net_profit": float(best.user_attrs.get("net_profit", 0)),
    "params": best.params,
    "all_trials": [
        {
            "number": t.number,
            "value": float(t.value) if t.value is not None else None,
            "params": t.params,
            "sharpe": float(t.user_attrs.get("sharpe", 0)),
            "maxdd": float(t.user_attrs.get("maxdd", 0)),
            "pf": float(t.user_attrs.get("pf", 0)),
            "win_rate": float(t.user_attrs.get("win_rate", 0)),
            "trades": int(t.user_attrs.get("trades", 0)),
        } for t in study.trials
    ],
}

out_path = Path("forex_rl/models/checkpoints/xauusd_optuna_best.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w") as f:
    json.dump(best_params, f, indent=2, default=str)
print(f"\nSaved best params to {out_path}")
print(f"Elapsed: {time.time()-t_start:.0f}s ({(time.time()-t_start)/60:.1f} min)")
