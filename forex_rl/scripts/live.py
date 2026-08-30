"""Live/paper trading with a trained RL agent.

Usage:
    # Paper trading (default)
    python scripts/live.py --model models/production/ppo_eurusd.zip

    # Dry run (log only, no orders)
    python scripts/live.py --model models/ppo_eurusd.zip --dry-run

    # Live trading (requires explicit --live flag)
    python scripts/live.py --model models/ppo_eurusd.zip --live

    # Multi-symbol paper trading
    python scripts/live.py --model models/ppo.zip --symbols EURUSD GBPUSD --timeframe H4
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live/paper trading with a trained RL agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Safety modes:
  --dry-run   Log all signals but never send orders (safest)
  (default)   Paper trading — simulates execution locally
  --live      Send real orders to MT5 (requires confirmation)
        """,
    )
    parser.add_argument(
        "--model", type=str, required=True,
        help="Path to trained model file (.zip).",
    )
    parser.add_argument(
        "--symbols", nargs="+", default=["EURUSD"],
        help="Symbols to trade (default: EURUSD).",
    )
    parser.add_argument(
        "--timeframe", type=str, default="H1",
        help="Bar timeframe (default: H1).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Dry run mode: log signals but never execute.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="LIVE trading mode: sends real orders. Use with caution!",
    )
    parser.add_argument(
        "--poll-interval", type=int, default=60,
        help="Seconds between trading iterations (default: 60).",
    )
    parser.add_argument(
        "--lookback", type=int, default=64,
        help="Observation lookback window (default: 64).",
    )
    parser.add_argument(
        "--daily-loss-limit", type=float, default=0.02,
        help="Max daily loss as fraction of equity (default: 0.02 = 2%%).",
    )
    args = parser.parse_args()

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"ERROR: Model not found: {model_path}")
        sys.exit(1)

    config = load_config()

    if args.live:
        print("\n" + "=" * 60)
        print("WARNING: LIVE TRADING MODE")
        print("=" * 60)
        print(f"  Model:  {args.model}")
        print(f"  Symbols: {', '.join(args.symbols)}")
        print(f"  TF:     {args.timeframe}")
        print("=" * 60)
        confirm = input("Type 'YES I UNDERSTAND' to proceed: ")
        if confirm != "YES I UNDERSTAND":
            print("Aborted.")
            sys.exit(0)

    mt5_cfg = {
        "terminal_path": config.get("mt5_terminal_path"),
        "login": config.get("mt5_login"),
        "password": config.get("mt5_password"),
        "server": config.get("mt5_server"),
    }
    mt5_cfg = {k: v for k, v in mt5_cfg.items() if v is not None}

    trader_config = {
        **mt5_cfg,
        "DRY_RUN": args.dry_run,
        "PAPER_TRADING": not args.live and not args.dry_run,
        "LIVE_TRADING": args.live,
        "POLL_INTERVAL": args.poll_interval,
        "SYMBOLS": args.symbols,
        "TIMEFRAME": args.timeframe,
        "AGENT_PATH": str(model_path),
        "DAILY_LOSS_LIMIT": args.daily_loss_limit,
        "RISK_PER_TRADE": config.get("risk_per_trade", 0.005),
        "LOOKBACK": args.lookback,
        "slippage_points": 10,
    }

    from forex_rl.src.execution.live_trader import LiveTrader
    trader = LiveTrader(trader_config)

    mode = "DRY RUN" if args.dry_run else ("LIVE" if args.live else "PAPER")
    print(f"\nStarting {mode} trading...")
    print(f"  Model: {model_path.name}")
    print(f"  Symbols: {', '.join(args.symbols)}")
    print(f"  Timeframe: {args.timeframe}")
    print(f"  Poll interval: {args.poll_interval}s")
    print(f"  Daily loss limit: {args.daily_loss_limit:.1%}")
    print()

    trader.run(
        agent_path=str(model_path),
        symbols=args.symbols,
        timeframe=args.timeframe,
    )


if __name__ == "__main__":
    main()
