"""Launcher: start the synthetic watcher with updated parameters."""
import os, sys
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"
sys.argv = [
    "run_synthetic_watcher.py",
    "--live", "--interval", "15",
    "--confidence-floor", "75",
    "--max-spike-age", "15",
    "--lookback", "60",
    "--log-file", r"E:\Project\Mt5 Ea\logs\trade_watch_live.log",
]
os.chdir(r"E:\Project\Mt5 Ea")
from trade_watch import main
raise SystemExit(main())
