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
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mt5_mcp.consensus import (
    check as consensus_check,
    publish as consensus_publish,
    risk_cap as consensus_risk_cap,
)
from mt5_mcp.memory.trade_journal import get_journal
from mt5_mcp.pipeline import run_gates
from mt5_mcp.tools.supply_demand import detect_zones as detect_sd_zones
from mt5_mcp.watcher_base import BaseWatcher, Signal

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


def _parse_args() -> argparse.Namespace:
    parser = BaseWatcher.base_arg_parser("Forex range breakout watcher")
    parser.add_argument("--confidence-floor", type=float, default=65.0)
    parser.add_argument("--range-bars", type=int, default=24)
    parser.add_argument("--min-rr", type=float, default=1.2)
    parser.add_argument("--rr", type=float, default=1.5)
    parser.add_argument("--symbols", type=str, default="")
    parser.add_argument("--trend-tf", type=str, default="H1")
    parser.add_argument("--lookback", type=int, default=400)
    parser.set_defaults(log_file=str(ROOT / "logs" / "forex_breakout.log"))
    parser.set_defaults(risk_percent=0.5)
    return parser.parse_args()


def _ema200_direction(client, symbol: str, timeframe: str) -> tuple[str, float | None]:
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
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


def _find_breakout(client, logger, args, symbol: str) -> dict | None:
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

    window = frame.iloc[-args.range_bars - 1 : -1]
    range_high = float(window["high"].max())
    range_low = float(window["low"].min())
    range_height = range_high - range_low

    trend, ema200 = _ema200_direction(client, symbol, args.trend_tf)
    tick = client.tick(symbol)
    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", bid))

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
                candidates.append({
                    "side": "BUY", "entry": entry_buy, "sl": sl_buy,
                    "tp": tp_buy, "rr": args.rr, "overshoot": overshoot_buy,
                    "bonus": zone_bonus,
                })
    if trend != "bull":
        entry_sell = range_low - buffer
        overshoot_sell = range_low - bid
        if overshoot_sell <= 0.5 * atr:
            sl_sell = range_high + 0.8 * atr
            risk_sell = sl_sell - entry_sell
            if risk_sell > 0:
                tp_sell = entry_sell - args.rr * risk_sell
                candidates.append({
                    "side": "SELL", "entry": entry_sell, "sl": sl_sell,
                    "tp": tp_sell, "rr": args.rr, "overshoot": overshoot_sell,
                    "bonus": zone_bonus,
                })
    if not candidates:
        return None

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
            symbol, range_low, range_high, atr, trend,
            best["side"], best["confidence"], args.confidence_floor,
        )
        return None
    return best


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    return list(FOREX_SYMBOLS)


class ForexBreakoutWatcher(BaseWatcher):
    SETUP = SETUP
    LOGGER_NAME = "forex_breakout_watcher"

    def symbols(self) -> list[str]:
        return _resolve_symbols(self.args)

    def client_key(self) -> str | None:
        return None

    def detect_signal(self, symbol: str) -> Signal | None:
        setup = _find_breakout(self._client, self.logger, self.args, symbol)
        if setup is None:
            return None
        return Signal(
            symbol=symbol,
            side=setup["side"],
            entry=setup["entry"],
            sl=setup["sl"],
            tp=setup["tp"],
            confidence=setup["confidence"],
            rr=setup["rr"],
            metadata=setup,
        )

    def compliance_gate(self, symbol: str, signal: Signal) -> tuple[bool, list[str]]:
        consensus_publish("breakout", symbol, signal.side, signal.confidence)
        cver = consensus_check(symbol, signal.side, "breakout", score=signal.confidence)
        if not cver.get("allowed"):
            return False, cver.get("violations", [])
        gate = run_gates(
            symbol=symbol, side=signal.side, lots=signal.lots or 0.01,
            sl_price=signal.sl, confidence=signal.confidence,
            source=SETUP, score=signal.confidence,
        )
        return gate.allowed, gate.violations

    def build_order(self, signal: Signal) -> tuple[str, float, float, float | None, float]:
        order_type = "buy_stop" if signal.side == "BUY" else "sell_stop"
        return order_type, signal.entry, signal.sl, signal.tp, signal.lots or 0.0

    def scan_once(self, client, dry_run: bool = False) -> int:
        try:
            equity = float(client.account_info().get("equity", 0))
        except Exception:
            equity = 0.0
        budget = equity * min(self.args.risk_percent, consensus_risk_cap()) / 100.0

        for symbol in self.symbols():
            try:
                if self._has_exposure(client, symbol):
                    continue

                signal = self.detect_signal(symbol)
                if signal is None:
                    continue

                lots = self._lot_size(client, symbol, signal.entry, signal.sl,
                                      risk_percent=min(self.args.risk_percent, consensus_risk_cap()))
                if lots is None:
                    continue
                signal.lots = lots

                setup = signal.metadata
                self.logger.info(
                    "%s qualifying: %s conf=%.1f entry=%.5f sl=%.5f tp=%.5f rr=%.2f lots=%.2f range=%.5f-%.5f",
                    symbol, signal.side, signal.confidence, signal.entry, signal.sl,
                    signal.tp or 0, signal.rr, lots, setup["range_low"], setup["range_high"],
                )

                allowed, violations = self.compliance_gate(symbol, signal)
                if not allowed:
                    self.logger.warning("%s blocked: %s", symbol, "; ".join(violations))
                    continue

                if dry_run:
                    self.logger.info("%s dry-run: no order submitted", symbol)
                    return 0

                tick = client.tick(symbol)
                bid = float(tick.get("bid", 0.0))
                ask = float(tick.get("ask", bid))
                crossed = (signal.side == "BUY" and signal.entry <= ask) or (
                    signal.side == "SELL" and signal.entry >= bid
                )

                if crossed:
                    from mt5_mcp.tools.trades import open_market
                    result = open_market(
                        symbol, signal.side, lots, sl=signal.sl, tp=signal.tp,
                        comment=f"{symbol} range breakout", confidence=signal.confidence,
                    )
                else:
                    from mt5_mcp.tools.trades import pending_order
                    order_type = "buy_stop" if signal.side == "BUY" else "sell_stop"
                    result = pending_order(
                        symbol, order_type, lots, price=signal.entry,
                        sl=signal.sl, tp=signal.tp,
                        comment=f"{symbol} range breakout",
                    )

                if result.accepted:
                    self._record_order(result, signal)
                    self.logger.info("%s order placed: ticket=%s", symbol, result.ticket)
                    self._notify_order(result, signal)
                    return 1
                self.logger.warning("%s order rejected: %s", symbol, result.error)
            except Exception as exc:
                self.logger.warning("%s scan error: %s", symbol, exc)
        return 0

    def _record_order(self, result, signal: Signal) -> None:
        setup = signal.metadata
        ticket = int(result.ticket) if result.ticket else None
        if not ticket:
            return
        risk = abs(signal.entry - signal.sl)
        rr = abs(signal.tp - signal.entry) / risk if risk and signal.tp else None
        try:
            get_journal().record_trade(
                ticket=ticket,
                symbol=signal.symbol,
                side=signal.side,
                setup=SETUP,
                entry=signal.entry,
                sl=signal.sl,
                tp=signal.tp,
                lots=signal.lots,
                open_time=datetime.now(timezone.utc).isoformat(),
                reason=(
                    f"{signal.symbol} range breakout {signal.side} "
                    f"range {setup['range_low']:.5f}-{setup['range_high']:.5f}"
                ),
                market_conditions=(
                    f"range_height={setup['range_high'] - setup['range_low']:.5f};"
                    f"atr={setup.get('atr', 0):.5f};overshoot={setup.get('overshoot', 0):.5f}"
                ),
                confidence=signal.confidence,
                rr=rr,
            )
        except Exception as exc:
            self.logger.warning("journal record failed: %s", exc)


def main() -> int:
    args = _parse_args()
    watcher = ForexBreakoutWatcher(args)
    return watcher.run()


if __name__ == "__main__":
    raise SystemExit(main())
