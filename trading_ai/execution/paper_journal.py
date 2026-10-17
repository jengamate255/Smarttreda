"""Bridge: PaperBroker closed positions → TradeJournal (memory stream for research)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from trading_ai.execution.paper import Position, PositionSide, PaperBroker
from trading_ai.memory.journal import (
    TradeJournal,
    TradeEntry,
    MarketRegime,
    SessionType,
    ExitReason,
    TradeOutcome,
)

logger = logging.getLogger(__name__)

_SESSION_MAP = {
    "asian": SessionType.ASIAN,
    "london": SessionType.LONDON,
    "new_york": SessionType.NEW_YORK,
    "overlap": SessionType.LONDON_NY_OVERLAP,
    "london_ny_overlap": SessionType.LONDON_NY_OVERLAP,
    "off": SessionType.OFF_HOURS,
    "off_hours": SessionType.OFF_HOURS,
}


def _map_session(raw: str | None) -> SessionType:
    if not raw:
        return SessionType.LONDON
    key = str(raw).lower().replace(" ", "_")
    return _SESSION_MAP.get(key, SessionType.LONDON)


def _map_regime(raw: str | None) -> MarketRegime:
    if not raw:
        return MarketRegime.RANGING
    key = str(raw).lower()
    for r in MarketRegime:
        if r.value in key or key in r.value:
            return r
    if "up" in key or "bull" in key:
        return MarketRegime.TRENDING_UP
    if "down" in key or "bear" in key:
        return MarketRegime.TRENDING_DOWN
    return MarketRegime.RANGING


def position_to_trade_entry(
    pos: Position,
    exit_price: float,
    strategy_id: str,
    session: str | None = None,
    regime: str | None = None,
    entry_reason: str = "paper_fill",
) -> TradeEntry:
    risk = abs(pos.entry_price - (pos.stop_loss or pos.entry_price))
    reward = abs(exit_price - pos.entry_price)
    if pos.side == PositionSide.SHORT:
        risk = abs((pos.stop_loss or pos.entry_price) - pos.entry_price)
        reward = abs(pos.entry_price - exit_price)

    r_multiple = (reward / risk) if risk > 0 and pos.realized_pnl >= 0 else (
        -(abs(pos.entry_price - exit_price) / risk) if risk > 0 else 0.0
    )
    # Simpler R based on PnL sign and initial risk
    if risk > 0:
        if pos.realized_pnl >= 0:
            r_multiple = abs(exit_price - pos.entry_price) / risk
        else:
            r_multiple = -abs(exit_price - pos.entry_price) / risk

    outcome = TradeOutcome.BREAKEVEN
    if pos.realized_pnl > 0.01:
        outcome = TradeOutcome.WIN
    elif pos.realized_pnl < -0.01:
        outcome = TradeOutcome.LOSS

    duration = 0.0
    if pos.opened_at and pos.last_update:
        try:
            duration = (pos.last_update - pos.opened_at).total_seconds() / 60.0
        except Exception:
            duration = 0.0

    meta = getattr(pos, "metadata", None) or {}
    exit_reason = ExitReason.MANUAL
    # Infer SL/TP hit
    if pos.stop_loss is not None and abs(exit_price - pos.stop_loss) < 1e-10:
        exit_reason = ExitReason.STOP_LOSS
    elif pos.take_profit is not None and abs(exit_price - pos.take_profit) < 1e-10:
        exit_reason = ExitReason.TAKE_PROFIT

    direction = "BUY" if pos.side == PositionSide.LONG else "SELL"
    return TradeEntry(
        timestamp=pos.opened_at.replace(tzinfo=None) if pos.opened_at.tzinfo else pos.opened_at,
        strategy_version=str(meta.get("strategy_id") or strategy_id),
        market_regime=_map_regime(regime or meta.get("regime")),
        symbol=pos.symbol,
        timeframe=str(meta.get("timeframe") or "M5"),
        session=_map_session(session or meta.get("session")),
        direction=direction,
        entry_price=pos.entry_price,
        stop_loss=pos.stop_loss or pos.entry_price,
        take_profit=pos.take_profit or pos.entry_price,
        spread_at_entry=float(meta.get("spread", 0.00015)),
        volatility_at_entry=float(meta.get("atr", 0.0)),
        entry_reason=entry_reason,
        features_snapshot=dict(meta),
        outcome=outcome,
        exit_price=exit_price,
        exit_time=(pos.last_update.replace(tzinfo=None) if pos.last_update else datetime.utcnow()),
        exit_reason=exit_reason,
        r_multiple=round(r_multiple, 4),
        duration_minutes=round(duration, 2),
        pnl_currency=round(pos.realized_pnl, 2),
    )


class PaperJournalBridge:
    """Subscribes to PaperBroker position closes and writes them to TradeJournal."""

    def __init__(
        self,
        broker: PaperBroker,
        journal: TradeJournal,
        strategy_id: str = "paper-default",
        session_provider: Callable[[], str] | None = None,
        regime_provider: Callable[[], str] | None = None,
    ):
        self.broker = broker
        self.journal = journal
        self.strategy_id = strategy_id
        self.session_provider = session_provider
        self.regime_provider = regime_provider
        self.recorded = 0
        # Hook into broker (always write to journal, then chain any prior hook)
        self._prev = getattr(broker, "on_position_closed", None)
        broker.on_position_closed = self._on_close  # type: ignore[attr-defined]

    def _on_close(self, position: Position, exit_price: float) -> None:
        try:
            session = self.session_provider() if self.session_provider else None
            regime = self.regime_provider() if self.regime_provider else None
            entry = position_to_trade_entry(
                position, exit_price, self.strategy_id, session=session, regime=regime
            )
            self.journal.record_trade(entry)
            self.recorded += 1
            logger.debug(
                "Journal recorded paper trade #%d %s pnl=%.2f",
                self.recorded, position.symbol, position.realized_pnl,
            )
        except Exception:
            logger.exception("Failed to journal closed paper position")
        if callable(self._prev):
            try:
                self._prev(position, exit_price)
            except Exception:
                pass
