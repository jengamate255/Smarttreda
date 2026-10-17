"""Smoke-test the SelfLearningLoop for N iterations."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from trading_ai.evolution.self_learning import SelfLearningLoop  # noqa: E402


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    prefix = sys.argv[2] if len(sys.argv) > 2 else "sl_smoke"
    loop = SelfLearningLoop(db_url_prefix=prefix)
    results = loop.run_n(n)
    for r in results:
        print(
            f"iter{r.iteration}: {r.decision} improved={r.improved} "
            f"parent_pf={r.parent_metrics.get('profit_factor')} "
            f"cand_pf={r.candidate_metrics.get('profit_factor')} "
            f"trades={r.candidate_metrics.get('total_trades')} "
            f"err={r.error}"
        )
        if r.reasons:
            print("  reasons:", r.reasons[:3])
    st = loop.get_status()
    keys = ["iterations_completed", "promotions", "rejections", "errors", "best_genome_id"]
    print("status:", {k: st[k] for k in keys})
    bm = st.get("best_metrics") or {}
    print("best_metrics:", {k: bm.get(k) for k in ["total_trades", "profit_factor", "expectancy"]})


if __name__ == "__main__":
    main()
