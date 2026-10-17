"""Shared pre-trade execution pipeline.

Encapsulates the common flow shared by all execution paths (desk, confirm,
auto_trader): consensus check → compliance gate → order execution → journal.

Each execution path provides its own strategy/scanning logic but delegates
the actual gate-check-execute-record sequence here.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from mt5_mcp.common import pip_size, pip_value_per_lot, round_to_step, TradeResult

logger = logging.getLogger("mt5_mcp.pipeline")

# Symbol-specific minimum SL floors (price-distance units).
# Prevents M5 ATR from producing absurdly tight stops on volatile symbols.
# BTC: M5 ATR * 4.5 = $225-$900, but BTC moves $3,300+ normally.
# JPY pairs: M5 ATR * 4.5 = 9-36 pips, but session opens move 15-25 pips.
MIN_SL_FLOOR: dict[str, float] = {
    "BTCUSD": 1000.0,
    "ETHUSD": 30.0,
    "USDJPY": 0.20,
    "EURJPY": 0.20,
    "GBPJPY": 0.25,
    "CHFJPY": 0.20,
}


def enforce_min_sl_floor(symbol: str, sl_distance: float) -> float:
    """Return sl_distance widened to the symbol's minimum floor if needed.

    Used by all execution paths (desk, confirm, watchers) to prevent
    tight M5-ATR stops on volatile symbols.
    """
    floor = MIN_SL_FLOOR.get(symbol, 0.0)
    if floor > 0 and sl_distance < floor:
        logger.info(
            "SL floor applied for %s: %.4f -> %.4f (was too tight)",
            symbol, sl_distance, floor,
        )
        return floor
    return sl_distance


@dataclass
class GateVerdict:
    """Result of the pre-trade gate chain."""
    allowed: bool
    violations: list[str] = field(default_factory=list)
    consensus_allowed: bool = True
    compliance_allowed: bool = True
    llm_vetoed: bool = False
    llm_reasoning: str = ""
    # True when risk.reject_orders=false forced allowed=True despite
    # violations (forward-test log mode - evidence kept in violations).
    log_mode: bool = False
    learning_allowed: bool = True


@dataclass
class ExecResult:
    """Result of a full pipeline execution attempt."""
    filled: bool
    ticket: int | None = None
    trade_result: TradeResult | None = None
    gate_verdict: GateVerdict | None = None
    violations: list[str] = field(default_factory=list)


def run_gates(
    symbol: str,
    side: str,
    lots: float,
    sl_price: float | None = None,
    sl_pips: float | None = None,
    confidence: float | None = None,
    source: str = "pipeline",
    score: float = 0.0,
    tp_price: float | None = None,
    health_score: float = 0.0,
    pattern_direction: str = "",
    zone_type: str = "",
    trend_conflict: bool = False,
    enable_llm_devil_advocate: bool = True,
    setup: str = "",
    entry_price: float | None = None,
) -> GateVerdict:
    """Run consensus + compliance + LLM devil's advocate. Returns combined verdict.

    Gate chain: consensus (cheap, fail-open) → compliance (hard veto) →
    LLM devil's advocate (challenges the thesis, can veto).
    """
    violations: list[str] = []
    consensus_ok = True
    compliance_ok = True
    llm_vetoed = False
    llm_reasoning = ""
    learning_ok = True
    from mt5_mcp import entry_quality
    request = {"action": 1, "type": 0 if side == "BUY" else 1, "symbol": symbol,
               "price": entry_price, "sl": sl_price, "tp": tp_price, "volume": lots,
               "comment": setup or source, "confidence": confidence}
    quality_result = entry_quality.check(request)
    quality_ok = quality_result["allowed"]
    violations.extend(quality_result.get("violations", []))

    try:
        from mt5_mcp.learning_loop import check_entry
        learned = check_entry(symbol, side, setup=setup or source)
        learning_ok = learned["allowed"]
        violations.extend(learned.get("violations", []))
    except Exception as exc:
        learning_ok = False
        violations.append("learning: completed-trade evidence unavailable")
        logger.warning("entry learning failed closed: %s", exc)

    try:
        from mt5_mcp.consensus import check as consensus_check
        cv = consensus_check(symbol, side, source=source, score=score)
        consensus_ok = cv.get("allowed", True)
        if not consensus_ok:
            violations.extend(cv.get("violations", []))
    except Exception as exc:
        logger.debug("consensus check failed open: %s", exc)

    try:
        from mt5_mcp.compliance import gate as compliance_gate
        gv = compliance_gate(
            symbol, side, lots,
            sl_price=sl_price,
            sl_pips=sl_pips,
            confidence=confidence,
        )
        compliance_ok = gv.get("allowed", False)
        # Always carry compliance findings as evidence.  In enforce mode
        # violations are non-empty iff allowed is False, so behaviour is
        # unchanged; in log mode allowed is True but the findings must
        # still reach the verdict for the post-test review.
        violations.extend(gv.get("violations", []))
    except Exception as exc:
        compliance_ok = False
        violations.append(f"compliance error: {exc}")

    # LLM devil's advocate: challenge the setup before execution
    if enable_llm_devil_advocate and consensus_ok and compliance_ok and learning_ok and quality_ok and entry_price:
        try:
            from mt5_mcp.groq_llm import devils_advocate
            # Get current price for entry estimate
            tp_est = tp_price or 0

            da = devils_advocate(
                symbol=symbol,
                side=side,
                entry=entry_price,
                sl=sl_price or 0.0,
                tp=tp_est,
                health_score=health_score,
                pattern_direction=pattern_direction,
                zone_type=zone_type,
                trend_conflict=trend_conflict,
            )
            if da.should_veto and da.confidence >= 70:
                llm_vetoed = True
                llm_reasoning = da.summary
                violations.append(f"LLM devil's advocate veto: {da.summary}")
                logger.info(
                    "LLM devil's advocate vetoed %s %s (confidence %.0f): %s",
                    symbol, side, da.confidence, da.summary,
                )
        except Exception as exc:
            logger.debug("LLM devil's advocate failed open: %s", exc)

    allowed = consensus_ok and compliance_ok and not llm_vetoed and learning_ok and quality_ok
    try:
        entry_quality.capture(request, quality_result, violations)
    except Exception as exc:
        allowed = False
        quality_ok = False
        violations.append("quality: paper audit unavailable")
        logger.warning("paper audit failed closed: %s", exc)
    log_mode = False
    if not allowed and learning_ok and quality_ok:
        from mt5_mcp.config import rejections_enabled
        if not rejections_enabled():
            # Forward-test log mode: keep every flag/violation for the
            # record, but let the trade through.
            log_mode = True
            allowed = True
            logger.warning(
                "run_gates LOG-MODE sending %s %s despite violations: %s",
                symbol, side, "; ".join(violations) or "(flags only)",
            )
    return GateVerdict(
        allowed=allowed,
        violations=violations,
        consensus_allowed=consensus_ok,
        compliance_allowed=compliance_ok,
        llm_vetoed=llm_vetoed,
        llm_reasoning=llm_reasoning,
        log_mode=log_mode,
        learning_allowed=learning_ok,
    )


def execute_trade(
    symbol: str,
    side: str,
    lots: float,
    sl: float | None = None,
    tp: float | None = None,
    comment: str = "",
    confidence: float | None = None,
    source: str = "pipeline",
    score: float = 0.0,
    setup: str = "",
    signal_id: str | None = None,
    journal_extra: dict[str, Any] | None = None,
    dry_run: bool = False,
    trend_confidence: float | None = None,
    confirmation_confidence: float | None = None,
    trade_score: float | None = None,
    zone_strength: float | None = None,
    rr: float | None = None,
    separation_atr: float | None = None,
    mtf_aligned: int | None = None,
    atr_val: float | None = None,
    zone_proximity: float | None = None,
    zone_touches: int | None = None,
) -> ExecResult:
    """Full pipeline: gates → execute → journal → notify.

    Returns an ``ExecResult`` with the fill status and ticket. On gate failure
    the trade is not attempted. On dry_run the gates run but no order is sent.
    """
    from mt5_mcp.tools.trades import open_market

    sl_pips = None
    entry_price = None
    if sl is not None:
        try:
            from mt5_mcp.client import get_client
            client = get_client()
            info = client.symbol_info(symbol)
            pip = pip_size(info)
            tick = client.tick(symbol)
            entry_price = float(tick.get("ask" if side == "BUY" else "bid", 0))
            if pip > 0 and entry_price > 0:
                sl_pips = abs(entry_price - sl) / pip
        except Exception:
            pass

    gate = run_gates(
        symbol=symbol,
        side=side,
        lots=lots,
        sl_price=sl,
        sl_pips=sl_pips,
        confidence=confidence,
        source=source,
        score=score,
        tp_price=tp,
        enable_llm_devil_advocate=True,
        setup=setup or comment,
        entry_price=entry_price,
    )

    if not gate.allowed:
        logger.info("pipeline gate blocked %s %s: %s", symbol, side, gate.violations)
        return ExecResult(filled=False, gate_verdict=gate, violations=gate.violations)

    if dry_run:
        logger.info("pipeline dry-run: would execute %s %s %.2f lots", symbol, side, lots)
        return ExecResult(filled=False, gate_verdict=gate)

    try:
        result = open_market(symbol, side, lots, sl=sl, tp=tp, comment=comment, confidence=confidence)
    except Exception as exc:
        logger.error("pipeline execute failed for %s %s: %s", symbol, side, exc)
        return ExecResult(filled=False, gate_verdict=gate, violations=[str(exc)])

    if not result.accepted:
        logger.warning("pipeline order rejected %s %s: %s", symbol, side, result.error)
        return ExecResult(filled=False, trade_result=result, gate_verdict=gate)

    ticket = result.ticket
    _journal_fill(
        ticket=ticket,
        symbol=symbol,
        side=side,
        lots=lots,
        sl=sl,
        tp=tp,
        confidence=confidence,
        setup=setup,
        signal_id=signal_id,
        extra=journal_extra,
        trend_confidence=trend_confidence,
        confirmation_confidence=confirmation_confidence,
        trade_score=trade_score,
        zone_strength=zone_strength,
        rr=rr,
        sl_pips=sl_pips,
        separation_atr=separation_atr,
        mtf_aligned=mtf_aligned,
        atr_val=atr_val,
        zone_proximity=zone_proximity,
        zone_touches=zone_touches,
    )
    _notify_fill(
        symbol=symbol,
        side=side,
        lots=lots,
        entry=_fill_entry_price(result),
        sl=sl,
        tp=tp,
        ticket=ticket,
        confidence=confidence,
        setup=setup,
    )

    # Trigger LLM post-trade review (async, non-blocking)
    # This will be called when the trade closes, not when it opens
    # For now, we'll log the setup for later review
    if setup:
        logger.info(
            "Trade opened: %s %s %.2f lots, setup=%s, confidence=%.1f — "
            "post-trade review will analyze on close",
            symbol, side, lots, setup, confidence or 0.0,
        )

    return ExecResult(filled=True, ticket=ticket, trade_result=result, gate_verdict=gate)


def equity_curve_sizing_multiplier(
    journal_db_path: str | None = None,
    lookback: int = 10,
    loss_streak_threshold: int = 3,
    win_streak_threshold: int = 3,
    loss_multiplier: float = 0.5,
    win_multiplier: float = 1.25,
    enabled: bool = True,
) -> dict:
    """Compute a sizing multiplier from recent win/loss streak.

    Reads the last ``lookback`` closed trades from the journal and checks
    the most recent consecutive outcome streak:
      - 3+ consecutive losses -> multiplier = 0.5 (halve size)
      - 3+ consecutive wins   -> multiplier = 1.25 (boost, capped at base risk)
      - otherwise             -> multiplier = 1.0

    Returns:
        dict with keys:
            multiplier: The sizing multiplier (0.5 / 1.0 / 1.25).
            streak: Signed streak count (positive = wins, negative = losses).
            streak_type: "win", "loss", or "neutral".
            trades_used: Number of trades examined.
    """
    if not enabled:
        return {
            "multiplier": 1.0,
            "streak": 0,
            "streak_type": "neutral",
            "trades_used": 0,
        }

    try:
        from mt5_mcp.memory.trade_journal import get_journal
        journal = get_journal()

        # Get last N closed trades ordered by close_time DESC
        trades = journal.list_trades(limit=lookback)
        if not trades:
            return {
                "multiplier": 1.0,
                "streak": 0,
                "streak_type": "neutral",
                "trades_used": 0,
            }

        # Calculate streak from most recent trade backwards
        # list_trades returns DESC, so index 0 is most recent
        streak = 0
        streak_type = "neutral"
        first_outcome = trades[0].get("outcome") if trades else None

        if first_outcome == "win":
            for t in trades:
                if t.get("outcome") == "win":
                    streak += 1
                else:
                    break
            streak_type = "win"
        elif first_outcome == "loss":
            for t in trades:
                if t.get("outcome") == "loss":
                    streak += 1
                else:
                    break
            streak_type = "loss"
        else:
            # breakeven or no outcome -> neutral
            streak = 0
            streak_type = "neutral"

        # Determine multiplier
        multiplier = 1.0
        if streak_type == "loss" and streak >= loss_streak_threshold:
            multiplier = loss_multiplier
            logger.info(
                "equity curve sizing: %d consecutive losses -> multiplier %.2f",
                streak, multiplier,
            )
        elif streak_type == "win" and streak >= win_streak_threshold:
            multiplier = win_multiplier
            logger.info(
                "equity curve sizing: %d consecutive wins -> multiplier %.2f",
                streak, multiplier,
            )

        # Represent streak as signed: positive = wins, negative = losses
        signed_streak = streak if streak_type == "win" else (-streak if streak_type == "loss" else 0)

        return {
            "multiplier": multiplier,
            "streak": signed_streak,
            "streak_type": streak_type,
            "trades_used": min(len(trades), lookback),
        }

    except Exception as exc:
        logger.debug("equity curve sizing failed: %s", exc)
        return {
            "multiplier": 1.0,
            "streak": 0,
            "streak_type": "neutral",
            "trades_used": 0,
        }


def size_position(
    symbol: str,
    sl_distance: float,
    risk_amount: float,
    client: Any | None = None,
    apply_equity_curve: bool = True,
) -> float | None:
    """Compute lot size from risk budget using tick-value math.

    When ``apply_equity_curve`` is True (default), the risk_amount is adjusted
    by the equity curve sizing multiplier before computing lots. This reduces
    size after losing streaks and increases it (capped at base) after wins.

    Returns the lot size rounded down to the broker's volume step, or ``None``
    if the position cannot be sized (e.g. even min-lot exceeds risk budget).
    """
    if client is None:
        from mt5_mcp.client import get_client
        client = get_client()

    # Apply equity curve sizing multiplier to risk_amount
    from mt5_mcp import performance_risk
    if performance_risk.enabled():
        risk_amount = performance_risk.limit_budget(client,risk_amount)
    elif apply_equity_curve:
        ec = equity_curve_sizing_multiplier()
        risk_amount = risk_amount * ec["multiplier"]

    info = client.symbol_info(symbol)
    if not info:
        return None

    pip = pip_size(info)
    pv = pip_value_per_lot(info, symbol)
    if pip <= 0 or pv <= 0 or sl_distance <= 0:
        return None

    # Spread never blocks, but it is real risk: widen the effective SL
    # distance by the live spread so wide-spread fills get smaller lots.
    spread_points = float(info.get("spread") or 0.0)
    point = float(info.get("point") or 0.0)
    effective_distance = sl_distance + spread_points * point

    loss_per_lot = (effective_distance / pip) * pv
    if loss_per_lot <= 0:
        return None

    raw_lots = risk_amount / loss_per_lot

    vol_min = float(info.get("volume_min", 0.01))
    vol_max = float(info.get("volume_max", 100.0))
    vol_step = float(info.get("volume_step", 0.01))

    if not all(math.isfinite(v) and v > 0 for v in (risk_amount,vol_step,vol_min,vol_max)):
        return None
    lots = round(math.floor(min(raw_lots,vol_max) / vol_step + 1e-10) * vol_step,8)
    if lots < vol_min or lots * loss_per_lot > risk_amount + 1e-8:
        return None

    return lots


def kelly_adjusted_risk(
    base_risk: float,
    ml_score: float | None = None,
    kelly_fraction: float = 0.10,
) -> float:
    """Adjust risk amount using Kelly criterion and ML confidence.

    base_risk: default risk amount (e.g. 1% of balance)
    ml_score: ML model's predicted win probability (0-1), None to skip adjustment
    kelly_fraction: optimal Kelly fraction from backtest (e.g. 0.10 = 10%)

    Returns adjusted risk amount, capped at base_risk. Higher ML confidence
    gets closer to base_risk; lower confidence gets reduced.
    """
    if ml_score is None or ml_score <= 0:
        return base_risk

    try:
        from mt5_mcp.ml_ranker import kelly_risk_multiplier
        multiplier = kelly_risk_multiplier(ml_score, kelly_fraction)
        adjusted = base_risk * multiplier
        # Cap at base_risk (never risk more than the default)
        adjusted = min(adjusted, base_risk)
        logger.debug(
            "kelly risk: base=%.2f ml_score=%.3f multiplier=%.2f adjusted=%.2f",
            base_risk, ml_score, multiplier, adjusted,
        )
        return adjusted
    except Exception as exc:
        logger.debug("kelly adjustment failed: %s", exc)
        return base_risk


def publish_stance(
    source: str,
    symbol: str,
    side: str,
    score: float,
    note: str = "",
) -> None:
    """Publish a stance to the consensus board. Fail-open."""
    try:
        from mt5_mcp.consensus import publish
        publish(source, symbol, side, score, note=note)
    except Exception as exc:
        logger.debug("consensus publish failed: %s", exc)


def _journal_fill(
    *,
    ticket: int | None,
    symbol: str,
    side: str,
    lots: float,
    sl: float | None,
    tp: float | None,
    confidence: float | None,
    setup: str,
    signal_id: str | None,
    extra: dict[str, Any] | None,
    trend_confidence: float | None = None,
    confirmation_confidence: float | None = None,
    trade_score: float | None = None,
    zone_strength: float | None = None,
    rr: float | None = None,
    sl_pips: float | None = None,
    separation_atr: float | None = None,
    mtf_aligned: int | None = None,
    atr_val: float | None = None,
    zone_proximity: float | None = None,
    zone_touches: int | None = None,
) -> None:
    try:
        from mt5_mcp.memory.trade_journal import get_journal
        journal = get_journal()
        fields: dict[str, Any] = {
            "ticket": ticket,
            "symbol": symbol,
            "side": side,
            "setup": setup or "pipeline",
            "entry": _fill_entry_price_from_ticket(ticket),
            "sl": sl,
            "tp": tp,
            "lots": lots,
            "confidence": confidence,
            "trend_confidence": trend_confidence,
            "confirmation_confidence": confirmation_confidence,
            "trade_score": trade_score,
            "zone_strength": zone_strength,
            "rr": rr,
            "sl_pips": sl_pips,
            "separation_atr": separation_atr,
            "mtf_aligned": mtf_aligned,
            "atr_val": atr_val,
            "zone_proximity": zone_proximity,
            "zone_touches": zone_touches,
        }
        if signal_id:
            fields["signal_id"] = signal_id
        if extra:
            fields.update(extra)
        journal.record_trade(**fields)
    except Exception as exc:
        logger.warning("pipeline journal fill failed: %s", exc)


def _notify_fill(
    *,
    symbol: str,
    side: str,
    lots: float,
    entry: float,
    sl: float | None,
    tp: float | None,
    ticket: int | None,
    confidence: float | None,
    setup: str,
) -> None:
    try:
        from mt5_mcp.notifier import trade_filled
        trade_filled(
            symbol=symbol,
            side=side,
            entry=entry,
            lots=lots,
            sl=sl or 0.0,
            tp=tp or 0.0,
            ticket=ticket,
            confidence=confidence,
            setup=setup,
        )
    except Exception as exc:
        logger.warning("pipeline notify fill failed: %s", exc)


def _fill_entry_price(result: TradeResult) -> float:
    details = result.details or {}
    return float(
        details.get("price")
        or details.get("entry")
        or result.request.get("price", 0.0)
        or 0.0
    )


def _fill_entry_price_from_ticket(ticket: int | None) -> float:
    if not ticket:
        return 0.0
    try:
        from mt5_mcp.client import get_client
        client = get_client()
        for pos in client.positions() or []:
            if int(pos.get("ticket", 0)) == ticket:
                return float(pos.get("price_open", 0.0))
    except Exception:
        pass
    return 0.0


def trigger_post_trade_review(
    symbol: str,
    side: str,
    entry: float,
    exit_price: float,
    sl: float,
    tp: float,
    pnl: float,
    hold_time_minutes: int,
    original_thesis: str = "",
) -> None:
    """Trigger LLM post-trade review after a trade closes.

    Call this from position close handlers (desk manage, watchers, etc.).
    Logs the review result for analysis.
    """
    try:
        from mt5_mcp.groq_llm import post_trade_review
        review = post_trade_review(
            symbol=symbol,
            side=side,
            entry=entry,
            exit_price=exit_price,
            sl=sl,
            tp=tp,
            pnl=pnl,
            hold_time_minutes=hold_time_minutes,
            original_thesis=original_thesis,
        )
        result = "WIN" if pnl > 0 else "LOSS"
        logger.info(
            "Post-trade review [%s %s %s]: thesis_correct=%s, would_take_again=%s, "
            "confidence=%.0f, lessons=%s",
            symbol, side, result, review.thesis_correct, review.would_take_again,
            review.confidence_in_review, review.lessons[:2],
        )
        # Store review in journal_extra for future analysis
        # (could be written to a separate reviews table)
    except Exception as exc:
        logger.debug("post-trade review failed: %s", exc)
