"""Order lifecycle management for the forex RL trading system.

Handles the full flow from signal to execution:
1. Receive signal from RL agent
2. Pass through RiskManager.can_trade()
3. Compute position size via PositionSizer
4. Calculate SL/TP
5. Send order via MT5Connector
6. Track order state
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any, Optional

from forex_rl.src.execution.mt5_connector import MT5Connector, MT5Error
from forex_rl.src.risk.risk_manager import RiskManager, RiskConfig, ProposedTrade
from forex_rl.src.risk.position_sizing import fixed_fractional

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    ticket: int
    symbol: str
    side: str
    entry_price: float
    volume: float
    sl: Optional[float]
    tp: Optional[float]
    entry_time: datetime
    bars_held: int = 0
    trailing_stop: Optional[float] = None
    trailing_step_pips: float = 50.0
    risk_amount: float = 0.0
    pnl: float = 0.0


@dataclass
class OrderManagerConfig:
    sl_atr_multiplier: float = 2.0
    tp_atr_multiplier: float = 3.0
    trailing_stop_pips: float = 50.0
    trailing_activate_pips: float = 30.0
    max_bars_in_trade: int = 200
    daily_loss_limit: float = 0.02
    daily_profit_target: float = 0.05


class OrderManager:
    """Manages the full lifecycle of orders from signal to close.

    Parameters
    ----------
    connector : MT5Connector
        Active MT5 connection.
    risk_manager : RiskManager
        Risk gatekeeper instance.
    config : dict, optional
        Override defaults. See OrderManagerConfig.
    """

    def __init__(
        self,
        connector: MT5Connector,
        risk_manager: RiskManager,
        config: Optional[dict] = None,
    ) -> None:
        self.connector = connector
        self.risk_manager = risk_manager
        cfg_data = config or {}
        self.sl_atr_mult = cfg_data.get("sl_atr_multiplier", 2.0)
        self.tp_atr_mult = cfg_data.get("tp_atr_multiplier", 3.0)
        self.trailing_stop_pips = cfg_data.get("trailing_stop_pips", 50.0)
        self.trailing_activate_pips = cfg_data.get("trailing_activate_pips", 30.0)
        self.max_bars_in_trade = cfg_data.get("max_bars_in_trade", 200)
        self.daily_loss_limit = cfg_data.get("daily_loss_limit", 0.02)
        self.daily_profit_target = cfg_data.get("daily_profit_target", 0.05)

        self._tracked: dict[int, TrackedPosition] = {}
        self._today_realized_pnl: float = 0.0
        self._today_start_equity: float = 0.0
        self._trade_count_today: int = 0
        self._last_daily_reset: Optional[date] = None

    # ── Public API ────────────────────────────────────────────────────

    def execute_signal(
        self,
        signal: dict,
        portfolio_state: dict,
        market_state: dict,
    ) -> dict:
        """Execute a trading signal through the full pipeline.

        Parameters
        ----------
        signal : dict
            RL agent signal with keys: ``action`` (BUY/SELL/HOLD),
            ``symbol``, ``confidence``.
        portfolio_state : dict
            Current portfolio state for risk checks.
        market_state : dict
            Current market data (price, ATR, spread).

        Returns
        -------
        dict with keys: ``success``, ``reason``, ``ticket``, ``lots``, etc.
        """
        self._maybe_reset_daily()

        action = signal.get("action", "HOLD").upper()
        symbol = signal.get("symbol", "")
        confidence = signal.get("confidence", 0.0)

        if action == "HOLD":
            return {"success": True, "reason": "HOLD signal", "ticket": None}

        if not symbol:
            return {"success": False, "reason": "No symbol in signal"}

        current_price = market_state.get("current_price", 0.0)
        atr = market_state.get("atr", 0.0)
        spread_pips = market_state.get("spread_pips", 1.5)

        if current_price <= 0:
            return {"success": False, "reason": "Invalid current price"}

        try:
            symbol_info = self.connector.get_symbol_info(symbol)
        except MT5Error as exc:
            return {"success": False, "reason": f"Symbol info error: {exc}"}

        tick_value = symbol_info.get("tick_value", 1.0)
        tick_size = symbol_info.get("tick_size", 0.0001)
        point = symbol_info.get("point", 0.0001)

        sl_dist = atr * self.sl_atr_mult if atr > 0 else current_price * 0.005
        tp_dist = atr * self.tp_atr_mult if atr > 0 else current_price * 0.0075

        if action == "BUY":
            entry = current_price + spread_pips * point * 0.5
            sl = entry - sl_dist
            tp = entry + tp_dist
        elif action == "SELL":
            entry = current_price - spread_pips * point * 0.5
            sl = entry + sl_dist
            tp = entry - tp_dist
        else:
            return {"success": False, "reason": f"Unknown action: {action}"}

        balance = portfolio_state.get("equity", 0.0)
        lots = fixed_fractional(
            balance=balance,
            risk_pct=self.risk_manager.cfg.risk_per_trade,
            sl_distance=sl_dist,
            tick_value=tick_value,
        )
        lots = max(lots, symbol_info.get("lot_min", 0.01))
        lots = min(lots, symbol_info.get("lot_max", 100.0))
        lot_step = symbol_info.get("lot_step", 0.01)
        if lot_step > 0:
            lots = round(lots / lot_step) * lot_step
        lots = round(lots, 2)

        proposed = ProposedTrade(
            symbol=symbol,
            side=action,
            entry_price=entry,
            sl_price=sl,
            tp_price=tp,
            lots=lots,
            tick_value=tick_value,
            tick_size=tick_size,
            spread_pips=spread_pips,
        )

        allowed, reason = self.risk_manager.can_trade(portfolio_state, proposed.__dict__)
        if not allowed:
            logger.info("Trade rejected by risk manager: %s", reason)
            return {"success": False, "reason": reason}

        try:
            result = self.connector.send_order(
                symbol=symbol,
                side=action,
                lots=lots,
                sl=sl,
                tp=tp,
                comment=f"RL-{confidence:.0f}",
            )
        except MT5Error as exc:
            logger.error("Order send failed: %s", exc)
            return {"success": False, "reason": str(exc)}

        ticket = result.get("order", result.get("deal", 0))
        if ticket:
            self._tracked[int(ticket)] = TrackedPosition(
                ticket=int(ticket),
                symbol=symbol,
                side=action,
                entry_price=entry,
                volume=lots,
                sl=sl,
                tp=tp,
                entry_time=datetime.utcnow(),
                risk_amount=self.risk_manager._estimate_trade_risk(proposed.__dict__),
            )
            self.risk_manager.increment_trade_count()
            self._trade_count_today += 1
            logger.info(
                "Order executed: %s %s %.2f lots @ %.5f SL=%.5f TP=%.5f ticket=%d",
                action, symbol, lots, entry, sl, tp, ticket,
            )

        return {
            "success": True,
            "reason": "FILLED",
            "ticket": int(ticket) if ticket else None,
            "lots": lots,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "symbol": symbol,
            "side": action,
        }

    def manage_open_positions(self, current_prices: dict[str, float]) -> list[dict]:
        """Check all open positions for SL/TP hits, trailing stops, timeout.

        Parameters
        ----------
        current_prices : dict
            Mapping of symbol → current mid-price.

        Returns
        -------
        List of action dicts (close reasons, etc.).
        """
        actions: list[dict] = []
        tickets_to_close: list[tuple[int, str]] = []

        for ticket, pos in list(self._tracked.items()):
            price = current_prices.get(pos.symbol)
            if price is None:
                continue

            pos.bars_held += 1

            if pos.side == "BUY":
                pnl_pips = (price - pos.entry_price) / (pos.entry_price * 0.0001) if pos.entry_price > 0 else 0
            else:
                pnl_pips = (pos.entry_price - price) / (pos.entry_price * 0.0001) if pos.entry_price > 0 else 0

            if pnl_pips >= self.trailing_activate_pips and pos.trailing_stop is None:
                if pos.side == "BUY":
                    pos.trailing_stop = price - self.trailing_stop_pips * pos.entry_price * 0.0001
                else:
                    pos.trailing_stop = price + self.trailing_stop_pips * pos.entry_price * 0.0001
                pos.trailing_step_pips = self.trailing_stop_pips

            if pos.trailing_stop is not None:
                if pos.side == "BUY" and price > pos.trailing_stop + 10 * pos.entry_price * 0.0001:
                    new_trail = price - self.trailing_stop_pips * pos.entry_price * 0.0001
                    if new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
                elif pos.side == "SELL" and price < pos.trailing_stop - 10 * pos.entry_price * 0.0001:
                    new_trail = price + self.trailing_stop_pips * pos.entry_price * 0.0001
                    if new_trail < pos.trailing_stop:
                        pos.trailing_stop = new_trail

            if pos.side == "BUY":
                if pos.trailing_stop is not None and price <= pos.trailing_stop:
                    tickets_to_close.append((ticket, "trailing_stop"))
                elif pos.sl is not None and price <= pos.sl:
                    tickets_to_close.append((ticket, "sl"))
                elif pos.tp is not None and price >= pos.tp:
                    tickets_to_close.append((ticket, "tp"))
            else:
                if pos.trailing_stop is not None and price >= pos.trailing_stop:
                    tickets_to_close.append((ticket, "trailing_stop"))
                elif pos.sl is not None and price >= pos.sl:
                    tickets_to_close.append((ticket, "sl"))
                elif pos.tp is not None and price <= pos.tp:
                    tickets_to_close.append((ticket, "tp"))

            if pos.bars_held >= self.max_bars_in_trade:
                tickets_to_close.append((ticket, "timeout"))

        for ticket, reason in tickets_to_close:
            pos = self._tracked.get(ticket)
            if pos is None:
                continue
            try:
                success = self.connector.close_position(ticket)
                if success:
                    price = current_prices.get(pos.symbol, pos.entry_price)
                    if pos.side == "BUY":
                        pnl = (price - pos.entry_price) * pos.volume * 10000.0
                    else:
                        pnl = (pos.entry_price - price) * pos.volume * 10000.0
                    pos.pnl = pnl
                    self._today_realized_pnl += pnl
                    actions.append({
                        "action": "close",
                        "ticket": ticket,
                        "reason": reason,
                        "pnl": pnl,
                        "symbol": pos.symbol,
                    })
                    del self._tracked[ticket]
                    logger.info("Closed %d (%s): pnl=%.2f reason=%s", ticket, pos.symbol, pnl, reason)
            except Exception as exc:
                logger.error("Failed to close %d: %s", ticket, exc)

        return actions

    def sync_with_broker(self) -> None:
        """Reconcile tracked positions with actual broker positions."""
        broker_positions = self.connector.get_positions()
        broker_tickets = {int(p.get("ticket", 0)) for p in broker_positions}
        orphaned = [t for t in self._tracked if t not in broker_tickets]
        for ticket in orphaned:
            pos = self._tracked.pop(ticket, None)
            if pos:
                logger.warning("Position %d no longer on broker, removing from tracker", ticket)

    def get_daily_pnl(self) -> float:
        """Calculate today's realized + unrealized P&L."""
        self._maybe_reset_daily()
        return self._today_realized_pnl

    def get_open_risk(self) -> float:
        """Total risk of all open positions as fraction of equity."""
        total_risk = sum(pos.risk_amount for pos in self._tracked.values())
        try:
            account = self.connector.get_account()
            equity = account.get("equity", 1.0)
        except Exception:
            equity = 1.0
        return total_risk / equity if equity > 0 else 0.0

    def get_tracked_positions(self) -> dict[int, TrackedPosition]:
        return dict(self._tracked)

    def get_portfolio_state(self) -> dict:
        """Build a portfolio state dict for the risk manager."""
        try:
            account = self.connector.get_account()
        except Exception:
            account = {"equity": 0.0, "balance": 0.0}

        equity = account.get("equity", 0.0)
        balance = account.get("balance", 0.0)

        return {
            "equity": equity,
            "balance": balance,
            "drawdown": 0.0,
            "max_drawdown": 0.0,
            "total_trades": self._trade_count_today,
            "open_positions": len(self._tracked),
            "daily_pnl": self._today_realized_pnl,
            "open_risk": self.get_open_risk(),
            "consecutive_losses": 0,
            "bars_since_last_trade": 0,
            "open_position_details": [],
        }

    # ── Private ───────────────────────────────────────────────────────

    def _maybe_reset_daily(self) -> None:
        today = date.today()
        if self._last_daily_reset != today:
            if self._today_start_equity == 0.0:
                try:
                    account = self.connector.get_account()
                    self._today_start_equity = account.get("equity", 0.0)
                except Exception:
                    self._today_start_equity = 0.0
            self._today_realized_pnl = 0.0
            self._trade_count_today = 0
            self._last_daily_reset = today
            self.risk_manager.set_daily_start(self._today_start_equity)
            logger.info("Daily reset: start_equity=%.2f", self._today_start_equity)
