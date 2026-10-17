"""Unified notification dispatcher for all trading engines.

Single entry point for trade events (fills, closes, signals, position updates).
Dispatches to Telegram (rich HTML), desktop popup, and JSONL event log.

All execution paths (desk, confirm, auto_trader) and watchers call this module.
"""

from __future__ import annotations

import logging
from typing import Any

from mt5_mcp import telegram_alerts, trade_events
from mt5_mcp.notify import (
    fmt_desk_sheet,
    fmt_position_update,
    fmt_scan_summary,
    fmt_trade_executed,
    fmt_trade_signal,
    fmt_watch_alert,
    notify,
)

logger = logging.getLogger("mt5_mcp.notifier")


def trade_filled(
    *,
    symbol: str,
    side: str,
    entry: float,
    lots: float,
    sl: float,
    tp: float,
    ticket: int | None = None,
    confidence: float | None = None,
    setup: str = "",
    account: str = "prop",
    rr: str = "",
    **extra: Any,
) -> None:
    """Notify all channels of a filled order."""
    try:
        html = telegram_alerts.trade_entry_msg(
            symbol=symbol,
            side=side,
            lots=lots,
            entry=entry,
            sl=sl,
            tp=tp,
            confidence=confidence,
            ticket=ticket,
            setup=setup,
            rr=rr,
        )
        telegram_alerts.send_telegram(html)

        notify_html = fmt_trade_executed(
            symbol=symbol,
            direction=side,
            entry=entry,
            lots=lots,
            sl=sl,
            tp=tp,
            note=setup,
        )
        notify(f"{account}_filled_{symbol}", f"{side} {symbol} {lots} lots", html=notify_html, force=True)

        trade_events.emit(
            account=account,
            kind="fill",
            symbol=symbol,
            message=f"{side} {symbol} {lots} lots @ {entry} SL={sl} TP={tp}",
        )
    except Exception as exc:
        logger.warning("trade_filled notification failed: %s", exc)


def trade_closed(
    *,
    symbol: str,
    side: str,
    pnl: float,
    ticket: int | None = None,
    reason: str = "",
    r_multiple: float | None = None,
    balance: float | None = None,
    account: str = "prop",
    **extra: Any,
) -> None:
    """Notify all channels of a closed position."""
    try:
        html = telegram_alerts.trade_close_msg(
            symbol=symbol,
            side=side,
            pnl=pnl,
            r_multiple=r_multiple,
            reason=reason,
            balance=balance,
            ticket=ticket,
        )
        telegram_alerts.send_telegram(html)

        notify_html = fmt_position_update(
            symbol=symbol,
            ticket=ticket or 0,
            action="closed",
            pnl=pnl,
            reason=reason,
        )
        notify(f"{account}_closed_{symbol}", f"{side} {symbol} PnL {pnl:.2f}", html=notify_html, force=True)

        trade_events.emit(
            account=account,
            kind="close",
            symbol=symbol,
            message=f"{side} {symbol} closed PnL={pnl:.2f} {reason}",
        )
    except Exception as exc:
        logger.warning("trade_closed notification failed: %s", exc)


def signal_detected(
    *,
    symbol: str,
    direction: str,
    confidence: float,
    detail: str = "",
    account: str = "prop",
    **extra: Any,
) -> None:
    """Notify all channels of a detected signal (pre-fill)."""
    try:
        html = telegram_alerts.signal_msg(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            detail=detail,
        )
        telegram_alerts.send_telegram(html)

        notify_html = fmt_trade_signal(
            symbol=symbol,
            direction=direction,
            confluence=confidence,
            note=detail,
        )
        notify(f"{account}_signal_{symbol}", f"{direction} {symbol} conf {confidence:.0f}%", html=notify_html)

        trade_events.emit(
            account=account,
            kind="signal",
            symbol=symbol,
            message=f"{direction} {symbol} conf={confidence:.0f}% {detail}",
        )
    except Exception as exc:
        logger.warning("signal_detected notification failed: %s", exc)


def position_update(
    *,
    symbol: str,
    ticket: int,
    action: str,
    entry: float | None = None,
    new_sl: float | None = None,
    pnl: float | None = None,
    reason: str = "",
    account: str = "prop",
    **extra: Any,
) -> None:
    """Notify all channels of a position management action (BE, trail, partial exit)."""
    try:
        html = fmt_position_update(
            symbol=symbol,
            ticket=ticket,
            action=action,
            entry=entry,
            new_sl=new_sl,
            pnl=pnl,
            reason=reason,
        )
        notify(f"{account}_pos_{symbol}_{action}", f"{action} {symbol}", html=html, force=True)

        trade_events.emit(
            account=account,
            kind="position",
            symbol=symbol,
            message=f"{action} #{ticket} {symbol} {reason}",
        )
    except Exception as exc:
        logger.warning("position_update notification failed: %s", exc)


def daily_summary(
    *,
    balance: float,
    equity: float,
    trades_today: int,
    wins: int,
    losses: int,
    pnl_today: float,
    open_positions: int,
    notes: str = "",
    account: str = "prop",
    **extra: Any,
) -> None:
    """Notify all channels of the daily trading summary."""
    try:
        html = telegram_alerts.daily_summary_msg(
            balance=balance,
            equity=equity,
            trades_today=trades_today,
            wins=wins,
            losses=losses,
            pnl_today=pnl_today,
            open_positions=open_positions,
            notes=notes,
        )
        telegram_alerts.send_telegram(html)

        trade_events.emit(
            account=account,
            kind="summary",
            symbol="",
            message=f"Daily summary: PnL={pnl_today:.2f} trades={trades_today} W/L={wins}/{losses}",
        )
    except Exception as exc:
        logger.warning("daily_summary notification failed: %s", exc)


__all__ = [
    "trade_filled",
    "trade_closed",
    "signal_detected",
    "position_update",
    "daily_summary",
    "fmt_desk_sheet",
    "fmt_position_update",
    "fmt_scan_summary",
    "fmt_trade_executed",
    "fmt_trade_signal",
    "fmt_watch_alert",
    "notify",
]
