"""Periodic live-trades Telegram update.

Scheduled by Windows task ``MT5R_LiveTrades`` every 30 minutes (InteractiveToken,
same pattern as MT5R_* tasks). Sends one HTML message listing every open
position on the connected account plus balance/equity/floating totals.
Silent when flat; never raises (pythonw runs it unattended).
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mt5_mcp.mt5_client import MT5Client  # noqa: E402
from mt5_mcp.notify import _esc, notify  # noqa: E402

LOG = ROOT / "logs" / "live_trades_update.log"
SIDE = {0: "BUY", 1: "SELL"}


def _log(line: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:
        pass


def _f(value: object, digits: int = 5) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def build_message(client: MT5Client) -> tuple[str, str] | None:
    """Return (plain, html) for the current book, or None when flat."""
    ai = client.account_info()
    positions = client.positions()
    if not positions:
        return None

    floating = sum(float(p.get("profit") or 0.0) for p in positions)
    floating_swap = sum(float(p.get("swap") or 0.0) for p in positions)
    now = datetime.now().strftime("%H:%M")

    html = [
        f"<b>LIVE TRADES</b> {now} | {_esc(ai.get('login', '?'))} {_esc(ai.get('server', ''))}",
        f"balance <code>{_f(ai.get('balance'), 2)}</code> | "
        f"equity <code>{_f(ai.get('equity'), 2)}</code> | "
        f"floating <code>{'+' if floating >= 0 else ''}{_f(floating, 2)}</code>",
    ]
    plain = [
        f"LIVE TRADES {now} | {ai.get('login', '?')} {ai.get('server', '')} "
        f"floating {'+' if floating >= 0 else ''}{_f(floating, 2)}",
    ]

    for p in sorted(positions, key=lambda x: str(x.get("symbol"))):
        symbol = str(p.get("symbol", "?"))
        side = SIDE.get(int(p.get("type", 0)), str(p.get("type")))
        vol = float(p.get("volume") or 0.0)
        ticket = p.get("ticket")
        open_px = p.get("price_open")
        profit = float(p.get("profit") or 0.0)
        sl = p.get("sl")
        tp = p.get("tp")
        current = p.get("price_current")
        if current in (None, 0, 0.0):
            try:
                tick = client.tick(symbol)
                bid = float(tick.get("bid") or 0.0)
                ask = float(tick.get("ask") or 0.0)
                current = bid if side == "BUY" else ask
            except Exception:
                current = open_px
        html += [
            f"\n<b>{_esc(symbol)}</b> {side} {vol:g} #{ticket}",
            f"  <code>{_f(open_px)}</code> → <code>{_f(current)}</code> | "
            f"{'+' if profit >= 0 else ''}<code>{_f(profit, 2)}</code>",
            f"  SL <code>{_f(sl)}</code> | TP <code>{_f(tp)}</code>",
        ]
        plain.append(
            f"{symbol} {side} {vol:g} #{ticket} {open_px}->{current} "
            f"{profit:+.2f} SL {sl} TP {tp}"
        )

    if floating_swap:
        html.append(f"\nswap <code>{_f(floating_swap, 2)}</code>")
    html.append(f"\nopen positions: {len(positions)}")
    plain.append(f"open positions: {len(positions)}")
    return "\n".join(plain), "\n".join(html)


def main() -> int:
    client = MT5Client()
    try:
        client.ensure_connected()
        msg = build_message(client)
        if msg is None:
            _log("flat - no update sent")
            return 0
        plain, html = msg
        sent = notify("live_trades_update", plain, force=True, html=html)
        _log(f"update sent={sent} ({html.count('#')} positions)")
        return 0 if sent else 2
    except Exception:
        _log("ERROR\n" + traceback.format_exc())
        return 1
    finally:
        try:
            client.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
