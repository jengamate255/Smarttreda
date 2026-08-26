"""CLI: python -m src.cli <command>

Commands:
    collect    pull historical candles from MT5 into data/raw
    features   build engineered features + labels from data/processed
    train      train model tasks (LightGBM / XGBoost / optional PyTorch)
    evaluate   score saved models on the test split
    backtest   walk-forward backtest with ML signals + baselines
    predict    latest bar decision payload from saved models
    paper      polling paper-trading loop (default mode, never live)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from src.config import Config, load_yaml
from src.utils import pip_size


def _config(args) -> Config:
    cfg = Config.from_file(args.config)
    if args.symbol:
        cfg.data["symbols"] = [args.symbol]
    if args.timeframe:
        cfg.data["timeframes"] = [args.timeframe]
    return cfg


def _default_pair(cfg: Config, args) -> tuple[str, str]:
    symbols = cfg.get("symbols") or ["EURUSD"]
    timeframes = cfg.get("timeframes") or ["M15"]
    return (args.symbol or symbols[0], args.timeframe or timeframes[0])


def _require_partition(cfg: Config, kind: str, symbol: str, timeframe: str):
    from src.data import storage

    if not storage.metadata_exists(cfg, kind, symbol, timeframe):
        sys.exit(
            f"No {kind} data for {symbol} {timeframe}. Run `python -m src.cli collect` first."
        )


def cmd_collect(args) -> None:
    from src.data.mt5_collector import collect_all

    cfg = _config(args)
    symbols = cfg.get("symbols")
    timeframes = cfg.get("timeframes")
    print(f"Collecting {symbols} x {timeframes} from MetaTrader 5 ...")
    for result in collect_all(cfg, symbols, timeframes):
        print(f"  {result['symbol']} {result['timeframe']}: {result['rows']} rows "
              f"({result['start']} .. {result['end']})")


def cmd_features(args) -> None:
    from src.data import storage
    from src.features.pipeline import attach_labels, build_features

    cfg = _config(args)
    symbols = cfg.get("symbols")
    timeframes = cfg.get("timeframes")
    htf_map = cfg.get("features.htf_context", {})

    for symbol in symbols:
        for timeframe in timeframes:
            _require_partition(cfg, "raw", symbol, timeframe)
            print(f"Building features: {symbol} {timeframe} ...")
            raw = storage.read_partition(cfg, "raw", symbol, timeframe)
            raw = raw.sort_values("time").reset_index(drop=True)

            htf_frames = {}
            for htf in htf_map.get(timeframe, {}):
                try:
                    htf_raw = storage.read_partition(cfg, "raw", symbol, htf)
                except FileNotFoundError:
                    print(f"  (warn: no raw data for HTF {htf}, skipping context)")
                    continue
                htf_frames[htf] = htf_raw

            features = build_features(raw, cfg=cfg.data, htf_frames=htf_frames, base_timeframe=timeframe)
            features = attach_labels(features, raw, cfg=cfg.data, symbol=symbol)
            storage.write_partition(
                features, cfg, "features", symbol, timeframe,
                extra_meta={"feature_version": cfg.get("project.feature_version")},
            )
            print(f"  {len(features)} rows -> data/features/{symbol}/{timeframe}")


def cmd_train(args) -> None:
    from src.data import storage
    from src.ml import model_registry
    from src.ml.evaluate import evaluate_classifier
    from src.ml.train import (
        date_splits,
        feature_columns,
        prepare_targets,
        train_lightgbm,
        train_torch,
        train_xgboost,
    )

    cfg = _config(args)
    symbol, timeframe = _default_pair(cfg, args)
    _require_partition(cfg, "features", symbol, timeframe)

    print(f"Training: {symbol} {timeframe}")
    features = storage.read_partition(cfg, "features", symbol, timeframe)
    splits = date_splits(features, cfg)
    X = features[feature_columns(features, cfg)].reset_index(drop=True)
    targets = prepare_targets(features, cfg, int(cfg.get("labels.primary_rr", 3)))
    rr = int(cfg.get("labels.primary_rr", 3))

    task_defs = {
        "setup_classifier": ("setup", ["binary"], 1),
        "direction": ("direction", ["multiclass"], 2),
        "outcome_probability": ("outcome", ["binary"], 1),
    }

    for task in cfg.get("model.tasks", list(task_defs)):
        y = targets[task_defs[task][0]]
        objective = task_defs[task][1][0]
        num_class = task_defs[task][2]
        for backend in cfg.get("model.tree_backends", ["lightgbm"]):
            if objective == "multiclass":
                train_fn = train_lightgbm if backend == "lightgbm" else train_xgboost
                model = train_fn(X, y, splits["train"], splits["validation"],
                                 objective="multiclass", num_class=3)
            else:
                train_fn = train_lightgbm if backend == "lightgbm" else train_xgboost
                model = train_fn(X, y, splits["train"], splits["validation"], objective="binary")

            proba = _proba(model, X, splits["test"], torch_model=False)
            metrics = evaluate_classifier(y[splits["test"]], proba, [0, 1] if num_class == 1 else [0, 1, 2])

            metadata = {
                "algorithm": backend,
                "feature_version": cfg.get("project.feature_version"),
                "dataset_version": storage.dataset_version(cfg, "features", symbol, timeframe),
                "training_period": f"{cfg.get('model.splits.train_start')}..{cfg.get('model.splits.train_end')}",
                "validation_period": f"{cfg.get('model.splits.validation_start')}..{cfg.get('model.splits.validation_end')}",
                "test_period": f"{cfg.get('model.splits.test_start')}..{cfg.get('model.splits.test_end')}",
                "hyperparameters": {},
                "feature_columns": feature_columns(features, cfg),
            }
            version = model_registry.save_model(
                cfg, symbol, timeframe, task, model, metadata,
                {"test": metrics, "validation": evaluate_classifier(
                    y[splits["validation"]],
                    _proba(model, X, splits["validation"], torch_model=False),
                    [0, 1] if num_class == 1 else [0, 1, 2],
                )},
            )
            print(f"  {task}/{backend} {version}: test accuracy={metrics['accuracy']:.4f}")

    torch_enabled = cfg.get("model.torch.enabled", False)
    if torch_enabled:
        try:
            for task in ("setup_classifier", "outcome_probability"):
                y = targets[task_defs[task][0]]
                model = train_torch(X, y, splits["train"], splits["validation"], 2, cfg.get("model.torch", {}))
                proba = _proba(model, X, splits["test"], torch_model=True)
                metrics = evaluate_classifier(y[splits["test"]], proba, [0, 1])
                version = model_registry.save_model(
                    cfg, symbol, timeframe, task, model,
                    {
                        "algorithm": "torch_mlp",
                        "feature_version": cfg.get("project.feature_version"),
                        "dataset_version": storage.dataset_version(cfg, "features", symbol, timeframe),
                        "training_period": f"{cfg.get('model.splits.train_start')}..{cfg.get('model.splits.train_end')}",
                        "validation_period": f"{cfg.get('model.splits.validation_start')}..{cfg.get('model.splits.validation_end')}",
                        "test_period": f"{cfg.get('model.splits.test_start')}..{cfg.get('model.splits.test_end')}",
                        "hyperparameters": cfg.get("model.torch", {}),
                        "feature_columns": feature_columns(features, cfg),
                    },
                    {"test": metrics},
                )
                print(f"  torch/{task} {version}: test accuracy={metrics['accuracy']:.4f}")
        except RuntimeError as exc:
            print(f"  torch skipped: {exc}")


def _proba(model, X, idx, torch_model: bool):
    import numpy as np

    if torch_model:
        from src.ml.torch_models import MLPClassifier

        import torch
        Xt = torch.tensor(X.iloc[idx].to_numpy(dtype=np.float32))
        return model.predict_proba(Xt)
    from src.ml.train import predict_proba
    return predict_proba(model, X.iloc[idx])


def cmd_evaluate(args) -> None:
    from src.data import storage
    from src.ml import model_registry
    from src.ml.evaluate import evaluate_classifier
    from src.ml.train import date_splits, feature_columns, prepare_targets

    cfg = _config(args)
    symbol, timeframe = _default_pair(cfg, args)
    features = storage.read_partition(cfg, "features", symbol, timeframe)
    splits = date_splits(features, cfg)
    X = features[feature_columns(features, cfg)].reset_index(drop=True)
    targets = prepare_targets(features, cfg, int(cfg.get("labels.primary_rr", 3)))

    for entry in model_registry.list_models(cfg):
        if entry["symbol"] != symbol or entry["timeframe"] != timeframe:
            continue
        meta = model_registry.load_metadata(cfg, symbol, timeframe, entry["task"], entry["version"])
        model, _ = model_registry.load_model(cfg, symbol, timeframe, entry["task"], entry["version"])
        task_target = {"setup_classifier": "setup", "direction": "direction", "outcome_probability": "outcome"}[entry["task"]]
        y = targets[task_target]
        classes = [0, 1, 2] if entry["task"] == "direction" else [0, 1]
        proba = _proba(model, X, splits["test"], torch_model=meta.get("algorithm") == "torch_mlp")
        m = evaluate_classifier(y[splits["test"]], proba, classes)
        print(f"{entry['task']}/{meta.get('algorithm')}/{entry['version']}: "
              f"acc={m['accuracy']:.4f} auc={m.get('auc')} logloss={m.get('logloss')}")


def cmd_backtest(args) -> None:
    import numpy as np
    import pandas as pd

    from src.backtest import baselines
    from src.backtest.engine import run_backtest
    from src.backtest.metrics import breakdown, compute_metrics
    from src.backtest.walkforward import walk_forward_backtest
    from src.data import storage

    cfg = _config(args)
    symbol, timeframe = _default_pair(cfg, args)
    _require_partition(cfg, "features", symbol, timeframe)
    features = storage.read_partition(cfg, "features", symbol, timeframe)

    if args.mode == "walkforward":
        report = walk_forward_backtest(
            features, cfg, symbol, timeframe,
            task_config={"backend": args.backend},
            chunk_months=int(args.chunk_months),
        )
        print(json.dumps(report, indent=2, default=str))
        out_path = cfg.get("backtest.results_dir", "backtest/results")
        from pathlib import Path
        Path(out_path).mkdir(parents=True, exist_ok=True)
        (Path(out_path) / f"walkforward_{symbol}_{timeframe}.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        return

    cutoff = pd.Timestamp(cfg.get("model.splits.test_start", "2024-01-01"), tz="UTC")
    t = pd.to_datetime(features["time"], utc=True)
    oos = features[t >= cutoff].reset_index(drop=True)

    strategies = {
        "random": lambda f: baselines.random_entry_strategy(f, seed=0),
        "ema_cross": lambda f: baselines.ema_cross_strategy(f),
        "supply_demand": lambda f: baselines.supply_demand_strategy(f),
    }
    if args.ml:
        strategies["ml"] = lambda f, _s=symbol, _t=timeframe: _ml_strategy(f, cfg, _s, _t)

    for name, strat in strategies.items():
        result = run_backtest(
            oos, strat, cfg=cfg.get("backtest", {}), symbol=symbol, timeframe=timeframe
        )
        metrics = compute_metrics(result.trades, result.equity_curve)
        print(f"== {name} ({symbol} {timeframe} OOS) ==")
        print(json.dumps(metrics, indent=2, default=str))


def _ml_strategy(frame, cfg, symbol: str, timeframe: str):
    """Build a causal ML signal strategy over a feature frame using saved models."""
    import numpy as np

    from src.backtest.walkforward import ml_signals_from_predictions
    from src.ml import model_registry
    from src.ml.train import feature_columns

    X = frame[feature_columns(frame, cfg)].reset_index(drop=True)
    setup_model, _ = model_registry.load_model(cfg, symbol, timeframe, "setup_classifier")
    dir_model, _ = model_registry.load_model(cfg, symbol, timeframe, "direction")
    from src.ml.train import predict_proba
    setup_proba = predict_proba(setup_model, X)
    dir_proba = predict_proba(dir_model, X)
    return ml_signals_from_predictions(frame, setup_proba, dir_proba, cfg)


def cmd_predict(args) -> None:
    from src.data.mt5_collector import MT5Collector
    from src.features.pipeline import build_features
    from src.ml.predict import decide

    cfg = _config(args)
    symbol, timeframe = _default_pair(cfg, args)

    collector = MT5Collector(cfg)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    df = collector.collect(symbol, timeframe, start, end)
    collector.close()

    features = build_features(df, cfg=cfg.data, htf_frames={}, base_timeframe=timeframe)
    last = features.iloc[[-1]].reset_index(drop=True)
    spread_pips = float(df["spread"].iloc[-1]) if "spread" in df.columns and len(df) else 0.0
    decision = decide(cfg, symbol, timeframe, last, spread_pips=spread_pips)
    print(json.dumps(decision, indent=2))


def _paper_iteration(
    cfg: Config,
    model_set,
    executor,
    df,
    symbol: str,
    timeframe: str,
    risk=None,
    equity: float | None = None,
    pip_value_per_lot: float = 10.0,
):
    """One polling iteration of the paper loop: features -> predict -> agent ->
    risk gates -> order.

    Returns (decision, order_result_or_None). With ``risk``/``equity`` provided,
    position size is derived from the risk budget and risk gates can block the
    order. Without them, a 0.01-lot fallback order is placed (pure fill logging).
    """
    from src.agent.trading_agent import TradingAgent, market_regime_from_features, session_from_features
    from src.execution.mt5_executor import OrderRequest
    from src.features.pipeline import build_features
    from src.ml.predict import predict_row

    features = build_features(df, cfg=cfg.data, htf_frames={}, base_timeframe=timeframe)
    last_idx = len(features) - 1
    row = features.iloc[[last_idx]]
    raw = predict_row(model_set, row)
    regime = market_regime_from_features(features, last_idx)
    session = session_from_features(features, last_idx)
    spread_pips = float(df["spread"].iloc[-1]) if "spread" in df.columns and len(df) else 0.0

    agent = TradingAgent(cfg)
    decision = agent.evaluate(
        {
            "setup_probability": raw["setup_valid"],
            "buy_probability": raw["direction"]["BUY"],
            "sell_probability": raw["direction"]["SELL"],
            "no_trade_probability": raw["direction"]["NO_TRADE"],
            "tp_before_sl_probability": raw["tp_before_sl"],
        },
        market_regime=regime, session=session, spread_pips=spread_pips,
    )

    result = None
    if decision.signal in ("BUY", "SELL"):
        if risk is not None and equity is not None:
            can, gate = risk.can_trade(equity)
            if not can:
                decision.reasons.append(f"risk gate: {gate} (equity {equity:.2f})")
                return decision, {"status": "blocked", "reason": gate, "equity": round(equity, 2)}

        entry = float(df["close"].iloc[-1])
        atr = float(features["atr"].iloc[-1])
        sl_mult = float(cfg.get_symbol("labels.atr_stop_multiple", symbol, 1.5))
        tp_mult = sl_mult * float(cfg.get_symbol("labels.primary_rr", symbol, 3))
        sl = entry - sl_mult * atr if decision.signal == "BUY" else entry + sl_mult * atr
        tp = entry + tp_mult * atr if decision.signal == "BUY" else entry - tp_mult * atr

        lots = 0.01
        if risk is not None and equity is not None:
            from src.risk.risk_engine import RiskManager, position_size
            from src.utils import pip_size

            percent = RiskManager.default_risk_percent(cfg, symbol)
            sized = position_size(
                equity, percent, entry, sl,
                pip_value_per_lot=pip_value_per_lot,
                pip_size=pip_size(symbol),
                min_lots=0.01,
            )
            lots = sized.lots
        result = executor.place_order(OrderRequest(
            symbol=symbol, side=decision.signal, lots=lots,
            entry=entry, sl=sl, tp=tp, timeframe=timeframe,
            reason="; ".join(decision.reasons),
        ))
    return decision, result


def cmd_paper(args) -> None:
    import time as time_module
    from pathlib import Path

    from src.agent.trading_agent import TradingAgent
    from src.data.mt5_collector import MT5Collector
    from src.execution.mt5_executor import MT5Executor, paper_equity
    from src.ml.predict import load_model_set
    from src.risk.risk_engine import RiskManager

    cfg = _config(args)
    symbol, timeframe = _default_pair(cfg, args)
    executor = MT5Executor(cfg, mode="PAPER")
    model_set = load_model_set(cfg, symbol, timeframe)
    poll = int(cfg.get("paper.poll_seconds", 300))
    lookback = int(cfg.get("paper.lookback_bars", 300))
    collector = MT5Collector(cfg)

    start_equity = float(cfg.get("paper.start_equity", 10_000.0))
    pip_value_per_lot = float(cfg.get("paper.pip_value_per_lot", 10.0))
    journal_path = Path(cfg.get("paper.journal_dir", "data/paper")) / "journal.json"
    risk = RiskManager.from_config(cfg, start_equity)
    equity = start_equity

    print(f"Paper trading {symbol} {timeframe} (poll {poll}s). Ctrl+C to stop.")
    while True:
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=max(7, lookback * int(cfg.get("bar_seconds", {}).get(timeframe, 900)) // 86400 + 1))
            df = collector.collect(symbol, timeframe, start, end)
            if "close" in df.columns and len(df):
                equity = paper_equity(journal_path, start_equity, float(df["close"].iloc[-1]),
                                      symbol, pip_value_per_lot)
            decision, result = _paper_iteration(
                cfg, model_set, executor, df, symbol, timeframe,
                risk=risk, equity=equity, pip_value_per_lot=pip_value_per_lot,
            )
            print(f"[{datetime.now(timezone.utc).isoformat()}] {decision.signal} "
                  f"({decision.raw['setup_probability']:.2f}, "
                  f"tp_bs={decision.raw['tp_before_sl_probability']:.2f}, "
                  f"equity={equity:.2f})")
            if result:
                if result.get("status") == "blocked":
                    print(f"  -> blocked by risk: {result['reason']}")
                else:
                    print(f"  -> order placed: {result.get('status')} {result.get('lots')} lots")
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f"  error: {exc}")
        time_module.sleep(poll)
    collector.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="src.cli", description="Forex AI agent CLI")
    parser.add_argument("--config", default=None, help="path to config yaml")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--timeframe", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("collect")
    p_features = sub.add_parser("features")
    p_train = sub.add_parser("train")
    p_train.add_argument("--backend", choices=["lightgbm", "xgboost"], default="lightgbm")
    sub.add_parser("evaluate")
    p_bt = sub.add_parser("backtest")
    p_bt.add_argument("--mode", choices=["holdout", "walkforward"], default="holdout")
    p_bt.add_argument("--backend", choices=["lightgbm", "xgboost"], default="lightgbm")
    p_bt.add_argument("--chunk-months", default=6)
    p_bt.add_argument("--ml", action="store_true", help="include ML strategy vs baselines")
    sub.add_parser("predict")
    sub.add_parser("paper")

    args = parser.parse_args(argv)
    handlers = {
        "collect": cmd_collect,
        "features": cmd_features,
        "train": cmd_train,
        "evaluate": cmd_evaluate,
        "backtest": cmd_backtest,
        "predict": cmd_predict,
        "paper": cmd_paper,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
