"""Scheduled task runner for the weekly performance report.

Run via schtasks weekly (Sunday 20:00):
    schtasks /Create /TN "MT5R_WeeklyReport" ^
        /TR "C:\\Users\\Dave\\run_weekly_report.bat" ^
        /SC WEEKLY /D SUN /ST 20:00 /IT /F

Or directly:
    python run_weekly_report.py
"""

from __future__ import annotations

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
            os.path.join(_BASE, "logs", "weekly_report.log"),
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("run_weekly_report")


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_BASE, ".env"))
    except ImportError:
        pass

    logger.info("Weekly report task starting")

    try:
        from mt5_mcp.weekly_report import send_weekly_report
        ok = send_weekly_report(days=7)
        logger.info("Weekly report sent: %s", ok)
        return 0 if ok else 1
    except Exception:
        logger.exception("Weekly report task failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
