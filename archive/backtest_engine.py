"""Proper backtesting engine for strategy validation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mt5_mcp.client import get_client, reset_client
from strategy_framework import get_client_instance

log = logging.getLogger("backtest")


@dataclass
class Trade:
    """Represents a completed trade."""
    ticket: int
    symbol: str
    side: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    sl: float
    tp: float
    lots: float
    pnl: float
    rr: float
    duration_hours: float
    setup: str


@dataclass
class BacktestResult:
    """Result of a backtest run."""
    strategy_name: str
    symbol: str
    start_date: datetime
    end_date: datetime
    total_bars: int
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    max_drawdown_pct: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    sharpe_ratio: float
    expectancy: float
    trades: list


class BacktestEngine:
    """Backtesting engine for strategies."""

    def __init__(self, strategy_instance, symbol: str, 
                 start_date: datetime, end_date: datetime,
                 timeframe: str = "M15"):
        self.strategy = strategy_instance
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.timeframe = timeframe
        self.client = None
        self.data = None

    def run(self) -> 'BacktestResult':
        """Run backtest."""
        self.client = get_client_instance()
        
        # Fetch historical data
        log.info(f"Fetching {self.timeframe} data for {self.symbol}...")
        data = self._fetch_data()
        
        if data is None or len(data) < 100:
            log.error(f"Insufficient data for {self.symbol}")
            return self._empty_result()

        log.info(f"Backtesting {self.strategy.name} on {self.symbol} ({len(data)} {self.timeframe} bars)")
        
        # Run simulation
        trades = self._run_simulation(data)
        
        return self._calculate_results(trades, data)

    def _fetch_data(self) -> pd.DataFrame | None:
        """Fetch historical OHLCV data."""
        try:
            # Calculate bars needed
            days = (self.end_date - self.start_date).days
            if self.timeframe == "M5":
                bars_per_day = 288
            elif self.timeframe == "M15":
                bars_per_day = 96
            elif self.timeframe == "H1":
                bars_per_day = 24
            else:
                bars_per_day = 24
            
            count = min(days * bars_per_day, 50000)
            
            data = self.client.rates(self.symbol, self.timeframe, count)
            if data is None or len(data) < 100:
                return None
            
            data = data.copy()
            data['time'] = pd.to_datetime(data['time'], utc=True)
            
            # Filter by date range
            data = data[
                (data['time'] >= self.start_date) & 
                (data['time'] <= self.end_date)
            ].reset_index(drop=True)
            
            # Set time as index for proper datetime operations
            data = data.set_index('time')
            
            # Ensure numeric types
            for col in ['open', 'high', 'low', 'close']:
                data[col] = pd.to_numeric(data[col], errors='coerce')
            
            return data.dropna()
        except Exception as e:
            log.error(f"Data fetch failed: {e}")
            return None

    def _run_simulation(self, data: pd.DataFrame) -> list:
        """Run simulation on historical data using strategy's logic."""
        trades = []
        
        log.info(f"Running simulation on {len(data)} bars...")
        
        # Pre-compute indicators for speed
        # Resample to H1 for EMA200 (index is now DatetimeIndex)
        data_h1 = data.resample('h').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
        }).dropna()
        
        if len(data_h1) >= 220:
            ema200 = data_h1['close'].ewm(span=200, adjust=False).mean()
            data_h1['ema200'] = ema200
            data_h1['trend'] = 'flat'
            data_h1.loc[data_h1['close'] > data_h1['ema200'] * 1.0005, 'trend'] = 'bull'
            data_h1.loc[data_h1['close'] < data_h1['ema200'] * 0.9995, 'trend'] = 'bear'
        else:
            data_h1['trend'] = 'flat'
            data_h1['ema200'] = 0
        
        # Map H1 trend back to M15 using forward fill on index
        # Map H1 trend back to M15
        data['h1_trend'] = pd.Series(data.index).map(data_h1['trend']).ffill()
        
        # Compute ATR on M15
        high = data['high']
        low = data['low']
        close = data['close']
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        data['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()
        
        # Donchian range (24 bars)
        data['range_high'] = data['high'].rolling(window=24).max().shift(1)
        data['range_low'] = data['low'].rolling(window=24).min().shift(1)
        data['range_height'] = data['range_high'] - data['range_low']
        
        # ATR for buffer
        data['buffer'] = data['atr'] * 0.5
        
        # Simulate trades
        open_position = None
        ticket_counter = 1000000
        
        for i in range(100, len(data)):  # Start after indicators are ready
            row = data.iloc[i]
            prev_row = data.iloc[i-1]
            
            if pd.isna(row['atr']) or pd.isna(row['range_high']) or pd.isna(row['h1_trend']):
                continue
            
            # Check for exit first
            if open_position is not None:
                exit_reason = None
                exit_price = None
                
                if open_position['side'] == 'BUY':
                    if prev_row['low'] <= open_position['sl']:
                        exit_price = open_position['sl']
                        exit_reason = 'SL'
                    elif prev_row['high'] >= open_position['tp']:
                        exit_price = open_position['tp']
                        exit_reason = 'TP'
                else:  # SELL
                    if prev_row['high'] >= open_position['sl']:
                        exit_price = open_position['sl']
                        exit_reason = 'SL'
                    elif prev_row['low'] <= open_position['tp']:
                        exit_price = open_position['tp']
                        exit_reason = 'TP'
                
                if exit_reason:
                    pnl = (exit_price - open_position['entry']) * open_position['lots'] * 100000 if open_position['side'] == 'BUY' else (open_position['entry'] - exit_price) * open_position['lots'] * 100000
                    duration = (data.index[i] - open_position['entry_time']).total_seconds() / 3600
                    
                    trades.append({
                        'ticket': open_position['ticket'],
                        'symbol': self.symbol,
                        'side': open_position['side'],
                        'entry_time': open_position['entry_time'],
                        'entry_price': open_position['entry'],
                        'exit_time': data.index[i],
                        'exit_price': exit_price,
                        'sl': open_position['sl'],
                        'tp': open_position['tp'],
                        'lots': open_position['lots'],
                        'pnl': pnl,
                        'rr': open_position['rr'],
                        'duration_hours': duration,
                        'setup': open_position['setup']
                    })
                    open_position = None
            
            # Check for new entry (only if no open position)
            if open_position is None and row['h1_trend'] != 'flat':
                atr = row['atr']
                buffer = row['buffer']
                range_high = row['range_high']
                range_low = row['range_low']
                trend = row['h1_trend']
                
                # Get current price (use close as proxy)
                current_price = row['close']
                
# Long breakout
                if row['h1_trend'] == 'bull' and current_price >= row['range_high'] - row['buffer']:
                    entry = row['range_high'] + row['buffer']
                    sl = row['range_low'] - 0.8 * row['atr']
                    tp = entry + 1.5 * (entry - sl)
                    rr = 1.5
                    
                    lots = 0.1  # Fixed for backtest
                    
                    open_position = {
                        'ticket': 1000000 + len(trades),
                        'side': 'BUY',
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'lots': lots,
                        'rr': rr,
                        'entry_time': data.index[i],  # Store datetime index
                        'setup': 'oil_breakout_buy'
                    }
                
                # Short breakout
                elif row['h1_trend'] == 'bear' and current_price <= row['range_low'] + row['buffer']:
                    entry = row['range_low'] - row['buffer']
                    sl = row['range_high'] + 0.8 * row['atr']
                    tp = entry - 1.5 * (sl - entry)
                    rr = 1.5
                    
                    lots = 0.1
                    
                    open_position = {
                        'ticket': 1000000 + len(trades),
                        'side': 'SELL',
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'lots': lots,
                        'rr': rr,
                        'entry_time': data.index[i],
                        'setup': 'oil_breakout_sell'
                    }
        
        # Close any open position at end
        if open_position is not None:
            exit_price = data.iloc[-1]['close']
            pnl = (exit_price - open_position['entry']) * open_position['lots'] * 100000 if open_position['side'] == 'BUY' else (open_position['entry'] - exit_price) * open_position['lots'] * 100000
            duration = (data.index[-1] - open_position['entry_time']).total_seconds() / 3600
            
            trades.append({
                'ticket': open_position['ticket'],
                'symbol': self.symbol,
                'side': open_position['side'],
                'entry_time': open_position['entry_time'],
                'entry_price': open_position['entry'],
                'exit_time': data.index[-1],
                'exit_price': exit_price,
                'sl': open_position['sl'],
                'tp': open_position['tp'],
                'lots': open_position['lots'],
                'pnl': pnl,
                'rr': open_position['rr'],
                'duration_hours': duration,
                'setup': open_position['setup']
            })
        
        # Convert to Trade objects
        trade_objects = []
        for t in trades:
            trade_objects.append(Trade(
                ticket=t['ticket'],
                symbol=t['symbol'],
                side=t['side'],
                entry_time=t['entry_time'],
                entry_price=t['entry_price'],
                exit_time=t['exit_time'],
                exit_price=t['exit_price'],
                sl=t['sl'],
                tp=t['tp'],
                lots=t['lots'],
                pnl=t['pnl'],
                rr=t['rr'],
                duration_hours=t['duration_hours'],
                setup=t['setup']
            ))
        
        log.info(f"Simulation complete: {len(trade_objects)} trades generated")
        return trade_objects

    def _empty_result(self) -> 'BacktestResult':
        return BacktestResult(
            strategy_name=self.strategy.name if hasattr(self.strategy, 'name') else 'unknown',
            symbol=self.symbol,
            start_date=self.start_date,
            end_date=self.end_date,
            total_bars=0,
            total_trades=0, wins=0, losses=0, win_rate=0,
            total_pnl=0, max_drawdown=0, max_drawdown_pct=0,
            profit_factor=0, avg_win=0, avg_loss=0, sharpe_ratio=0,
            expectancy=0, trades=[]
        )

    def _calculate_results(self, trades: list, data: pd.DataFrame) -> 'BacktestResult':
        if not trades:
            return self._empty_result()

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]

        total_pnl = sum(t.pnl for t in trades)
        wins_count = len(wins)
        losses_count = len(losses)
        total_trades = len(trades)
        win_rate = (wins_count / total_trades * 100) if total_trades > 0 else 0

        avg_win = np.mean([t.pnl for t in wins]) if wins else 0
        avg_loss = np.mean([t.pnl for t in losses]) if losses else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
        expectancy = (win_rate/100 * avg_win) + ((100-win_rate)/100 * avg_loss)

        # Max drawdown
        equity = 0
        peak = 0
        max_dd = 0
        max_dd_pct = 0
        for t in trades:
            equity += t.pnl
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = (dd / peak * 100) if peak > 0 else 0

        # Sharpe (simplified)
        returns = [t.pnl for t in trades]
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0

        return BacktestResult(
            strategy_name="backtest",
            symbol=self.symbol,
            start_date=self.start_date,
            end_date=self.end_date,
            total_bars=0,
            total_trades=len(trades),
            wins=len(wins),
            losses=len(losses),
            win_rate=len(wins)/len(trades)*100 if trades else 0,
            total_pnl=sum(t.pnl for t in trades),
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            profit_factor=abs(avg_win / avg_loss) if avg_loss != 0 else float('inf'),
            avg_win=avg_win if wins else 0,
            avg_loss=avg_loss if losses else 0,
            sharpe_ratio=sharpe,
            expectancy=expectancy,
            trades=trades
        )


def run_backtest(strategy_class, symbol: str, days: int = 30, timeframe: str = "M15") -> dict:
    """Quick backtest helper."""
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    log.info(f"Backtest requested for {symbol} over {days} days")
    return {"status": "ready", "message": "Use BacktestEngine directly with strategy instance"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Backtest engine ready - import and use BacktestEngine class")