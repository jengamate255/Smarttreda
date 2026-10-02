"""C: drive guard — measure, clean safe caches, alert when critically low.

C: chronically sits at ~0.3 GB free (140 GB used) and breaks caches/copies.
Safe cleanup targets (all re-downloadable / regenerable):
  - uv cache          (uv cache clean)
  - pip cache         (pip cache purge)
  - %TEMP% files older than 7 days (best effort, skips locked)
  - MT5 history caches (*.hst/*.chr) older than 90 days (best effort, skips
    files the live terminal has open)
Report-only: MetaQuotes bulk, opencode snapshot store (rescue git DB - never touch).

Usage:
  python -X utf8 mt5_mcp\\disk_guard.py            # clean + alert if <1 GB free
  python -X utf8 mt5_mcp\\disk_guard.py --report   # no cleanup, print sizes
Exit code 2 when free space stays below ALERT_FREE_GB after cleanup.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("mt5_mcp.disk_guard")

ALERT_FREE_GB = 1.0
TEMP_MAX_AGE_DAYS = 7
MT5_HISTORY_MAX_AGE_DAYS = 90

UV_CACHE = Path(os.environ.get("LOCALAPPDATA", "")) / "uv"
PIP_CACHE = Path(os.environ.get("LOCALAPPDATA", "")) / "pip" / "Cache"
TEMP_DIR = Path(os.environ.get("TEMP", ""))
METAOQUOTES = Path(os.environ.get("APPDATA", "")) / "MetaQuotes"
OPENCODE_SHARE = Path(os.environ.get("USERPROFILE", "")) / ".local" / "share" / "opencode"


def _dir_size(path: Path) -> float:
    total = 0
    try:
        for f in path.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def free_gb() -> float:
    return shutil.disk_usage("C:\\").free / (1024**3)


def _run(cmd: list[str]) -> bool:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            logger.warning("%s -> rc=%s %s", cmd, r.returncode, (r.stderr or "")[:200])
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s failed: %s", cmd, exc)
        return False


def clean_temp(older_than_days: int = TEMP_MAX_AGE_DAYS) -> int:
    removed, freed = 0, 0
    cutoff = time.time() - older_than_days * 86400
    if not TEMP_DIR.is_dir():
        return 0
    for entry in TEMP_DIR.iterdir():
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir():
                sz = _dir_size(entry)
                shutil.rmtree(entry, ignore_errors=True)
            else:
                sz = entry.stat().st_size
                entry.unlink(missing_ok=True)
                freed += sz
            removed += 1
        except OSError:
            continue
    logger.info("temp: removed %d entries (~%.0f MB)", removed, freed / 1e6)
    return removed


def clean_mt5_history(older_than_days: int = MT5_HISTORY_MAX_AGE_DAYS) -> int:
    """Delete old MT5 downloaded-history files. Locked/in-use files are skipped
    by Windows (best effort); the terminal re-downloads whatever it still needs."""
    removed = 0
    cutoff = time.time() - older_than_days * 86400
    if not METAOQUOTES.is_dir():
        return 0
    for pattern in ("**/*.hst", "**/*.chr"):
        for f in METAOQUOTES.glob(pattern):
            try:
                if f.stat().st_mtime >= cutoff:
                    continue
                f.unlink()
                removed += 1
            except OSError:
                continue
    logger.info("mt5 history: removed %d files older than %dd", removed, older_than_days)
    return removed


def sizes() -> dict[str, float]:
    return {
        "uv_cache_gb": round(_dir_size(UV_CACHE) / 1e9, 2),
        "pip_cache_gb": round(_dir_size(PIP_CACHE) / 1e9, 2),
        "temp_gb": round(_dir_size(TEMP_DIR) / 1e9, 2),
        "metaquotes_gb": round(_dir_size(METAOQUOTES) / 1e9, 2),
        "opencode_share_gb": round(_dir_size(OPENCODE_SHARE) / 1e9, 2),
    }


def run(do_clean: bool = True) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    before = free_gb()
    sizes_before = sizes() if not do_clean else None

    if do_clean:
        _run([sys.executable, "-m", "pip", "cache", "purge"])
        uv = shutil.which("uv")
        if uv:
            _run([uv, "cache", "clean"])
        clean_temp()
        clean_mt5_history()

    after = free_gb()
    cleaned = sizes() if not do_clean else None
    line = f"C: free {before:.2f} -> {after:.2f} GB"
    logger.info(line)

    if after < ALERT_FREE_GB:
        detail = ""
        if do_clean:
            s = sizes()
            detail = (
                "\nmetaquotes=%.1fGB opencode=%.1fGB uv=%.1fGB temp=%.1fGB"
                % (s["metaquotes_gb"], s["opencode_share_gb"], s["uv_cache_gb"], s["temp_gb"])
            )
        try:
            from mt5_mcp.notify import notify
            notify(
                "disk_guard",
                "C: drive CRITICALLY LOW: %.2f GB free after cleanup (alert < %.1f).%s"
                % (after, ALERT_FREE_GB, detail),
                force=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("alert failed: %s", exc)
        return 2

    if sizes_before or cleaned:
        logger.info("sizes: %s", sizes_before or cleaned)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", action="store_true", help="measure only, no cleanup")
    args = p.parse_args()
    if args.report:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        for k, v in sizes().items():
            logger.info("%-22s %.2f GB", k, v)
        logger.info("C: free %.2f GB", free_gb())
        return 0
    return run(do_clean=True)


if __name__ == "__main__":
    raise SystemExit(main())
