"""Trade Decision Engine: structured TRADE / NO_TRADE with full explanation.

Contract D1 (see architecture.md): every decision is a JSON-serialisable
dict with fixed keys, so downstream systems (journal, risk, MCP) never
need to parse free-text.

Decision flow (prompt section 7):
  1. Location context  — where is price, what levels surround it
  2. Regime + structure — trend vs range, bullish vs bearish
  3. Setup pattern     — zone / sweep / location trigger + confirmation
  4. ML scores         — P(1R), P(2R), P(3R), expected_R
  5. Risk / sizing     — position size, max daily loss, session gate
  6. Final gate        — expected_R >= floor, all context consistent

Output: Decision with kind="TRADE" or kind="NO_TRADE", reason[].
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from ml_engine.config import EngineConfig
from ml_engine.models.scorer import ScorerResult
from ml_engine.setups.composer import Setup


@dataclass
class Decision:
    kind: str                  # "TRADE" | "NO_TRADE" | "HOLD"
    symbol: str
    direction: str             # "long" | "short" | ""
    entry: float
    stop: float
    target: float
    risk_reward: float
    position_size: float       # lots (0.01 = 1 micro lot)
    risk_percent: float        # actual % risked on this trade
    regime: str
    vol_regime: str
    htf_bias: str
    structure_bias: str        # "bullish" | "bearish" | "neutral"
    location_context: dict
    zone_context: dict
    liquidity_context: dict
    price_action_context: dict
    definition: str
    p_1r: float
    p_2r: float
    p_3r: float
    expected_r: float
    base_rate_1r: float
    base_rate_2r: float
    base_rate_3r: float
    reason: list[str]
    invalidation: list[str]
    timestamp: str = ""
    time: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def no_trade(
        symbol: str,
        reasons: list[str],
        timestamp: str = "",
    ) -> Decision:
        return Decision(
            kind="NO_TRADE",
            symbol=symbol,
            direction="",
            entry=0.0, stop=0.0, target=0.0,
            risk_reward=0.0,
            position_size=0.0, risk_percent=0.0,
            regime="", vol_regime="", htf_bias="",
            structure_bias="",
            location_context={}, zone_context={},
            liquidity_context={}, price_action_context={},
            definition="",
            p_1r=0.0, p_2r=0.0, p_3r=0.0,
            expected_r=0.0,
            base_rate_1r=0.0, base_rate_2r=0.0, base_rate_3r=0.0,
            reason=reasons,
            invalidation=[],
            timestamp=timestamp,
        )


def _sizing(
    risk_reward: float,
    risk_pct: float,
    max_daily_loss_pct: float,
    daily_loss_used: float,
    account_balance: float,
    pip_value: float,
    atr: float,
    stop_distance: float,
    min_lot: float,
    max_lot: float,
) -> tuple[float, float]:
    """Position sizing: fixed-fractional risk with daily loss guard.

    Returns (position_size_lots, actual_risk_percent).
    """
    risk_pct = min(risk_pct, max(0.0, risk_pct - daily_loss_used))
    if risk_pct <= 0 or account_balance <= 0:
        return 0.0, 0.0

    risk_amount = account_balance * (risk_pct / 100.0)
    # pip_value is per-lot per-pip; stop_distance in price terms
    # lot_size = risk_amount / (stop_distance / pip_size * pip_value_per_lot)
    # For forex: pip_value_per_lot = 10 per pip (standard lot), pip_size = 0.0001
    # Simplified: lots = risk_amount / (stop_distance * 100000)
    #   (1 standard lot = 100,000 units; $1/pip = $10/pip for standard)
    if stop_distance <= 0:
        return 0.0, 0.0
    # For cross-pairs (JPY): pip = 0.01, pip_value_per_lot = $6.67
    # Use generic: risk_amount / (stop_distance * contract_size)
    contract_size = 100000.0  # standard lot
    lots = risk_amount / (stop_distance * contract_size)
    lots = round(max(min_lot, min(max_lot, lots)), 2)
    actual_risk_pct = (lots * stop_distance * contract_size / account_balance * 100) if account_balance > 0 else 0.0
    return lots, actual_risk_pct


def decide(
    setup: Setup,
    ml_result: ScorerResult,
    cfg: EngineConfig,
    *,
    account_balance: float = 10000.0,
    daily_loss_used: float = 0.0,
    atr: float = 0.0,
) -> Decision:
    """Produce a structured TRADE or NO_TRADE decision.

    Parameters
    ----------
    setup : Setup from the composer
    ml_result : ScorerResult from the multi-target scorer
    cfg : EngineConfig
    account_balance : current account equity
    daily_loss_used : cumulative loss % used today (for daily limit)
    atr : current ATR (for sizing)
    """
    reasons: list[str] = []
    invalidations: list[str] = []
    timestamp = datetime.now(timezone.utc).isoformat()

    # ---- ML gate -----------------------------------------------------------
    if ml_result.expected_r < cfg.setup.min_risk_reward:
        reasons.append(f"expected_R ({ml_result.expected_r:.3f}) < min ({cfg.setup.min_risk_reward})")

    if ml_result.p_1r < 0.35:
        reasons.append(f"p_1r ({ml_result.p_1r:.3f}) too low (< 0.35)")

    # ---- Regime / structure consistency -------------------------------------
    regime = setup.market_regime
    vol = setup.vol_regime
    htf = setup.htf_bias
    bias_str = "bullish" if setup.structure_context.get("bias", 0) > 0 else (
        "bearish" if setup.structure_context.get("bias", 0) < 0 else "neutral"
    )

    # HTF bias should align with direction
    want_htf = "bullish" if setup.direction == "long" else "bearish"
    if htf and htf not in (want_htf, "neutral"):
        reasons.append(f"htf_bias ({htf}) conflicts with direction ({setup.direction})")

    # Regime: trending setups should not fire in choppy regime
    if regime == "choppy" and setup.definition.startswith("location"):
        reasons.append("choppy regime + location setup is low-quality")

    # Vol regime: skip ultra-high vol (spike risk)
    if vol == "high_volatility":
        reasons.append("high volatility regime — elevated spike risk")

    # ---- Location quality --------------------------------------------------
    loc = setup.location_context
    if loc:
        if loc.get("dist_atr", 999) > 2.0:
            reasons.append(f"location too far ({loc['dist_atr']:.1f} ATR)")
        if loc.get("strength", 0) < 50:
            reasons.append(f"location strength ({loc['strength']:.0f}) < 50")
    else:
        reasons.append("no location context")

    # ---- Zone quality ------------------------------------------------------
    z = setup.zone_context
    if z:
        if z.get("strength", 0) < 40:
            reasons.append(f"zone strength ({z['strength']:.0f}) < 40")
        if z.get("status") == "tested":
            reasons.append("zone already tested — reduced freshness")

    # ---- Invalidation list -------------------------------------------------
    if setup.direction == "long":
        invalidations.append(f"close below stop {setup.stop:.5f}")
        if loc:
            invalidations.append(f"break below location low {loc.get('price_low', 0):.5f}")
    else:
        invalidations.append(f"close above stop {setup.stop:.5f}")
        if loc:
            invalidations.append(f"break above location high {loc.get('price_high', 0):.5f}")

    # ---- Daily loss guard --------------------------------------------------
    max_daily = cfg.backtest.risk_percent * 20  # 10x the per-trade risk as daily limit
    if daily_loss_used >= max_daily:
        reasons.append(f"daily loss limit reached ({daily_loss_used:.1f}% >= {max_daily:.1f}%)")

    # ---- Sizing (if all gates pass) ----------------------------------------
    stop_dist = abs(setup.entry - setup.stop)
    position_size, risk_pct = _sizing(
        risk_reward=setup.risk_reward,
        risk_pct=cfg.backtest.risk_percent,
        max_daily_loss_pct=cfg.backtest.risk_percent * 20,
        daily_loss_used=daily_loss_used,
        account_balance=account_balance,
        pip_value=cfg.backtest.pip_value_per_lot,
        atr=atr if atr > 0 else stop_dist,
        stop_distance=stop_dist,
        min_lot=0.01,
        max_lot=cfg.backtest.max_concurrent * 1.0,  # crude lot cap
    )

    # ---- Final decision ----------------------------------------------------
    # TRADE only if: no blocking reasons AND position_size > 0
    blocking = [r for r in reasons if any(kw in r for kw in (
        "daily loss", "htf_bias", "expected_R", "p_1r",
    ))]

    if not blocking and position_size > 0:
        kind = "TRADE"
    else:
        kind = "NO_TRADE"
        position_size = 0.0

    return Decision(
        kind=kind,
        symbol=setup.symbol,
        direction=setup.direction if kind == "TRADE" else "",
        entry=setup.entry,
        stop=setup.stop,
        target=setup.target,
        risk_reward=setup.risk_reward,
        position_size=position_size,
        risk_percent=risk_pct,
        regime=regime,
        vol_regime=vol,
        htf_bias=htf,
        structure_bias=bias_str,
        location_context=loc,
        zone_context=z,
        liquidity_context=setup.liquidity_context,
        price_action_context=setup.price_action_context,
        definition=setup.definition,
        p_1r=ml_result.p_1r,
        p_2r=ml_result.p_2r,
        p_3r=ml_result.p_3r,
        expected_r=ml_result.expected_r,
        base_rate_1r=ml_result.base_rate_1r,
        base_rate_2r=ml_result.base_rate_2r,
        base_rate_3r=ml_result.base_rate_3r,
        reason=reasons,
        invalidation=invalidations,
        timestamp=timestamp,
    )
