"""Upload a dataset partition to Google Drive (or produce a zip + instructions).

Workflow:
    python scripts/upload_dataset.py --symbol EURUSD --timeframe M15 --kind features

The dataset is zipped into exports/ and, when `gdrive` or `rclone` is
available and configured, copied to your Drive folder. Otherwise the script
prints the exact Google Colab cell to mount Drive and unzip it.

No credentials are hard-coded; use env vars GDRIVE_FOLDER_ID and optionally
GDRIVE_REMOTE (rclone remote name) / GDRIVE_UPLOAD_METHOD (gdrive|rclone).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Config  # noqa: E402
from src.data.storage import zip_dataset  # noqa: E402

COLAB_MOUNT_SNIPPET = """\
# In Google Colab: mount Drive and unzip the dataset
from google.colab import drive
drive.mount('/content/drive')
!unzip -q "/content/drive/MyDrive/forex_ai_data/{zip_name}" -d "/content/forex-ai-agent/data/"
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Package and upload a dataset partition")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--kind", default="features", choices=["raw", "processed", "features"])
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = Config.from_file(args.config) if args.config else Config()
    zip_path = zip_dataset(cfg, args.kind, args.symbol, args.timeframe)
    print(f"packaged -> {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")

    method = os.environ.get("GDRIVE_UPLOAD_METHOD", "none").lower()
    folder = os.environ.get("GDRIVE_FOLDER_ID")
    if method == "gdrive" and folder:
        if not shutil.which("gdrive"):
            print("gdrive CLI not found; install from https://github.com/prasmussen/gdrive")
            return 1
        subprocess.run(["gdrive", "upload", "--parent", folder, str(zip_path)], check=True)
        print("uploaded to Google Drive.")
    elif method == "rclone":
        remote = os.environ.get("GDRIVE_REMOTE", "gdrive")
        subprocess.run(
            ["rclone", "copyto", str(zip_path), f"{remote}:{folder or ''}/{zip_path.name}"],
            check=True,
        )
        print("uploaded via rclone.")
    else:
        print("\nNo upload method configured. Choose one:")
        print("  1) pip install gdrive + GDRIVE_UPLOAD_METHOD=gdrive GDRIVE_FOLDER_ID=...")
        print("  2) rclone config + GDRIVE_UPLOAD_METHOD=rclone GDRIVE_REMOTE=gdrive")
        print("  3) Manually upload the zip to Drive (drag & drop), then in Colab run:\n")
        print(COLAB_MOUNT_SNIPPET.format(zip_name=zip_path.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
