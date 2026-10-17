"""Scheduled task runner for ML model auto-retrain.

Run via schtasks weekly (Sunday 03:00):
    schtasks /Create /TN "MT5R_RetrainML" ^
        /TR "C:\\Users\\Dave\\run_retrain_ml.bat" ^
        /SC WEEKLY /D SUN /ST 03:00 /IT /F

The .bat launcher (no-space path copy) is needed because schtasks /TR
chokes on paths with spaces.

Or directly:
    python run_retrain_ml.py
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
            os.path.join(_BASE, "logs", "retrain_ml.log"),
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("run_retrain_ml")


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_BASE, ".env"))
    except ImportError:
        pass

    logger.info("ML retrain task starting")

    try:
        from mt5_mcp.ml_ranker import retrain_with_new_data

        result = retrain_with_new_data(include_journal=True)
        logger.info("Retrain result: %s", json.dumps(result, indent=2, default=str))

        # Send Telegram notification on retrain
        if result["status"] == "retrained":
            try:
                from mt5_mcp.notify import notify
                html = (
                    "\U0001F9E0 <b>ML MODEL RETRAINED</b>\n"
                    f"trades: {result['old_n_trades']} \u2192 {result['new_n_trades']} "
                    f"(+{result['new_trades_added']})\n"
                    f"AUC: {result['old_auc']:.4f} \u2192 {result['new_auc']:.4f} "
                    f"(\u0394{result['auc_delta']:+.4f})"
                )
                if result.get("walk_forward_auc") is not None:
                    html += f"\nwalk-forward AUC: {result['walk_forward_auc']:.4f}"
                notify("ml_retrain", "Model retrained", html=html, force=True)
            except Exception as exc:
                logger.warning("Telegram notify failed: %s", exc)

        elif result["status"] == "rejected":
            logger.warning(
                "Model NOT replaced: new AUC %.4f < old AUC %.4f - tolerance",
                result["new_auc"], result["old_auc"],
            )

        return 0

    except Exception:
        logger.exception("ML retrain task failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
