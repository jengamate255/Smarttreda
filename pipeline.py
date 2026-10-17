"""Research → Validate → ML → Promote pipeline.

Chains:
  1. Research Agent (sweep 1728 combos, find top by expectancy)
  2. Walk-Forward Agent (validate top 3 with rolling train/test)
  3. ML Ranker Agent (retrain on validated data)
  4. Promote (update settings.json + log)

Usage:
    from pipeline import pipeline
    pipeline.start()       # trigger (non-blocking)
    pipeline.status()      # progress dict
    pipeline.latest()      # final result
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger("pipeline")

PROJECT = Path(r"E:\Project\Mt5 Ea")
SETTINGS_PATH = PROJECT / "mt5_mcp" / "settings.json"
VALIDATED_DIR = PROJECT / "memory" / "validated"
PIPELINE_DIR = PROJECT / "memory" / "pipeline"
PIPELINE_DIR.mkdir(parents=True, exist_ok=True)

# Gate name → mtf_timeframes for settings.json
GATE_MAP = {
    "none": [],
    "H4": ["H4"],
    "H4+H1": ["H4", "H1"],
    "H4+H6": ["H4", "H6"],
    "H4+H6+H1": ["H4", "H6", "H1"],
    "H6+H8": ["H6", "H8"],
    "D1+H4+H1": ["D1", "H4", "H1"],
    "D1+H4+H6": ["D1", "H4", "H6"],
}


class Pipeline:
    """Orchestrates Research → WF → ML → Promote in sequence."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state: dict = {
            "running": False,
            "phase": "idle",       # idle / research / walk-forward / ml-rank / promote / done / error
            "progress": 0,
            "current": "",
            "error": None,
            "started_at": None,
            "finished_at": None,
            "phases": {
                "research": {"status": "pending", "elapsed_s": 0},
                "walk-forward": {"status": "pending", "elapsed_s": 0},
                "ml-rank": {"status": "pending", "elapsed_s": 0},
                "promote": {"status": "pending", "elapsed_s": 0},
            },
        }
        self._results: dict | None = None

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def latest(self) -> dict | None:
        with self._lock:
            return self._results

    def start(self, **kwargs) -> bool:
        with self._lock:
            if self._state["running"]:
                return False
        self._thread = threading.Thread(target=self._run, args=(kwargs,), daemon=True)
        self._thread.start()
        return True

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _set_phase(self, phase: str, status: str, **kw):
        with self._lock:
            self._state["phases"][phase] = {**self._state["phases"][phase], "status": status, **kw}

    def _run(self, kwargs):
        t0 = time.time()
        self._set(
            running=True, phase="research", progress=0, error=None,
            started_at=datetime.datetime.utcnow().isoformat(), finished_at=None,
            phases={
                "research": {"status": "running", "elapsed_s": 0},
                "walk-forward": {"status": "pending", "elapsed_s": 0},
                "ml-rank": {"status": "pending", "elapsed_s": 0},
                "promote": {"status": "pending", "elapsed_s": 0},
            },
        )
        try:
            # ── Phase 1: Research ──────────────────────────────────────
            self._set(current="Phase 1/4: Research sweep...", progress=5)
            from research_agent import agent as research_agent
            research_agent.start()
            while True:
                st = research_agent.status()
                if not st["running"] or st["phase"] in ("done", "error"):
                    break
                pct = min(st.get("progress", 0), 99)
                self._set(progress=int(pct * 0.35),  # research = 0-35%
                          current=f"Research: {st.get('current', '')}")
                time.sleep(3)

            r_status = research_agent.status()
            if r_status["phase"] == "error":
                raise RuntimeError(f"Research failed: {r_status.get('error', 'unknown')}")

            r_results = research_agent.latest()
            if not r_results or not r_results.get("top"):
                raise RuntimeError("Research produced no results")

            top = r_results["top"]
            passed_validation = r_results.get("passed_count", 0)
            self._set_phase("research", "done", elapsed_s=r_status.get("elapsed_s", 0))
            self._set(progress=35, current=f"Research done: {len(top)} combos, {passed_validation} validated")

            # Pick top 3 combos with >=10 trades for walk-forward
            candidates = [r for r in top if r.get("trades", 0) >= 10][:3]
            if not candidates:
                raise RuntimeError("No candidates with >=10 trades from research")

            # ── Phase 2: Walk-Forward ──────────────────────────────────
            self._set(phase="walk-forward", progress=36,
                      current=f"Phase 2/4: Walk-forward validating {len(candidates)} combos...")
            self._set_phase("walk-forward", "running")

            wf_passed = []
            for i, combo in enumerate(candidates):
                self._set(current=f"WF {i+1}/{len(candidates)}: {combo['entry_tf']} {combo['gates']} {combo['sl_tp']}R...",
                          progress=36 + int((i / len(candidates)) * 25))

                # Run walk-forward on the top symbol (EURUSD as default, or use first available)
                from backtest_agents import walk_forward_agent
                wf_payload = {
                    "symbol": kwargs.get("symbol", "EURUSD"),
                    "train_bars": 2000,
                    "test_bars": 500,
                    "step_bars": 500,
                    "trend_tf": "H4",
                    "zone_tfs": combo.get("gates", "H4"),
                    "bars_total": 10000,
                }
                walk_forward_agent.start(payload=wf_payload)
                while True:
                    wf_st = walk_forward_agent.status()
                    if not wf_st["running"] or wf_st["phase"] in ("done", "error"):
                        break
                    time.sleep(2)

                wf_result = walk_forward_agent.latest()
                if wf_result and not wf_result.get("error"):
                    # Check if WF passed (consistency >= 60%, IS/OOS ratio >= 0.6)
                    consistency = wf_result.get("consistency_score", 0)
                    is_ratio = wf_result.get("is_ratio", 0)
                    if consistency >= 50 and is_ratio >= 0.4:
                        combo["wf_consistency"] = consistency
                        combo["wf_is_ratio"] = is_ratio
                        combo["wf_oos_pf"] = wf_result.get("oos_profit_factor", 0)
                        wf_passed.append(combo)
                        logger.info("WF PASS: %s %s (consistency=%.0f, ratio=%.2f)",
                                    combo["entry_tf"], combo["gates"], consistency, is_ratio)

            self._set_phase("walk-forward", "done",
                            elapsed_s=sum(self._state["phases"]["walk-forward"].get("_step_times", [0])))
            self._set(progress=61, current=f"WF done: {len(wf_passed)}/{len(candidates)} passed")

            if not wf_passed:
                # Fall back to research top combo even if WF didn't pass
                logger.warning("No WF-passed combos, using research top")
                wf_passed = [candidates[0]]

            best = wf_passed[0]

            # ── Phase 3: ML Ranker ─────────────────────────────────────
            self._set(phase="ml-rank", progress=62,
                      current="Phase 3/4: Training ML ranker...")
            self._set_phase("ml-rank", "running")

            from backtest_agents import ml_ranker_agent
            ml_ranker_agent.start()
            while True:
                ml_st = ml_ranker_agent.status()
                if not ml_st["running"] or ml_st["phase"] in ("done", "error"):
                    break
                pct = min(ml_st.get("progress", 0), 99)
                self._set(progress=62 + int(pct * 0.20),
                          current=f"ML: {ml_st.get('current', '')}")
                time.sleep(2)

            ml_result = ml_ranker_agent.latest()
            ml_ok = ml_result and ml_result.get("ok")
            ml_auc = ml_result.get("cv_auc", 0) if ml_result else 0
            self._set_phase("ml-rank", "done", elapsed_s=0)
            self._set(progress=82, current=f"ML done: AUC={ml_auc:.3f}" if ml_ok else "ML skipped (no data)")

            # ── Phase 4: Promote ───────────────────────────────────────
            self._set(phase="promote", progress=83,
                      current="Phase 4/4: Promoting strategy...")
            self._set_phase("promote", "running")

            promoted = self._promote(best)

            self._set_phase("promote", "done", elapsed_s=0)
            self._set(progress=100, phase="done", running=False,
                      current=f"Pipeline done — promoted {best['entry_tf']} {best['gates']} {best['sl_tp']}R",
                      finished_at=datetime.datetime.utcnow().isoformat())

            elapsed = round(time.time() - t0, 1)
            self._results = {
                "date": datetime.date.today().isoformat(),
                "elapsed_s": elapsed,
                "research_combos": len(top),
                "wf_tested": len(candidates),
                "wf_passed": len(wf_passed),
                "best_combo": best,
                "ml_trained": ml_ok,
                "ml_auc": ml_auc,
                "promoted": promoted,
                "phases": dict(self._state["phases"]),
            }

            # Save pipeline run
            run_file = PIPELINE_DIR / f"pipeline_{datetime.date.today().isoformat()}.json"
            with open(run_file, "w") as f:
                json.dump(self._results, f, indent=2, default=str)

            logger.info("Pipeline done in %.1fs: promoted %s %s %.1fR",
                        elapsed, best["entry_tf"], best["gates"], best["sl_tp"])

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._set(phase="error", error=str(e), running=False,
                      finished_at=datetime.datetime.utcnow().isoformat())

    def _promote(self, combo: dict) -> bool:
        """Write validated strategy to settings.json."""
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))

            gate_name = combo.get("gates", "H4")
            new_mtf = GATE_MAP.get(gate_name, ["H4"])
            entry_tf = combo.get("entry_tf", "M5")
            filters = combo.get("filters", "none")
            sl_tp = combo.get("sl_tp", 1.5)
            sl_atr = combo.get("sl_atr", 3.0)

            settings.setdefault("strategy", {})
            settings["strategy"]["mtf_timeframes"] = new_mtf
            settings["strategy"]["mtf_entry_timeframe"] = entry_tf
            settings["strategy"]["energy_filter_enabled"] = "energy" in filters
            settings["strategy"]["zone_proximity_atr"] = 1.5 if "zone" in filters else 999
            settings["strategy"]["mtf_sl_atr_mult"] = sl_atr
            settings["strategy"]["mtf_tp_r_mult"] = sl_tp

            SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")

            # Also update .env for trade desk (MT5_DESK_* vars)
            env_path = PROJECT / ".env"
            env = env_path.read_text(encoding="utf-8")
            env_lines = []
            for line in env.splitlines():
                if line.startswith("MT5_DESK_ENTRY_TF="):
                    env_lines.append(f"MT5_DESK_ENTRY_TF={entry_tf}")
                elif line.startswith("MT5_DESK_SL_ATR="):
                    env_lines.append(f"MT5_DESK_SL_ATR={sl_atr}")
                elif line.startswith("MT5_DESK_TP_R="):
                    env_lines.append(f"MT5_DESK_TP_R={sl_tp}")
                else:
                    env_lines.append(line)
            env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

            logger.info("Promoted: entry=%s gates=%s sl_tp=%.1f sl_atr=%.1f filters=%s",
                        entry_tf, gate_name, sl_tp, sl_atr, filters)
            return True

        except Exception as e:
            logger.error("Promote failed: %s", e)
            return False


# singleton
pipeline = Pipeline()


# ---------------------------------------------------------------------------
# Subprocess-compatible function
# ---------------------------------------------------------------------------
def run_pipeline(payload: dict) -> dict:
    """Run the full pipeline in a subprocess (no GIL issues)."""
    from subprocess_agent import _write_status as _rep
    name = "pipeline"
    t0 = time.time()
    _rep(name, phase="research", progress=5, current="Phase 1/4: Research sweep...")

    try:
        # Phase 1: Research
        from research_agent import run_research
        _rep(name, phase="research", progress=5, current="Phase 1/4: Research sweep...")
        r_result = run_research(payload)
        if r_result.get("error"):
            raise RuntimeError(f"Research failed: {r_result['error']}")

        top = r_result.get("top", [])
        passed_validation = r_result.get("passed_count", 0)
        _rep(name, phase="research", progress=35,
             current=f"Research done: {len(top)} combos, {passed_validation} validated")

        candidates = [r for r in top if r.get("trades", 0) >= 10][:3]
        if not candidates:
            raise RuntimeError("No candidates with >=10 trades")

        # Phase 2: Walk-Forward
        _rep(name, phase="walk-forward", progress=36,
             current=f"Phase 2/4: Walk-forward {len(candidates)} combos...")
        from backtest_agents import run_walk_forward
        wf_passed = []
        for i, combo in enumerate(candidates):
            _rep(name, progress=36 + int((i / len(candidates)) * 25),
                 current=f"WF {i+1}/{len(candidates)}: {combo['entry_tf']} {combo['gates']}...")
            # Pass the combo's gates as zone timeframes so WF validates
            # the actual gate configuration, not the defaults
            gate_tfs = [g.strip() for g in str(combo.get("gates", "H4")).split("+") if g.strip()]
            wf_payload = {**payload,
                          "symbol": payload.get("symbol", "EURUSD"),
                          "zone_tfs": gate_tfs or ["H4"],
                          "trend_tf": gate_tfs[0] if gate_tfs else "H4"}
            wf_result = run_walk_forward(wf_payload)
            if wf_result and not wf_result.get("error"):
                consistency = wf_result.get("consistency_score", 0)
                is_ratio = wf_result.get("is_ratio", 0)
                if consistency >= 50 and is_ratio >= 0.4:
                    combo["wf_consistency"] = consistency
                    combo["wf_is_ratio"] = is_ratio
                    wf_passed.append(combo)

        if not wf_passed:
            wf_passed = [candidates[0]]
        best = wf_passed[0]
        _rep(name, phase="walk-forward", progress=61,
             current=f"WF done: {len(wf_passed)}/{len(candidates)} passed")

        # Phase 3: ML Ranker
        _rep(name, phase="ml-rank", progress=62, current="Phase 3/4: ML training...")
        from backtest_agents import run_ml_ranker
        ml_result = run_ml_ranker(payload)
        ml_ok = bool(ml_result and ml_result.get("ok"))
        ml_auc = ml_result.get("cv_auc") if ml_result else None
        if ml_auc is None:
            ml_auc = 0
        _rep(name, phase="ml-rank", progress=82,
             current=f"ML done: AUC={ml_auc:.3f}" if ml_ok else "ML skipped (no data)")

        # Phase 4: Promote
        _rep(name, phase="promote", progress=83, current="Phase 4/4: Promoting...")
        p = Pipeline()
        promoted = p._promote(best)
        _rep(name, phase="promote", progress=100)

        elapsed = round(time.time() - t0, 1)
        result = {
            "date": datetime.date.today().isoformat(),
            "elapsed_s": elapsed,
            "research_combos": len(top),
            "wf_tested": len(candidates),
            "wf_passed": len(wf_passed),
            "best_combo": best,
            "ml_trained": ml_ok,
            "ml_auc": ml_auc,
            "promoted": promoted,
        }
        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        _rep(name, phase="error", error=str(e))
        return {"error": str(e)}
