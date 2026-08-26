"""Download trained models from Google Drive or GitHub Releases into models/.

    python scripts/download_model.py --symbol EURUSD --timeframe M15 \
        --source gdrive --zip /path/or/drive/id/or/url/to/models_zip

Sources:
  gdrive  — a zip produced by the Colab notebook's export step, downloaded via
            `gdrive download <id>` or copied via rclone.
  release — GitHub Releases artifact (uses `gh` CLI if present, else prints the
            download URL).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Config, PROJECT_ROOT  # noqa: E402


def _unzip_models(zip_path: Path) -> None:
    models_root = PROJECT_ROOT / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(models_root)
    print(f"extracted models into {models_root}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["gdrive", "release", "local"], required=True)
    parser.add_argument("--zip", default=None, help="local zip, drive file id, or release tag/url")
    parser.add_argument("--repo", default="yourname/forex-ai-agent", help="owner/repo for releases")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    Config.from_file(args.config) if args.config else Config()

    if args.source == "local":
        if not args.zip:
            print("--zip <local zip path> required for local source")
            return 1
        _unzip_models(Path(args.zip))
        return 0

    if args.source == "release":
        if shutil.which("gh"):
            subprocess.run(
                ["gh", "release", "download", "--repo", args.repo, "--pattern", "*.zip",
                 "--dir", str(PROJECT_ROOT / "exports")],
                check=True,
            )
            for z in (PROJECT_ROOT / "exports").glob("*.zip"):
                _unzip_models(z)
            return 0
        print(f"gh CLI not found. Download the model zip from the release page and run:\n"
              f"  python scripts/download_model.py --source local --zip path/to/models.zip")
        return 1

    # gdrive
    method = os.environ.get("GDRIVE_UPLOAD_METHOD", "gdrive").lower()
    file_id = args.zip
    if not file_id:
        print("--zip <drive file id> required for gdrive source")
        return 1
    dest = PROJECT_ROOT / "exports" / f"models_{file_id}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("gdrive"):
        subprocess.run(["gdrive", "download", "--id", file_id, "--path", str(dest.parent)], check=True)
        _unzip_models(dest)
    elif shutil.which("rclone"):
        remote = os.environ.get("GDRIVE_REMOTE", "gdrive")
        subprocess.run(["rclone", "copyto", f"{remote}:{file_id}", str(dest)], check=True)
        _unzip_models(dest)
    else:
        print("Neither gdrive nor rclone installed. Download manually and use --source local.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
