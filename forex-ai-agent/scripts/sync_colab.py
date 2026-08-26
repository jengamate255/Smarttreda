"""Full Colab synchronization cycle:

    Local:  package dataset  ->  upload to Drive
    Colab:  (run the notebook manually — it pulls the dataset, trains, exports
            a models zip back to Drive)
    Local:  download models zip  ->  unzip into models/

Usage:
    python scripts/sync_colab.py upload   --symbol EURUSD --timeframe M15
    python scripts/sync_colab.py download --source gdrive --zip <file_id>
    python scripts/sync_colab.py status

No permanent network connection is maintained; this is a reproducible
push/pull workflow.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("upload")
    p_up.add_argument("--symbol", required=True)
    p_up.add_argument("--timeframe", required=True)
    p_up.add_argument("--kind", default="features")

    p_down = sub.add_parser("download")
    p_down.add_argument("--source", required=True, choices=["gdrive", "release", "local"])
    p_down.add_argument("--zip", default=None)
    p_down.add_argument("--repo", default="yourname/forex-ai-agent")

    sub.add_parser("status")

    args = parser.parse_args()
    base = str(Path(__file__).resolve().parents[1])

    if args.command == "upload":
        subprocess.run(
            [sys.executable, "scripts/upload_dataset.py",
             "--symbol", args.symbol, "--timeframe", args.timeframe, "--kind", args.kind],
            cwd=base, check=True,
        )
        print("\nNext: open notebooks/forex_agent_colab.ipynb in Google Colab and run all cells.")
        return 0

    if args.command == "download":
        cmd = [sys.executable, "scripts/download_model.py", "--source", args.source]
        if args.zip:
            cmd += ["--zip", args.zip]
        if args.repo:
            cmd += ["--repo", args.repo]
        subprocess.run(cmd, cwd=base, check=True)
        print("\nNext: python -m src.cli evaluate && python -m src.cli backtest --ml")
        return 0

    # status
    data_root = Path(base) / "data"
    models_root = Path(base) / "models"
    n_raw = len(list(data_root.glob("raw/*/*/year=*"))) if data_root.exists() else 0
    n_feat = len(list(data_root.glob("features/*/*/year=*"))) if data_root.exists() else 0
    n_models = len(list(models_root.glob("*/*/*/v*"))) if models_root.exists() else 0
    print(f"raw year-partitions: {n_raw}")
    print(f"feature year-partitions: {n_feat}")
    print(f"model versions: {n_models}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
