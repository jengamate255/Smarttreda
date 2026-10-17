"""Run the recursive self-learning loop automatically.

Usage:
  python tools/run_auto_loop.py                 # 10 iterations, sync
  python tools/run_auto_loop.py --iterations 25
  python tools/run_auto_loop.py --background --interval 15 --max-iterations 50
  python tools/run_auto_loop.py --status        # print current status only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from trading_ai.evolution.self_learning import SelfLearningLoop  # noqa: E402


def _print_status(loop: SelfLearningLoop) -> None:
    st = loop.get_status()
    print(json.dumps(st, indent=2, default=str))


async def _run_background(loop: SelfLearningLoop, interval: float, max_iter: int | None) -> None:
    loop.start(interval_s=interval, max_iterations=max_iter)
    print(f"Self-learning loop started (interval={interval}s, max_iterations={max_iter})")
    try:
        while loop.state.running:
            await asyncio.sleep(1)
            st = loop.get_status()
            print(
                f"[iter={st['iterations_completed']} promo={st['promotions']} "
                f"rej={st['rejections']} err={st['errors']}]",
                flush=True,
            )
    except KeyboardInterrupt:
        print("Interrupted; stopping...")
    finally:
        await loop.stop()
        _print_status(loop)


def main() -> None:
    parser = argparse.ArgumentParser(description="Automatic recursive self-learning loop")
    parser.add_argument("--iterations", type=int, default=10, help="Iterations for sync mode")
    parser.add_argument("--background", action="store_true", help="Run as background task")
    parser.add_argument("--interval", type=float, default=15.0, help="Seconds between background iterations")
    parser.add_argument("--max-iterations", type=int, default=None, help="Stop background loop after N iterations")
    parser.add_argument("--status", action="store_true", help="Print status and exit")
    parser.add_argument("--db-prefix", default="auto_loop", help="SQLite db file prefix")
    args = parser.parse_args()

    loop = SelfLearningLoop(db_url_prefix=args.db_prefix)

    if args.status:
        _print_status(loop)
        return

    if args.background:
        asyncio.run(_run_background(loop, args.interval, args.max_iterations))
        return

    print(f"Running {args.iterations} self-learning iterations...")
    results = loop.run_n(args.iterations)
    for r in results:
        print(
            f"iter{r.iteration}: {r.decision} improved={r.improved} "
            f"pf {r.parent_metrics.get('profit_factor'):.4f} -> {r.candidate_metrics.get('profit_factor'):.4f} "
            f"trades={r.candidate_metrics.get('total_trades')}"
        )
    print("--- final status ---")
    _print_status(loop)


if __name__ == "__main__":
    main()
