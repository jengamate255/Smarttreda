"""Two-agent cross-review gate for live trade entries.

Before ANY live order is placed through ``open_market``, two Groq agents must
independently review it and reach unanimity:

  1. ALPHA (Maker) reviews the bot plan + fresh market data -> APPROVE/REJECT.
  2. BETA (Checker) receives the same data PLUS Alpha's verdict and attacks it.
  3. ALPHA gets Beta's objections for a final reconsideration (cross-review).
  4. The trade executes ONLY if both finals are APPROVE.

Fail-closed: Groq unreachable, unparseable output, or any exception -> BLOCKED.
Kill-switch: set MT5_DUAL_REVIEW=false to bypass (not recommended live).

The hard rules encode the GBPCHF lesson (2026-09-21): a stale Friday bar +
``transition`` regime + decaying confirmation must never pass, no matter how
high the headline score is.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mt5_mcp.groq_llm import generate as groq_generate, is_enabled as groq_enabled

logger = logging.getLogger("mt5_mcp.dual_review")

BASE_DIR = Path(__file__).resolve().parent
REVIEW_LOG = BASE_DIR / "memory" / "dual_review.jsonl"

ENABLED = os.getenv("MT5_DUAL_REVIEW", "true").strip().lower() not in ("0", "false", "no", "off")
MODEL_ALPHA = os.getenv("MT5_DUAL_REVIEW_MODEL_ALPHA", "qwen/qwen3.8-27b")
MODEL_BETA = os.getenv("MT5_DUAL_REVIEW_MODEL_BETA", "qwen/qwen3.8-27b")
MAX_TOKENS = int(os.getenv("MT5_DUAL_REVIEW_MAX_TOKENS", "500"))
LLM_CALL_DELAY = float(os.getenv("MT5_DUAL_REVIEW_CALL_DELAY", "20"))

# Floors mirror analysis_agent.py gates
MIN_CONFIDENCE = float(os.getenv("ANALYSIS_MIN_CONFIDENCE", "75"))
MIN_TRADE_SCORE = float(os.getenv("ANALYSIS_MIN_SCORE", "70"))
MIN_HEALTH = float(os.getenv("ANALYSIS_MIN_HEALTH", "55"))
MIN_RR = float(os.getenv("ANALYSIS_MIN_RR", "3.0"))
MAX_BAR_AGE_HOURS = float(os.getenv("MT5_DUAL_REVIEW_MAX_BAR_AGE_H", "24"))

_TRENDING = {"trending_bullish", "trending-bullish", "bullish", "trending_bearish", "trending-bearish", "bearish"}


class ReviewVerdict(BaseModel):
    """One agent's verdict on a proposed trade."""

    agent_id: str = ""
    decision: str = "REJECT"  # APPROVE | REJECT
    confidence: float = 0.0
    objections: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    rationale: str = ""
    latency_s: float = 0.0


class DualReviewResult(BaseModel):
    """Outcome of the two-agent cross-review."""

    approved: bool = False
    reason: str = ""
    alpha: ReviewVerdict = Field(default_factory=ReviewVerdict)
    beta: ReviewVerdict = Field(default_factory=ReviewVerdict)
    rounds: int = 0
    review_id: str = ""


ALPHA_SYSTEM = """You are ALPHA, the Maker agent of a two-agent trading gate for a SIM MT5 account.
Your job: argue FOR the proposed trade ONLY if the evidence supports it; otherwise REJECT.
A second agent (BETA) will attack your verdict, so be strict now.
Output STRICT JSON only, no other text: {"decision": "APPROVE" or "REJECT", "confidence": 0-100, "objections": ["..."], "conditions": ["..."], "rationale": "..."}.
Hard rules — REJECT if ANY fails:
1. Bot plan valid=true and its action matches the proposed side.
2. confidence>=%CONF% and trade_score>=%SCORE% and risk_reward>=%RR% and health>=%HEALTH%.
3. Regime must be a clean trend matching the side (bullish regime for BUY, bearish for SELL). REJECT transition/ranging/sideways/unknown regimes.
4. SL and TP present, RR>=%RR%. TP may be capped at 2x the natural target (100 pips) — this is acceptable if the strategy produced it with a warning. Do NOT reject for TP cap alone.
5. REJECT if SL is > 3x the natural TP max (200 pips) — SL too wide means the zone is too far from entry.
6. Lot size: if the proposed lots differ from the plan's lots, check that the difference is < 30%. Minor rounding is OK; major mismatches indicate a manual override error.
Lesson from a past loser (GBPCHF 2026-09-21): stale Friday bar + transition regime + decaying confirmation passed on headline score alone and lost money. Never repeat that."""


BETA_SYSTEM = """You are BETA, the Checker agent of a two-agent trading gate for a SIM MT5 account.
ALPHA (Maker) has reviewed the same evidence and issued a verdict, shown below.
Your job: Verify Alpha's math and catch REAL errors — not nitpick.
Output STRICT JSON only, no other text: {"decision": "APPROVE" or "REJECT", "confidence": 0-100, "objections": ["..."], "conditions": ["..."], "rationale": "..."}.
Only REJECT if you find a CONCRETE, MATERIAL error:
- SL/TP/RR math is objectively wrong (calculate it yourself from entry, SL, TP prices)
- Lot size doesn't match the risk model AND the difference is > 20%
- Regime is clearly wrong (e.g., evidence says ranging but trade assumes trending)
- Bar data is stale (>24h old or weekend gap)
- Zone is broken/invalid
Do NOT reject for:
- Minor lot rounding (broker constraints)
- Subjective zone quality opinions
- "Could be better" conditions — only hard failures
Approve if the trade is materially sound. A missed trade costs nothing, but a false rejection wastes opportunity."""


ALPHA_FINAL_SYSTEM = """You are ALPHA, the Maker agent. BETA (Checker) has attacked your verdict with the objections below.
Reconsider honestly: if any objection is valid, flip to REJECT.
Output STRICT JSON only, no other text: {"decision": "APPROVE" or "REJECT", "confidence": 0-100, "objections": ["..."], "conditions": ["..."], "rationale": "..."}."""


def _fill(template: str) -> str:
    return (
        template.replace("%CONF%", str(MIN_CONFIDENCE))
        .replace("%SCORE%", str(MIN_TRADE_SCORE))
        .replace("%RR%", str(MIN_RR))
        .replace("%HEALTH%", str(MIN_HEALTH))
        .replace("%BARH%", str(MAX_BAR_AGE_HOURS))
    )


def _parse_verdict(agent_id: str, content: str, latency: float) -> ReviewVerdict:
    """Parse strict-JSON verdict; unparseable -> REJECT (fail-closed).

    Handles common Groq output issues: markdown code blocks, trailing commas,
    single quotes, text before/after JSON, partial outputs.
    """
    text = (content or "").strip()
    data: dict[str, Any] | None = None

    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # Try direct parse first
    try:
        data = json.loads(text)
    except Exception:
        pass

    # Try extracting JSON block from surrounding text
    if data is None:
        m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if m:
            candidate = m.group(0)
            # Fix trailing commas before } or ]
            candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                data = json.loads(candidate)
            except Exception:
                pass

    # Try single-quote to double-quote conversion
    if data is None:
        fixed = text.replace("'", '"')
        m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", fixed, re.DOTALL)
        if m:
            candidate = m.group(0)
            candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                data = json.loads(candidate)
            except Exception:
                pass

    # Fallback: extract decision and confidence from truncated/partial JSON
    if data is None:
        dec_match = re.search(r'"decision"\s*:\s*"(APPROVE|REJECT)"', text, re.IGNORECASE)
        conf_match = re.search(r'"confidence"\s*:\s*(\d+(?:\.\d+)?)', text)
        if dec_match:
            data = {
                "decision": dec_match.group(1).upper(),
                "confidence": float(conf_match.group(1)) if conf_match else 50.0,
                "objections": [],
                "conditions": [],
                "rationale": text[:500],
            }

    if not isinstance(data, dict) or str(data.get("decision", "")).upper() not in ("APPROVE", "REJECT"):
        return ReviewVerdict(
            agent_id=agent_id,
            decision="REJECT",
            confidence=0.0,
            objections=["unparseable agent output (fail-closed)"],
            rationale=text[:500],
            latency_s=latency,
        )
    try:
        conf = float(data.get("confidence", 0.0))
    except Exception:
        conf = 0.0
    objs = data.get("objections", []) or []
    conds = data.get("conditions", []) or []
    return ReviewVerdict(
        agent_id=agent_id,
        decision=str(data["decision"]).upper(),
        confidence=max(0.0, min(100.0, conf)),
        objections=[str(o) for o in objs][:10],
        conditions=[str(c) for c in conds][:10],
        rationale=str(data.get("rationale", ""))[:1000],
        latency_s=latency,
    )


def _call(agent_id: str, model: str, temperature: float, system: str, user: str, retries: int = 3) -> ReviewVerdict:
    started = time.perf_counter()
    last_exc = None
    for attempt in range(retries):
        try:
            resp = groq_generate(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                model=model,
                temperature=temperature,
                max_tokens=MAX_TOKENS,
            )
            content = resp.content
            break
        except Exception as exc:
            last_exc = exc
            msg = str(exc)
            if "429" in msg or "rate_limit" in msg.lower():
                wait = min(60, 20 * (attempt + 1))
                logger.warning("dual-review %s rate-limited (attempt %d), waiting %ds", agent_id, attempt + 1, wait)
                time.sleep(wait)
                continue
            # Server disconnect / timeout — retry with backoff
            if attempt < retries - 1:
                wait = 10 * (attempt + 1)
                logger.warning("dual-review %s call failed (attempt %d), retrying in %ds: %s", agent_id, attempt + 1, wait, exc)
                time.sleep(wait)
                continue
            logger.warning("dual-review %s call failed: %s", agent_id, exc)
            return ReviewVerdict(
                agent_id=agent_id,
                decision="REJECT",
                objections=[f"{agent_id} LLM call failed (fail-closed): {exc}"],
                rationale="",
                latency_s=time.perf_counter() - started,
            )
    else:
        logger.warning("dual-review %s exhausted retries: %s", agent_id, last_exc)
        return ReviewVerdict(
            agent_id=agent_id,
            decision="REJECT",
            objections=[f"{agent_id} LLM call failed after {retries} retries (fail-closed): {last_exc}"],
            rationale="",
            latency_s=time.perf_counter() - started,
        )
    return _parse_verdict(agent_id, content, time.perf_counter() - started)


def _evidence_block(
    symbol: str,
    side: str,
    lots: float,
    sl: float | None,
    tp: float | None,
    plan: dict[str, Any],
    health: float,
    account: dict[str, Any],
) -> str:
    # Strip ML fields — ML model is unreliable with current training data
    clean_plan = {k: v for k, v in plan.items() if k not in ("ml_score", "ml_note")}

    # Infer regime from htf_trend for Alpha/Beta clarity
    htf = str(plan.get("htf_trend", "")).lower().strip()
    if "bull" in htf:
        regime = "trending_bullish"
    elif "bear" in htf:
        regime = "trending_bearish"
    elif "side" in htf or "rang" in htf:
        regime = "ranging"
    elif "trans" in htf:
        regime = "transition"
    else:
        regime = htf or "unknown"
    clean_plan["regime"] = regime

    return json.dumps(
        {
            "proposed_order": {"symbol": symbol, "side": side, "lots": lots, "sl": sl, "tp": tp},
            "fresh_bot_plan": clean_plan,
            "fresh_health_score": health,
            "account": account,
            "floors": {
                "confidence": MIN_CONFIDENCE,
                "score": MIN_TRADE_SCORE,
                "rr": MIN_RR,
                "health": MIN_HEALTH,
                "max_bar_age_h": MAX_BAR_AGE_HOURS,
            },
            "now_utc": datetime.now(timezone.utc).isoformat(),
        },
        default=str,
    )


def _log_review(result: DualReviewResult, symbol: str, side: str) -> None:
    try:
        REVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(REVIEW_LOG, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "review_id": result.review_id,
                        "at": datetime.now(timezone.utc).isoformat(),
                        "symbol": symbol,
                        "side": side,
                        "approved": result.approved,
                        "reason": result.reason,
                        "alpha": result.alpha.model_dump(),
                        "beta": result.beta.model_dump(),
                        "rounds": result.rounds,
                    },
                    default=str,
                )
                + "\n"
            )
    except Exception as exc:
        logger.debug("dual-review log write failed: %s", exc)


def review_trade(
    symbol: str,
    side: str,
    lots: float,
    sl: float | None = None,
    tp: float | None = None,
    plan: dict[str, Any] | None = None,
    health: float | None = None,
    account: dict[str, Any] | None = None,
) -> DualReviewResult:
    """Run the two-agent cross-review. Fail-closed on any error.

    If plan/health are omitted they are fetched FRESH from the bot so a stale
    cached plan can never sneak through (the GBPCHF rule).
    """
    review_id = f"dr_{uuid.uuid4().hex[:8]}"
    result = DualReviewResult(review_id=review_id)
    side = side.upper()

    if not ENABLED:
        result.approved = True
        result.reason = "dual-review disabled (MT5_DUAL_REVIEW=false)"
        return result
    if not groq_enabled():
        result.reason = "dual-review: Groq not configured (fail-closed)"
        logger.warning("%s %s %s blocked: %s", review_id, symbol, side, result.reason)
        _log_review(result, symbol, side)
        return result

    # --- Fresh evidence (never trust a cached plan) ---
    try:
        if plan is None or health is None:
            from mt5_mcp.strategy import evaluate_strategy
            from mt5_mcp.market_intel import market_health

            fresh_plan = evaluate_strategy(symbol)
            fresh_health_raw = market_health(symbol)
            plan = dict(fresh_plan) if isinstance(fresh_plan, dict) else {"raw": str(fresh_plan)}
            if isinstance(fresh_health_raw, dict):
                health = float(fresh_health_raw.get("health_score", fresh_health_raw.get("health", 0.0)) or 0.0)
            else:
                try:
                    health = float(fresh_health_raw)
                except Exception:
                    health = 0.0
        if account is None:
            from mt5_mcp.client import get_client

            client = get_client()
            try:
                account = dict(client.account_info() or {})
            except Exception:
                account = {}
            try:
                account["open_positions"] = len(client.positions() or [])
            except Exception:
                pass
    except Exception as exc:
        result.reason = f"dual-review: fresh evidence fetch failed (fail-closed): {exc}"
        logger.warning("%s %s %s blocked: %s", review_id, symbol, side, result.reason)
        _log_review(result, symbol, side)
        return result

    evidence = _evidence_block(symbol, side, lots, sl, tp, plan or {}, health or 0.0, account or {})

    # --- Round 1: Alpha proposes ---
    alpha = _call("ALPHA", MODEL_ALPHA, 0.2, _fill(ALPHA_SYSTEM),
                  "PROPOSED TRADE + FRESH EVIDENCE:\n" + evidence)
    result.rounds = 1
    time.sleep(LLM_CALL_DELAY)

    # --- Round 2: Beta attacks (sees Alpha's verdict) ---
    beta_user = (
        "PROPOSED TRADE + FRESH EVIDENCE:\n" + evidence
        + "\n\nALPHA'S VERDICT:\n" + json.dumps(alpha.model_dump(), default=str)
    )
    beta = _call("BETA", MODEL_BETA, 0.3, _fill(BETA_SYSTEM), beta_user)
    result.rounds = 2
    time.sleep(LLM_CALL_DELAY)

    # --- Round 3: Alpha final reconsideration (cross-review) ---
    alpha_final_user = (
        "YOUR ORIGINAL VERDICT:\n" + json.dumps(alpha.model_dump(), default=str)
        + "\n\nBETA'S OBJECTIONS:\n" + json.dumps(beta.objections)
        + "\n\nBETA'S FULL VERDICT:\n" + json.dumps(beta.model_dump(), default=str)
        + "\n\nEVIDENCE (unchanged):\n" + evidence
    )
    alpha_final = _call("ALPHA-FINAL", MODEL_ALPHA, 0.1, ALPHA_FINAL_SYSTEM, alpha_final_user)
    result.rounds = 3

    result.alpha = alpha_final
    result.beta = beta

    if alpha_final.decision == "APPROVE" and beta.decision == "APPROVE":
        result.approved = True
        result.reason = (
            f"unanimous APPROVE (alpha-final conf {alpha_final.confidence:.0f}, "
            f"beta conf {beta.confidence:.0f})"
        )
    else:
        blockers = []
        if alpha_final.decision != "APPROVE":
            blockers += alpha_final.objections or ["alpha-final REJECT"]
        if beta.decision != "APPROVE":
            blockers += beta.objections or ["beta REJECT"]
        result.reason = "blocked: " + " | ".join(blockers[:6])

    logger.info(
        "%s %s %s %s after %d rounds (alpha: %s, beta: %s)",
        review_id, symbol, side,
        "APPROVED" if result.approved else "BLOCKED",
        result.rounds, alpha_final.decision, beta.decision,
    )
    _log_review(result, symbol, side)
    return result


def gate(
    symbol: str,
    side: str,
    lots: float,
    sl: float | None = None,
    tp: float | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    """Choke-point wrapper for open_market. Returns {"approved", "reason", ...}."""
    try:
        res = review_trade(symbol, side, lots, sl=sl, tp=tp)
        return {
            "approved": res.approved,
            "reason": res.reason,
            "review_id": res.review_id,
            "alpha": res.alpha.model_dump(),
            "beta": res.beta.model_dump(),
        }
    except Exception as exc:
        logger.exception("dual-review gate crashed (fail-closed)")
        return {"approved": False, "reason": f"dual-review crashed (fail-closed): {exc}"}
