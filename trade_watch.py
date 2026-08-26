"""Continuous Boom/Crash signal watcher.

The default mode is dry-run. Pass ``--live`` only when the watcher should
submit pending orders through the normal compliance and trade preflight gates.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mt5_mcp.client import get_client, reset_client
from mt5_mcp.common import BOOM_CRASH_SYMBOLS, pip_size, pip_value_per_lot, round_to_step
from mt5_mcp.config import get_settings
from mt5_mcp.memory.trade_guard import SETUP, annotate_closed_trades, evaluate as learning_gate
from mt5_mcp.memory.trade_journal import get_journal
from mt5_mcp.self_tune import current_symbol, retrain_from_live
from mt5_mcp.tools.boom_crash_strategy import detect_spike, mt5_symbol
from mt5_mcp.tools.boom_crash_exits import get_exit_config, run_exit_management
from mt5_mcp.tools.trades import open_market, pending_order
from mt5_mcp.telegram_alerts import send_telegram
from mt5_mcp.trade_events import emit as emit_event
from mt5_mcp.visual_notify import popup


def _logger(log_path: Path | None) -> logging.Logger:
    logger = logging.getLogger("boom_crash_watcher")
    logger.setLevel(logging.INFO)
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
    parser.add_argument("--interval", type=float, default=15.0, help="seconds between scans")
    parser.add_argument("--confidence-floor", type=float, default=90.0)
    parser.add_argument("--max-spike-age", type=int, default=15)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--spike-threshold-atr", type=float, default=2.5)
    parser.add_argument("--min-spike-ticks", type=int, default=8)
    parser.add_argument("--signal-expiry-seconds", type=float, default=20.0)
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="comma-separated subset of BOOM/CRASH symbols to scan (default: all or settings watchlist)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=ROOT / "logs" / "trade_watch.log",
    )
    return parser.parse_args()


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    """Determine which symbols to scan.

    Priority:  --symbols CLI flag  >  settings.json watchlist  >  all BOOM/CRASH.
    """
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    settings = get_settings()
    watchlist = getattr(settings, "boom_crash", None)
    if watchlist and hasattr(watchlist, "watchlist") and watchlist.watchlist:
        return list(watchlist.watchlist)
    return list(BOOM_CRASH_SYMBOLS)


def _lot_size(client, symbol: str, entry: float, sl: float, budget: float) -> float:
    info = client.symbol_info(symbol)
    pip = pip_size(info)
    pip_value = pip_value_per_lot(info)
    loss_per_lot = abs(entry - sl) / pip * pip_value if pip and pip_value else 0.0
    minimum = float(info.get("volume_min", 0.1) or 0.1)
    maximum = float(info.get("volume_max", 100.0) or 100.0)
    step = float(info.get("volume_step", 0.01) or 0.01)
    raw = budget / loss_per_lot if loss_per_lot > 0 else minimum
    return round_to_step(raw, step, floor=minimum, ceil=maximum)


def _has_exposure(client, symbol: str) -> bool:
    positions = client.positions(symbol) or []
    orders = client.pending_orders(symbol) or []
    return bool(positions or orders)


def _has_opposite_exposure(client, symbol: str) -> bool:
    """True when a position exists on the opposite Boom/Crash family.

    BOOM and CRASH are opposite exposures on the same volatility engine:
    holding both doubles margin while the drifts net out (item #24)."""
    kind = "boom" if "boom" in symbol.lower() else (
        "crash" if "crash" in symbol.lower() else ""
    )
    if not kind:
        return False
    opposite = "crash" if kind == "boom" else "boom"
    for pos in client.positions() or []:
        if opposite in str(pos.get("symbol", "")).lower():
            return True
    return False



def _market_fallback(
    client,
    logger: logging.Logger,
    api_symbol: str,
    symbol: str,
    side: str,
    entry_level: float,
    sl: float,
    tp: float,
    lots: float,
    signal,
    signal_id: str = None,
) -> bool:
    """Fill as a market order when the stop level was just crossed.

    Synthetics have no order queue, so the stop would fill at the current
    market price anyway; chasing is bounded to half an ATR of overshoot.
    """
    from mt5_mcp.memory.trade_journal import get_journal
    journal = get_journal()
    tick = client.tick(symbol)
    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", bid))
    atr = float(signal.atr) or 1e-9
    if side == "BUY":
        overshoot = ask - entry_level
        price = ask
    else:
        overshoot = entry_level - bid
        price = bid
    if overshoot < 0.0 or overshoot > atr * 0.5:
        logger.info(
            "%s skipped: market %.5f too far from entry %.5f (overshoot %.5f > %.5f)",
            api_symbol,
            price,
            entry_level,
            overshoot,
            atr * 0.5,
        )
        return False
    result = None
    for attempt in range(2):
        result = open_market(
            symbol,
            side,
            lots,
            sl=sl,
            tp=tp,
            comment=f"{api_symbol} spike-drift {signal.retracement * 100:.1f}% market",
        )
        if result.accepted:
            break
        time.sleep(0.3)
    if result is not None and result.accepted and result.ticket:
        _record_order(
            ticket=int(result.ticket),
            symbol=symbol,
            side=side,
            entry=float(getattr(result, "entry", price) or price),
            sl=sl,
            tp=tp,
            lots=lots,
            signal=signal,
        )
        if signal_id:
            journal.update_signal_outcome(
                signal_id,
                ticket=int(result.ticket),
                filled=True,
                trade_time=datetime.now(timezone.utc).isoformat(),
            )
        logger.info(
            "%s order placed via market fallback: ticket=%s retcode=%s",
            api_symbol,
            result.ticket,
            result.retcode,
        )
        popup(
            f"[MT5] {side} {api_symbol} filled",
            f"ticket #{result.ticket}\n"
            f"lots={lots:.2f} entry~{price:.2f}\n"
            f"SL={sl:.2f} TP=off (chandelier trail)\n"
            f"conf={signal.confidence:.0f}% age={signal.spike_age_candles}",
        )
        send_telegram(
            f"<b>🚀 FILLED {side} {api_symbol}</b>\n"
            f"ticket #{result.ticket} | lots={lots:.2f}\n"
            f"entry~{price:.2f} | SL={sl:.2f}\n"
            f"conf={signal.confidence:.0f}% | age={signal.spike_age_candles}"
        )
        emit_event(
            "deriv", "filled", api_symbol,
            f"FILLED {side} {api_symbol} #{result.ticket} lots={lots:.2f} entry~{price:.2f} SL={sl:.2f}",
        )
        return True
    logger.warning("%s market fallback rejected: %s", api_symbol, result.error)
    return False


def _record_order(
    *,
    ticket: int,
    symbol: str,
    side: str,
    entry: float,
    sl: float,
    tp: float,
    lots: float,
    signal,
) -> None:
    """Persist the decision context so the eventual exit can teach the guard."""
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
            f"{symbol} spike-drift {signal.retracement * 100:.1f}% stop order; "
            "directional drift with pullback entry"
        ),
        market_conditions=(
            f"spike_age_candles={int(signal.spike_age_candles)};"
            f"spike_size={signal.spike_size:.5f};atr={signal.atr:.5f};"
            f"confidence={signal.confidence:.1f}"
        ),
        confidence=signal.confidence,
        rr=rr,
    )


def learn_from_history(client, logger: logging.Logger, max_spike_age: int) -> None:
    try:
        result = get_journal().sync_from_mt5(client, days=30)
        updated = annotate_closed_trades(max_spike_age)
        if result.get("added") or result.get("updated") or updated:
            logger.info(
                "learning sync added=%s updated=%s annotated=%s",
                result.get("added", 0),
                result.get("updated", 0),
                updated,
            )
    except Exception as exc:
        logger.warning("learning sync failed: %s", exc)


def scan_once(args: argparse.Namespace, logger: logging.Logger, client) -> bool:
    """Scan all symbols and optionally submit the first qualifying signal."""
    account = client.account_info()
    equity = float(account.get("equity", account.get("balance", 0.0)) or 0.0)
    risk = get_settings().risk
    budget = (
        equity
        * float(risk.max_risk_percent)
        / 100.0
        * 0.8
    )
    placed = False

    for api_symbol in _resolve_symbols(args):
        symbol = mt5_symbol(api_symbol)
        try:
            if _has_exposure(client, symbol):
                continue
            if _has_opposite_exposure(client, symbol):
                logger.info(
                    "%s skipped: opposite %s exposure open (correlation guard)",
                    api_symbol,
                    "CRASH" if "boom" in symbol.lower() else "BOOM",
                )
                continue

            sym_tune = current_symbol(api_symbol)
            if sym_tune.get("enabled") is False:
                logger.info("%s skipped: self-tuning disabled (negative edge)", api_symbol)
                continue
            signal = detect_spike(
                api_symbol,
                lookback_minutes=args.lookback,
                spike_threshold_atr=float(sym_tune.get("spike_threshold_atr", args.spike_threshold_atr)),
                min_spike_ticks=args.min_spike_ticks,
            )
            if signal is None:
                continue
            signal_seen = time.monotonic()
            learned = learning_gate(
                api_symbol,
                base_max_spike_age=args.max_spike_age,
                base_confidence_floor=args.confidence_floor,
            )
            if not learned["allowed"]:
                logger.warning("%s skipped: %s", api_symbol, "; ".join(learned["violations"]))
                continue
            if signal.confidence < learned["confidence_floor"]:
                continue
            if signal.spike_age_candles > learned["max_spike_age"]:
                logger.info(
                    "%s skipped: spike age %s bars exceeds %s",
                    api_symbol,
                    signal.spike_age_candles,
                    learned["max_spike_age"],
                )
                continue

            # Recompute immediately before sizing/submission. A synthetic
            # spike can move through its entry zone in seconds, so a stale
            # scan result must never be submitted unchanged.
            if time.monotonic() - signal_seen > args.signal_expiry_seconds:
                logger.info("%s skipped: signal expired before revalidation", api_symbol)
                continue
            fresh = detect_spike(
                api_symbol,
                lookback_minutes=args.lookback,
                spike_threshold_atr=args.spike_threshold_atr,
                min_spike_ticks=args.min_spike_ticks,
            )
            if fresh is None or fresh.confidence < learned["confidence_floor"]:
                logger.info("%s skipped: signal expired before revalidation", api_symbol)
                continue
            if fresh.spike_age_candles > learned["max_spike_age"]:
                logger.info("%s skipped: revalidated signal is stale", api_symbol)
                continue
            entry_shift = abs(float(fresh.entry_price) - float(signal.entry_price))
            max_shift = max(float(fresh.atr), float(signal.atr), 1e-9) * 0.5
            if entry_shift > max_shift:
                logger.info("%s skipped: entry moved %.5f during revalidation", api_symbol, entry_shift)
                continue
            signal = fresh

            # Derive trade parameters BEFORE logging so all fields are available
            tick = client.tick(symbol)
            bid = float(tick.get("bid", 0.0))
            ask = float(tick.get("ask", bid))
            side = "BUY" if signal.drift_direction == "up" else "SELL"
            entry = float(signal.entry_price)
            sl = float(signal.sl_price)
            tp = None  # chandelier trail handles exit
            lots = _lot_size(
                client,
                symbol,
                entry,
                sl,
                budget * float(sym_tune.get("size_multiplier", 1.0)),
            )

            # Log signal to journal for quality tracking
            journal = get_journal()
            signal_id = str(uuid.uuid4())
            journal.log_signal(
                signal_id=signal_id,
                symbol=api_symbol,
                side=side,
                setup=SETUP,
                timeframe="M1",
                entry=entry,
                sl=sl,
                tp=tp,
                lots=lots,
                confidence=signal.confidence,
                setup_confidence=signal.confidence,
                trend_confidence=signal.confidence,
                zone_strength=signal.confidence,
                confirmation_confidence=signal.confidence,
                rr=signal.rr_used if hasattr(signal, "rr_used") else 1.5,
                sl_pips=None,
                tp_pips=None,
                atr=signal.atr,
                range_high=signal.spike_high,
                range_low=signal.spike_low,
                atr_value=signal.atr,
                compactness=None,
                trend_dir=signal.drift_direction,
                market_conditions=f"spike_age_candles={signal.spike_age_candles};spike_size={signal.spike_size:.5f};atr={signal.atr:.5f}",
                signal_time=datetime.now(timezone.utc).isoformat(),
                filled=False,
            )
            logger.debug("%s signal logged: %s", api_symbol, signal_id)
            if (side == "BUY" and entry <= ask) or (side == "SELL" and entry >= bid):
                if _market_fallback(
                    client, logger, api_symbol, symbol, side, entry, sl, tp, lots, signal, signal_id
                ):
                    placed = True
                continue

            order_type = "buy_stop" if side == "BUY" else "sell_stop"
            logger.info(
                "%s qualifying: conf=%.1f age=%s entry=%.5f sl=%.5f tp=%s lots=%.2f",
                api_symbol,
                signal.confidence,
                signal.spike_age_candles,
                entry,
                sl,
                "off (chandelier trail)" if tp is None else f"{tp:.5f}",
                lots,
            )

            if not args.live:
                logger.info("%s dry-run: no order submitted", api_symbol)
                return False

            result = pending_order(
                symbol,
                order_type,
                lots,
                price=entry,
                sl=sl,
                tp=tp,
                comment=f"{api_symbol} spike-drift {signal.retracement * 100:.1f}%",
            )
            if result.accepted:
                if result.ticket:
                    _record_order(
                        ticket=int(result.ticket),
                        symbol=symbol,
                        side=side,
                        entry=entry,
                        sl=sl,
                        tp=tp,
                        lots=lots,
                        signal=signal,
                    )
                    # Update signal with ticket and filled status
                    journal.update_signal_outcome(
                        signal_id,
                        ticket=int(result.ticket),
                        filled=True,
                        trade_time=datetime.now(timezone.utc).isoformat(),
                    )
                logger.info("%s order placed: ticket=%s retcode=%s", api_symbol, result.ticket, result.retcode)
                popup(
                    f"[MT5] {side} {api_symbol} order placed",
                    f"ticket #{result.ticket}\n"
                    f"lots={lots:.2f} at {entry:.2f} ({order_type})\n"
                    f"SL={sl:.2f} TP=off (chandelier trail)\n"
                    f"conf={signal.confidence:.0f}% age={signal.spike_age_candles}",
                )
                send_telegram(
                    f"<b>🚀 ORDER PLACED {side} {api_symbol}</b>\n"
                    f"ticket #{result.ticket} | lots={lots:.2f}\n"
                    f"entry={entry:.2f} ({order_type}) | SL={sl:.2f}\n"
                    f"conf={signal.confidence:.0f}% | age={signal.spike_age_candles}"
                )
                emit_event(
                    "deriv", "order", api_symbol,
                    f"ORDER PLACED {side} {api_symbol} #{result.ticket} lots={lots:.2f} @ {entry:.2f} ({order_type})",
                )
                placed = True
                continue
            logger.warning("%s order rejected: %s", api_symbol, result.error)
            race = "retcode=10015" in str(result.error) or "Invalid price" in str(result.error)
            if not race:
                continue
            # The stop level sits where the price is hovering; a brief tick
            # above/below the level at send time makes MT5 reject it. Retry
            # with a fresh tick while the level is still on the right side of
            # the market, and only fall back to a market fill once crossed.
            resolved = False
            for _ in range(3):
                tick = client.tick(symbol)
                ask2 = float(tick.get("ask", 0.0))
                bid2 = float(tick.get("bid", ask2))
                crossed = (
                    side == "BUY" and entry <= ask2
                ) or (side == "SELL" and entry >= bid2)
                if crossed:
                    if _market_fallback(
                        client,
                        logger,
                        api_symbol,
                        symbol,
                        side,
                        entry,
                        sl,
                        tp,
                        lots,
                        signal,
                        signal_id,
                    ):
                        placed = True
                    resolved = True
                    break
                result = pending_order(
                    symbol,
                    order_type,
                    lots,
                    price=entry,
                    sl=sl,
                    tp=tp,
                    comment=f"{api_symbol} spike-drift {signal.retracement * 100:.1f}%",
                )
                if result.accepted:
                    if result.ticket:
                        _record_order(
                            ticket=int(result.ticket),
                            symbol=symbol,
                            side=side,
                            entry=entry,
                            sl=sl,
                            tp=tp,
                            lots=lots,
                            signal=signal,
                        )
                        # Update signal with ticket and filled status
                        journal.update_signal_outcome(
                            signal_id,
                            ticket=int(result.ticket),
                            filled=True,
                            trade_time=datetime.now(timezone.utc).isoformat(),
                        )
                    logger.info(
                        "%s order placed: ticket=%s retcode=%s",
                        api_symbol,
                        result.ticket,
                        result.retcode,
                    )
                    popup(
                        f"[MT5] {side} {api_symbol} order placed",
                        f"ticket #{result.ticket}\n"
                        f"lots={lots:.2f} at {entry:.2f} ({order_type})\n"
                        f"SL={sl:.2f} TP=off (chandelier trail)\n"
                        f"conf={signal.confidence:.0f}% age={signal.spike_age_candles}",
                    )
                    send_telegram(
                        f"<b>🚀 ORDER PLACED {side} {api_symbol}</b>\n"
                        f"ticket #{result.ticket} | lots={lots:.2f}\n"
                        f"entry={entry:.2f} ({order_type}) | SL={sl:.2f}\n"
                        f"conf={signal.confidence:.0f}% | age={signal.spike_age_candles}"
                    )
                    emit_event(
                        "deriv", "order", api_symbol,
                        f"ORDER PLACED {side} {api_symbol} #{result.ticket} lots={lots:.2f} @ {entry:.2f} ({order_type})",
                    )
                    placed = True
                    resolved = True
                    break
            if not resolved:
                logger.warning("%s order rejected after retries: %s", api_symbol, result.error)
        except Exception as exc:
            logger.warning("%s scan error: %s", api_symbol, exc)
    return placed


def main() -> int:
    args = _args()
    logger = _logger(args.log_file)
    reset_client()
    # Use Deriv client for synthetic symbols
    client = get_client("CRASH1000")
    last_learning_sync = 0.0
    daily_start_equity = None
    daily_start_date = None
    daily_halt = False
    logger.info(
        "watcher started mode=%s interval=%.1fs confidence>=%.1f age<=%s",
        "LIVE" if args.live else "DRY-RUN",
        args.interval,
        args.confidence_floor,
        args.max_spike_age,
    )
    try:
        while True:
            try:
                client.ensure_connected()

                # --- Daily loss circuit breaker ---
                today = datetime.now(timezone.utc).date()
                if daily_start_date != today:
                    daily_start_date = today
                    daily_halt = False
                    try:
                        acct = client.account_info()
                        daily_start_equity = float(acct.get("equity", acct.get("balance", 0)))
                    except Exception:
                        daily_start_equity = None
                    if daily_start_equity:
                        logger.info("daily circuit breaker reset: equity=%.2f", daily_start_equity)

                if daily_halt:
                    if args.once:
                        return 0
                    time.sleep(max(1.0, args.interval))
                    continue

                if daily_start_equity and daily_start_equity > 0:
                    try:
                        acct = client.account_info()
                        current_equity = float(acct.get("equity", acct.get("balance", 0)))
                        daily_loss_pct = (daily_start_equity - current_equity) / daily_start_equity * 100
                        max_daily_loss = float(get_settings().risk.max_daily_loss_percent)
                        if daily_loss_pct >= max_daily_loss:
                            daily_halt = True
                            logger.warning(
                                "DAILY LOSS HALT: equity %.2f -> %.2f (%.1f%% loss) >= %.1f%% limit",
                                daily_start_equity, current_equity, daily_loss_pct, max_daily_loss,
                            )
                            send_telegram(
                                f"<b>DAILY LOSS HALT</b>\n"
                                f"Equity: ${current_equity:.2f}\n"
                                f"Loss: {daily_loss_pct:.1f}% >= {max_daily_loss:.1f}%\n"
                                f"Trading paused for today."
                            )
                            if args.once:
                                return 0
                            time.sleep(max(1.0, args.interval))
                            continue
                    except Exception:
                        pass  # if we can't check equity, don't block trading
                if args.once or time.monotonic() - last_learning_sync >= 60.0:
                    learn_from_history(client, logger, args.max_spike_age)
                    try:
                        tune = retrain_from_live()
                        if tune:
                            logger.info(
                                "self-tune: parameters updated for %d symbols",
                                len(tune.get("symbols", {})),
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("self-tune retrain failed: %s", exc)
                    last_learning_sync = time.monotonic()
                try:
                    # Chandelier exit management (research 2026-08-13): arm at
                    # +1R, trail the SL at 1.0-1.5 ATR, 1R partial exit, no TP.
                    exit_summary = run_exit_management(client, logger)
                    if exit_summary.get("modified") or exit_summary.get("partials"):
                        logger.info(
                            "exit management: checked=%s modified=%s partials=%s",
                            exit_summary.get("checked", 0),
                            exit_summary.get("modified", 0),
                            exit_summary.get("partials", 0),
                        )
                except Exception as exc:  # noqa: BLE001 - exits must never kill the cycle
                    logger.warning("exit management cycle failed: %s", exc)
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
