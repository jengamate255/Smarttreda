"""BacktestEngine: event-driven backtesting with no look-ahead bias."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from trading_ai.backtesting.simulator import TradeSimulator
from trading_ai.models import (
    BacktestConfig,
    ExitReason,
    OpenPosition,
    PerformanceMetrics,
    Side,
    Signal,
    TradeRecord,
)


@dataclass
class BacktestResult:
    trades: list[TradeRecord]
    equity_curve: pd.Series
    metrics: PerformanceMetrics
    drawdown_series: pd.Series
    open_positions_history: list[dict[str, Any]] = field(default_factory=list)


class BacktestEngine:
    def __init__(
        self,
        strategy: Callable[..., Signal | None],
        data: pd.DataFrame,
        config: BacktestConfig,
    ) -> None:
        self.strategy = strategy
        self.config = config
        self.data = data.copy()
        self.rng = random.Random(config.seed)
        self.simulator = TradeSimulator(config, self.rng)

        self._validate_data()
        self._reset_state()

    def _validate_data(self) -> None:
        required = {"open", "high", "low", "close"}
        # Accept either a 'timestamp' column or a DatetimeIndex
        has_timestamp_col = "timestamp" in self.data.columns
        has_dt_index = isinstance(self.data.index, pd.DatetimeIndex)
        if not has_timestamp_col and not has_dt_index:
            raise ValueError("Data must have a 'timestamp' column or DatetimeIndex")
        missing = required - set(self.data.columns)
        if missing:
            raise ValueError(f"Data missing required columns: {missing}")

    def _reset_state(self) -> None:
        self.equity = self.config.initial_capital
        self.peak_equity = self.equity
        self.open_positions: list[OpenPosition] = []
        self.closed_trades: list[TradeRecord] = []
        self.equity_history: list[tuple[datetime, float]] = []
        self.drawdown_history: list[tuple[datetime, float]] = []
        self.trade_counter = 0
        self.position_history: list[dict[str, Any]] = []

    def run(self) -> BacktestResult:
        self._reset_state()
        strategy_state: dict[str, Any] = {}

        for i in range(len(self.data)):
            row = self.data.iloc[i]
            bar_open = float(row["open"])
            bar_high = float(row["high"])
            bar_low = float(row["low"])
            bar_close = float(row["close"])
            bar_volume = float(row.get("volume", 0))

            # Handle timestamp from column or index
            if "timestamp" in self.data.columns:
                timestamp = row["timestamp"]
            elif isinstance(self.data.index, pd.DatetimeIndex):
                timestamp = self.data.index[i]
                if hasattr(timestamp, 'to_pydatetime'):
                    timestamp = timestamp.to_pydatetime()
            else:
                timestamp = datetime.now()

            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp)

            self._update_positions(bar_open, bar_high, bar_low, bar_close, timestamp, i)

            lookback = self.data.iloc[max(0, i - 500) : i + 1]
            signal = self.strategy(lookback, strategy_state)

            if signal is not None:
                self._process_signal(signal, timestamp, bar_close)

            unrealized = self._calculate_unrealized_pnl(bar_close)
            total_equity = self.equity + unrealized
            self.equity_history.append((timestamp, total_equity))

            if total_equity > self.peak_equity:
                self.peak_equity = total_equity
            drawdown = (self.peak_equity - total_equity) / self.peak_equity if self.peak_equity > 0 else 0.0
            self.drawdown_history.append((timestamp, drawdown))

            if self._check_kill_switch(total_equity):
                self._close_all_positions(timestamp, bar_close, ExitReason.KILL_SWITCH)
                break

        # Get last timestamp from column or index
        if "timestamp" in self.data.columns:
            last_ts = self.data.iloc[-1]["timestamp"]
        elif isinstance(self.data.index, pd.DatetimeIndex):
            last_ts = self.data.index[-1]
            if hasattr(last_ts, 'to_pydatetime'):
                last_ts = last_ts.to_pydatetime()
        else:
            last_ts = datetime.now()
        if isinstance(last_ts, str):
            last_ts = datetime.fromisoformat(last_ts)

        self._close_all_positions(
            last_ts,
            float(self.data.iloc[-1]["close"]),
            ExitReason.END_OF_DATA,
        )

        return self._build_result()

    def _process_signal(self, signal: Signal, timestamp: datetime, current_price: float) -> None:
        if len(self.open_positions) >= self.config.max_open_positions:
            return

        for pos in self.open_positions:
            if pos.symbol == signal.symbol and pos.side == signal.side:
                return

        stop_distance = abs(signal.entry_price - signal.stop_loss)
        if stop_distance <= 0:
            return

        risk_amount = self.equity * self.config.risk_per_trade
        volume = risk_amount / stop_distance

        if volume <= 0:
            return

        fill = self.simulator.simulate_entry(
            signal.symbol, signal.side, current_price, volume, timestamp
        )
        if not fill.filled:
            return

        position = self.simulator.create_position(
            trade_id=self._next_trade_id(),
            symbol=signal.symbol,
            side=signal.side,
            entry_price=fill.fill_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            volume=volume,
            entry_time=timestamp,
            metadata=signal.metadata,
        )

        if self.config.use_trailing_stop:
            position.trailing_stop_distance = self.config.trailing_stop_pips * self.config.pip_value

        self.open_positions.append(position)
        self.equity -= fill.commission + fill.spread_cost + fill.slippage_cost

        self.position_history.append({
            "timestamp": timestamp,
            "trade_id": position.trade_id,
            "action": "open",
            "symbol": position.symbol,
            "side": position.side.value,
            "price": fill.fill_price,
            "volume": volume,
        })

    def _update_positions(
        self,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        timestamp: datetime,
        bar_index: int,
    ) -> None:
        positions_to_close: list[tuple[OpenPosition, float, ExitReason]] = []

        bar_symbol: str | None = None
        if "symbol" in self.data.columns:
            bar_symbol = str(self.data.iloc[bar_index]["symbol"])

        for position in self.open_positions:
            if bar_symbol is not None and position.symbol != bar_symbol:
                self.simulator.update_mae_mfe(position, bar_high, bar_low)
                self.simulator.update_trailing_stop(position, bar_high, bar_low)
                position.bars_held += 1
                continue

            self.simulator.update_mae_mfe(position, bar_high, bar_low)
            self.simulator.update_trailing_stop(position, bar_high, bar_low)
            position.bars_held += 1
            position.current_price = bar_close

            should_exit, reason, exit_price = self.simulator.check_exit(
                position, bar_open, bar_high, bar_low, bar_close
            )
            if should_exit:
                positions_to_close.append((position, exit_price, reason))

        for position, exit_price, reason in positions_to_close:
            self._close_position(position, exit_price, timestamp, reason)

    def _close_position(
        self,
        position: OpenPosition,
        exit_price: float,
        timestamp: datetime,
        reason: ExitReason,
    ) -> None:
        record = self.simulator.close_position(
            position, exit_price, timestamp, reason, position.bars_held
        )
        self.equity += record.pnl
        self.closed_trades.append(record)

        self.position_history.append({
            "timestamp": timestamp,
            "trade_id": position.trade_id,
            "action": "close",
            "symbol": position.symbol,
            "side": position.side.value,
            "price": exit_price,
            "pnl": record.pnl,
            "reason": reason.value,
        })

        if position in self.open_positions:
            self.open_positions.remove(position)

    def _close_all_positions(
        self, timestamp: datetime, current_price: float, reason: ExitReason
    ) -> None:
        for position in list(self.open_positions):
            self._close_position(position, current_price, timestamp, reason)

    def _calculate_unrealized_pnl(self, current_price: float) -> float:
        total = 0.0
        for position in self.open_positions:
            if position.side == Side.BUY:
                pnl = (current_price - position.entry_price) * position.volume
            else:
                pnl = (position.entry_price - current_price) * position.volume
            total += pnl
        return total

    def _check_kill_switch(self, total_equity: float) -> bool:
        if self.config.initial_capital <= 0:
            return False
        drawdown = (self.config.initial_capital - total_equity) / self.config.initial_capital
        if drawdown >= self.config.max_drawdown:
            return True
        daily_loss = self._calculate_daily_loss()
        if daily_loss >= self.config.max_daily_loss:
            return True
        return False

    def _calculate_daily_loss(self) -> float:
        if not self.equity_history:
            return 0.0
        today_start = self.equity_history[-1][0]
        if isinstance(today_start, str):
            today_start = datetime.fromisoformat(today_start)
        start_equity = self.config.initial_capital
        for ts, eq in self.equity_history:
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts.date() == today_start.date():
                start_equity = eq
                break
        current_equity = self.equity_history[-1][1]
        if start_equity <= 0:
            return 0.0
        return (start_equity - current_equity) / start_equity

    def _get_symbol_from_bar(self, bar_index: int) -> str:
        if "symbol" in self.data.columns:
            return str(self.data.iloc[bar_index]["symbol"])
        return "DEFAULT"

    def _next_trade_id(self) -> int:
        self.trade_counter += 1
        return self.trade_counter

    def _build_result(self) -> BacktestResult:
        equity_series = pd.Series(
            [eq for _, eq in self.equity_history],
            index=pd.DatetimeIndex([ts for ts, _ in self.equity_history]),
            name="equity",
        )
        drawdown_series = pd.Series(
            [dd for _, dd in self.drawdown_history],
            index=pd.DatetimeIndex([ts for ts, _ in self.drawdown_history]),
            name="drawdown",
        )

        from trading_ai.evaluation.performance import PerformanceAnalyzer

        analyzer = PerformanceAnalyzer(self.config)
        metrics = analyzer.calculate(self.closed_trades, equity_series, drawdown_series)

        return BacktestResult(
            trades=self.closed_trades,
            equity_curve=equity_series,
            metrics=metrics,
            drawdown_series=drawdown_series,
            open_positions_history=self.position_history,
        )
