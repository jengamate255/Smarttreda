"""Continuous forex breakout watcher (prop account, FundingPips2-SIM).

Strategy: Donchian range breakout on M15 with an H1 EMA-200 trend filter.

* The range is the highest-high/lowest-low of the last ``--range-bars``
  M15 bars (a compact consolidation window).
* A buy_stop/sell_stop is placed just beyond the range edge. If the price
  has already crossed the edge (within half an ATR of overshoot), the trade
  is filled as a market order instead.
* The H1 EMA-200 acts as a directional filter: BUY breakouts are only taken
  above it, SELL breakouts only below it.
* SL sits on the far side of the range (plus an ATR buffer), TP is a fixed
  R:R multiple of the risk.
* Supply/demand zones are a confidence bonus: a fresh strong zone hugging
  the range edge lifts the signal score.

Default mode is dry-run. Pass ``--live`` to submit orders through the normal
compliance gate (which also enforces prop-style daily limits).
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
from mt5_mcp.tools.supply_demand import detect_zones as detect_sd_zones
from mt5_mcp.tools.trades import open_market, pending_order
from mt5_mcp.telegram_alerts import send_telegram
from mt5_mcp.trade_events import emit as emit_event
from mt5_mcp.visual_notify import popup

FOREX_SYMBOLS: tuple[str, ...] = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "USDCHF",
    "XAUUSD",
)
SETUP = "forex_zone_breakout"


def _logger(log_path: Path | None) -> logging.Logger:
    logger = logging.getLogger("forex_breakout_watcher")
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
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between scans")
    parser.add_argument("--confidence-floor", type=float, default=65.0)
    parser.add_argument("--range-bars", type=int, default=24, help="bars in the M15 range window")
    parser.add_argument("--min-rr", type=float, default=1.2, help="skip breakouts offering less R:R")
    parser.add_argument("--rr", type=float, default=1.5, help="take-profit distance in R multiples")
    parser.add_argument("--risk-percent", type=float, default=0.5, help="% of equity risked per trade")
    parser.add_argument("--symbols", type=str, default="", help="comma-separated override (default: all majors)")
    parser.add_argument("--trend-tf", type=str, default="H1", help="trend filter timeframe")
    parser.add_argument("--lookback", type=int, default=400)
    parser.add_argument(
        "--log-file",
        type=Path,
        default=ROOT / "logs" / "forex_breakout.log",
    )
    return parser.parse_args()


def _has_exposure(client, symbol: str) -> bool:
    positions = client.positions(symbol) or []
    orders = client.pending_orders(symbol) or []
    return bool(positions or orders)


def _ema200_direction(client, symbol: str, timeframe: str) -> tuple[str, float | None]:
    """Return ('bull'|'bear'|'flat', ema200) for the trend timeframe.

    The SIM terminal serves thin history (gappy bars), so request a large
    count and use the last 200 closes actually returned.
    """
    frame = client.rates(symbol, timeframe, 1000)
    if frame is None or len(frame) < 220:
        return "flat", None
    closes = frame["close"].astype(float)
    ema200 = float(closes.tail(220).ewm(span=200, adjust=False).mean().iloc[-1])
    last = float(closes.iloc[-1])
    atr = _atr(frame, 14)
    if last > ema200 + 0.3 * atr:
        return "bull", ema200
    if last < ema200 - 0.3 * atr:
        return "bear", ema200
    return "flat", ema200


def _atr(frame, period: int = 14) -> float:
    import pandas as pd

    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


def _lot_size(client, symbol: str, entry: float, sl: float, budget: float) -> float:
    info = client.symbol_info(symbol)
    pip = pip_size(info)
    pip_value = pip_value_per_lot(info)
    loss_per_lot = abs(entry - sl) / pip * pip_value if pip and pip_value else 0.0
    minimum = float(info.get("volume_min", 0.01) or 0.01)
    maximum = float(info.get("volume_max", 100.0) or 100.0)
    step = float(info.get("volume_step", 0.01) or 0.01)
    raw = budget / loss_per_lot if loss_per_lot > 0 else minimum
    return round_to_step(raw, step, floor=minimum, ceil=maximum)


def _record_order(
    *,
    ticket: int,
    symbol: str,
    side: str,
    entry: float,
    sl: float,
    tp: float,
    lots: float,
    confidence: float,
    setup: dict,
) -> None:
    risk = abs(entry - sl)
    rr = abs(tp - entry) / risk if risk and tp is not None else None
    get_journal().record_trade(
        ticket=ticket,
        symbol=symbol,
        side=side,
        setup=SETUP,
        entry=entry,
        sl=sl,
        tp=tp,
        lots=lots,
        open_time=datetime.now(timezone.utc).isoformat(),
        reason=(
            f"{symbol} range breakout {side} "
            f"range {setup['range_low']:.5f}-{setup['range_high']:.5f}"
        ),
        market_conditions=(
            f"range_height={setup['range_high'] - setup['range_low']:.5f};"
            f"atr={setup.get('atr', 0):.5f};overshoot={setup.get('overshoot', 0):.5f}"
        ),
        confidence=confidence,
        rr=rr,
    )


def _find_breakout(client, logger: logging.Logger, args, symbol: str) -> dict | None:
    """Look for a tradable range breakout on one symbol.

    Returns a dict describing the trade or None.
    """
    info = client.symbol_info(symbol)
    if not info:
        logger.warning("%s: no symbol info (possibly not subscribed)", symbol)
        return None
    frame = client.rates(symbol, "M15", max(args.lookback, args.range_bars * 2 + 60))
    if frame is None or len(frame) < args.range_bars + 10:
        logger.warning("%s: not enough bars on M15", symbol)
        return None
    atr = _atr(frame, 14)
    if atr <= 0:
        return None

    # Donchian range over the trailing window (exclude the forming bar).
    window = frame.iloc[-args.range_bars - 1 : -1]
    range_high = float(window["high"].max())
    range_low = float(window["low"].min())
    range_height = range_high - range_low

    trend, ema200 = _ema200_direction(client, symbol, args.trend_tf)
    tick = client.tick(symbol)
    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", bid))

    # Zones hugging the range edges act as confirmation.
    zone_bonus = 0.0
    try:
        zones = detect_sd_zones(frame, lookback=args.lookback)
        for z in zones:
            if z.broken or z.strength < 40.0:
                continue
            if z.type == "demand" and abs(z.lower - range_low) <= 0.6 * atr:
                zone_bonus = max(zone_bonus, 8.0)
            elif z.type == "supply" and abs(z.upper - range_high) <= 0.6 * atr:
                zone_bonus = max(zone_bonus, 8.0)
    except Exception as exc:
        logger.warning("%s: zone bonus skipped: %s", symbol, exc)

    candidates = []
    buffer = 0.2 * atr
    if trend != "bear":
        entry_buy = range_high + buffer
        overshoot_buy = ask - range_high
        if overshoot_buy <= 0.5 * atr:
            sl_buy = range_low - 0.8 * atr
            risk_buy = entry_buy - sl_buy
            if risk_buy > 0:
                tp_buy = entry_buy + args.rr * risk_buy
                candidates.append(
                    {
                        "side": "BUY",
                        "entry": entry_buy,
                        "sl": sl_buy,
                        "tp": tp_buy,
                        "rr": args.rr,
                        "overshoot": overshoot_buy,
                        "bonus": zone_bonus if zone_bonus else 0.0,
                    }
                )
    if trend != "bull":
        entry_sell = range_low - buffer
        overshoot_sell = range_low - bid
        if overshoot_sell <= 0.5 * atr:
            sl_sell = range_high + 0.8 * atr
            risk_sell = sl_sell - entry_sell
            if risk_sell > 0:
                tp_sell = entry_sell - args.rr * risk_sell
                candidates.append(
                    {
                        "side": "SELL",
                        "entry": entry_sell,
                        "sl": sl_sell,
                        "tp": tp_sell,
                        "rr": args.rr,
                        "overshoot": overshoot_sell,
                        "bonus": zone_bonus if zone_bonus else 0.0,
                    }
                )
    if not candidates:
        return None

    # Confidence: range compactness (tight ranges break better), trend
    # alignment, and zone confirmation.
    compactness = min(100.0, 2.0 * atr / range_height * 100.0) if range_height > 0 else 50.0
    for cand in candidates:
        trend_align = (
            25.0
            if (cand["side"] == "BUY" and trend == "bull")
            or (cand["side"] == "SELL" and trend == "bear")
            else (12.0 if trend == "flat" else 0.0)
        )
        momentum = min(100.0, max(0.0, cand["overshoot"] / max(0.5 * atr, 1e-12) * 100.0))
        confidence = 0.45 * compactness + trend_align + 0.2 * momentum + cand["bonus"]
        cand["confidence"] = confidence
        cand["range_high"] = range_high
        cand["range_low"] = range_low
        cand["atr"] = atr

    best = max(candidates, key=lambda c: c["confidence"])
    if best["confidence"] < args.confidence_floor:
        logger.info(
            "%s: range %.5f-%.5f atr=%.6f trend=%s best=%s conf=%.1f below floor %.1f",
            symbol,
            range_low,
            range_high,
            atr,
            trend,
            best["side"],
            best["confidence"],
            args.confidence_floor,
        )
        return None
    return best


def scan_once(args: argparse.Namespace, logger: logging.Logger, client) -> bool:
    account = client.account_info()
    equity = float(account.get("equity", account.get("balance", 0.0)) or 0.0)
    budget = equity * args.risk_percent / 100.0

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        symbols = list(FOREX_SYMBOLS)

    for symbol in symbols:
        try:
            if _has_exposure(client, symbol):
                continue
            setup = _find_breakout(client, logger, args, symbol)
            if setup is None:
                continue
            entry, sl, tp = setup["entry"], setup["sl"], setup["tp"]
            lots = _lot_size(client, symbol, entry, sl, budget)
            logger.info(
                "%s qualifying: %s conf=%.1f entry=%.5f sl=%.5f tp=%.5f rr=%.2f lots=%.2f range=%.5f-%.5f",
                symbol,
                setup["side"],
                setup["confidence"],
                entry,
                sl,
                tp,
                setup["rr"],
                lots,
                setup["range_low"],
                setup["range_high"],
            )

            if not args.live:
                logger.info("%s dry-run: no order submitted", symbol)
                return False

            tick = client.tick(symbol)
            bid = float(tick.get("bid", 0.0))
            ask = float(tick.get("ask", bid))
            crossed = (setup["side"] == "BUY" and entry <= ask) or (
                setup["side"] == "SELL" and entry >= bid
            )
            if crossed:
                result = open_market(
                    symbol,
                    setup["side"],
                    lots,
                    sl=sl,
                    tp=tp,
                    comment=f"{symbol} range breakout",
                    confidence=setup["confidence"],
                )
            else:
                order_type = "buy_stop" if setup["side"] == "BUY" else "sell_stop"
                result = pending_order(
                    symbol,
                    order_type,
                    lots,
                    price=entry,
                    sl=sl,
                    tp=tp,
                    comment=f"{symbol} range breakout",
                )
            if result.accepted:
                if result.ticket:
                    _record_order(
                        ticket=int(result.ticket),
                        symbol=symbol,
                        side=setup["side"],
                        entry=float(getattr(result, "entry", entry) or entry),
                        sl=sl,
                        tp=tp,
                        lots=lots,
                        confidence=setup["confidence"],
                        setup=setup,
                    )
                logger.info(
                    "%s order placed: ticket=%s retcode=%s",
                    symbol,
                    result.ticket,
                    result.retcode,
                )
                popup(
                    f"[PROP] {setup['side']} {symbol} breakout order",
                    f"ticket #{result.ticket}\n"
                    f"lots={lots:.2f} at {entry:.5f}\n"
                    f"SL={sl:.5f} TP={tp:.5f} RR={setup['rr']:.1f}\n"
                    f"conf={setup['confidence']:.0f}% range {setup['range_low']:.5f}-{setup['range_high']:.5f}",
                )
                send_telegram(
                    f"<b>🚀 {setup['side']} {symbol} BREAKOUT ORDER</b>\n"
                    f"ticket #{result.ticket} | lots={lots:.2f}\n"
                    f"entry={entry:.5f} | SL={sl:.5f} | TP={tp:.5f}\n"
                    f"RR={setup['rr']:.1f} | conf={setup['confidence']:.0f}%\n"
                    f"range {setup['range_low']:.5f}-{setup['range_high']:.5f}"
                )
                emit_event(
                    "prop", "order", symbol,
                    f"BREAKOUT ORDER {setup['side']} {symbol} #{result.ticket} lots={lots:.2f} "
                    f"@ {entry:.5f} conf={setup['confidence']:.0f}%",
                )
                return True
            logger.warning("%s order rejected: %s", symbol, result.error)
        except Exception as exc:
            logger.warning("%s scan error: %s", symbol, exc)
    return False


def main() -> int:
    args = _args()
    logger = _logger(args.log_file)
    reset_client()
    client = get_client()
    logger.info(
        "forex breakout watcher started mode=%s interval=%.0fs conf>=%.1f rr>=%.1f range_bars=%s",
        "LIVE" if args.live else "DRY-RUN",
        args.interval,
        args.confidence_floor,
        args.min_rr,
        args.range_bars,
    )
    try:
        while True:
            try:
                client.ensure_connected()
                placed = scan_once(args, logger, client)
                if placed:
                    logger.info("order submitted; waiting for the next scan cycle")
            except Exception as exc:
                logger.exception("watch cycle failed: %s", exc)
            if args.once:
                return 0
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        logger.info("watcher stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
