"""Move orphan scripts to archive/."""
from pathlib import Path
import shutil

root = Path(r"E:\Project\Mt5 Ea")
archive = root / "archive"
archive.mkdir(exist_ok=True)

keep_files = {
    "web_dashboard.py", "trade_watch.py", "run_synthetic_watcher.py",
    "btc_trend_watch.py", "run_btc_watcher.py", "start_services.ps1",
    "service_watchdog.py", "backtest_boom_crash.py", "backtest_all_vol.py",
    "backtest_top6.py", "analyze_backtest.py", "requirements.txt",
    ".env", ".gitignore", "Dockerfile", "docker-compose.yml",
    "test_routing.py", "README.md", "config.json",
    "forex_breakout_watch.py", "server.py",
}

moved = 0
for pattern in ["*.py", "*.ps1"]:
    for f in root.glob(pattern):
        if f.name not in keep_files:
            dest = archive / f.name
            if not dest.exists():
                shutil.move(str(f), str(dest))
                moved += 1

py_left = len(list(root.glob("*.py")))
ps1_left = len(list(root.glob("*.ps1")))
print(f"Moved {moved} files to archive/")
print(f"Remaining root .py: {py_left}")
print(f"Remaining root .ps1: {ps1_left}")
