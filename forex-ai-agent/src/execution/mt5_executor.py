"""MT5 execution interface with strict mode gating.

Modes:
    BACKTEST — no terminal contact; used by the backtester.
    PAPER    — records simulated fills in a journal JSON; default.
    DEMO     — real MT5 orders on a demo account (explicit config).
    LIVE     — real MT5 orders; requires BOTH config execution.mode: LIVE AND
               env MT5_ALLOW_LIVE=1. Double-gated so accidental live trading is
               impossible.

The AI only produces decisions; this module decides HOW/WHETHER an order is
submitted and records everything.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import Config

MODES = ("BACKTEST", "PAPER", "DEMO", "LIVE")


@dataclass
class OrderRequest:
    symbol: str
    side: str  # BUY | SELL
    lots: float
    entry: float
    sl: float
    tp: float
    timeframe: str = ""
    reason: str = ""
    meta: dict = field(default_factory=dict)


class ExecutionError(RuntimeError):
    pass


class MT5Executor:
    def __init__(self, cfg: Config, mode: str | None = None):
        self.cfg = cfg
        self.mode = (mode or str(cfg.get("execution.mode", "PAPER"))).upper()
        if self.mode not in MODES:
            raise ValueError(f"execution mode must be one of {MODES}")
        if self.mode in ("DEMO", "LIVE"):
            self._require_terminal()

    def _require_terminal(self) -> None:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:
            raise ExecutionError("MetaTrader5 package not installed") from exc
        if not mt5.initialize():
            raise ExecutionError(f"MT5 terminal initialize() failed: {mt5.last_error()}")

    @staticmethod
    def _assert_live_authorized(cfg: Config) -> None:
        env_flag = cfg.get("execution.allow_live_env", "MT5_ALLOW_LIVE")
        if cfg.env(env_flag, "0") != "1":
            raise ExecutionError(
                f"LIVE mode blocked: set execution.mode: LIVE in config AND "
                f"{env_flag}=1 in the environment. Both are required."
            )

    def place_order(self, order: OrderRequest) -> dict:
        if self.mode == "BACKTEST":
            raise ExecutionError("BACKTEST mode does not place orders; run the backtester instead.")

        if self.mode == "PAPER":
            return self._paper_fill(order)

        self._assert_live_authorized(self.cfg)
        return self._mt5_order(order)

    def _paper_fill(self, order: OrderRequest) -> dict:
        journal_path = Path(self.cfg.get("paper.journal_dir", "data/paper")) / "journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        if journal_path.exists():
            entries = json.loads(journal_path.read_text(encoding="utf-8"))
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "mode": "PAPER",
            "symbol": order.symbol,
            "side": order.side,
            "lots": order.lots,
            "entry": order.entry,
            "sl": order.sl,
            "tp": order.tp,
            "reason": order.reason,
            "meta": order.meta,
        }
        entries.append(record)
        journal_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        return {"status": "paper_fill_recorded", "journal": str(journal_path), **record}

    def _mt5_order(self, order: OrderRequest) -> dict:
        import MetaTrader5 as mt5

        self._require_terminal()
        symbol_info = mt5.symbol_info(order.symbol)
        if symbol_info is None:
            raise ExecutionError(f"symbol {order.symbol} not available in the terminal")

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": order.symbol,
            "volume": float(order.lots),
            "type": mt5.ORDER_TYPE_BUY if order.side == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": mt5.symbol_info_tick(order.symbol).ask if order.side == "BUY"
                     else mt5.symbol_info_tick(order.symbol).bid,
            "sl": float(order.sl),
            "tp": float(order.tp),
            "deviation": 20,
            "magic": 424242,
            "comment": "forex-ai-agent",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            detail = result.comment if result is not None else "no result"
            raise ExecutionError(f"order rejected: retcode={result.retcode if result else None}, {detail}")
        return {
            "status": "executed",
            "mode": self.mode,
            "order_id": result.order,
            "symbol": order.symbol,
            "side": order.side,
            "lots": order.lots,
            "entry": result.price,
        }

    def mode_label(self) -> str:
        return self.mode


def paper_equity(
    journal_path: Path,
    start_equity: float,
    last_price: float,
    symbol: str,
    pip_value_per_lot: float = 10.0,
) -> float:
    """Mark the paper journal against ``last_price`` and return account equity.

    Open entries whose TP/SL was crossed at ``last_price`` are closed (SL wins
    ties) and their realized P&L is frozen in the journal, so equity = start
    equity + realized P&L of closed positions — idempotent and double-count safe.
    """
    if not journal_path.exists():
        return start_equity
    entries = json.loads(journal_path.read_text(encoding="utf-8"))
    pip = float(pip_size_of(symbol))
    changed = False
    for entry in entries:
        if entry.get("symbol", "").upper() != symbol.upper() or entry.get("status", "open") != "open":
            continue
        side = entry.get("side")
        entry_px = float(entry["entry"])
        sl = float(entry["sl"]) if entry.get("sl") is not None else None
        tp = float(entry["tp"]) if entry.get("tp") is not None else None
        exit_price = None
        if side == "BUY":
            if sl is not None and last_price <= sl:
                exit_price = sl
            elif tp is not None and last_price >= tp:
                exit_price = tp
        elif side == "SELL":
            if sl is not None and last_price >= sl:
                exit_price = sl
            elif tp is not None and last_price <= tp:
                exit_price = tp
        if exit_price is None:
            continue
        direction = 1.0 if side == "BUY" else -1.0
        pnl_pips = (exit_price - entry_px) / pip * direction
        realized = pnl_pips * pip_value_per_lot * float(entry.get("lots", 0.01))
        entry["status"] = "closed"
        entry["exit_price"] = exit_price
        entry["exit_time"] = datetime.now(timezone.utc).isoformat()
        entry["realized_pnl"] = round(realized, 2)
        changed = True
    if changed:
        journal_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    realized = sum(float(e.get("realized_pnl", 0.0)) for e in entries)
    return start_equity + realized


def pip_size_of(symbol: str) -> float:
    from src.utils import pip_size
    return pip_size(symbol)
