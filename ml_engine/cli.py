"""Pipeline orchestration CLI.

Usage:
    python -m ml_engine.cli ingest   [--symbols ...] [--refresh]
    python -m ml_engine.cli features [--symbols ...]
    python -m ml_engine.cli setups   [--symbols ...]
    python -m ml_engine.cli dataset  [--symbols ...]
    python -m ml_engine.cli train    [--model lgbm] [--target win_2r]
    python -m ml_engine.cli report
    python -m ml_engine.cli run-all  # full pipeline end to end
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from ml_engine import DATASET_VERSION, FEATURE_VERSION, LABEL_VERSION
from ml_engine.config import (
    DATASETS_DIR,
    DEFAULT_CONFIG,
    FEATURES_DIR,
    REPORTS_DIR,
    EngineConfig,
    ensure_dirs,
)
from ml_engine.data.ingest import ingest
from ml_engine.data.validate import validate

logger = logging.getLogger("ml_engine.cli")


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def stage_ingest(cfg: EngineConfig, symbols: list[str], refresh: bool) -> dict:
    from ml_engine.data.ingest import save_raw
    ensure_dirs()
    report = {}
    for symbol in symbols:
        for tf in cfg.timeframes:
            key = f"{symbol}_{tf}"
            try:
                df = ingest(symbol, tf, refresh=refresh)
                rep = validate(df, symbol, tf)
                report[key] = rep.to_dict()
                status = "OK" if rep.ok else "FAIL"
                logger.info(
                    "%s %s: %d rows %s -> %s [%s] gaps=%.1f%% wknd=%d",
                    symbol, tf, rep.rows, rep.start, rep.end, status,
                    rep.gap_ratio * 100, rep.weekend_gaps,
                )
                if not rep.ok and refresh:
                    # try one repair pass
                    from ml_engine.data.validate import clean

                    df2 = clean(df)
                    rep2 = validate(df2, symbol, tf)
                    if rep2.ok:
                        save_raw(df2, symbol, tf)
                        report[key] = rep2.to_dict()
                        logger.info("%s %s: repaired -> OK", symbol, tf)
            except Exception as exc:
                report[key] = {"ok": False, "errors": [str(exc)]}
                logger.error("%s %s: %s", symbol, tf, exc)
    ok = sum(1 for r in report.values() if r.get("ok"))
    logger.info("ingest: %d/%d frames research-grade", ok, len(report))
    return report


def stage_features(cfg: EngineConfig, symbols: list[str]) -> dict:
    from ml_engine.data.ingest import load_raw
    from ml_engine.features.build import build_features

    ensure_dirs()
    summary = {}
    for symbol in symbols:
        for tf in cfg.timeframes:
            try:
                df = load_raw(symbol, tf)
                feats = build_features(df, symbol, tf, cfg)
                out = FEATURES_DIR / f"{symbol}_{tf}_features.parquet"
                feats.to_parquet(out, index=False)
                summary[f"{symbol}_{tf}"] = {
                    "rows": len(feats), "cols": len(feats.columns),
                    "nan_frac": float(feats.isna().mean().mean()),
                }
                logger.info("features %s %s: %d rows x %d cols", symbol, tf, len(feats), len(feats.columns))
            except Exception as exc:
                summary[f"{symbol}_{tf}"] = {"error": str(exc)}
                logger.error("features %s %s: %s", symbol, tf, exc)
    return summary


def stage_setups(cfg: EngineConfig, symbols: list[str]) -> dict:
    """Compose setups + labels per symbol on the base timeframe, with
    higher-TF structure bias aligned from completed HTF bars only."""
    from ml_engine.data.ingest import load_raw
    from ml_engine.setups.composer import compose_setups
    from ml_engine.setups.labels import label_setups

    ensure_dirs()
    base_tf = cfg.base_timeframe
    summary = {}
    for symbol in symbols:
        try:
            df = load_raw(symbol, base_tf)
            # HTF bias from completed bars only
            htf_bias = pd.Series(0.0, index=df.index)
            for htf in cfg.higher_timeframes:
                try:
                    hdf = load_raw(symbol, htf)
                    # bias series on the HTF frame, aligned to base via
                    # completed-bar close times (no in-progress leakage)
                    hseries = _htf_bias_series(hdf, cfg)
                    minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}[htf]
                    close_times = pd.to_datetime(hdf["time"]).astype("datetime64[ns]").add(pd.Timedelta(minutes=minutes))
                    base_times = pd.to_datetime(df["time"]).astype("datetime64[ns]")
                    aligned = pd.merge_asof(
                        pd.DataFrame({"time": base_times}),
                        pd.DataFrame({"time": close_times, "bias": hseries}),
                        on="time", direction="backward",
                    )
                    htf_bias = htf_bias + aligned["bias"].fillna(0.0).to_numpy()
                except Exception as exc:
                    logger.warning("HTF %s bias unavailable for %s: %s", htf, symbol, exc)
            setups = compose_setups(df, symbol, base_tf, cfg, htf_bias=htf_bias)
            labels = label_setups(setups, df, cfg)
            setups_df = pd.DataFrame(
                [
                    {
                        "setup_id": s.setup_id, "symbol": s.symbol, "time": s.time,
                        "index": s.index, "direction": s.direction,
                        "entry": s.entry, "stop": s.stop, "target": s.target,
                        "risk_reward": s.risk_reward,
                        "structure_context": json.dumps(s.structure_context, default=str),
                        "zone_context": json.dumps(s.zone_context, default=str),
                        "liquidity_context": json.dumps(s.liquidity_context, default=str),
                        "price_action_context": json.dumps(s.price_action_context, default=str),
                    }
                    for s in setups
                ]
            )
            out = DATASETS_DIR / f"{symbol}_{base_tf}_setups.parquet"
            if len(setups_df):
                setups_df.merge(labels.reset_index(), on="setup_id").to_parquet(out, index=False)
            summary[symbol] = {
                "setups": len(setups),
                "long": sum(1 for s in setups if s.direction == "long"),
                "short": sum(1 for s in setups if s.direction == "short"),
                "win_2r_rate": float(labels["win_2r"].mean()) if len(labels) else None,
            }
            logger.info("setups %s: %d (L=%d S=%d) win2r=%.3f",
                        symbol, len(setups), summary[symbol]["long"],
                        summary[symbol]["short"],
                        summary[symbol]["win_2r_rate"] or 0)
        except Exception as exc:
            summary[symbol] = {"error": str(exc)}
            logger.error("setups %s: %s", symbol, exc)
    return summary


def _htf_bias_series(hdf: pd.DataFrame, cfg: EngineConfig) -> pd.Series:
    from ml_engine.setups.composer import _structure_bias
    from ml_engine.structure.events import structure_events

    events = structure_events(hdf, cfg.swing)
    n = len(hdf)
    bias = np.zeros(n)
    for i in range(n):
        bias[i] = _structure_bias(events, i)
    return pd.Series(bias)


def stage_dataset(cfg: EngineConfig, symbols: list[str]) -> tuple[pd.DataFrame, str]:
    """Assemble the pooled dataset: features at setup bar + labels."""
    from ml_engine.data.ingest import load_raw
    from ml_engine.datasets.build import (
        assert_no_label_leak,
        build_dataset,
        dataset_hash,
        feature_columns,
    )
    from ml_engine.features.build import build_features

    ensure_dirs()
    base_tf = cfg.base_timeframe
    all_setups = []
    for symbol in symbols:
        try:
            setups_path = DATASETS_DIR / f"{symbol}_{base_tf}_setups.parquet"
            if not setups_path.exists():
                continue
            setups_df = pd.read_parquet(setups_path)
            if len(setups_df) == 0:
                continue
            # Rebuild Setup objects for the dataset builder
            from ml_engine.setups.composer import Setup as _S
            for _, r in setups_df.iterrows():
                all_setups.append(
                    _S(
                        setup_id=r["setup_id"], symbol=r["symbol"], time=r["time"],
                        index=int(r["index"]), direction=r["direction"],
                        entry=float(r["entry"]), stop=float(r["stop"]),
                        target=float(r["target"]), risk_reward=float(r["risk_reward"]),
                    )
                )
        except Exception as exc:
            logger.error("dataset gather %s: %s", symbol, exc)

    if not all_setups:
        logger.error("no setups found; run the setups stage first")
        return pd.DataFrame(), ""

    # Features: rebuild on the fly per symbol, join by (symbol, index)
    feature_parts = []
    for symbol in symbols:
        try:
            df = load_raw(symbol, base_tf)
            feats = build_features(df, symbol, base_tf, cfg)
            setups_path = DATASETS_DIR / f"{symbol}_{base_tf}_setups.parquet"
            if not setups_path.exists():
                continue
            setups_df = pd.read_parquet(setups_path)
            sel = [s for s in all_setups if s.symbol == symbol]
            labels = setups_df.set_index("setup_id")[
                ["mfe_r", "mae_r", "win_1r", "win_2r", "win_3r", "win_4r",
                 "r_multiple", "exit_bars", "tp_first", "sl_first", "neither"]
            ]
            dataset, _meta = build_dataset(sel, feats, labels, FEATURE_VERSION)
            feature_parts.append(dataset)
        except Exception as exc:
            logger.error("dataset build %s: %s", symbol, exc)

    if not feature_parts:
        return pd.DataFrame(), ""
    pooled = pd.concat(feature_parts, ignore_index=True)
    from ml_engine.datasets.build import add_symbol_onehot

    pooled = add_symbol_onehot(pooled)
    fcols = feature_columns(pooled, FEATURE_VERSION)
    assert_no_label_leak(fcols)
    dhash = dataset_hash(pooled, fcols)
    out = DATASETS_DIR / f"pooled_{base_tf}_{dhash}.parquet"
    pooled.to_parquet(out, index=False)
    logger.info(
        "dataset: %d setups, %d features, hash=%s, win_2r=%.3f -> %s",
        len(pooled), len(fcols), dhash,
        float(pooled["win_2r"].mean()) if len(pooled) else float("nan"), out,
    )
    # Persist the feature column list alongside
    (DATASETS_DIR / f"pooled_{base_tf}_{dhash}_cols.json").write_text(
        json.dumps(fcols), encoding="utf-8"
    )
    return pooled, dhash


def stage_train(
    cfg: EngineConfig,
    symbols: list[str],
    model_type: str,
    target: str,
    db_path=None,
) -> dict:
    """Walk-forward training + evaluation; records the experiment."""
    from ml_engine.datasets.build import feature_columns, xy
    from ml_engine.experiments.registry import record_experiment, record_fold_metrics
    from ml_engine.models.evaluate import (
        bootstrap_expectancy,
        calibration_buckets,
        classification_metrics,
        max_drawdown_r,
        threshold_sweep,
        top_decile_edge,
        trading_metrics,
    )
    from ml_engine.models.train import fit_calibrator_on_train, fit_model
    from ml_engine.validation.walkforward import assert_chronology, make_folds

    pooled, dhash = stage_dataset(cfg, symbols)
    if len(pooled) == 0:
        logger.error("empty dataset")
        return {}
    fcols = feature_columns(pooled, FEATURE_VERSION)
    X, y, meta = xy(pooled, fcols, target)
    if len(y) < 200:
        logger.error("dataset too small for walk-forward: %d setups", len(y))
        return {}

    folds = make_folds(meta["time"], cfg.walk_forward)
    assert_chronology(folds)
    if not folds:
        logger.error("not enough history for walk-forward folds")
        return {}
    logger.info("walk-forward: %d folds", len(folds))

    fold_rows = []
    oos_p = []
    oos_y = []
    oos_r = []
    for f in folds:
        Xtr, ytr = X[f.train_index], y[f.train_index]
        Xte, yte = X[f.test_index], y[f.test_index]
        if len(np.unique(ytr)) < 2:
            fold_rows.append({"fold": f.fold, "skipped": "single-class train"})
            continue
        model = fit_model(
            Xtr, ytr, fcols, target, model_type,
            train_period=(str(f.train_start), str(f.train_end)),
        )
        # Calibrate on the train fold only (temporal holdout of its last 30%)
        model = fit_calibrator_on_train(model, Xtr, ytr)
        p = model.predict_proba(Xte)
        clf = classification_metrics(yte, p)
        r_mult = pooled.iloc[f.test_index]["r_multiple"].to_numpy(float)
        tm = trading_metrics(r_mult, p, threshold=0.5)
        tm_all = trading_metrics(r_mult)
        fold_rows.append(
            {
                "fold": f.fold,
                "train": f"{f.train_start} -> {f.train_end}",
                "test": f"{f.test_start} -> {f.test_end}",
                "n_train": len(ytr), "n_test": len(yte),
                "test_base_rate": clf["base_rate"],
                "auc": clf["auc"],
                "brier": clf["brier"],
                "all_trades": tm_all,
                "filtered_trades": tm,
            }
        )
        oos_p.append(p)
        oos_y.append(yte)
        oos_r.append(r_mult)
        record_fold_metrics(f"{model_type}-{target}-{dhash}", f.fold,
                            {"auc": clf["auc"], "brier": clf["brier"], "trading": tm})

    oos_p = np.concatenate(oos_p) if oos_p else np.array([])
    oos_y = np.concatenate(oos_y) if oos_y else np.array([])
    oos_r = np.concatenate(oos_r) if oos_r else np.array([])

    pooled_clf = classification_metrics(oos_y, oos_p)
    pooled_trades = trading_metrics(oos_r, oos_p, threshold=0.5)
    pooled_all = trading_metrics(oos_r)
    # Best-threshold selection WITH multiple-testing awareness: pick the
    # best sweep row, then bootstrap it and report honestly.
    sweep = threshold_sweep(oos_r, oos_p)
    evaluable = [row for row in sweep if not row.get("not_evaluable")]
    best_row = max(evaluable, key=lambda r: r["expectancy_r"]) if evaluable else None
    if best_row is not None:
        keep = oos_p >= best_row["threshold"]
        best_row["bootstrap"] = bootstrap_expectancy(oos_r[keep])
        best_row["note"] = (
            f"best of {len(evaluable)} thresholds -> selection bias present; "
            "treat as upper bound"
        )

        result = {
            "model_type": model_type,
            "target": target,
            "dataset_hash": dhash,
            "folds": fold_rows,
            "oos": {
                "classification": pooled_clf,
                "calibration": calibration_buckets(oos_y, oos_p),
                "trading_all": pooled_all,
                "trading_filtered": pooled_trades,
                "threshold_sweep": sweep,
                "best_threshold": best_row,
                "top_decile": top_decile_edge(oos_r, oos_p),
                "max_drawdown_pct_at_0.5risk": max_drawdown_r(oos_r, 0.5),
            },
            "git_commit": _git_commit(),
        }

    record_experiment(
        experiment_id=f"{model_type}-{target}-{dhash}",
        dataset_hash=dhash,
        dataset_version=DATASET_VERSION,
        feature_version=FEATURE_VERSION,
        label_version=LABEL_VERSION,
        model_type=model_type,
        target=target,
        params={"model": model_type, "seed": 42},
        train_period=[str(fold_rows[0].get("train", "")), str(fold_rows[-1].get("train", ""))],
        test_period=[str(fold_rows[0].get("test", "")), str(fold_rows[-1].get("test", ""))],
        metrics={
            "oos_auc": pooled_clf["auc"],
            "oos_brier": pooled_clf["brier"],
            "top_decile_expectancy_r": result["oos"]["top_decile"].get("expectancy_r"),
            "top_decile_ci": [
                result["oos"]["top_decile"].get("bootstrap", {}).get("ci_low"),
                result["oos"]["top_decile"].get("bootstrap", {}).get("ci_high"),
            ],
            "expectancy_all_r": pooled_all.get("expectancy_r"),
            "best_threshold": (
                {
                    "threshold": result["oos"]["best_threshold"]["threshold"],
                    "expectancy_r": result["oos"]["best_threshold"]["expectancy_r"],
                    "profit_factor": result["oos"]["best_threshold"]["profit_factor"],
                    "n": result["oos"]["best_threshold"]["trades"],
                    "ci": [
                        result["oos"]["best_threshold"]["bootstrap"]["ci_low"],
                        result["oos"]["best_threshold"]["bootstrap"]["ci_high"],
                    ],
                }
                if result["oos"].get("best_threshold")
                else None
            ),
            "n_folds": len(fold_rows),
        },
        git_commit=_git_commit(),
        notes="walk-forward OOS pooled",
    )

    out = REPORTS_DIR / f"wf_{model_type}_{target}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    logger.info("report -> %s", out)
    return result


def stage_report() -> list[dict]:
    from ml_engine.experiments.registry import list_experiments

    return list_experiments()


def stage_locations(cfg: EngineConfig, symbols: list[str]) -> dict:
    """Run location engine on all symbols and report stats."""
    from ml_engine.data.ingest import load_raw
    from ml_engine.locations import detect_locations

    total = {}
    for symbol in symbols:
        for tf in cfg.timeframes:
            key = f"{symbol}_{tf}"
            try:
                df = load_raw(symbol, tf)
                locs = detect_locations(df, tf, cfg)
                kind_counts = {}
                for loc in locs:
                    kind_counts[loc.kind] = kind_counts.get(loc.kind, 0) + 1
                total[key] = {"count": len(locs), "kinds": kind_counts}
                logger.info("%s %s: %d locations %s", symbol, tf, len(locs), kind_counts)
            except Exception as exc:
                total[key] = {"count": 0, "error": str(exc)}
                logger.error("%s %s: %s", symbol, tf, exc)
    return total


def stage_decide(cfg: EngineConfig, symbols: list[str], model_type: str) -> dict:
    """Run full decision pipeline: features -> locations -> regime -> setups -> scorer -> decide."""
    import pickle

    from ml_engine.data.ingest import load_raw
    from ml_engine.decision import decide
    from ml_engine.features.build import build_features
    from ml_engine.features.regime import classify_regimes
    from ml_engine.locations import detect_locations
    from ml_engine.models.scorer import MultiTargetScorer
    from ml_engine.setups.composer import compose_setups

    # Load trained scorer
    model_path = DATASET_VERSION / f"scorer_{model_type}.pkl"
    if model_path.exists():
        with open(model_path, "rb") as f:
            scorer = pickle.load(f)
    else:
        logger.warning("No trained scorer at %s, using dummy", model_path)
        scorer = MultiTargetScorer(model_type="rf", calibrate=False)

    decisions = {}
    for symbol in symbols:
        tf = cfg.base_timeframe
        try:
            df = load_raw(symbol, tf)
            feats = build_features(df, cfg)
            detect_locations(df, tf, cfg)
            classify_regimes(feats, cfg.regime)
            setups = compose_setups(feats, symbol, tf, cfg)

            for s in setups:
                fv = [feats[c].iloc[s.index] for c in feats.columns if c.startswith("feat_")]
                X = np.array([fv], dtype=float)
                if hasattr(scorer, "predict"):
                    results = scorer.predict(X)
                    ml = results[0] if results else None
                else:
                    from ml_engine.models.scorer import ScorerResult
                    ml = ScorerResult(p_1r=0.5, p_2r=0.25, p_3r=0.1,
                                      expected_r=-0.25, base_rate_1r=0.5,
                                      base_rate_2r=0.25, base_rate_3r=0.1)
                d = decide(s, ml, cfg, account_balance=10000)
                decisions[s.setup_id] = d.to_dict()
                if d.kind == "TRADE":
                    logger.info("TRADE %s %s %s entry=%.5f stop=%.5f target=%.5f E[R]=%.3f",
                                symbol, s.direction, s.definition, s.entry, s.stop, s.target,
                                ml.expected_r)
        except Exception as exc:
            logger.error("%s: %s", symbol, exc)

    logger.info("decisions: %d total, %d TRADE, %d NO_TRADE",
                len(decisions),
                sum(1 for d in decisions.values() if d["kind"] == "TRADE"),
                sum(1 for d in decisions.values() if d["kind"] == "NO_TRADE"))
    return decisions


def stage_manage(cfg: EngineConfig, symbols: list[str]) -> dict:
    """Run management simulation on recent setups."""
    from ml_engine.backtest.management import management_summary, simulate_management
    from ml_engine.data.ingest import load_raw
    from ml_engine.features.build import build_features
    from ml_engine.setups.composer import compose_setups

    all_results = []
    for symbol in symbols:
        tf = cfg.base_timeframe
        try:
            df = load_raw(symbol, tf)
            feats = build_features(df, cfg)
            setups = compose_setups(feats, symbol, tf, cfg)
            high = df["high"].to_numpy(float)
            low = df["low"].to_numpy(float)
            close = df["close"].to_numpy(float)
            atr = feats["feat_atr"].to_numpy(float) if "feat_atr" in feats.columns else np.ones(len(df)) * 0.001

            for s in setups[-50:]:  # last 50 setups
                results = simulate_management(s, high, low, close, atr)
                all_results.extend(results)
        except Exception as exc:
            logger.error("%s: %s", symbol, exc)

    summary = management_summary(all_results)
    logger.info("management summary: %s", json.dumps(summary, indent=2))
    return summary


def stage_journal(cfg: EngineConfig, symbols: list[str]) -> dict:
    """Show journal summary."""
    from ml_engine.journal import TradeJournal

    j = TradeJournal()
    summary = j.summary()
    j.close()
    logger.info("journal: %s", json.dumps(summary, indent=2))
    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(prog="ml_engine")
    parser.add_argument("stage", choices=[
        "ingest", "features", "setups", "dataset", "train", "report",
        "locations", "decide", "manage", "journal", "run-all"])
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--model", default="lgbm", choices=["lr", "rf", "lgbm", "xgb"])
    parser.add_argument("--target", default="win_2r")
    args = parser.parse_args(argv)

    cfg = DEFAULT_CONFIG
    symbols = args.symbols or list(cfg.symbols)

    if args.stage == "ingest":
        stage_ingest(cfg, symbols, args.refresh)
    elif args.stage == "features":
        stage_features(cfg, symbols)
    elif args.stage == "setups":
        stage_setups(cfg, symbols)
    elif args.stage == "dataset":
        dataset, dhash = stage_dataset(cfg, symbols)
        print(f"dataset rows={len(dataset)} hash={dhash}")
    elif args.stage == "train":
        result = stage_train(cfg, symbols, args.model, args.target)
        print(json.dumps(result.get("oos", {}), indent=2, default=str))
    elif args.stage == "report":
        for exp in stage_report():
            print(json.dumps(exp, indent=2, default=str))
    elif args.stage == "locations":
        result = stage_locations(cfg, symbols)
        print(json.dumps(result, indent=2, default=str))
    elif args.stage == "decide":
        result = stage_decide(cfg, symbols, args.model)
        print(json.dumps(result, indent=2, default=str))
    elif args.stage == "manage":
        result = stage_manage(cfg, symbols)
        print(json.dumps(result, indent=2, default=str))
    elif args.stage == "journal":
        result = stage_journal(cfg, symbols)
        print(json.dumps(result, indent=2, default=str))
    elif args.stage == "run-all":
        t0 = time.time()
        stage_ingest(cfg, symbols, False)
        stage_features(cfg, symbols)
        stage_setups(cfg, symbols)
        result = stage_train(cfg, symbols, args.model, args.target)
        print("=== OOS SUMMARY ===")
        print(json.dumps(result.get("oos", {}), indent=2, default=str))
        logger.info("run-all completed in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
