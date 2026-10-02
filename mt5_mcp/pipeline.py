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


@dataclass
class GateVerdict:
    """Result of the pre-trade gate chain."""
    allowed: bool
    violations: list[str] = field(default_factory=list)
    consensus_allowed: bool = True
    compliance_allowed: bool = True


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
) -> GateVerdict:
    """Run consensus check + compliance gate. Returns a combined verdict.

    Consensus is checked first (cheap, fail-open). Compliance is the hard veto.
    Both return the same ``{allowed, violations}`` shape so they chain cleanly.
    """
    violations: list[str] = []
    consensus_ok = True
    compliance_ok = True

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
        if not compliance_ok:
            violations.extend(gv.get("violations", []))
    except Exception as exc:
        compliance_ok = False
        violations.append(f"compliance error: {exc}")

    return GateVerdict(
        allowed=consensus_ok and compliance_ok,
        violations=violations,
        consensus_allowed=consensus_ok,
        compliance_allowed=compliance_ok,
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

    return ExecResult(filled=True, ticket=ticket, trade_result=result, gate_verdict=gate)


def size_position(
    symbol: str,
    sl_distance: float,
    risk_amount: float,
    client: Any | None = None,
) -> float | None:
    """Compute lot size from risk budget using tick-value math.

    Returns the lot size rounded down to the broker's volume step, or ``None``
    if the position cannot be sized (e.g. even min-lot exceeds risk budget).
    """
    if client is None:
        from mt5_mcp.client import get_client
        client = get_client()

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

    lots = round_to_step(raw_lots, vol_step, floor=vol_min, ceil=vol_max)

    min_risk = (vol_min / vol_step) * loss_per_lot * vol_step
    if lots < vol_min and min_risk > risk_amount * 1.5:
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
