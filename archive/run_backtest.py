"""Run backtests on implemented strategies."""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_engine import BacktestEngine
from oil_breakout_strategy import OilBreakoutStrategy, OilBreakoutConfig
from ger40_london_open_strategy import GER40LondonOpenStrategy, GER40LondonOpenConfig
from strategy_framework import get_client_instance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("run_backtest")


def run_oil_breakout_backtest(symbol: str = "USOIL", days: int = 30):
    """Run backtest for Oil Breakout strategy."""
    log.info(f"=== Backtesting Oil Breakout on {symbol} ===")
    
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    config = OilBreakoutConfig(symbol=symbol)
    strategy = OilBreakoutStrategy(config)
    
    # Get client for strategy
    from mt5_mcp.client import get_client, reset_client
    reset_client()
    client = get_client()
    client.ensure_connected()
    strategy.client = client
    
    engine = BacktestEngine(
        strategy_instance=strategy,
        symbol=symbol,
        start_date=datetime.now(timezone.utc) - timedelta(days=days),
        end_date=datetime.now(timezone.utc),
        timeframe="M15"
    )
    
    result = engine.run()
    print_result(result)
    return result


def print_result(result):
    """Pretty print backtest results."""
    print(f"\n{'='*60}")
    print(f"BACKTEST RESULT: {result.strategy_name} on {result.symbol}")
    print(f"{'='*60}")
    print(f"Period: {result.start_date.strftime('%Y-%m-%d')} to {result.end_date.strftime('%Y-%m-%d')}")
    print(f"Total Trades: {result.total_trades}")
    print(f"Wins: {result.wins} | Losses: {result.losses}")
    print(f"Win Rate: {result.win_rate:.2f}%")
    print(f"Total P&L: {result.total_pnl:.2f}")
    print(f"Max Drawdown: {result.max_drawdown:.2f} ({result.max_drawdown_pct:.2f}%)")
    print(f"Profit Factor: {result.profit_factor:.2f}")
    print(f"Avg Win: {result.avg_win:.2f}")
    print(f"Avg Loss: {result.avg_loss:.2f}")
    print(f"Expectancy: {result.expectancy:.2f}")
    print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
    
    if result.trades:
        print(f"\nFirst 5 trades:")
        for i, t in enumerate(result.trades[:5]):
            print(f"  {t.side} {t.symbol} @ {t.entry_price:.5f} -> {t.exit_price:.5f} | P&L: {t.pnl:.2f} | {t.duration_hours:.1f}h")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    
    print("=" * 60)
    print("BACKTESTING STRATEGIES")
    print("=" * 60)
    
    # Test Oil Breakout on USOIL
    print("\n[1/2] Testing Oil Breakout on USOIL...")
    try:
        result = run_oil_breakout_backtest("USOIL", days=14)
    except Exception as e:
        print(f"USOIL backtest failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*60)