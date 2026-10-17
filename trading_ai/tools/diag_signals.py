"""Diagnose why each strategy produces no signals."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd

from trading_ai.data.generator import SyntheticDataGenerator
from trading_ai.strategies.genome.schema import StrategyGenome
from trading_ai.strategies.trend import TrendAgent
from trading_ai.strategies.momentum import MomentumAgent
from trading_ai.strategies.breakout import BreakoutAgent
from trading_ai.strategies.mean_reversion import MeanReversionAgent
from trading_ai.strategies.volatility import VolatilityAgent
from trading_ai.strategies.supply_demand import SupplyDemandStrategy


def main():
    gen = SyntheticDataGenerator(seed=42)
    data = gen.generate_multi_timeframe(
        "EURUSD", base_timeframe="M5",
        timeframes=("M5", "M15", "H1", "H4"),
        base_count=5000, initial_price=1.1000, trend="bullish",
    )
    for tf, df in data.items():
        print(f"{tf}: {len(df)} bars, close=[{df['close'].iloc[-1]:.4f}], "
              f"first={df['close'].iloc[0]:.4f}")

    ms = {"symbol": "EURUSD", "session": "london",
          "volatility_regime": "normal", "atr": 0.001}
    genome = StrategyGenome(
        strategy_id="diag", market=["EURUSD", "*"],
        timeframes=["H4", "H1", "M15", "M5"],
        filters=[],
    )

    # ---- Trend ----
    print("\n=== TREND ===")
    t = TrendAgent(genome)
    h4, h1, m15, m5 = data["H4"], data["H1"], data["M15"], data["M5"]
    h4_t = t._identify_trend(h4)
    h1_t = t._identify_trend(h1)
    print(f"h4_trend={h4_t} h1_trend={h1_t}")
    if h4_t != "neutral" and h4_t == h1_t:
        sl = t._find_structure_level(h4, h1, h4_t)
        print(f"structure={sl}")
        if sl:
            pb = t._check_pullback(m15, sl, h4_t)
            print(f"pullback={pb}")
            if pb:
                ent = t._time_entry(m5, h4_t, sl)
                print(f"entry={ent}")
    sig = t.analyze(data, ms)
    print(f"final signal={sig}")

    # ---- Momentum ----
    print("\n=== MOMENTUM ===")
    mo = MomentumAgent(genome)
    mom = mo._detect_momentum(h1, m15)
    print(f"momentum={mom}")
    if mom:
        cont = mo._find_continuation(m15, mom)
        print(f"continuation={cont}")
        if cont:
            ent = mo._time_entry(m5, mom)
            print(f"entry={ent}")
    sig = mo.analyze(data, ms)
    print(f"final signal={sig}")

    # ---- Breakout ----
    print("\n=== BREAKOUT ===")
    b = BreakoutAgent(genome)
    cons = b._detect_consolidation(h1, m15)
    print(f"consolidation={cons}")
    if cons:
        br = b._check_breakout(m15, cons)
        print(f"breakout={br}")
        if br:
            conf = b._confirm_breakout(m5, br)
            print(f"confirm={conf}")
    sig = b.analyze(data, ms)
    print(f"final signal={sig}")

    # ---- MeanRev ----
    print("\n=== MEANREV ===")
    mr = MeanReversionAgent(genome)
    oe = mr._detect_overextension(h4, h1)
    print(f"overextension={oe}")
    if oe:
        mean = mr._calculate_mean(m15, oe)
        print(f"mean={mean}")
        if mean:
            ent = mr._time_entry(m5, oe, mean)
            print(f"entry={ent}")
    sig = mr.analyze(data, ms)
    print(f"final signal={sig}")

    # ---- Volatility ----
    print("\n=== VOLATILITY ===")
    v = VolatilityAgent(genome)
    comp = v._detect_compression(h4, h1)
    print(f"compression={comp}")
    if comp:
        exp = v._detect_expansion(m15, comp)
        print(f"expansion={exp}")
        if exp:
            ent = v._time_entry(m5, exp, comp)
            print(f"entry={ent}")
    sig = v.analyze(data, ms)
    print(f"final signal={sig}")

    # ---- SupplyDemand ----
    print("\n=== SUPPLY/DEMAND ===")
    sd = SupplyDemandStrategy(genome)
    h4s = sd._analyze_structure(h4)
    h1s = sd._analyze_structure(h1)
    print(f"h4={h4s['trend']} h1={h1s['trend']}")
    dz = sd._find_demand_zones(h1, h4)
    sz = sd._find_supply_zones(h1, h4)
    print(f"demand_zones={len(dz)} supply_zones={len(sz)}")
    conf = sd._confirm_zones(m15, dz, sz)
    print(f"m15_confirm={conf}")
    if conf:
        ent = sd._find_entry(m5, conf)
        print(f"entry={ent}")
    sig = sd.analyze(data, ms)
    print(f"final signal={sig}")


if __name__ == "__main__":
    main()
