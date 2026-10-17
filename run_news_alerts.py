"""Scheduled task runner for news calendar Telegram alerts.

Run via schtasks every 15 minutes:
    schtasks /Create /TN "MT5R_NewsAlerts" ^
        /TR "C:\\Users\\Dave\\run_news_alerts.bat" ^
        /SC MINUTE /MO 15 /IT /F

Or directly:
    python run_news_alerts.py
"""

from __future__ import annotations

import json
import logging
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(_BASE, "logs", "news_alerts.log"),
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("run_news_alerts")


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_BASE, ".env"))
    except ImportError:
        pass

    logger.info("News alerts task starting")

    try:
        from mt5_mcp.news_alerts import check_and_alert
        result = check_and_alert()
        logger.info("News alerts result: %s", json.dumps(result, indent=2, default=str))
        return 0 if result["status"] == "ok" else 1
    except Exception:
        logger.exception("News alerts task failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
