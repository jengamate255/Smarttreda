"""Serve the Trading AI API with file logging.

Used by service_watchdog.py so the API survives tool-shell/session exits:
    python -u trading_ai\\tools\\serve.py

Logs to logs/trading_ai_api.log. Port comes from TRADING_AI_PORT (default 8010).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log_dir = ROOT / "logs"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "trading_ai_api.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("trading_ai.serve")

PORT = int(os.getenv("TRADING_AI_PORT", "8010"))

if __name__ == "__main__":
    import uvicorn

    logger.info("starting Trading AI API on http://127.0.0.1:%d (root=%s)", PORT, ROOT)
    uvicorn.run(
        "trading_ai.api.app:app",
        host="127.0.0.1",
        port=PORT,
        log_level="info",
        log_config=None,
    )
