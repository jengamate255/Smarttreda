"""Download historical data for all configured symbols/timeframes.

Usage:
    python scripts/download_data.py
    python scripts/download_data.py --symbol EURUSD --timeframe H1 --start 2018-01-01 --end 2025-12-31
    python scripts/download_data.py --all --start 2018-01-01
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
DATA_DIR = PROJECT_ROOT / "data" / "raw"


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def get_connector(config: dict):
    from forex_rl.src.execution.mt5_connector import MT5Connector
    mt5_cfg = {
        "terminal_path": config.get("mt5_terminal_path"),
        "login": config.get("mt5_login"),
        "password": config.get("mt5_password"),
        "server": config.get("mt5_server"),
    }
    mt5_cfg = {k: v for k, v in mt5_cfg.items() if v is not None}
    connector = MT5Connector(mt5_cfg)
    if not connector.connect():
        logger.error("Failed to connect to MT5: %s", connector._last_error)
        sys.exit(1)
    return connector


def download_symbol_tf(
    connector,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
) -> int:
    from forex_rl.src.data.loader import DataLoader

    loader = DataLoader(connector)
    try:
        df = loader.load_from_mt5(symbol, timeframe, start_date, end_date)
    except Exception as exc:
        logger.error("Failed to download %s %s: %s", symbol, timeframe, exc)
        return 0

    if df.empty:
        logger.warning("No data returned for %s %s", symbol, timeframe)
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{symbol}_{timeframe}.parquet"
    filepath = DATA_DIR / filename
    loader.save_to_parquet(df, filepath)
    logger.info("Saved %d bars: %s", len(df), filepath)
    return len(df)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download historical forex data from MT5",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--symbol", type=str, default=None,
        help="Single symbol to download (e.g. EURUSD). Default: all from config.",
    )
    parser.add_argument(
        "--timeframe", type=str, default=None,
        help="Single timeframe (e.g. H1). Default: all from config.",
    )
    parser.add_argument(
        "--start", type=str, default="2018-01-01",
        help="Start date YYYY-MM-DD (default: 2018-01-01).",
    )
    parser.add_argument(
        "--end", type=str, default="2025-12-31",
        help="End date YYYY-MM-DD (default: 2025-12-31).",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Download all symbols/timeframes from config.yaml.",
    )
    args = parser.parse_args()

    config = load_config()
    connector = get_connector(config)

    symbols = [args.symbol] if args.symbol else config.get("symbols", ["EURUSD"])
    timeframes = [args.timeframe] if args.timeframe else config.get("timeframes", ["H1"])

    logger.info(
        "Downloading: symbols=%s timeframes=%s start=%s end=%s",
        symbols, timeframes, args.start, args.end,
    )

    total_bars = 0
    for symbol in symbols:
        for tf in timeframes:
            n = download_symbol_tf(connector, symbol, tf, args.start, args.end)
            total_bars += n

    logger.info("Download complete: %d total bars", total_bars)
    connector.disconnect()


if __name__ == "__main__":
    main()
