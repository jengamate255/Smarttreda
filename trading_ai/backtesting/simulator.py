"""TradeSimulator: manages individual trade lifecycle with fill simulation."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trading_ai.models import (
    BacktestConfig,
    ExitReason,
    OpenPosition,
    Side,
    TradeRecord,
)


@dataclass
class FillResult:
    filled: bool = False
    fill_price: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    commission: float = 0.0
    reject_reason: str = ""


class TradeSimulator:
    def __init__(self, config: BacktestConfig, rng: random.Random | None = None) -> None:
        self.config = config
        self.rng = rng or random.Random(config.seed)
        self._commission_cache: dict[str, float] = {}

    def simulate_entry(
        self,
        symbol: str,
        side: Side,
        signal_price: float,
        volume: float,
        timestamp: datetime,
    ) -> FillResult:
        spread = self._get_spread(symbol)
        spread_cost = spread * volume

        if side == Side.BUY:
            fill_price = signal_price + spread / 2
        else:
            fill_price = signal_price - spread / 2

        slippage = self._get_slippage(symbol)
        slippage_cost = slippage * volume

        if side == Side.BUY:
            fill_price += slippage
        else:
            fill_price -= slippage

        commission = self._get_commission(volume)

        return FillResult(
            filled=True,
            fill_price=fill_price,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            commission=commission,
        )

    def create_position(
        self,
        trade_id: int,
        symbol: str,
        side: Side,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        volume: float,
        entry_time: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> OpenPosition:
        return OpenPosition(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            entry_time=entry_time,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            volume=volume,
            initial_volume=volume,
            current_price=entry_price,
            metadata=metadata or {},
        )

    def check_exit(
        self,
        position: OpenPosition,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
    ) -> tuple[bool, ExitReason, float]:
        if position.side == Side.BUY:
            return self._check_buy_exit(position, bar_open, bar_high, bar_low, bar_close)
        return self._check_sell_exit(position, bar_open, bar_high, bar_low, bar_close)

    def _check_buy_exit(
        self,
        position: OpenPosition,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
    ) -> tuple[bool, ExitReason, float]:
        if bar_low <= position.stop_loss:
            return True, ExitReason.STOP_LOSS, position.stop_loss
        if bar_high >= position.take_profit:
            return True, ExitReason.TAKE_PROFIT, position.take_profit
        if position.trailing_stop is not None and bar_low <= position.trailing_stop:
            return True, ExitReason.TRAILING_STOP, position.trailing_stop
        return False, ExitReason.MANUAL, bar_close

    def _check_sell_exit(
        self,
        position: OpenPosition,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
    ) -> tuple[bool, ExitReason, float]:
        if bar_high >= position.stop_loss:
            return True, ExitReason.STOP_LOSS, position.stop_loss
        if bar_low <= position.take_profit:
            return True, ExitReason.TAKE_PROFIT, position.take_profit
        if position.trailing_stop is not None and bar_high >= position.trailing_stop:
            return True, ExitReason.TRAILING_STOP, position.trailing_stop
        return False, ExitReason.MANUAL, bar_close

    def update_mae_mfe(self, position: OpenPosition, bar_high: float, bar_low: float) -> None:
        if position.side == Side.BUY:
            adverse = position.entry_price - bar_low
            favorable = bar_high - position.entry_price
        else:
            adverse = bar_high - position.entry_price
            favorable = position.entry_price - bar_low

        if adverse > position.mae:
            position.mae = adverse
        if favorable > position.mfe:
            position.mfe = favorable

    def update_trailing_stop(self, position: OpenPosition, bar_high: float, bar_low: float) -> None:
        if not self.config.use_trailing_stop or position.trailing_stop_distance is None:
            return

        if position.side == Side.BUY:
            new_trail = bar_high - position.trailing_stop_distance
            if position.trailing_stop is None or new_trail > position.trailing_stop:
                position.trailing_stop = new_trail
        else:
            new_trail = bar_low + position.trailing_stop_distance
            if position.trailing_stop is None or new_trail < position.trailing_stop:
                position.trailing_stop = new_trail

    def close_position(
        self,
        position: OpenPosition,
        exit_price: float,
        exit_time: datetime,
        exit_reason: ExitReason,
        bars_held: int,
    ) -> TradeRecord:
        pip_value = self.config.pip_value
        if position.side == Side.BUY:
            pnl_pips = (exit_price - position.entry_price) / pip_value
        else:
            pnl_pips = (position.entry_price - exit_price) / pip_value

        pnl = pnl_pips * position.volume * pip_value

        stop_distance_pips = abs(position.entry_price - position.stop_loss) / pip_value
        if stop_distance_pips > 0:
            r_multiple = pnl_pips / stop_distance_pips
        else:
            r_multiple = 0.0

        pnl_pct = pnl / self.config.initial_capital if self.config.initial_capital > 0 else 0.0

        return TradeRecord(
            trade_id=position.trade_id,
            symbol=position.symbol,
            side=position.side,
            entry_time=position.entry_time,
            exit_time=exit_time,
            entry_price=position.entry_price,
            exit_price=exit_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            volume=position.volume,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            r_multiple=r_multiple,
            mae=position.mae,
            mfe=position.mfe,
            bars_held=bars_held,
            metadata=position.metadata,
        )

    def partial_close(
        self,
        position: OpenPosition,
        close_pct: float,
        current_price: float,
        exit_time: datetime,
        bars_held: int,
    ) -> tuple[TradeRecord, float]:
        close_volume = position.initial_volume * close_pct
        close_volume = min(close_volume, position.volume)
        remaining_volume = position.volume - close_volume

        original_volume = position.volume
        position.volume = close_volume

        pip_value = self.config.pip_value
        if position.side == Side.BUY:
            pnl_pips = (current_price - position.entry_price) / pip_value
        else:
            pnl_pips = (position.entry_price - current_price) / pip_value

        pnl = pnl_pips * close_volume * pip_value

        stop_distance_pips = abs(position.entry_price - position.stop_loss) / pip_value
        r_multiple = pnl_pips / stop_distance_pips if stop_distance_pips > 0 else 0.0

        record = TradeRecord(
            trade_id=position.trade_id,
            symbol=position.symbol,
            side=position.side,
            entry_time=position.entry_time,
            exit_time=exit_time,
            entry_price=position.entry_price,
            exit_price=current_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            volume=close_volume,
            pnl=pnl,
            pnl_pct=pnl / self.config.initial_capital if self.config.initial_capital > 0 else 0.0,
            exit_reason=ExitReason.PARTIAL,
            r_multiple=r_multiple,
            mae=position.mae,
            mfe=position.mfe,
            bars_held=bars_held,
            metadata={**position.metadata, "partial_close_pct": close_pct},
        )

        position.volume = remaining_volume
        position.initial_volume = remaining_volume
        return record, remaining_volume

    def _get_spread(self, symbol: str) -> float:
        if self.config.spread_model == "fixed":
            return self.config.spread_pips * self.config.pip_value
        if self.config.spread_model == "random":
            base = self.config.spread_pips * self.config.pip_value
            variance = self.rng.uniform(0.5, 2.0) * base
            return variance
        return self.config.spread_pips * self.config.pip_value

    def _get_slippage(self, symbol: str) -> float:
        if self.config.slippage_model == "fixed":
            return self.config.slippage_pips * self.config.pip_value
        if self.config.slippage_model == "random":
            base = self.config.slippage_pips * self.config.pip_value
            return self.rng.uniform(0, 2 * base)
        return 0.0

    def _get_commission(self, volume: float) -> float:
        if self.config.commission_model == "per_lot":
            lots = volume / self.config.lot_size
            return lots * self.config.commission_per_lot
        if self.config.commission_model == "fixed":
            return self.config.commission_per_lot
        return 0.0
