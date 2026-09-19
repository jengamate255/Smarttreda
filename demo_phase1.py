"""Phase 1 Demo - Trading Intelligence Platform in Action."""

import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))

from mt5_mcp.data_schema import NormalizedCandle
from mt5_mcp.orchestrator import ChiefTradingAgent, OrchestratorInput
from mt5_mcp.asset import AssetClass, asset_class_from_symbol
from mt5_mcp.backtest_engine import BacktestConfig, BacktestEngine
from mt5_mcp.mcp_server import TradingMCPServer
from mt5_mcp.dashboard import TradingDashboard


def generate_sample_candles(symbol: str, n: int = 200) -> list[NormalizedCandle]:
    """Generate realistic sample candles."""
    import random
    random.seed(42)

    base_prices = {
        "EURUSD": 1.1000,
        "GBPUSD": 1.2700,
        "USDJPY": 149.50,
        "XAUUSD": 2350.00,
        "BTCUSD": 64000.00,
    }

    base = base_prices.get(symbol, 1.1000)
    candles = []
    timestamp = datetime(2024, 6, 1, tzinfo=timezone.utc)

    price = base
    for i in range(n):
        # Create trending + ranging patterns
        if i < 50:
            trend = 0.0002  # Uptrend
        elif i < 100:
            trend = -0.0001  # Downtrend
        elif i < 150:
            trend = 0.00015  # Uptrend
        else:
            trend = 0  # Ranging

        change = random.gauss(trend, 0.0008)
        price = price * (1 + change)

        open_p = price
        high_p = price * (1 + abs(random.gauss(0, 0.0003)))
        low_p = price * (1 - abs(random.gauss(0, 0.0003)))
        close_p = price * (1 + random.gauss(0, 0.0002))

        candles.append(NormalizedCandle(
            timestamp=timestamp + timedelta(hours=i),
            symbol=symbol,
            timeframe="H1",
            open=open_p,
            high=max(open_p, high_p, close_p),
            low=min(open_p, low_p, close_p),
            close=close_p,
            volume=random.randint(50, 500),
            spread=random.randint(1, 5),
        ))

    return candles


def demo_orchestrator():
    """Demo the full orchestrator pipeline."""
    print("=" * 70)
    print("PHASE 1 DEMO - TRADING INTELLIGENCE PLATFORM")
    print("=" * 70)

    print("\n1. ORCHESTRATOR PIPELINE")
    print("-" * 70)

    agent = ChiefTradingAgent(enable_llm=False)
    candles = generate_sample_candles("EURUSD", 200)

    input_data = OrchestratorInput(
        task_id="demo_001",
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        candles=candles,
    )

    result = agent.run_pipeline(input_data)

    print(f"\nSymbol: EURUSD")
    print(f"Candles analyzed: {len(candles)}")
    print(f"Data quality valid: {result.data_quality.is_valid}")
    print(f"Candidate setups: {len(result.candidate_setups)}")
    print(f"Risk can trade: {result.risk_result.can_trade}")
    print(f"Risk explanation: {result.risk_result.explanation}")
    print(f"Red team observations: {len(result.red_team_observations)}")

    if result.candidate_setups:
        print("\nCandidate Trade Setups:")
        for i, setup in enumerate(result.candidate_setups[:3], 1):
            entry = f"{setup.entry:.5f}" if setup.entry else "N/A"
            sl = f"{setup.stop_loss:.5f}" if setup.stop_loss else "N/A"
            tp = f"{setup.take_profit:.5f}" if setup.take_profit else "N/A"
            rr = f"{setup.expected_rr:.1f}" if setup.expected_rr else "N/A"
            print(f"  {i}. {setup.direction} | Entry: {entry} | "
                  f"SL: {sl} | TP: {tp} | RR: {rr}")

    if result.red_team_observations:
        print("\nRed Team Analysis (first setup):")
        print(f"  {result.red_team_observations[0][:200]}...")

    return result


def candles_to_dicts(candles: list[NormalizedCandle]) -> list[dict]:
    """Convert NormalizedCandle objects to dicts for API calls."""
    return [{
        "timestamp": c.timestamp.isoformat(),
        "symbol": c.symbol,
        "timeframe": c.timeframe,
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "volume": c.volume,
        "spread": c.spread,
    } for c in candles]


def demo_mcp_server():
    """Demo the MCP Server endpoints."""
    print("\n\n2. MCP SERVER ENDPOINTS")
    print("-" * 70)

    server = TradingMCPServer(enable_llm=False)

    # Health check
    health = server.health_check()
    print(f"\nHealth Check:")
    print(f"  Status: {health['status']}")
    print(f"  Tools: {health['tools']}")
    print(f"  LLM enabled: {health['llm_enabled']}")

    # Analyze market
    candles = generate_sample_candles("GBPUSD", 200)
    candle_dicts = candles_to_dicts(candles)

    analysis = server.analyze_market("GBPUSD", candle_dicts)
    print(f"\nMarket Analysis (GBPUSD):")
    print(f"  Status: {analysis['status']}")
    if analysis['status'] == 'success':
        a = analysis['analysis']
        print(f"  Data quality valid: {a['data_quality_valid']}")
        print(f"  Candidate setups: {len(a['candidate_setups'])}")
        print(f"  Risk can trade: {a['risk_can_trade']}")
        if a['candidate_setups']:
            s = a['candidate_setups'][0]
            entry = f"{s['entry']:.5f}" if s['entry'] else "N/A"
            sl = f"{s['stop_loss']:.5f}" if s['stop_loss'] else "N/A"
            tp = f"{s['take_profit']:.5f}" if s['take_profit'] else "N/A"
            print(f"  First setup: {s['direction']} | Entry: {entry} | SL: {sl} | TP: {tp}")
    else:
        print(f"  Error: {analysis.get('error', 'unknown')}")

    # Run backtest
    backtest = server.run_backtest("GBPUSD", candle_dicts)
    print(f"\nBacktest Results:")
    print(f"  Status: {backtest['status']}")
    if backtest['status'] == 'success':
        r = backtest['results']
        print(f"  Total trades: {r['total_trades']}")
        print(f"  Win rate: {r['win_rate']:.1%}")
        print(f"  Profit factor: {r['profit_factor']:.2f}")
        print(f"  Total PnL: ${r['total_pnl']:.2f}")


def demo_dashboard():
    """Demo the Dashboard API."""
    print("\n\n3. DASHBOARD API")
    print("-" * 70)

    dashboard = TradingDashboard(enable_llm=False)

    # Health
    health = dashboard.health()
    print(f"\nDashboard Health:")
    print(f"  Platform: {health['data']['platform']}")
    print(f"  Version: {health['data']['version']}")
    print(f"  Modules: {health['data']['modules']}")

    # Analyze symbol
    candles = generate_sample_candles("USDJPY", 200)
    candle_dicts = candles_to_dicts(candles)

    analysis = dashboard.analyze_symbol("USDJPY", candle_dicts)
    print(f"\nUSDJPY Analysis:")
    print(f"  Status: {analysis['status']}")
    if analysis['status'] == 'success':
        d = analysis['data']
        print(f"  Candidate setups: {len(d['candidate_setups'])}")
        print(f"  Risk can trade: {d['risk_can_trade']}")
        if d['candidate_setups']:
            s = d['candidate_setups'][0]
            entry = f"{s['entry']:.2f}" if s['entry'] else "N/A"
            sl = f"{s['stop_loss']:.2f}" if s['stop_loss'] else "N/A"
            tp = f"{s['take_profit']:.2f}" if s['take_profit'] else "N/A"
            print(f"  First setup: {s['direction']} | Entry: {entry} | SL: {sl} | TP: {tp}")
    else:
        print(f"  Error: {analysis.get('error', 'unknown')}")

    # Portfolio summary
    summary = dashboard.get_portfolio_summary()
    print(f"\nPortfolio Summary:")
    print(f"  Total trades: {summary['data']['total_trades']}")
    print(f"  Total PnL: ${summary['data']['total_pnl']:.2f}")


def demo_backtest():
    """Demo the backtesting framework."""
    print("\n\n4. BACKTESTING FRAMEWORK")
    print("-" * 70)

    config = BacktestConfig(
        initial_capital=10000.0,
        risk_per_trade=0.01,
        max_positions=3,
    )
    engine = BacktestEngine(config)

    candles = generate_sample_candles("EURUSD", 300)
    result = engine.run(candles, "EURUSD")

    print(f"\nBacktest: EURUSD (300 candles)")
    print(f"  Total trades: {result.total_trades}")
    print(f"  Winning trades: {result.winning_trades}")
    print(f"  Losing trades: {result.losing_trades}")
    print(f"  Win rate: {result.win_rate:.1%}")
    print(f"  Profit factor: {result.profit_factor:.2f}")
    print(f"  Total PnL: ${result.total_pnl:.2f}")
    print(f"  Max drawdown: ${result.max_drawdown:.2f}")
    print(f"  Sharpe ratio: {result.sharpe_ratio:.3f}")
    print(f"  Expectancy: ${result.expectancy:.2f}")


if __name__ == "__main__":
    demo_orchestrator()
    demo_mcp_server()
    demo_dashboard()
    demo_backtest()

    print("\n" + "=" * 70)
    print("PHASE 1 DEMO COMPLETE")
    print("=" * 70)
