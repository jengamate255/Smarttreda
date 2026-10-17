"""Comprehensive feature verification test."""
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd
from trading_ai.data.generator import SyntheticDataGenerator
from trading_ai.features.engine import FeatureEngine

def main():
    t0 = time.time()
    gen = SyntheticDataGenerator(seed=42)
    df = gen.generate("EURUSD", "H1", initial_price=1.1000)
    t_gen = time.time() - t0
    print(f"[1] Generator: {len(df)} H1 candles in {t_gen:.2f}s")

    # --- FeatureEngine ---
    t0 = time.time()
    fe = FeatureEngine()
    features = fe.compute(df)
    t_feat = time.time() - t0
    print(f"[2] FeatureEngine: {features.shape[1]} features x {features.shape[0]} bars in {t_feat:.2f}s")

    print(f"    Columns: {list(features.columns)}")
    print()
    print("    Per-column stats:")
    for col in features.columns:
        s = features[col]
        if s.dtype == object:
            uniq = s.dropna().unique()[:5]
            print(f"      {col:25s}  strings  sample={list(uniq)}")
        else:
            n_nan = s.isna().sum()
            print(f"      {col:25s}  NaN={n_nan:5d} ({n_nan/len(s):5.1%})  min={s.min():.6g}  max={s.max():.6g}")

    # NaN breakdown
    sr_cols = ["dist_to_sr", "dist_to_zone"]
    roc_cols = ["roc_5", "roc_10", "roc_20"]
    sr_nan = features[sr_cols].isna().sum().sum()
    roc_nan = features[roc_cols].isna().sum().sum()
    other_nan = int(features.isna().sum().sum()) - sr_nan - roc_nan
    print()
    print(f"    NaN breakdown: structure={sr_nan} (no S/R configured, EXPECTED), "
          f"roc_warmup={roc_nan} (EXPECTED), other={other_nan} (should be 0)")

    # --- Look-ahead bias test ---
    print()
    print("[3] Look-ahead bias test:")
    f1 = fe.compute(df)

    df2 = df.copy()
    df2.iloc[-1, df2.columns.get_loc("close")] = 999.99
    df2.iloc[-1, df2.columns.get_loc("high")] = 999.99
    f2 = fe.compute(df2)
    diff_last = (f1.iloc[:-1].fillna(-999) != f2.iloc[:-1].fillna(-999)).any(axis=1).sum()
    print(f"    Rows before last changed by corrupting last candle: {diff_last} (must be 0)")

    df3 = df.copy()
    mid = len(df3) // 2
    df3.iloc[mid, df3.columns.get_loc("close")] = 999.99
    f3 = fe.compute(df3)
    diff_mid = (f1.iloc[:mid].fillna(-999) != f3.iloc[:mid].fillna(-999)).any(axis=1).sum()
    print(f"    Rows before midpoint changed by corrupting midpoint: {diff_mid} (must be 0)")

    look_ahead_ok = diff_last == 0 and diff_mid == 0
    print(f"    Result: {'PASS' if look_ahead_ok else 'FAIL'}")

    # --- Feature values sanity ---
    print()
    print("[4] Feature value sanity:")
    br = features["body_ratio"]
    assert (br >= 0).all() and (br <= 1).all(), "body_ratio out of [0,1]"
    print(f"    body_ratio in [0,1]: PASS (min={br.min():.3f}, max={br.max():.3f})")

    uw = features["upper_wick_ratio"]
    lw = features["lower_wick_ratio"]
    assert (uw >= 0).all() and (lw >= 0).all(), "wick ratio negative"
    print(f"    wick ratios >= 0: PASS")

    atr = features["atr"]
    assert (atr >= 0).all(), "ATR negative"
    print(f"    ATR >= 0: PASS (mean={atr.mean():.6f})")

    bb = features["bollinger_bandwidth"]
    assert (bb >= 0).all(), "Bollinger bandwidth negative"
    print(f"    Bollinger bandwidth >= 0: PASS")

    roc5 = features["roc_5"].dropna()
    print(f"    ROC_5 range: [{roc5.min():.4f}, {roc5.max():.4f}]")

    sess = features["session"]
    print(f"    Session labels: {sess.value_counts().to_dict()}")

    bar_r = features["bar_range"]
    assert (bar_r >= 0).all(), "bar_range negative"
    print(f"    bar_range >= 0: PASS")

    print()
    print("ALL FEATURE TESTS PASSED" if look_ahead_ok else "SOME TESTS FAILED")
    return 0 if look_ahead_ok else 1

if __name__ == "__main__":
    sys.exit(main())
