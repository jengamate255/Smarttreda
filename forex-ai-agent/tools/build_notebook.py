"""Regenerates notebooks/forex_agent_colab.ipynb with strict JSON escaping."""
import json

MD1 = """# Forex AI Agent - Google Colab Training

Trains the supervised models (setup classifier, direction, TP-before-SL outcome) on MT5 historical data collected on your Windows machine.

**Workflow:** Local `upload_dataset.py` -> this notebook (train) -> local `download_model.py`.

No credentials are hard-coded. Optional values come from Colab Secrets (key=value pairs) or environment variables."""

C1 = """# 1. Install dependencies (mirrors requirements.txt)
!pip install -q pandas numpy pyarrow scikit-learn lightgbm xgboost optuna joblib PyYAML matplotlib
# torch is preinstalled on Colab; ensure it exists
try:
    import torch
except ImportError:
    !pip install -q torch
print('setup done')"""

C2 = """# 2. Clone the repository (idempotent)
import os
import subprocess

REPO_URL = os.environ.get('FAA_REPO', 'https://github.com/yourname/forex-ai-agent.git')
if not os.path.isdir('forex-ai-agent'):
    subprocess.run(['git', 'clone', '--depth', '1', REPO_URL], check=True)
os.chdir('forex-ai-agent')
print('repo ready:', os.getcwd())"""

C3 = """# 3. Optionally mount Google Drive for dataset/models
MOUNT_DRIVE = os.environ.get('FAA_MOUNT_DRIVE', '1') == '1'
if MOUNT_DRIVE:
    from google.colab import drive
    drive.mount('/content/drive')
    print('Drive mounted at /content/drive')
else:
    print('Drive mount skipped')

SYMBOL = os.environ.get('FAA_SYMBOL', 'EURUSD')
TIMEFRAME = os.environ.get('FAA_TIMEFRAME', 'M15')
DATA_DIR = '/content/forex-ai-agent/data'
DRIVE_DATA = '/content/drive/MyDrive/forex_ai_data' if MOUNT_DRIVE else None
DRIVE_MODELS = '/content/drive/MyDrive/forex_ai_models' if MOUNT_DRIVE else None
print(f'SYMBOL={SYMBOL} TIMEFRAME={TIMEFRAME}')"""

C4 = """# 4. Download / select the dataset
# Preferred: zip produced by scripts/upload_dataset.py (features_EURUSD_M15.zip).
# Option A: pull from Drive
import glob, shutil, zipfile
from pathlib import Path

zip_sources = []
if DRIVE_DATA:
    zip_sources += glob.glob(f'{DRIVE_DATA}/*.zip')
zip_sources += glob.glob('*.zip') + glob.glob('/content/*.zip')

target_zip = None
for z in zip_sources:
    if f'_{SYMBOL}_' in z and f'_{TIMEFRAME}.' in z:
        target_zip = z
        break
if target_zip is None:
    print('No matching zip found. Upload it with:')
    print('  from google.colab import files; files.upload()')
    raise SystemExit('dataset zip required')

kind = 'features' if 'features_' in Path(target_zip).name else 'raw'
dest = Path(DATA_DIR) / kind / SYMBOL / TIMEFRAME
dest.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(target_zip) as zf:
    zf.extractall(dest)
print(f'unzipped {target_zip} -> {dest}')"""

C5 = """# 5. Verify dataset integrity
from src.data.storage import read_partition, integrity_report, metadata_exists
from src.config import Config

cfg = Config()
assert metadata_exists(cfg, kind, SYMBOL, TIMEFRAME), 'metadata missing - bad zip?'
dataset_df = read_partition(cfg, kind, SYMBOL, TIMEFRAME)
print('rows:', len(dataset_df), 'range:', dataset_df['time'].min(), '->', dataset_df['time'].max())
report = integrity_report(dataset_df, ['time', 'open', 'high', 'low', 'close'])
print('integrity:', report)
assert not report['fatal'], 'dataset failed integrity checks - do not train on it'
print('integrity OK')"""

C6 = """# 6. Build features if only raw data was uploaded (skip if a features zip was used)
if kind == 'raw':
    from src.features.pipeline import attach_labels, build_features
    from src.data.storage import read_partition, write_partition
    raw = read_partition(cfg, 'raw', SYMBOL, TIMEFRAME)
    htf_frames = {}
    for htf in cfg.get('features.htf_context', {}).get(TIMEFRAME, {}):
        try:
            htf_frames[htf] = read_partition(cfg, 'raw', SYMBOL, htf)
        except FileNotFoundError:
            pass
    feats = build_features(raw, cfg=cfg.data, htf_frames=htf_frames, base_timeframe=TIMEFRAME)
    feats = attach_labels(feats, raw, cfg=cfg.data, symbol=SYMBOL)
    write_partition(feats, cfg, 'features', SYMBOL, TIMEFRAME)
    print('features built:', feats.shape)
else:
    print('features dataset already present')"""

C7 = """# 7. Train LightGBM (Model A: setup, Model B: direction, Model C: TP-before-SL)
import numpy as np
from src.data.storage import read_partition, dataset_version
from src.ml.train import date_splits, feature_columns, prepare_targets, train_lightgbm

features = read_partition(cfg, 'features', SYMBOL, TIMEFRAME)
splits = date_splits(features, cfg)
X = features[feature_columns(features, cfg)].reset_index(drop=True)
targets = prepare_targets(features, cfg, int(cfg.get('labels.primary_rr', 3)))
rr = int(cfg.get('labels.primary_rr', 3))

models = {}
for task, key, objective in [
    ('setup_classifier', 'setup', 'binary'),
    ('direction', 'direction', 'multiclass'),
    ('outcome_probability', 'outcome', 'binary'),
]:
    if objective == 'multiclass':
        models[task] = train_lightgbm(X, targets[key], splits['train'], splits['validation'],
                                      objective='multiclass', num_class=3)
    else:
        models[task] = train_lightgbm(X, targets[key], splits['train'], splits['validation'],
                                      objective='binary')
    print(f'{task}: trained')
print('LightGBM done. train={} val={} test={} rows'.format(
    len(splits['train']), len(splits['validation']), len(splits['test'])))"""

C8 = """# 8. Train XGBoost (same targets)
from src.ml.train import train_xgboost

xg_models = {}
for task, key in [('setup_classifier', 'setup'), ('direction', 'direction'),
                  ('outcome_probability', 'outcome')]:
    if task == 'direction':
        xg_models[task] = train_xgboost(X, targets[key], splits['train'], splits['validation'],
                                        objective='multi:softprob', num_class=3)
    else:
        xg_models[task] = train_xgboost(X, targets[key], splits['train'], splits['validation'],
                                        objective='binary:logistic')
    print(f'{task}: trained')
print('XGBoost done')"""

C9 = """# 9. Optional PyTorch MLP (CPU fallback; CUDA auto-detected)
import torch
print('CUDA available:', torch.cuda.is_available())

TRAIN_TORCH = os.environ.get('FAA_TORCH', '1') == '1'
torch_models = {}
if TRAIN_TORCH:
    from src.ml.train import train_torch
    for task, key in [('setup_classifier', 'setup'), ('outcome_probability', 'outcome')]:
        torch_models[task] = train_torch(X, targets[key], splits['train'], splits['validation'],
                                         2, cfg.get('model.torch', {}))
        print(f'torch {task}: trained')
else:
    print('torch training skipped (FAA_TORCH=0)')"""

C10 = """# 10. Optuna hyperparameter optimization (LightGBM, direction task)
import optuna
import lightgbm as lgb

N_TRIALS = int(os.environ.get('FAA_TRIALS', '20'))
y_dir = targets['direction']


def objective(trial):
    params = {
        'objective': 'multiclass', 'num_class': 3, 'metric': 'multi_logloss',
        'verbose': -1, 'seed': 42, 'n_jobs': -1,
        'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.2, log=True),
        'num_leaves': trial.suggest_int('num_leaves', 16, 256),
        'min_child_samples': trial.suggest_int('min_child_samples', 20, 200),
        'feature_fraction': trial.suggest_float('feature_fraction', 0.5, 1.0),
        'bagging_fraction': trial.suggest_float('bagging_fraction', 0.5, 1.0),
    }
    dtr = lgb.Dataset(X.iloc[splits['train']], label=y_dir[splits['train']])
    dval = lgb.Dataset(X.iloc[splits['validation']], label=y_dir[splits['validation']])
    m = lgb.train(params, dtr, num_boost_round=300, valid_sets=[dval],
                  callbacks=[lgb.early_stopping(30)])
    proba = m.predict(X.iloc[splits['validation']])
    logloss = -float(np.mean([np.log(proba[i, y_dir[splits['validation'][i]]])
                              for i in range(len(splits['validation']))]))
    return logloss


study = optuna.create_study(direction='minimize')
study.optimize(objective, n_trials=N_TRIALS, show_progress_bar=False)
print('best params:', study.best_params, 'best logloss:', study.best_value)
optuna_params = study.best_params"""

C11 = """# 11. Evaluate models on the held-out test split
import json
import numpy as np
from src.ml.evaluate import evaluate_classifier, expected_value_table, realized_r_from_labels

label = features[f'label_rr{rr}'].to_numpy()
r_targets = realized_r_from_labels(label, rr)
report = {}


def eval_models(mdict, backend):
    out = {}
    for task, model in mdict.items():
        try:
            proba = model.predict_proba(X.iloc[splits['test']])
        except Exception:
            import xgboost as xgb
            proba = model.predict(xgb.DMatrix(X.iloc[splits['test']]))
            if proba.ndim == 1:
                proba = np.column_stack([1 - proba, proba])
        classes = [0, 1, 2] if task == 'direction' else [0, 1]
        m = evaluate_classifier(targets[task][splits['test']], proba, classes, 'test')
        out[task] = m
        print(f'{backend}/{task}: acc={m["accuracy"]:.4f} auc={m.get("auc")} logloss={m.get("logloss")}')
    return out


report['lightgbm'] = eval_models(models, 'lightgbm')
report['xgboost'] = eval_models(xg_models, 'xgboost')
if TRAIN_TORCH:
    report['torch'] = eval_models(torch_models, 'torch')

p_out = models['outcome_probability'].predict_proba(X.iloc[splits['test']])[:, 1]
y_out = targets['outcome'][splits['test']]
report['lightgbm']['outcome_probability']['expected_value'] = expected_value_table(
    p_out, y_out, r_targets[splits['test']], [0.5, 0.6, 0.65, 0.7, 0.75, 0.8])
print(json.dumps(report, indent=2, default=str))"""

C12 = """# 12. Walk-forward test (expanding window retraining + out-of-sample backtest)
from src.backtest.walkforward import walk_forward_backtest

wf = walk_forward_backtest(features, cfg, SYMBOL, TIMEFRAME,
                           {'backend': 'lightgbm'},
                           chunk_months=int(os.environ.get('FAA_CHUNK_MONTHS', '6')))
report['walk_forward'] = wf
print('walk-forward metrics:', json.dumps(wf['metrics'], indent=2))"""

C13 = """# 13. Save models into the registry
from src.ml import model_registry


def persist(mdict, backend):
    for task, model in mdict.items():
        meta = {
            'algorithm': backend,
            'feature_version': cfg.get('project.feature_version'),
            'dataset_version': dataset_version(cfg, 'features', SYMBOL, TIMEFRAME),
            'training_period': f"{cfg.get('model.splits.train_start')}..{cfg.get('model.splits.train_end')}",
            'validation_period': f"{cfg.get('model.splits.validation_start')}..{cfg.get('model.splits.validation_end')}",
            'test_period': f"{cfg.get('model.splits.test_start')}..{cfg.get('model.splits.test_end')}",
            'hyperparameters': optuna_params if backend == 'lightgbm' else {},
            'feature_columns': feature_columns(features, cfg),
        }
        metrics = {'test': report[backend][task]}
        if backend == 'lightgbm':
            metrics['walk_forward'] = wf['metrics']
        v = model_registry.save_model(cfg, SYMBOL, TIMEFRAME, task, model, meta, metrics)
        print(f'{backend}/{task} -> {v}')


persist(models, 'lightgbm')
persist(xg_models, 'xgboost')
if TRAIN_TORCH:
    persist(torch_models, 'torch_mlp')"""

C14 = """# 14. Save metrics + experiment log
import json
import datetime
from pathlib import Path

experiment = {
    'experiment_id': datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S'),
    'symbol': SYMBOL,
    'timeframe': TIMEFRAME,
    'dataset_version': dataset_version(cfg, 'features', SYMBOL, TIMEFRAME),
    'feature_version': cfg.get('project.feature_version'),
    'splits': cfg.get('model.splits'),
    'metrics': report,
}
Path('exports').mkdir(exist_ok=True)
with open(f'exports/experiment_{experiment["experiment_id"]}.json', 'w') as fh:
    json.dump(experiment, fh, indent=2, default=str)
print('experiment logged:', experiment['experiment_id'])"""

C15 = """# 15. Export artifacts (models + experiment log) and make them downloadable
import shutil

export_name = f'models_{SYMBOL}_{TIMEFRAME}.zip'
shutil.make_archive(f'exports/{export_name[:-4]}', 'zip', 'models')
if DRIVE_MODELS:
    Path(DRIVE_MODELS).mkdir(parents=True, exist_ok=True)
    shutil.copy(f'exports/{export_name}', DRIVE_MODELS)
    print(f'copied to Drive: {DRIVE_MODELS}/{export_name}')
print('local export:', f'exports/{export_name}')
from google.colab import files
files.download(f'exports/{export_name}')"""

MD2 = """## Back on Windows

```bash
python scripts/download_model.py --source gdrive --zip <file_id>   # or --source local --zip models_EURUSD_M15.zip
python -m src.cli evaluate --symbol EURUSD --timeframe M15
python -m src.cli backtest --symbol EURUSD --timeframe M15 --ml
python -m src.cli paper --symbol EURUSD --timeframe M15
```

The quantitative models decide; the LLM layer (agent/llm_interface.py) only explains. Never set `execution.mode: LIVE` without `MT5_ALLOW_LIVE=1`."""

cells = []
for src, kind in [
    (MD1, "markdown"), (C1, "code"), (C2, "code"), (C3, "code"), (C4, "code"),
    (C5, "code"), (C6, "code"), (C7, "code"), (C8, "code"), (C9, "code"),
    (C10, "code"), (C11, "code"), (C12, "code"), (C13, "code"), (C14, "code"),
    (C15, "code"), (MD2, "markdown"),
]:
    cell = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    cells.append(cell)

nb = {
    "cells": cells,
    "metadata": {
        "accelerator": "GPU",
        "colab": {"provenance": []},
        "kernelspec": {"display_name": "Python 3", "name": "python3"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = "notebooks/forex_agent_colab.ipynb"
with open(out, "w", encoding="utf-8") as fh:
    json.dump(nb, fh, indent=1, ensure_ascii=False)

import json as j
with open(out, encoding="utf-8") as fh:
    j.load(fh)
print(f"wrote strict-JSON notebook: {out}")
