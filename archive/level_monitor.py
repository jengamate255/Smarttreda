"""Continuous level monitor for key synthetic-index levels.

Polls the web dashboard's /api/quote endpoint and fires a desktop popup +
log entry when either watched level resolves:

- Crash 300 Index: breaks above the 1,698-1,706 double-bottom neckline
  (confirmation at bid > 1,706.0, target 1,779)
- Boom 300 Index: exits the 601.6-613.5 decision range in either direction

Usage:  python level_monitor.py [--port 8050] [--interval 5]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mt5_mcp.visual_notify import popup  # noqa: E402

WATCHES = {
    "Crash 300 Index": {
        "level": 1706.0,
        "zone": "1,698-1,706 double-bottom neckline",
        "direction": "above",
        "target": "1,779",
        "label": "CRASH300 BREAKOUT",
    },
    "Boom 300 Index": {
        "level_lo": 601.6,
        "level_hi": 613.5,
        "zone": "601.6-613.5 decision range",
        "direction": "outside",
        "label": "BOOM300 RESOLUTION",
    },
}

logger = logging.getLogger("level_monitor")


def _setup_logging(log_path: Path) -> None:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def fetch_quote(port: int, symbol: str) -> float | None:
    url = (
        f"http://127.0.0.1:{port}/api/quote?symbol="
        + urllib.parse.quote(symbol)
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return float(data.get("bid"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("quote fetch failed for %s: %s", symbol, exc)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch key synthetic levels")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()

    _setup_logging(ROOT / "logs" / "level_monitor.log")

    fired: dict[str, bool] = {sym: False for sym in WATCHES}
    logger.info(
        "level monitor started | port=%s interval=%ss watches=%s",
        args.port,
        args.interval,
        ", ".join(WATCHES),
    )

    while True:
        for symbol, cfg in WATCHES.items():
            bid = fetch_quote(args.port, symbol)
            if bid is None:
                continue
            hit = False
            detail = ""
            if cfg["direction"] == "above" and bid > cfg["level"]:
                hit = True
                detail = f"bid {bid:.3f} > {cfg['level']} (zone {cfg['zone']}) -> target {cfg['target']}"
            elif cfg["direction"] == "outside" and (
                bid < cfg["level_lo"] or bid > cfg["level_hi"]
            ):
                side = "BELOW" if bid < cfg["level_lo"] else "ABOVE"
                hit = True
                detail = f"bid {bid:.3f} {side} zone {cfg['zone']}"

            if hit and not fired[symbol]:
                fired[symbol] = True
                msg = f"{cfg['label']}: {detail}"
                logger.info("TRIGGERED %s", msg)
                popup(cfg["label"], msg, 30)
            elif hit and fired[symbol]:
                logger.info("still active: %s bid=%.3f", symbol, bid)

        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
