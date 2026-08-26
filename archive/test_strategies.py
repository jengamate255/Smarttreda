"""Test runner for all strategies."""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy_framework import registry, setup_logging
from oil_breakout_strategy import register_oil_breakout
from ger40_london_open_strategy import register_ger40_london_open

# Setup logging
setup_logging("test_runner", logging.INFO)
log = logging.getLogger("test_runner")

def register_all_strategies():
    """Register all strategies."""
    register_oil_breakout()
    register_ger40_london_open()
    # Add more strategies here as they're created

def run_single_cycle():
    """Run one cycle of all strategies."""
    results = {}
    for name, strategy in registry.strategies.items():
        try:
            log.info(f"Running {name}...")
            results[name] = strategy.run_cycle()
            log.info(f"{name}: {len(results[name])} trades executed")
        except Exception as e:
            log.exception(f"Strategy {name} failed: {e}")
            results[name] = []
    return results

def run_continuous(interval: int = 60):
    """Run strategies continuously with specified interval."""
    log.info(f"Starting continuous run (interval={interval}s)")
    cycle = 0
    while True:
        cycle += 1
        log.info(f"=== Cycle {cycle} ===")
        results = run_single_cycle()
        
        # Print summary
        total_trades = sum(len(r) for r in results.values())
        log.info(f"Cycle {cycle} complete: {total_trades} total trades")
        
        for name, trades in results.items():
            if trades:
                for t in trades:
                    if t.success:
                        log.info(f"  {name}: TRADE EXECUTED - Ticket {t.ticket}")
        
        if interval > 0:
            log.info(f"Sleeping {interval}s...")
            time.sleep(interval)

def run_single_test():
    """Run one test cycle and exit."""
    register_all_strategies()
    results = run_single_cycle()
    
    print("\n=== TEST RESULTS ===")
    for name, trades in registry.strategies.items():
        print(f"{name}: {registry.strategies[name].trades_executed} executed today")
    
    return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
    
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--continuous":
        register_all_strategies()
        run_continuous(60)
    else:
        run_single_test()