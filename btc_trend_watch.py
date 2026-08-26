"""BTC H4 Trend Pullback Watcher (Prop account).

Strategy: EMA50/200 trend filter with pullback entries.

* Trend filter: EMA50 vs EMA200 on H4 timeframe
* Entry: Pullback to EMA50 in direction of trend (price between EMA50 and EMA200)
* Exit: Chandelier trailing stop at 2.5x ATR from extreme
* SL: 2.0x ATR from entry
* Risk: 0.5% per trade

Default mode is dry-run. Pass ``--live`` to submit orders through compliance gate.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mt5_mcp.client import get_client, reset_client
from mt5_mcp.common import pip_size, pip_value_per_lot, round_to_step
from mt5_mcp.config import get_settings
from mt5_mcp.memory.trade_journal import get_journal
from mt5_mcp.tools.trades import open_market, pending_order
from mt5_mcp.telegram_alerts import send_telegram
from mt5_mcp.trade_events import emit as emit_event
from mt5_mcp.visual_notify import popup

BTC_SYMBOLS: tuple[str, ...] = ("BTCUSD",)
SETUP = "btc_ema_pullback"


def _logger(log_path: Path | None) -> logging.Logger:
    logger = logging.getLogger("btc_trend_watcher")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="submit accepted signals")
    parser.add_argument("--once", action="store_true", help="scan once and exit")
    parser.add_argument("--interval", type=float, default=300.0, help="seconds between scans (default 5 min)")
    parser.add_argument("--ema-fast", type=int, default=50, help="fast EMA period")
    parser.add_argument("--ema-slow", type=int, default=200, help="slow EMA period")
    parser.add_argument("--atr-period", type=int, default=14, help="ATR period")
    parser.add_argument("--sl-atr", type=float, default=2.0, help="SL distance in ATR units")
    parser.add_argument("--trail-atr", type=float, default=2.5, help="trailing stop distance in ATR units")
    parser.add_argument("--risk-percent", type=float, default=0.5, help="risk per trade %")
    parser.add_argument("--timeframe", type=str, default="H4", help="timeframe for analysis")
    parser.add_argument("--min-bars-between", type=int, default=20, help="min bars between trades on same symbol")
    parser.add_argument(
        "--log-file",
        type=Path,
        default=ROOT / "logs" / "btc_trend_live.log",
    )
    return parser.parse_args()


def _lot_size(client, symbol: str, entry: float, sl: float, budget: float) -> float:
    info = client.symbol_info(symbol)
    pip = pip_size(info)
    pip_value = pip_value_per_lot(info)
    loss_per_lot = abs(entry - sl) / pip * pip_value if pip and pip_value else 0.0
    minimum = float(info.get("volume_min", 0.01) or 0.01)
    maximum = float(info.get("volume_max", 1.0) or 1.0)
    step = float(info.get("volume_step", 0.01) or 0.01)
    raw = budget / loss_per_lot if loss_per_lot > 0 else minimum
    return round_to_step(raw, step, floor=minimum, ceil=maximum)


def _has_exposure(client, symbol: str) -> bool:
    positions = client.positions(symbol) or []
    orders = client.pending_orders(symbol) or []
    return bool(positions or orders)


def _ema(series, period: int):
    return series.ewm(span=period, adjust=False).mean()


def _atr(high, low, close, period: int = 14):
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


import pandas as pd


def _scan_symbol(
    args: argparse.Namespace,
    logger: logging.Logger,
    client,
    api_symbol: str,
    budget: float,
) -> bool:
    """Scan BTC for EMA pullback signal. Returns True if order placed."""
    try:
        # Fetch H4 data
        frame = client.rates(api_symbol, args.timeframe, 500)
        if frame is None or len(frame) < args.ema_slow + 50:
            logger.warning("%s: insufficient data", api_symbol)
            return False

        # Calculate indicators
        close = frame["close"]
        high = frame["high"]
        low = frame["low"]

        ema_fast = _ema(close, args.ema_fast)
        ema_slow = _ema(close, args.ema_slow)
        atr_series = _atr(high, low, close, args.atr_period)

        # Current values (previous completed bar)
        ema_f = ema_fast.iloc[-2]
        ema_s = ema_slow.iloc[-2]
        atr_val = atr_series.iloc[-2]
        current_price = close.iloc[-2]

        if pd.isna(ema_f) or pd.isna(ema_s) or pd.isna(atr_val) or atr_val <= 0:
            return False

        # Trend direction
        trend_up = ema_f > ema_s

        # Debug logging
        logger.debug(
            "%s: price=%.2f ema_f=%.2f ema_s=%.2f atr=%.2f trend_up=%s in_zone=%s",
            api_symbol,
            current_price,
            ema_f,
            ema_s,
            atr_val,
            trend_up,
            ema_s < current_price < ema_f if trend_up else ema_f < current_price < ema_s,
        )

        # Pullback entry zone
        if trend_up:
            # Long: price between EMA50 and EMA200
            if not (ema_s < current_price < ema_f):
                logger.debug("%s: no pullback zone (price above EMA fast)", api_symbol)
                return False
            direction = "BUY"
            entry = float(ema_f)  # enter near fast EMA
            sl = entry - atr_val * args.sl_atr
        else:
            # Short: price between EMA200 and EMA50
            if not (ema_f < current_price < ema_s):
                logger.debug("%s: no pullback zone (price below EMA fast)", api_symbol)
                return False
            direction = "SELL"
            entry = float(ema_f)
            sl = entry + atr_val * args.sl_atr

        # Check min bars between trades
        if _has_exposure(client, api_symbol):
            logger.info("%s: existing exposure, skipping", api_symbol)
            return False

        # Calculate lots
        lots = _lot_size(client, api_symbol, entry, sl, budget)
        if lots <= 0:
            return False

        logger.info(
            "%s %s signal: entry=%.2f sl=%.2f atr=%.2f lots=%.2f",
            api_symbol,
            direction,
            entry,
            sl,
            atr_val,
            lots,
        )

        if args.live:
            # Place pending order at entry level
            order_type = "buy_stop" if direction == "BUY" else "sell_stop"
            result = pending_order(
                api_symbol,
                order_type,
                lots,
                price=entry,
                sl=sl,
                tp=None,  # chandelier trail manages exit
                comment=f"{SETUP} {args.ema_fast}/{args.ema_slow} {args.timeframe}",
            )
            if result.accepted:
                logger.info("%s order placed: ticket=%s", api_symbol, result.ticket)
                return True
            else:
                logger.warning("%s order failed: %s", api_symbol, result.error)

        return False

    except Exception as exc:
        logger.exception("%s scan error: %s", api_symbol, exc)
        return False


def main() -> int:
    args = _args()
    logger = _logger(args.log_file)
    reset_client()

    # Use MT5 client for BTC (prop account)
    client = get_client("BTCUSD")
    logger.info("BTC Trend Watcher started mode=%s interval=%.0fs EMA=%d/%d",
                "LIVE" if args.live else "DRY-RUN", args.interval, args.ema_fast, args.ema_slow)

    last_trade_time = {}

    while True:
        logger.debug("Starting new scan cycle")
        try:
            client.ensure_connected()

            try:
                account = client.account_info()
                equity = float(account.get("equity", account.get("balance", 0.0)) or 0.0)
            except Exception as exc:
                logger.warning("Failed to get account info: %s, using default", exc)
                equity = 10000.0

            budget = equity * args.risk_percent / 100.0

            placed = False
            for sym in BTC_SYMBOLS:
                if placed:
                    break
                # Check min bars between trades
                last = last_trade_time.get(sym)
                if last and (time.time() - last) < args.min_bars_between * 60:
                    continue

                if _scan_symbol(args, logger, client, sym, budget):
                    last_trade_time[sym] = time.time()
                    placed = True
                    logger.info("order submitted; waiting for next cycle")
                    break

            if args.once:
                return 0

            if placed:
                logger.info("order submitted; waiting for the next scan cycle")

            logger.debug("Sleeping for %.1f seconds", args.interval)
            time.sleep(max(1.0, args.interval))
            logger.debug("Woke up from sleep")

        except KeyboardInterrupt:
            logger.info("watcher stopped")
            return 0
        except Exception as exc:
            logger.exception("watch cycle failed: %s", exc)
            time.sleep(max(5.0, args.interval))
            logger.info("Continuing after error...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())