"""Screener alerts - zone proximity and structure break notifications.

Monitors consensus allowlist symbols for:
1. Price approaching supply/demand zones (within 2 ATR)
2. Market structure breaks (BOS/CHoCH)

Sends Telegram alerts with cooldown to avoid spamming the same setup.

Run via schtasks every 30 minutes:
    schtasks /Create /TN "MT5R_ScreenerAlerts" ^
        /TR "C:\\Users\\Dave\\run_screener_alerts.bat" ^
        /SC MINUTE /MO 30 /IT /F
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("mt5_mcp.screener_alerts")

MODULE_DIR = Path(__file__).resolve().parent
STATE_PATH = MODULE_DIR / "memory" / "screener_alerts_state.json"

# Alert cooldown: don't alert same symbol+setup within this many seconds
COOLDOWN_SEC = 4 * 3600.0  # 4 hours

# Zone proximity: alert when price is within this many ATRs of a zone
ZONE_PROXIMITY_ATR = 2.0

# Timeframe for analysis
ANALYSIS_TIMEFRAME = "H1"
CANDLE_COUNT = 100


def _load_state() -> dict:
    """Load alert cooldown state."""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return {"alerts": {}}


def _save_state(state: dict) -> None:
    """Save alert state (atomic write)."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _prune_state(state: dict) -> dict:
    """Remove alert entries older than COOLDOWN_SEC * 2."""
    now = time.time()
    cutoff = now - COOLDOWN_SEC * 2
    alerts = state.get("alerts", {})
    state["alerts"] = {k: v for k, v in alerts.items() if float(v.get("ts", 0)) > cutoff}
    return state


def _alert_key(symbol: str, alert_type: str) -> str:
    """Unique key for cooldown tracking."""
    return f"{symbol}_{alert_type}"


def _is_on_cooldown(state: dict, key: str) -> bool:
    """Check if an alert key is still on cooldown."""
    alerts = state.get("alerts", {})
    entry = alerts.get(key)
    if not entry:
        return False
    return (time.time() - float(entry.get("ts", 0))) < COOLDOWN_SEC


def _mark_alerted(state: dict, key: str, detail: str) -> None:
    """Record that an alert was sent."""
    state.setdefault("alerts", {})[key] = {
        "ts": time.time(),
        "detail": detail[:80],
    }


def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Compute ATR from a DataFrame with high/low/close columns."""
    if len(df) < period + 1:
        return 0.0
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr_values = np.convolve(tr, np.ones(period) / period, mode="valid")
    return float(atr_values[-1]) if len(atr_values) > 0 else 0.0


def _df_to_normalized_candles(df: pd.DataFrame, symbol: str, timeframe: str) -> list:
    """Convert a gateway DataFrame to NormalizedCandle list."""
    from mt5_mcp.data_schema import NormalizedCandle
    from mt5_mcp.asset import asset_class_from_symbol

    ac = asset_class_from_symbol(symbol)
    candles = []
    for _, row in df.iterrows():
        try:
            ts = row["time"]
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            elif hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime().replace(tzinfo=timezone.utc)
            candle = NormalizedCandle(
                timestamp=ts,
                symbol=symbol,
                timeframe=timeframe,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("tick_volume", 0)),
                spread=float(row.get("spread", 0)),
                asset_class=ac,
            )
            candles.append(candle)
        except (ValueError, KeyError, TypeError):
            continue
    return candles


def _esc(value: object) -> str:
    """Escape text for Telegram HTML."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fmt_zone_alert(symbol: str, zone_type: str, zone_mid: float,
                    price: float, dist_atr: float) -> str:
    """Format a zone proximity alert as HTML."""
    emoji = "\U0001F4B0" if zone_type == "demand" else "\U0001F534"
    direction = "BUY zone" if zone_type == "demand" else "SELL zone"
    return (
        f"{emoji} <b>SCREENER: ZONE PROXIMITY</b>\n"
        f"<b>{_esc(symbol)}</b> \u2014 near {direction}\n"
        f"price: <code>{price:g}</code>\n"
        f"zone mid: <code>{zone_mid:g}</code>\n"
        f"distance: <code>{dist_atr:.1f} ATR</code>"
    )


def _fmt_structure_alert(symbol: str, break_type: str, break_level: float,
                         trend: str, price: float) -> str:
    """Format a structure break alert as HTML."""
    emoji = "\U0001F504" if break_type == "CHoCH" else "\U0001F4A5"
    return (
        f"{emoji} <b>SCREENER: STRUCTURE BREAK</b>\n"
        f"<b>{_esc(symbol)}</b> \u2014 {_esc(break_type)}\n"
        f"trend: {_esc(trend)}\n"
        f"break level: <code>{break_level:g}</code>\n"
        f"price: <code>{price:g}</code>"
    )


def _scan_symbol(symbol: str, client) -> list[dict]:
    """Scan one symbol for zone proximity and structure breaks.

    Returns a list of alert dicts (may be empty).
    """
    from mt5_mcp.zone_agent import ZoneAgent
    from mt5_mcp.structure_agent import StructureAgent

    alerts = []

    # Fetch candles
    try:
        df = client.rates(symbol, ANALYSIS_TIMEFRAME, CANDLE_COUNT)
    except Exception as exc:
        logger.warning("Failed to fetch %s %s: %s", symbol, ANALYSIS_TIMEFRAME, exc)
        return alerts

    if df is None or len(df) < 20:
        return alerts

    current_price = float(df["close"].iloc[-1])
    atr_val = _compute_atr(df)
    if atr_val <= 0:
        return alerts

    candles = _df_to_normalized_candles(df, symbol, ANALYSIS_TIMEFRAME)
    if len(candles) < 10:
        return alerts

    # --- Zone proximity ---
    try:
        zone_agent = ZoneAgent()
        zones = zone_agent.detect_zones(candles, symbol, ANALYSIS_TIMEFRAME)
        for zone in zones:
            if zone.midpoint is None:
                continue
            dist = abs(current_price - zone.midpoint)
            dist_atr = dist / atr_val
            if dist_atr <= ZONE_PROXIMITY_ATR:
                alerts.append({
                    "type": "zone",
                    "symbol": symbol,
                    "zone_type": zone.type,
                    "zone_mid": zone.midpoint,
                    "price": current_price,
                    "dist_atr": round(dist_atr, 2),
                    "key": _alert_key(symbol, f"zone_{zone.type}_{zone.midpoint:.6f}"),
                })
    except Exception as exc:
        logger.warning("Zone detection failed for %s: %s", symbol, exc)

    # --- Structure breaks ---
    try:
        struct_agent = StructureAgent()
        structure = struct_agent.analyze(candles, symbol, ANALYSIS_TIMEFRAME)
        if structure.structure_break is not None and structure.structure_break_type is not None:
            alerts.append({
                "type": "structure",
                "symbol": symbol,
                "break_type": structure.structure_break_type,
                "break_level": structure.structure_break,
                "trend": structure.trend,
                "price": current_price,
                "key": _alert_key(symbol, f"struct_{structure.structure_break_type}"),
            })
    except Exception as exc:
        logger.warning("Structure analysis failed for %s: %s", symbol, exc)

    return alerts


def scan_and_alert(client=None) -> dict:
    """Run screener scan on all consensus allowlist symbols and send alerts.

    Returns a summary dict.
    """
    from mt5_mcp.consensus import allowed_symbols

    if client is None:
        try:
            from mt5_mcp.gateway_client import GatewayClient
            client = GatewayClient()
        except Exception as exc:
            logger.error("Failed to create GatewayClient: %s", exc)
            return {"status": "error", "reason": str(exc), "symbols_scanned": 0}

    symbols = sorted(allowed_symbols())
    if not symbols:
        return {"status": "ok", "reason": "empty allowlist", "symbols_scanned": 0, "alerts_sent": 0}

    state = _load_state()
    state = _prune_state(state)

    total_alerts = 0
    symbols_scanned = 0
    alert_details = []

    for symbol in symbols:
        symbols_scanned += 1
        symbol_alerts = _scan_symbol(symbol, client)

        for alert in symbol_alerts:
            key = alert["key"]

            # Check cooldown
            if _is_on_cooldown(state, key):
                continue

            # Format and send
            if alert["type"] == "zone":
                html = _fmt_zone_alert(
                    alert["symbol"], alert["zone_type"],
                    alert["zone_mid"], alert["price"], alert["dist_atr"],
                )
                msg = f"{symbol} near {alert['zone_type']} zone"
            elif alert["type"] == "structure":
                html = _fmt_structure_alert(
                    alert["symbol"], alert["break_type"],
                    alert["break_level"], alert["trend"], alert["price"],
                )
                msg = f"{symbol} {alert['break_type']} break"
            else:
                continue

            try:
                from mt5_mcp.notify import notify
                notify(f"screener_{key}", msg, html=html, force=True)
                _mark_alerted(state, key, msg)
                total_alerts += 1
                alert_details.append(f"{symbol}: {msg}")
                logger.info("Screener alert: %s", msg)
            except Exception as exc:
                logger.warning("Failed to send screener alert for %s: %s", symbol, exc)

    _save_state(state)

    return {
        "status": "ok",
        "symbols_scanned": symbols_scanned,
        "alerts_sent": total_alerts,
        "alert_details": alert_details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Screener alerts (zones + structure)")
    parser.add_argument("--scan", action="store_true", help="run scan and alert")
    parser.add_argument("--status", action="store_true", help="show alert state")
    args = parser.parse_args()

    if args.status:
        state = _load_state()
        alerts = state.get("alerts", {})
        print(f"Active cooldowns: {len(alerts)}")
        for key, info in sorted(alerts.items(), key=lambda x: x[1].get("ts", 0)):
            age_h = (time.time() - info.get("ts", 0)) / 3600
            print(f"  {key} ({age_h:.1f}h ago) - {info.get('detail', '')}")
        return 0

    result = scan_and_alert()
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
