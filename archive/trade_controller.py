"""Unified Trade Controller - consolidates all strategies into one dispatcher.

Replace individual strategy launches with:
    python trade_controller.py --start boom_crash
    python trade_controller.py --start forex_breakout
    python trade_controller.py --start gold_ndx_scalp
    python trade_controller.py --start ai_vision
    python trade_controller.py --status
    python trade_controller.py --stop all
"""

from __future__ import annotations

import sys
import time
import subprocess
import signal
import logging
import argparse
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mt5_mcp.memory.trade_journal import get_journal
from mt5_mcp.tools.compliance import compliance_check, compliance_status
from mt5_mcp.tools.risk import risk_report

logger = logging.getLogger("trade_controller")

# --------------------------------------------------------------------------
# Strategy registry
# --------------------------------------------------------------------------

STRATEGIES = {
    "boom_crash": {
        "script": "trade_watch.py",
        "display": "Boom/Crash Spike-Drift Watcher",
        "account": "Deriv-Demo (login 41204968)",
        "interval": 15,
        "confidence_floor": 90.0,
        "max_spike_age": 15,
        "risk_percent": 0.5,
    },
    "forex_breakout": {
        "script": "forex_breakout_watch.py",
        "display": "Forex Breakout Watcher",
        "account": "FundingPips2-SIM (login 11911448)",
        "interval": 60,
        "confidence_floor": 65.0,
        "range_bars": 24,
        "rr": 1.5,
        "risk_percent": 0.5,
    },
    "gold_ndx_scalp": {
        "script": "gold_ndx_scalp.py",
        "display": "Gold & NDX Scalper",
        "account": "FundingPips2-SIM (login 11911448)",
        "interval": "daily",
        "risk_percent": 1.0,
        "max_trades_per_day": 2,
        "daily_loss_limit": 5.0,
        "max_drawdown": 8.0,
    },
    "ai_vision": {
        "script": "ai_vision_daemon.py",
        "display": "AI Vision Daemon",
        "account": "All accounts (analysis only)",
        "interval": 15,  # minutes
    },
}


# --------------------------------------------------------------------------
# Process management
# --------------------------------------------------------------------------

_processes: dict[str, int] = {}


def is_running(strategy: str) -> bool:
    """Check if a strategy process is already running."""
    pid = _processes.get(strategy)
    if pid is None:
        return False
    # Verify process still exists
    import subprocess
    try:
        subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True)
        return True
    except Exception:
        return False


def start_strategy(name: str) -> bool:
    """Start a strategy process. Returns True if started/newly started."""
    if name not in STRATEGIES:
        logger.error(f"Unknown strategy: {name}")
        return False

    if is_running(name):
        logger.warning(f"{name} already running (PID {_processes[name]})")
        return True

    strategy = STRATEGIES[name]
    script = ROOT / strategy["script"]

    if not script.exists():
        logger.error(f"Script not found: {script}")
        return False

    # Kill any existing process for this strategy (clean restart)
    if name in _processes:
        old_pid = _processes[name]
        logger.info(f"Killing existing {name} (PID {old_pid})")
        try:
            subprocess.run(["taskkill", "/PID", str(old_pid), "/F"], capture_output=True)
            time.sleep(1)
        except Exception as e:
            logger.warning(f"Failed to kill old process: {e}")

    # Start new process
    cmd = [sys.executable, str(script)]
    # Pass --once flag for testing; remove for continuous mode
    # strategy-specific args will be added per-strategy
    proc = subprocess.Popen(cmd)

    _processes[name] = proc.pid
    logger.info(f"Started {strategy['display']} (PID {proc.pid}) on {strategy['account']}")
    return True


def stop_strategy(name: str) -> bool:
    """Stop a strategy process."""
    if name not in _processes:
        logger.warning(f"{name} not running")
        return False

    pid = _processes[name]
    logger.info(f"Stopping {name} (PID {pid})")
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(1)
        del _processes[name]
        logger.info(f"Stopped {name}")
        return True
    except Exception as e:
        logger.error(f"Failed to stop {name}: {e}")
        return False


def stop_all() -> bool:
    """Stop all running strategies."""
    for name in list(_processes.keys()):
        stop_strategy(name)
    return True


def list_strategies() -> list[dict]:
    """Return strategy information including running status."""
    result = []
    for name, cfg in STRATEGIES.items():
        result.append(
            {
                "name": name,
                "display": cfg["display"],
                "account": cfg["account"],
                "running": is_running(name),
            }
        )
    return result


# --------------------------------------------------------------------------
# Compliance & Risk Pre-Flight
# --------------------------------------------------------------------------

def check_pre_flight(strategy: str) -> dict | None:
    """Run compliance and risk checks before starting a strategy.

    Returns dict with 'allowed': True/False and 'reason' if disallowed.
    """
    strategy_cfg = STRATEGIES[strategy]
    settings = get_settings()
    risk_cfg = RiskConfig()

    # Check general trading allowed via risk report
    report = risk_report(risk_cfg=risk_cfg)
    allowed = report.get("trading_allowed", True)
    risk_info = report.get("warnings", [""])[0] if not allowed else ""
    if not allowed:
        return {"allowed": False, "reason": f"General risk limit: {risk_info}"}

    # Strategy-specific checks
    if strategy == "gold_ndx_scalp":
        # Check compliance status
        comp = compliance_status()
        if comp.get("state") == "YELLOW":
            return {
                "allowed": False,
                "reason": f"Prop compliance: {comp.get('violation', 'check config')}",
            }
        if comp.get("state") == "RED":
            return {
                "allowed": False,
                "reason": f"Prop compliance blocked: {comp.get('violation', 'daily loss limit')}",
            }

        # Check gold scalper specific limits
        daily_loss = comp.get("daily_loss_percent", 0)
        if daily_loss >= strategy_cfg.get("daily_loss_limit", 5.0):
            return {
                "allowed": False,
                "reason": f"Daily loss limit: {daily_loss:.1f}% >= {strategy_cfg['daily_loss_limit']}%",
            }

        # Check max trades per day
        trade_count = _get_scalper_trade_today_count()
        max_trades = strategy_cfg.get("max_trades_per_day", 2)
        if trade_count >= max_trades:
            return {
                "allowed": False,
                "reason": f"Trades today: {trade_count} >= max {max_trades}",
            }

        # Check overall drawdown
        overall_dd = comp.get("overall_drawdown_percent", 0)
        max_dd = strategy_cfg.get("max_drawdown", 8.0)
        if overall_dd >= max_dd:
            return {
                "allowed": False,
                "reason": f"Overall drawdown: {overall_dd:.1f}% >= {max_dd}%",
            }

    # Check max open risk
    report = risk_report()
    if report.get("max_open_risk_percent"):
        current_open_risk = report.get("open_risk_percent", 0)
        max_open_risk = risk_cfg.max_open_risk_percent
        if current_open_risk >= max_open_risk:
            return {
                "allowed": False,
                "reason": f"Open risk: {current_open_risk:.1f}% >= {max_open_risk}%",
            }

    return {"allowed": True, "reason": "All checks passed"}


def _get_scalper_trade_today_count() -> int:
    """Count trades today from journal for gold scalper compliance."""
    try:
        j = get_journal()
        # Use journal's list_trades with today's date filter
        trades = j.list_trades(limit=10000)  # large limit
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = sum(1 for t in trades if t.get("open_time", "").startswith(today))
        return count
    except Exception:
        return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unified Trade Controller - dispatch and manage all strategies"
    )
    sub = parser.add_subparsers(dest="command")

    # --- start ---
    p_start = sub.add_parser("start", help="Start a strategy")
    p_start.add_argument(
        "strategy", choices=STRATEGIES.keys(), help="Strategy to start"
    )

    # --- stop ---
    p_stop = sub.add_parser("stop", help="Stop a strategy")
    p_stop.add_argument("strategy", choices=[*STRATEGIES.keys(), "all"], help="Strategy to stop")

    # --- status ---
    p_status = sub.add_parser("status", help="Show status of all strategies")

    # --- restart ---
    p_restart = sub.add_parser("restart", help="Restart a strategy")
    p_restart.add_argument("strategy", choices=STRATEGIES.keys(), help="Strategy to restart")

    # --- list ---
    p_list = sub.add_parser("list", help="List all strategies and status")

    args = parser.parse_args()

    if args.command == "start":
        logger.info(f"Starting {args.strategy}...")
        result = start_strategy(args.strategy)
        if not result:
            print(f"❌ Failed to start {args.strategy}")
        else:
            print(f"✅ {args.strategy} started/running")

    elif args.command == "stop":
        if args.strategy == "all":
            stop_all()
            print("🛑 All strategies stopped")
        else:
            result = stop_strategy(args.strategy)
            if not result:
                print(f"❌ {args.strategy} was not running")
            else:
                print(f"⏹️ {args.strategy} stopped")

    elif args.command == "status":
        strategies = list_strategies()
        print(f"\n{'Strategy':<20} {'Status':<10} {'Account'}")
        print("-" * 55)
        for s in strategies:
            status = "RUNNING" if s["running"] else "STOPPED"
            print(f"{s['name']:<20} {status:<10} {s['account']}")

    elif args.command == "restart":
        stop_strategy(args.strategy)
        time.sleep(2)
        start_strategy(args.strategy)
        print(f"🔄 {args.strategy} restarted")

    elif args.command == "list":
        strategies = list_strategies()
        print(f"\n{'Name':<15} {'Display':<30} {'Running':<8} {'Account'}")
        print("-" * 70)
        for s in strategies:
            running = "Yes" if s["running"] else "No"
            print(f"{s['name']:<15} {s['display']:<30} {running:<8} {s['account']}")

    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    sys.exit(main())