"""Empirical validation of recursive self-learning over N generations."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from trading_ai.evolution.self_learning import SelfLearningLoop  # noqa: E402


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    prefix = sys.argv[2] if len(sys.argv) > 2 else "sl_proof"
    loop = SelfLearningLoop(db_url_prefix=prefix)

    baseline = loop._backtest_genome(loop._current_genome)
    print("BASELINE:", json.dumps({
        "pf": round(baseline.get("profit_factor", 0), 4),
        "exp": round(baseline.get("expectancy", 0), 4),
        "trades": baseline.get("total_trades"),
        "wr": round(baseline.get("win_rate", 0), 4),
    }, indent=2))

    results = loop.run_n(n)
    print("\nITERATION LOG:")
    for r in results:
        p_pf = r.parent_metrics.get("profit_factor")
        c_pf = r.candidate_metrics.get("profit_factor")
        p_ex = r.parent_metrics.get("expectancy")
        c_ex = r.candidate_metrics.get("expectancy")
        print(
            f"  iter{r.iteration:02d} {r.decision:8s} "
            f"pf {p_pf:.4f}->{c_pf:.4f} "
            f"exp {p_ex:+.4f}->{c_ex:+.4f} "
            f"trades {r.parent_metrics.get('total_trades')}->{r.candidate_metrics.get('total_trades')} "
            f"{'PROMOTED' if r.decision=='PROMOTE' else ''}"
        )

    st = loop.get_status()
    best = st.get("best_metrics") or {}
    print("\nFINAL:")
    print(f"  baseline pf={baseline.get('profit_factor'):.4f} exp={baseline.get('expectancy'):.4f}")
    print(f"  best     pf={best.get('profit_factor', 0):.4f} exp={best.get('expectancy', 0):.4f}")
    print(f"  iterations={st['iterations_completed']} promotions={st['promotions']} "
          f"rejections={st['rejections']} errors={st['errors']}")
    print(f"  best_genome={st['best_genome_id']}")

    improved = best.get("profit_factor", 0) > baseline.get("profit_factor", 0)
    print(f"\nRECURSIVE IMPROVEMENT: {'YES' if improved else 'NO'} "
          f"(pf {baseline.get('profit_factor', 0):.4f} -> {best.get('profit_factor', 0):.4f})")

    # Lineage depth: count promotions as generations
    print(f"GENERATIONS: {st['promotions']} promotions form the current lineage")
    print(f"HISTORY (last 10):")
    for h in st.get("history_tail", [])[-10:]:
        print(f"  {h}")


if __name__ == "__main__":
    main()
