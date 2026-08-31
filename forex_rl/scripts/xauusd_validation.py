"""XAUUSD Walk-Forward + Optuna validation.

- Walk-forward: 3 expanding folds on 5000 bars, PPO per fold 30k steps + 5k synthetic warmup.
- Optuna: 12 trials on 3500 train / 750 val, 20k steps per trial, objective Sharpe - 0.5*MaxDD.
"""
import sys
import os
import json
import time
import logging
from pathlib import Path

# Ensure UTF-8 for Windows
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

from forex_rl.scripts.train_v2 import prepare_features_fast, create_synthetic_trending_env
from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.policy import AgentConfig
from forex_rl.src.backtest.engine import BacktestEngine, BacktestConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("xauusd_validation")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def safe_pf(m):
    pf = m.get("profit_factor", 0)
    if pf == float("inf") or pf > 100:
        return pf if pf != float("inf") else 999.0
    return float(pf)

def fmt_pf(pf):
    if pf == float("inf") or pf >= 999:
        return "inf"
    return f"{pf:.3f}"

def run_walk_forward():
    print("="*70)
    print("WALK-FORWARD VALIDATION  —  XAUUSD  —  5000 bars / 3 expanding folds")
    print("="*70)

    df, features, feature_names = prepare_features_fast("XAUUSD", n_bars=5000, top_n_features=20)
    print(f"[DATA] Bars={len(df)}  Features={features.shape[1]}  Names={feature_names}")
    n = len(features)
    assert n == 5000, f"Expected 5000 bars, got {n}"

    folds = [
        (0, 2500, 2500, 3500),
        (0, 3500, 3500, 4250),
        (0, 4250, 4250, 5000),
    ]

    results = []
    bt_config = BacktestConfig()

    for i, (tr_s, tr_e, te_s, te_e) in enumerate(folds):
        print("\n" + "-"*70)
        print(f"FOLD {i}: train [{tr_s}:{tr_e}] ({tr_e-tr_s} bars)  -> test [{te_s}:{te_e}] ({te_e-te_s} bars)")
        print("-"*70)

        train_df = df.iloc[tr_s:tr_e].reset_index(drop=True)
        train_feat = features[tr_s:tr_e]
        test_df = df.iloc[te_s:te_e].reset_index(drop=True)
        test_feat = features[te_s:te_e]

        env_config = EnvConfig(
            lookback=64,
            action_mode="discrete",
            reward_type="simple",
            max_episode_steps=min(500, len(train_feat)-64),
            normalize_obs=True,
            inaction_penalty=0.05,
        )
        train_env = ForexTradingEnv(train_df, env_config, features=train_feat)
        test_env = ForexTradingEnv(test_df, env_config, features=test_feat)

        print(f"  Env: lookback=64 reward=simple inaction_penalty=0.05 normalize=True")
        print(f"  Train env obs shape {train_env.observation_space.shape}")

        # Synthetic warmup 5k steps
        print(f"  [Warmup] synthetic trending env 5k steps...")
        synth_env = create_synthetic_trending_env(2000)
        warmup_cfg = AgentConfig(
            algorithm="PPO",
            learning_rate=3e-4,
            n_steps=512,
            batch_size=64,
            n_epochs=4,
            hidden_sizes=(64,64),
            ent_coef=0.15,
            verbose=0,
            seed=42+i,
        )
        warmup_agent = PPOAgent(warmup_cfg)
        warmup_agent.train(synth_env, total_timesteps=5000)
        print(f"  Warmup done.")

        # Real training 30k steps with specified params
        print(f"  [Train] PPO ent_coef=0.15 lr=3e-4 n_steps=512 batch=64 hidden=64,64 gamma=0.995 for 30000 steps ...")
        agent_cfg = AgentConfig(
            algorithm="PPO",
            learning_rate=3e-4,
            n_steps=512,
            batch_size=64,
            n_epochs=4,
            hidden_sizes=(64,64),
            ent_coef=0.15,
            gamma=0.995,
            verbose=0,
            seed=42+i,
        )
        agent = PPOAgent(agent_cfg)
        # Build model and copy warmup weights
        agent.model = agent._build_model(train_env)
        try:
            agent.model.policy.load_state_dict(warmup_agent.model.policy.state_dict(), strict=False)
            print(f"  Copied warmup weights.")
        except Exception as e:
            print(f"  [WARN] could not copy warmup weights: {e}")

        t0 = time.time()
        train_result = agent.train(train_env, total_timesteps=30000)
        elapsed = time.time() - t0
        print(f"  Train done: {elapsed:.1f}s  mean_reward={train_result.mean_reward:.4f}")

        # Backtest on test fold
        bt_engine = BacktestEngine(bt_config)
        bt_result = bt_engine.run(agent, test_env)
        m = bt_result.metrics

        trades = int(m.get("num_trades", m.get("total_trades", 0)))
        pf = safe_pf(m)
        sharpe = float(m.get("sharpe_ratio", 0))
        wr = float(m.get("win_rate", 0))*100
        net = float(m.get("net_profit", 0))
        maxdd = float(m.get("max_drawdown", 0))*100
        sortino = float(m.get("sortino_ratio", 0))
        total_ret = float(m.get("total_return", 0))*100

        print(f"  ── FOLD {i} RESULTS ──")
        print(f"     Trades={trades}  PF={fmt_pf(pf)}  Sharpe={sharpe:.3f}  WR={wr:.1f}%  Net=${net:.2f}  MaxDD={maxdd:.2f}%  Sortino={sortino:.3f}  Ret={total_ret:.2f}%")

        results.append({
            "fold": i,
            "train_range": [tr_s, tr_e],
            "test_range": [te_s, te_e],
            "trades": trades,
            "profit_factor": float(pf) if pf!=float("inf") else 999.0,
            "sharpe": sharpe,
            "win_rate": wr/100,
            "win_rate_pct": wr,
            "net_profit": net,
            "max_drawdown": maxdd/100,
            "max_drawdown_pct": maxdd,
            "sortino": sortino,
            "total_return": total_ret/100,
            "metrics_raw": {k: float(v) if isinstance(v,(int,float,np.floating)) else v for k,v in m.items() if isinstance(v,(int,float,np.floating))},
        })

        # free
        del train_env, test_env, agent, warmup_agent, synth_env

    # Aggregate
    print("\n" + "="*70)
    print("WALK-FORWARD SUMMARY (Out-of-Sample)")
    print("="*70)
    # Table header
    print(f"{'Fold':<6} {'Train':<12} {'Test':<12} {'Trades':<8} {'PF':<8} {'Sharpe':<8} {'WR%':<8} {'Net $':<10} {'MaxDD%':<8}")
    print("-"*70)
    for r in results:
        tr = f"{r['train_range'][0]}:{r['train_range'][1]}"
        te = f"{r['test_range'][0]}:{r['test_range'][1]}"
        pf_s = fmt_pf(r["profit_factor"])
        print(f"{r['fold']:<6} {tr:<12} {te:<12} {r['trades']:<8} {pf_s:<8} {r['sharpe']:<8.3f} {r['win_rate_pct']:<8.1f} {r['net_profit']:<10.2f} {r['max_drawdown_pct']:<8.2f}")

    sharpes = [r["sharpe"] for r in results]
    pfs = [r["profit_factor"] for r in results if r["profit_factor"] < 900]
    mean_sharpe = float(np.mean(sharpes)) if sharpes else 0.0
    mean_pf = float(np.mean(pfs)) if pfs else 0.0
    stability = float(np.std(sharpes)) if len(sharpes)>1 else 0.0
    worst_dd = float(max(r["max_drawdown_pct"] for r in results)) if results else 0.0
    total_trades = int(sum(r["trades"] for r in results))
    mean_wr = float(np.mean([r["win_rate_pct"] for r in results])) if results else 0.0
    total_net = float(sum(r["net_profit"] for r in results))

    print("-"*70)
    print(f"Aggregate OOS:")
    print(f"  Mean Sharpe   : {mean_sharpe:.3f}")
    print(f"  Mean PF       : {mean_pf:.3f}")
    print(f"  Stability (std Sharpe): {stability:.3f}")
    print(f"  Worst MaxDD   : {worst_dd:.2f}%")
    print(f"  Total Trades  : {total_trades}")
    print(f"  Mean WR       : {mean_wr:.1f}%")
    print(f"  Total Net P&L : ${total_net:.2f}")

    # Recommendation logic
    do_trade = (mean_sharpe > 0.5 and worst_dd < 15.0 and stability < 1.0)
    recommendation = "TRADE" if do_trade else "DO_NOT_TRADE"
    print(f"\nRecommendation logic: mean Sharpe>0.5 ({mean_sharpe:.3f}>0.5={mean_sharpe>0.5}) "
          f"and worst DD<15% ({worst_dd:.2f}%<15%={worst_dd<15.0}) "
          f"and stability<1.0 ({stability:.3f}<1.0={stability<1.0}) => {recommendation}")

    summary = {
        "symbol": "XAUUSD",
        "n_bars": 5000,
        "folds": results,
        "aggregate": {
            "mean_sharpe": mean_sharpe,
            "mean_pf": mean_pf,
            "stability_std_sharpe": stability,
            "worst_maxdd_pct": worst_dd,
            "worst_maxdd": worst_dd/100,
            "total_trades": total_trades,
            "mean_win_rate_pct": mean_wr,
            "total_net_profit": total_net,
        },
        "recommendation": recommendation,
        "criteria": {
            "mean_sharpe_gt_0.5": mean_sharpe > 0.5,
            "worst_dd_lt_15pct": worst_dd < 15.0,
            "stability_lt_1.0": stability < 1.0,
        }
    }

    out_path = Path("forex_rl/models/checkpoints/xauusd_walkforward.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nSaved walk-forward results to {out_path}")

    return summary, df, features

def run_optuna(df, features):
    print("\n" + "="*70)
    print("OPTUNA HYPERPARAM SEARCH  —  XAUUSD  —  12 trials, 20k steps each")
    print("="*70)
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        print("[ERROR] optuna not installed. Installing...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "optuna", "-q"])
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Prepare splits
    n = len(features)
    train_feat = features[0:3500]
    val_feat = features[3500:4250]
    train_df = df.iloc[0:3500].reset_index(drop=True)
    val_df = df.iloc[3500:4250].reset_index(drop=True)
    print(f"[DATA] Train 0:3500 ({len(train_feat)} bars)  Val 3500:4250 ({len(val_feat)} bars)")

    bt_config = BacktestConfig()

    def objective(trial):
        ent_coef = trial.suggest_float("ent_coef", 0.08, 0.20)
        lr = trial.suggest_float("learning_rate", 1e-4, 5e-4, log=True)
        sl_atr = trial.suggest_float("sl_atr", 1.2, 2.5)
        inaction_penalty = trial.suggest_float("inaction_penalty", 0.03, 0.08)

        env_cfg_train = EnvConfig(
            lookback=64,
            action_mode="discrete",
            reward_type="simple",
            max_episode_steps=min(500, len(train_feat)-64),
            normalize_obs=True,
            inaction_penalty=inaction_penalty,
            sl_atr_multiplier=sl_atr,
        )
        env_cfg_val = EnvConfig(
            lookback=64,
            action_mode="discrete",
            reward_type="simple",
            max_episode_steps=min(500, len(val_feat)-64),
            normalize_obs=True,
            inaction_penalty=inaction_penalty,
            sl_atr_multiplier=sl_atr,
        )
        train_env = ForexTradingEnv(train_df, env_cfg_train, features=train_feat)
        val_env = ForexTradingEnv(val_df, env_cfg_val, features=val_feat)

        agent_cfg = AgentConfig(
            algorithm="PPO",
            learning_rate=lr,
            n_steps=512,
            batch_size=64,
            n_epochs=4,
            hidden_sizes=(64,64),
            ent_coef=ent_coef,
            gamma=0.995,
            verbose=0,
            seed=42+trial.number,
        )
        agent = PPOAgent(agent_cfg)

        agent.train(train_env, total_timesteps=20000)

        bt_engine = BacktestEngine(bt_config)
        bt_result = bt_engine.run(agent, val_env)
        m = bt_result.metrics
        sharpe = float(m.get("sharpe_ratio", 0))
        maxdd = float(m.get("max_drawdown", 0))
        # objective = Sharpe - 0.5*MaxDD
        obj = sharpe - 0.5*maxdd
        trades = int(m.get("num_trades", 0))
        pf = safe_pf(m)
        wr = float(m.get("win_rate",0))*100

        # report
        print(f"  Trial {trial.number:2d}: ent={ent_coef:.3f} lr={lr:.1e} sl_atr={sl_atr:.2f} inact={inaction_penalty:.3f} "
              f"=> Sharpe={sharpe:.3f} MaxDD={maxdd*100:.2f}% Obj={obj:.3f} Trades={trades} PF={fmt_pf(pf)} WR={wr:.1f}%")

        # store extra attrs
        trial.set_user_attr("sharpe", sharpe)
        trial.set_user_attr("maxdd", maxdd)
        trial.set_user_attr("trades", trades)
        trial.set_user_attr("pf", pf)
        trial.set_user_attr("win_rate", wr)
        trial.set_user_attr("net_profit", float(m.get("net_profit",0)))

        # cleanup
        del train_env, val_env, agent

        return obj

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=12)

    print("\n" + "-"*70)
    print("OPTUNA RESULTS (12 trials)")
    print("-"*70)
    # Table sorted by value
    trials_sorted = sorted(study.trials, key=lambda t: t.value if t.value is not None else -999, reverse=True)
    print(f"{'Trial':<6} {'Obj':<8} {'Sharpe':<8} {'MaxDD%':<8} {'PF':<8} {'WR%':<8} {'Trades':<8} {'ent':<6} {'lr':<10} {'sl_atr':<7} {'inact':<6}")
    print("-"*95)
    for t in trials_sorted:
        ent = t.params.get("ent_coef",0)
        lr = t.params.get("learning_rate",0)
        sl = t.params.get("sl_atr",0)
        ina = t.params.get("inaction_penalty",0)
        sharpe = t.user_attrs.get("sharpe",0)
        maxdd = t.user_attrs.get("maxdd",0)*100
        pf_s = fmt_pf(t.user_attrs.get("pf",0))
        wr = t.user_attrs.get("win_rate",0)
        tr = t.user_attrs.get("trades",0)
        val = t.value if t.value else 0
        print(f"{t.number:<6} {val:<8.3f} {sharpe:<8.3f} {maxdd:<8.2f} {pf_s:<8} {wr:<8.1f} {tr:<8} {ent:<6.3f} {lr:<10.1e} {sl:<7.2f} {ina:<6.3f}")

    best = study.best_trial
    print("-"*95)
    print(f"BEST TRIAL #{best.number}: objective={best.value:.4f}  Sharpe={best.user_attrs.get('sharpe',0):.3f}  MaxDD={best.user_attrs.get('maxdd',0)*100:.2f}%")
    print(f"  Params: ent_coef={best.params['ent_coef']:.4f}  lr={best.params['learning_rate']:.2e}  sl_atr={best.params['sl_atr']:.3f}  inaction_penalty={best.params['inaction_penalty']:.4f}")
    print(f"  Trades={best.user_attrs.get('trades')} PF={fmt_pf(best.user_attrs.get('pf',0))} WR={best.user_attrs.get('win_rate'):.1f}% Net=${best.user_attrs.get('net_profit'):.2f}")

    best_params = {
        "trial": best.number,
        "objective": float(best.value),
        "sharpe": float(best.user_attrs.get("sharpe",0)),
        "max_drawdown": float(best.user_attrs.get("maxdd",0)),
        "max_drawdown_pct": float(best.user_attrs.get("maxdd",0)*100),
        "profit_factor": float(best.user_attrs.get("pf",0)),
        "win_rate": float(best.user_attrs.get("win_rate",0)),
        "win_rate_frac": float(best.user_attrs.get("win_rate",0)/100),
        "trades": int(best.user_attrs.get("trades",0)),
        "net_profit": float(best.user_attrs.get("net_profit",0)),
        "params": best.params,
        "all_trials": [
            {
                "number": t.number,
                "value": float(t.value) if t.value is not None else None,
                "params": t.params,
                "sharpe": float(t.user_attrs.get("sharpe",0)),
                "maxdd": float(t.user_attrs.get("maxdd",0)),
                "pf": float(t.user_attrs.get("pf",0)),
                "win_rate": float(t.user_attrs.get("win_rate",0)),
                "trades": int(t.user_attrs.get("trades",0)),
            } for t in study.trials
        ]
    }

    out_path = Path("forex_rl/models/checkpoints/xauusd_optuna_best.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(best_params, f, indent=2, default=str)
    print(f"\nSaved best params to {out_path}")

    return best_params, study

if __name__ == "__main__":
    t_start = time.time()
    wf_summary, df, features = run_walk_forward()
    best_params, study = run_optuna(df, features)
    elapsed = time.time() - t_start
    print("\n" + "="*70)
    print(f"VALIDATION COMPLETE — total elapsed {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  Walk-forward: {wf_summary['aggregate']['mean_sharpe']:.3f} mean Sharpe, rec={wf_summary['recommendation']}")
    print(f"  Optuna best: trial {best_params['trial']} obj={best_params['objective']:.3f} params={best_params['params']}")
    print("="*70)
