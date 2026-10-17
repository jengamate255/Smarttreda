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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mt5_mcp.watcher_base import BaseWatcher, Signal

BTC_SYMBOLS: tuple[str, ...] = ("BTCUSD",)
SETUP = "btc_ema_pullback"


def _ema(series, period: int):
    return series.ewm(span=period, adjust=False).mean()


def _atr(high, low, close, period: int = 14):
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _parse_args() -> argparse.Namespace:
    parser = BaseWatcher.base_arg_parser("BTC H4 Trend Pullback Watcher")
    parser.add_argument("--ema-fast", type=int, default=50)
    parser.add_argument("--ema-slow", type=int, default=200)
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--sl-atr", type=float, default=2.0)
    parser.add_argument("--trail-atr", type=float, default=2.5)
    parser.add_argument("--timeframe", type=str, default="H4")
    parser.add_argument("--min-bars-between", type=int, default=20)
    parser.set_defaults(log_file=str(ROOT / "logs" / "btc_trend_live.log"))
    parser.set_defaults(interval=300.0, risk_percent=0.5)
    return parser.parse_args()


class BtcTrendWatcher(BaseWatcher):
    SETUP = SETUP
    LOGGER_NAME = "btc_trend_watcher"
    DEFAULT_INTERVAL = 300.0
    DEFAULT_RISK_PERCENT = 0.5

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)
        self._last_trade_time: dict[str, float] = {}

    def symbols(self) -> list[str]:
        return list(BTC_SYMBOLS)

    def client_key(self) -> str | None:
        return "BTCUSD"

    def volume_defaults(self) -> tuple[float, float]:
        return (0.01, 1.0)

    def detect_signal(self, symbol: str) -> Signal | None:
        client = self._client
        frame = client.rates(symbol, self.args.timeframe, 500)
        if frame is None or len(frame) < self.args.ema_slow + 50:
            self.logger.warning("%s: insufficient data", symbol)
            return None

        close = frame["close"]
        high = frame["high"]
        low = frame["low"]

        ema_fast = _ema(close, self.args.ema_fast)
        ema_slow = _ema(close, self.args.ema_slow)
        atr_series = _atr(high, low, close, self.args.atr_period)

        ema_f = ema_fast.iloc[-2]
        ema_s = ema_slow.iloc[-2]
        atr_val = atr_series.iloc[-2]
        current_price = close.iloc[-2]

        if pd.isna(ema_f) or pd.isna(ema_s) or pd.isna(atr_val) or atr_val <= 0:
            return None

        trend_up = ema_f > ema_s

        self.logger.debug(
            "%s: price=%.2f ema_f=%.2f ema_s=%.2f atr=%.2f trend_up=%s",
            symbol, current_price, ema_f, ema_s, atr_val, trend_up,
        )

        if trend_up:
            if not (ema_s < current_price < ema_f):
                return None
            return Signal(
                symbol=symbol, side="BUY",
                entry=float(ema_f), sl=float(ema_f - atr_val * self.args.sl_atr),
                confidence=60.0,
                metadata={"atr": float(atr_val), "trend": "bull"},
            )
        else:
            if not (ema_f < current_price < ema_s):
                return None
            return Signal(
                symbol=symbol, side="SELL",
                entry=float(ema_f), sl=float(ema_f + atr_val * self.args.sl_atr),
                confidence=60.0,
                metadata={"atr": float(atr_val), "trend": "bear"},
            )

    def compliance_gate(self, symbol: str, signal: Signal) -> tuple[bool, list[str]]:
        from mt5_mcp.pipeline import run_gates
        gate = run_gates(
            symbol=symbol, side=signal.side, lots=signal.lots or 0.01,
            sl_price=signal.sl, confidence=signal.confidence,
            source=SETUP, score=signal.confidence,
        )
        return gate.allowed, gate.violations

    def build_order(self, signal: Signal) -> tuple[str, float, float, float | None, float]:
        order_type = "buy_stop" if signal.side == "BUY" else "sell_stop"
        return order_type, signal.entry, signal.sl, None, signal.lots or 0.0

    def _cooldown_ok(self, symbol: str) -> bool:
        last = self._last_trade_time.get(symbol)
        if last and (time.time() - last) < self.args.min_bars_between * 60:
            return False
        return True

    def scan_once(self, client, dry_run: bool = False) -> int:
        placed = 0
        for symbol in self.symbols():
            if self._has_exposure(client, symbol):
                continue
            if not self._cooldown_ok(symbol):
                continue

            signal = self.detect_signal(symbol)
            if signal is None:
                continue

            lots = self._lot_size(client, symbol, signal.entry, signal.sl)
            if lots is None:
                continue
            signal.lots = lots

            allowed, violations = self.compliance_gate(symbol, signal)
            if not allowed:
                self.logger.info("compliance blocked %s %s: %s", symbol, signal.side, violations)
                continue

            self.logger.info(
                "%s %s signal: entry=%.2f sl=%.2f atr=%.2f lots=%.2f",
                symbol, signal.side, signal.entry, signal.sl,
                signal.metadata.get("atr", 0), lots,
            )

            if dry_run:
                self.logger.info("%s dry-run: no order submitted", symbol)
                continue

            order_type, price, sl, tp, _lots = self.build_order(signal)
            result = self._place_order(client, order_type, symbol, signal.side, lots, price, sl, tp)

            if result and getattr(result, "accepted", False):
                self._last_trade_time[symbol] = time.time()
                self._record_and_notify(result, signal)
                placed += 1
            elif result:
                self.logger.warning("%s order failed: %s", symbol, result.error)
        return placed

    def _record_and_notify(self, result, signal: Signal) -> None:
        from mt5_mcp.memory.trade_journal import get_journal
        ticket = int(result.ticket) if result.ticket else None
        try:
            get_journal().record_trade(
                ticket=ticket,
                symbol=signal.symbol,
                side=signal.side,
                setup=SETUP,
                entry=signal.entry,
                sl=signal.sl,
                tp=None,
                lots=signal.lots,
                open_time=datetime.now(timezone.utc).isoformat(),
                reason=f"{signal.symbol} EMA pullback {signal.side}",
                market_conditions=f"atr={signal.metadata.get('atr', 0):.2f};trend={signal.metadata.get('trend', '')}",
                confidence=signal.confidence,
            )
        except Exception as exc:
            self.logger.warning("journal record failed: %s", exc)
        self._notify_order(result, signal)


def main() -> int:
    args = _parse_args()
    watcher = BtcTrendWatcher(args)
    return watcher.run()


if __name__ == "__main__":
    raise SystemExit(main())
