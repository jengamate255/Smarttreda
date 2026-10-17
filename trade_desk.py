"""Hedge-fund style trade desk: multi-analyst daily scan -> confluence ->
position management, running as a continuous background service.

Analysts (each returns a score per symbol, 0-100):
  1. MarketHealth  - regime, volatility, session, news, liquidity
  2. ChartPatterns - geometric formations w/ AI quality scores
  3. SupplyDemand  - zone strength / freshness / retest proximity
  4. Trend         - EMA50/200 + market structure alignment
  5. Momentum      - RSI/MACD/volume confluence

Confluence engine:
  Combines analyst votes into a per-symbol conviction (0-100) with a
  suggested direction. Only symbols clearing DESK_MIN_CONFLUENCE produce a
  trade plan. Correlated symbols compete: best conviction per correlation
  group wins (hedge-fund style portfolio construction, not shotgun).

Execution policy (fail-closed, like auto_trader):
  * compliance gate on every order (risk, spread, news, correlation caps)
  * position sizing by risk % (default 0.25%)
  * max positions from compliance; slots freed by closes
  * every decision journaled (trade_journal) for the coach/learning loop
  * kill-switch file stops the desk instantly
  * daily trade cap from compliance state

Position management (every cycle, for open desk positions):
  * break-even at +1R
  * trailing stop after +1.5R (chandelier-style, 1.5*ATR)
  * time-stop: flatten positions older than MAX_HOLD_HOURS unless in profit

Modes:
  MT5_DESK_ENABLED=1 and MT5_DESK_MODE=SIM guard live orders.
  --dry-run prints plans without sending anything.

Usage:
  python trade_desk.py            # continuous
  python trade_desk.py --once    # one scan cycle
  python trade_desk.py --scan    # analysis only, print the desk sheet
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[0]
MODULE_DIR = PROJECT_DIR / "mt5_mcp"
for p in (str(PROJECT_DIR), str(MODULE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

load_dotenv(PROJECT_DIR / ".env")

from mt5_mcp.client import get_client  # noqa: E402
from mt5_mcp.chart_patterns import analyze_chart  # noqa: E402
from mt5_mcp.compliance import CORRELATION_GROUPS, evaluate as eval_compliance, gate  # noqa: E402
from mt5_mcp.memory.trade_journal import get_journal  # noqa: E402
from mt5_mcp.notify import notify, fmt_trade_executed, fmt_trade_signal, fmt_desk_sheet, fmt_position_update, fmt_scan_summary  # noqa: E402
from mt5_mcp.supply_demand import detect_zones  # noqa: E402
from mt5_mcp.market_intel import market_health  # noqa: E402
from mt5_mcp.learning_loop import adjust_pattern_score, learning_snapshot  # noqa: E402
from mt5_mcp.strategy import analyze_trend  # noqa: E402
from tools.trades import MAGIC, close_position, modify_sl_tp, open_market  # noqa: E402

# ── desk configuration ──────────────────────────────────────────────
LOG_PATH = PROJECT_DIR / "logs" / "trade_desk.log"
STATE_PATH = MODULE_DIR / "memory" / "desk_state.json"
EVENTS_PATH = MODULE_DIR / "memory" / "desk_events.jsonl"
KILL_PATH = MODULE_DIR / "memory" / "DESK_KILL"

DESK_ENABLED = os.getenv("MT5_DESK_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
DESK_MODE = os.getenv("MT5_DESK_MODE", "SIM").strip().upper()
DESK_RISK_PERCENT = float(os.getenv("MT5_DESK_RISK_PERCENT", "0.25"))
# Fixed dollar risk per trade. When > 0 it overrides the percent-based sizing.
DESK_RISK_FIXED = float(os.getenv("MT5_DESK_RISK_FIXED", "90"))
SCAN_INTERVAL_MIN = float(os.getenv("MT5_DESK_SCAN_MINUTES", "30"))   # full scan cadence
MANAGE_INTERVAL_SEC = float(os.getenv("MT5_DESK_MANAGE_SECONDS", "60"))  # mgmt cadence
DESK_MIN_CONFLUENCE = float(os.getenv("MT5_DESK_MIN_CONFLUENCE", "75"))
DESK_MIN_HEALTH = float(os.getenv("MT5_DESK_MIN_HEALTH", "55"))
MAX_HOLD_HOURS = float(os.getenv("MT5_DESK_MAX_HOLD_HOURS", "96"))
DESK_MAGIC = int(os.getenv("MT5_DESK_MAGIC", str(MAGIC + 7)))  # distinguish desk orders

# Champion-combo levels (Colab exhaustive sweep 2026-09-12, 136k combos):
# M5 entry, H4+H6 gates, SL 3.0xATR, TP 1.5R, partial exit at +1R.
# Bootstrap: P(profit)=100%, exp_p5=+$6.94, PF floor 1.90 at $25 risk.
DESK_ENTRY_TF = os.getenv("MT5_DESK_ENTRY_TF", "M5")
DESK_SL_ATR_MULT = float(os.getenv("MT5_DESK_SL_ATR", "3.0"))
DESK_TP_R_MULT = float(os.getenv("MT5_DESK_TP_R", "1.5"))

# Symbol-specific minimum SL floors (price-distance units).
# M5 ATR * 4.5 is too tight for volatile symbols — BTC's M5 noise ($50-$200)
# produces SL of $225-$900, but BTC regularly moves $3,300+ (3.3% pullbacks).
# JPY pairs see 15-25 pip session-open chops that clip 9-13 pip M5 stops.
# These floors ensure the SL survives normal volatility before the trade develops.
MIN_SL_FLOOR: dict[str, float] = {
    "BTCUSD": 1000.0,   # ~$1000 min (vs $225-$900 from M5 ATR)
    "ETHUSD": 30.0,     # ~$30 min (ETH ATR ~$30-50 on M5)
    "USDJPY": 0.20,     # 20 pips min (vs 9-36 pips from M5 ATR)
    "EURJPY": 0.20,     # 20 pips
    "GBPJPY": 0.25,     # 25 pips (wider JPY cross)
    "CHFJPY": 0.20,     # 20 pips
}

# Position management feature flags (default: enabled)
PARTIAL_CLOSE_ENABLED = os.getenv("MT5_DESK_PARTIAL_CLOSE", "1").strip().lower() in ("1", "true", "yes", "on")
BREAKEVEN_ENABLED = os.getenv("MT5_DESK_BREAKEVEN", "1").strip().lower() in ("1", "true", "yes", "on")
PARTIAL_CLOSE_FRACTION = float(os.getenv("MT5_DESK_PARTIAL_FRACTION", "0.5"))  # close 50% at 1R
PARTIAL_CLOSE_R_TRIGGER = float(os.getenv("MT5_DESK_PARTIAL_R", "1.0"))  # trigger at +1R
BREAKEVEN_BUFFER_POINTS = int(os.getenv("MT5_DESK_BE_BUFFER_POINTS", "2"))  # SL offset from entry

# Early loss cut (dead trade detection): exit trades that are deeply negative
# early. Data shows 1-4h holds have 25% WR — these are dead trades walking.
# Cut at -0.5R after 2h to recycle capital into better setups.
EARLY_LOSS_CUT_ENABLED = os.getenv("MT5_DESK_EARLY_LOSS_CUT", "1").strip().lower() in ("1", "true", "yes", "on")
EARLY_LOSS_CUT_HOURS = float(os.getenv("MT5_DESK_EARLY_LOSS_HOURS", "2"))  # min age before cut
EARLY_LOSS_CUT_R = float(os.getenv("MT5_DESK_EARLY_LOSS_R", "-0.5"))  # cut if r_multiple < this

# tradeable universe — majors, metals, indices, energy, crypto (SIM broker)
# JPY crosses added 2026-09-12: champion combo validated OOS on CHFJPY/EURJPY/
# GBPJPY/NZDJPY (52 trades, WR 42.3%, EV +22.5p, PF 1.85; stable across both
# time halves). AUDJPY/CADJPY excluded (marginal/negative EV).
# New crosses added 2026-09-12 (stability-validated both halves, no neg months):
# GBPCHF (+12.5p), NZDCAD (+12.4p), AUDNZD (+22.0p), EURAUD (+13.1p), EURCHF (+5.7p).
# JP225 REMOVED 2026-09-12: champion backtest EV -326p (0/4 WR) — no edge there.
UNIVERSE: list[str] = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "CHFJPY", "EURJPY", "GBPJPY", "NZDJPY",
    "GBPCHF", "NZDCAD", "AUDNZD", "EURAUD", "EURCHF",
    "XAUUSD", "XAGUSD", "USOIL",
    "SPX500", "GER40",
    "BTCUSD", "ETHUSD",
]

# analyst weights in the confluence score
WEIGHTS = {
    "health": 0.15,
    "patterns": 0.30,
    "zones": 0.20,
    "trend": 0.20,
    "momentum": 0.15,
}

logger = logging.getLogger("mt5_desk")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
    if sys.stdout is not None:
        logger.addHandler(logging.StreamHandler())


def _log_event(kind: str, payload: dict[str, Any]) -> None:
    if isinstance(payload, list):  # a bare actions list — wrap, don't crash (fixes 15:20 cycle error)
        payload = {"actions": payload}
    payload = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
    try:
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("state save failed: %s", exc)


# ── rejection cooldown ───────────────────────────────────────────────
# A pick that the broker/gate rejects (margin, filling, stops) goes on a
# per-symbol cooldown so one bad symbol can't burn every scan cycle.

COOLDOWN_MINUTES = float(os.getenv("MT5_DESK_COOLDOWN_MIN", "120"))


def _cooldown_symbol(symbol: str, reason: str) -> None:
    state = _load_state()
    cd = state.setdefault("cooldowns", {})
    until = time.time() + COOLDOWN_MINUTES * 60
    cd[symbol] = {"until": until, "reason": reason[:200]}
    _save_state(state)
    logger.info("cooldown %s for %.0fm: %s", symbol, COOLDOWN_MINUTES, reason[:120])


def _cooldown_remaining(symbol: str) -> float:
    """Minutes left on cooldown (0 = free to trade)."""
    try:
        cd = _load_state().get("cooldowns", {}).get(symbol)
        if not cd:
            return 0.0
        left = (float(cd.get("until") or 0.0) - time.time()) / 60.0
        if left <= 0:
            state = _load_state()
            state.get("cooldowns", {}).pop(symbol, None)
            _save_state(state)
            return 0.0
        return left
    except Exception:
        return 0.0


def _cooldown_reason(symbol: str) -> str:
    try:
        return str(_load_state().get("cooldowns", {}).get(symbol, {}).get("reason") or "")
    except Exception:
        return ""


# ── analysts ────────────────────────────────────────────────────────

def _frame(client, symbol: str, tf: str = "H1", count: int = 350) -> pd.DataFrame | None:
    """Fetch OHLCV frame once per symbol (analysts take DataFrames)."""
    try:
        frame = client.rates(symbol, tf, count)
        from mt5_mcp.entry_quality import closed_frame, enabled
        return closed_frame(frame) if enabled() else frame
    except Exception as exc:
        logger.debug("rates %s %s failed: %s", symbol, tf, exc)
        return None


def analyst_health(client, symbol: str) -> dict[str, Any]:
    """Market Health analyst (0-100) + verdict."""
    try:
        report = market_health(symbol, "H1", lookback=300)
        return {
            "score": float(report.get("health_score", 0.0)),
            "regime": report.get("regime", "?"),
            "verdict": report.get("verdict", "?"),
            "detail": report,
        }
    except Exception as exc:
        return {"score": 0.0, "regime": "?", "verdict": "error", "error": str(exc)}


def analyst_patterns(client, symbol: str) -> dict[str, Any]:
    """Chart-pattern analyst: best actionable pattern score w/ direction."""
    try:
        frame = _frame(client, symbol, "H1", 400)
        if frame is None or len(frame) < 60:
            return {"score": 0.0, "direction": None}
        report = analyze_chart(frame, symbol=symbol, timeframe="H1", lookback=350)
        patterns = report.get("patterns") or []
        if not patterns:
            return {"score": 0.0, "direction": None}
        # Rank only executable setups in quality mode, including learned trust.
        best, best_score, best_dir = None, 0.0, None
        best_raw_score, best_factor = 0.0, 1.0
        from mt5_mcp.entry_quality import enabled as quality_enabled, pattern_entry_check
        strict = quality_enabled()
        quote = client.tick(symbol) if strict else {}
        for p in patterns:
            if p.get("direction") not in ("bullish", "bearish"):
                continue
            side = "BUY" if p["direction"] == "bullish" else "SELL"
            if strict and not pattern_entry_check(
                    p, side, quote.get("ask" if side == "BUY" else "bid"))["allowed"]:
                continue
            score = float(p.get("ai_score", p.get("quality_score", 0)) or 0)
            if str(p.get("status")) == "confirmed":
                score = min(100.0, score * 1.15)
            raw_score, learned_factor = score, 1.0
            if strict:
                score, learned_factor = adjust_pattern_score(p.get("pattern"), score, symbol=symbol)
            if score > best_score:
                best, best_score, best_dir = p, score, p["direction"]
                best_raw_score, best_factor = raw_score, learned_factor
        if not best:
            return {"score": 0.0, "direction": None}
        # Learning loop: journal win rate per setup adjusts trust.
        raw_score, learned_factor = best_raw_score, best_factor
        if not strict:
            best_score, learned_factor = adjust_pattern_score(best.get("pattern"), best_score, symbol=symbol)
        return {
            "score": best_score,
            "raw_score": raw_score,
            "learned_factor": learned_factor,
            "direction": "BUY" if best_dir == "bullish" else "SELL",
            "pattern": best.get("pattern"),
            "status": best.get("status"),
            "target": best.get("target"),
            "invalidation": best.get("invalidation"),
        }
    except Exception as exc:
        return {"score": 0.0, "direction": None, "error": str(exc)}


def analyst_zones(client, symbol: str) -> dict[str, Any]:
    """Supply/demand analyst: nearest fresh zone strength + direction."""
    try:
        tick = client.tick(symbol)
        price = float(tick.get("bid") or 0.0)
        frame = _frame(client, symbol, "H1", 400)
        if frame is None or len(frame) < 60 or not price:
            return {"score": 0.0, "direction": None}
        zones = detect_zones(frame, lookback=300) or []
        best, best_score, best_dir, best_dist = None, 0.0, None, None
        from mt5_mcp.entry_quality import enabled as quality_enabled
        strict = quality_enabled()
        for z in zones:
            if strict and (not getattr(z, "fresh", False) or getattr(z, "broken", False)):
                continue
            zl = getattr(z, "lower", None)
            zu = getattr(z, "upper", None)
            ztype = str(getattr(z, "type", "") or "")
            if zl is None or zu is None:
                continue
            # distance from price to the zone edge (0 if inside)
            dist = 0.0 if zl <= price <= zu else min(abs(price - zl), abs(price - zu))
            strength = float(getattr(z, "strength", 0) or 0)
            # fresh zones that price is approaching score best (within 1%)
            proximity_bonus = max(0.0, 40.0 * (1.0 - dist / (price * 0.01)))
            score = min(100.0, strength * 0.7 + proximity_bonus)
            if getattr(z, "fresh", False):
                score *= 1.1
            if score > best_score:
                best, best_score = z, score
                best_dist = dist
                best_dir = "BUY" if "demand" in ztype.lower() else "SELL"
        if not best:
            return {"score": 0.0, "direction": None}
        return {
            "score": best_score,
            "direction": best_dir,
            "zone_type": getattr(best, "type", ""),
            "zone_lower": float(getattr(best, "lower", 0)),
            "zone_upper": float(getattr(best, "upper", 0)),
            "distance": best_dist,
            "fresh": bool(getattr(best, "fresh", False)),
            "broken": bool(getattr(best, "broken", False)),
        }
    except Exception as exc:
        return {"score": 0.0, "direction": None, "error": str(exc)}


# MTF trend cache: symbol -> (ts, result). Higher TFs change slowly, so
# W1/D1 are cached 6h, H4 2h, H1 30min, M30 10min, M15 5min. Prevents the
# rate-fetch stampede that made scans 25x slower after the MTF upgrade.
_MTF_CACHE: dict[str, tuple[float, dict]] = {}
_MTF_TTL = {"W1": 6 * 3600, "D1": 6 * 3600, "H4": 2 * 3600, "H1": 1800, "M30": 600, "M15": 300}


def _mtf_trend(client, symbol: str, tf: str) -> dict[str, Any]:
    """One timeframe's trend verdict, cached by TTL."""
    key = f"{symbol}:{tf}"
    now = time.time()
    hit = _MTF_CACHE.get(key)
    from mt5_mcp.entry_quality import enabled as quality_enabled
    ttl = _MTF_TTL.get(tf,300)
    if quality_enabled() and tf in ("H1", "H4"):
        ttl = min(30, ttl)
    if hit and now - hit[0] < ttl:
        return hit[1]
    bars = 300 if tf in ("W1", "D1", "M15", "M30") else 600 if tf == "H4" else 400
    try:
        frame = _frame(client, symbol, tf, bars)
        if frame is None or len(frame) < 220:
            out = {"tf": tf, "direction": None, "note": "insufficient data"}
        else:
            res = analyze_trend(frame)
            if isinstance(res, dict):
                direction = str(res.get("direction", "")).lower()
                confidence = float(res.get("confidence", 0) or 0)
            else:
                direction = str(getattr(res, "direction", "") or "").lower()
                confidence = float(getattr(res, "confidence", 0) or 0)
            if direction not in ("bullish", "bearish"):
                out = {"tf": tf, "direction": "sideways", "confidence": confidence}
            else:
                out = {"tf": tf, "direction": "BUY" if direction == "bullish" else "SELL",
                       "confidence": confidence}
    except Exception as exc:
        out = {"tf": tf, "direction": None, "error": str(exc)}
    if len(_MTF_CACHE) > 200:
        _MTF_CACHE.clear()
    _MTF_CACHE[key] = (now, out)
    return out


def analyst_trend(client, symbol: str) -> dict[str, Any]:
    """Trend analyst: Multi-timeframe confluence (W1 → D1 → H4 → H1 → M30 → M15).

    All TFs must agree on direction. If any disagree, score is 0 and the
    symbol is flagged with which TFs conflicted. Uses TTL caching so a
    15-symbol scan does not hammer MT5 with 90 rate fetches.
    """
    try:
        mtf_results = [_mtf_trend(client, symbol, tf) for tf in ("W1", "D1", "H4", "H1", "M30", "M15")]
        directional = [d for d in mtf_results if d.get("direction") in ("BUY", "SELL")]

        if not directional:
            return {"score": 25.0, "direction": None, "note": "all timeframes sideways",
                    "mtf": mtf_results}

        primary_dir = directional[0]["direction"]
        aligned = sum(1 for d in directional if d["direction"] == primary_dir)
        total = len(directional)

        if aligned < total:
            disagree = [d["tf"] for d in directional if d["direction"] != primary_dir]
            return {
                "score": 0.0,
                "direction": None,
                "note": f"MTF conflict: {', '.join(disagree)} vs {primary_dir}",
                "mtf": mtf_results,
            }

        avg_conf = sum(d.get("confidence", 0) for d in directional) / max(1, total)
        return {
            "score": avg_conf * 100.0,
            "direction": primary_dir,
            "mtf": mtf_results,
            "mtf_aligned": aligned,
            "mtf_total": total,
        }
    except Exception as exc:
        return {"score": 0.0, "direction": None, "error": str(exc)}


def analyst_momentum(client, symbol: str) -> dict[str, Any]:
    """Momentum analyst: RSI direction + MACD histogram + volume."""
    try:
        df = _frame(client, symbol, "H1", 60)
        if df is None or len(df) < 40:
            return {"score": 0.0, "direction": None}
        closes = df["close"].astype(float)
        # RSI(14)
        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
        rsi_now = float(rsi.iloc[-1])
        # MACD histogram
        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
        hist_now = float(hist.iloc[-1])
        hist_prev = float(hist.iloc[-2])
        # volume ratio
        vol = df["tick_volume"].astype(float)
        vol_ratio = float(vol.iloc[-5:].mean() / max(1e-9, vol.iloc[-30:].mean()))

        score = 50.0
        direction = None
        if rsi_now > 55:
            score += 15; direction = "BUY"
        elif rsi_now < 45:
            score += 15; direction = "SELL"
        else:
            score -= 10
        if hist_now > 0 and hist_now > hist_prev:
            score += 20; direction = direction or "BUY"
        elif hist_now < 0 and hist_now < hist_prev:
            score += 20; direction = direction or "SELL"
        if vol_ratio > 1.2:
            score += 10
        return {"score": max(0.0, min(100.0, score)), "direction": direction,
                "rsi": round(rsi_now, 1), "macd_hist": hist_now, "volume_ratio": round(vol_ratio, 2)}
    except Exception as exc:
        return {"score": 0.0, "direction": None, "error": str(exc)}


# ── confluence engine ───────────────────────────────────────────────

def confluence(analyses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Blend analyst scores into one conviction + agreed direction."""
    direction_votes: dict[str, float] = {"BUY": 0.0, "SELL": 0.0}
    total = 0.0
    for name, res in analyses.items():
        score = float(res.get("score", 0) or 0)
        direction = res.get("direction")
        w = WEIGHTS.get(name, 0.1)
        total += score * w
        if direction in direction_votes:
            direction_votes[direction] += score * w
    weight_sum = sum(WEIGHTS.values())
    blended = total / weight_sum if weight_sum else 0.0
    votes = direction_votes["BUY"] + direction_votes["SELL"]
    if votes <= 0:
        return {"conviction": round(blended, 1), "direction": None, "agreement": 0.0}
    agreement = max(direction_votes.values()) / votes
    direction = "BUY" if direction_votes["BUY"] >= direction_votes["SELL"] else "SELL"
    from mt5_mcp.entry_quality import enabled as quality_enabled
    if quality_enabled():
        opposing = direction_votes["SELL" if direction == "BUY" else "BUY"]
        blended = (total - opposing) / weight_sum if weight_sum else 0.0
    # conviction is blended score scaled by directional agreement
    conviction = blended * (0.5 + 0.5 * agreement)
    return {
        "conviction": round(conviction, 1),
        "direction": direction,
        "agreement": round(agreement * 100, 0),
        "votes": {k: round(v, 1) for k, v in direction_votes.items()},
    }


def correlation_group(symbol: str) -> str:
    return CORRELATION_GROUPS.get(symbol.upper(), "other:" + symbol.upper())


def build_desk_sheet(client, symbols: list[str]) -> list[dict[str, Any]]:
    """Run all analysts over the universe; return ranked desk sheet."""
    sheet: list[dict[str, Any]] = []
    for sym in symbols:
        analyses = {
            "health": analyst_health(client, sym),
            "patterns": analyst_patterns(client, sym),
            "zones": analyst_zones(client, sym),
            "trend": analyst_trend(client, sym),
            "momentum": analyst_momentum(client, sym),
        }
        conf = confluence(analyses)
        entry = {
            "symbol": sym,
            "price": None,
            "health": analyses["health"].get("score"),
            "health_verdict": analyses["health"].get("verdict"),
            "regime": analyses["health"].get("regime"),
            "pattern": analyses["patterns"].get("pattern"),
            "pattern_score": analyses["patterns"].get("score"),
            "zone_score": analyses["zones"].get("score"),
            "trend_score": analyses["trend"].get("score"),
            "momentum_score": analyses["momentum"].get("score"),
            "confluence": conf["conviction"],
            "direction": conf["direction"],
            "agreement": conf.get("agreement"),
            "analysts": analyses,
        }
        try:
            t = client.tick(sym)
            entry["price"] = float(t.get("bid") or 0.0)
            entry["entry_bid"] = float(t.get("bid") or 0.0)
            entry["entry_ask"] = float(t.get("ask") or 0.0)
        except Exception:
            pass
        sheet.append(entry)
    sheet.sort(key=lambda e: float(e.get("confluence") or 0), reverse=True)
    return sheet


def selection_reasons(entry: dict[str, Any], traded_today: set[str]) -> list[str]:
    """Explain the same checks used by portfolio selection, without broker calls."""
    from mt5_mcp.entry_quality import enabled as quality_enabled, pattern_entry_check
    reasons = []
    side = entry.get("direction")
    if side not in ("BUY", "SELL"):
        reasons.append("no entry direction")
    for field, floor in (("confluence", DESK_MIN_CONFLUENCE), ("health", DESK_MIN_HEALTH)):
        value = float(entry.get(field) or 0)
        if not math.isfinite(value) or value < floor:
            reasons.append(f"{field} below selection floor")
    if entry["symbol"] in traded_today:
        reasons.append("symbol already traded today")
    if quality_enabled():
        analysts = entry.get("analysts", {})
        pattern, zones, trend = (analysts.get(key, {}) for key in ("patterns", "zones", "trend"))
        price = entry.get("entry_ask" if side == "BUY" else "entry_bid")
        reasons.extend(pattern_entry_check(pattern, side, price)["reasons"])
        if zones.get("direction") != side:
            reasons.append("selected zone direction disagrees with entry")
        if not zones.get("fresh") or zones.get("broken", False):
            reasons.append("selected zone is not fresh and unbroken")
        directions = {value.get("tf"): value.get("direction") for value in trend.get("mtf", [])}
        if any(directions.get(tf) != side for tf in ("H1", "H4")):
            reasons.append("H1/H4 direction disagrees with entry")
        if "entry_plan" in entry and entry["entry_plan"] is None:
            reasons.append("no measured stop/target plan supports required RR")
    return list(dict.fromkeys(reasons))


def portfolio_select(sheet: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    """Pick the best eligible symbol per correlation group; record the reasons."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    traded_today: set[str] = set(state.get("daily_trades", {}).get(today, []))
    picks: dict[str, dict[str, Any]] = {}
    for entry in sheet:
        sym = entry["symbol"]
        reasons = selection_reasons(entry, traded_today)
        entry["selection"] = {"eligible": not reasons, "picked": False, "reasons": reasons}
        if reasons:
            continue
        grp = correlation_group(sym)
        cur = picks.get(grp)
        if cur is None or float(entry["confluence"]) > float(cur["confluence"]):
            picks[grp] = entry
    for entry in sheet:
        selection = entry["selection"]
        selection["picked"] = picks.get(correlation_group(entry["symbol"])) is entry
        if selection["eligible"] and not selection["picked"]:
            selection["reasons"].append("lower-ranked candidate in same correlation group")
    return list(picks.values())


def selection_snapshot(sheet: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist bounded, explicit evidence for later offline selection reviews."""
    from mt5_mcp.entry_quality import enabled as quality_enabled
    fields = {"patterns": ("pattern", "direction", "status", "score", "raw_score", "learned_factor", "target", "invalidation"),
              "zones": ("direction", "score", "zone_lower", "zone_upper", "fresh", "broken"),
              "trend": ("direction", "score", "mtf"), "momentum": ("direction", "score")}
    return {"schema": 1, "quality_enabled": quality_enabled(),
            "min_confluence": DESK_MIN_CONFLUENCE, "min_health": DESK_MIN_HEALTH,
            "candidates": [{"symbol": entry["symbol"], "direction": entry.get("direction"),
                "confluence": entry.get("confluence"), "health": entry.get("health"),
                "entry_bid": entry.get("entry_bid"), "entry_ask": entry.get("entry_ask"),
                "entry_plan": entry.get("entry_plan"), "selection": entry.get("selection"),
                "analysts": {name: {key: entry.get("analysts", {}).get(name, {}).get(key) for key in keys}
                             for name, keys in fields.items()}} for entry in sheet]}


# ── execution ──────────────────────────────────────────────────────

def _plan_levels(entry: dict[str, Any]) -> dict[str, float] | None:
    """Keep configured stop floors; quality targets stay within usable structure."""
    analysts = entry.get("analysts", {})
    pat = analysts.get("patterns", {})
    zones = analysts.get("zones", {})
    direction = entry.get("direction")
    from mt5_mcp.entry_quality import enabled as quality_enabled, pattern_entry_check
    strict = quality_enabled()
    price = float(entry.get("entry_ask" if direction == "BUY" else "entry_bid") or entry.get("price") or 0.0)
    if not price:
        return None
    if strict and not pattern_entry_check(pat, direction, price)["allowed"]:
        return None
    sym = entry.get("symbol", "")

    # ATR from the entry frame (M5) — the sweep's engine measured ATR(14)
    atr_val = float(analysts.get("atr") or 0.0)
    if atr_val <= 0:
        try:
            client = get_client()
            df = _frame(client, sym, DESK_ENTRY_TF, 30)
            h, l, c = df["high"], df["low"], df["close"]
            prev = c.shift(1)
            tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
            atr_val = float(tr.rolling(14).mean().iloc[-1])
        except Exception:
            atr_val = 0.0
    if atr_val <= 0:
        if strict:
            return None
        atr_val = price * 0.001  # last-resort: 0.1% of price
    if strict and not math.isfinite(atr_val):
        return None

    stop = DESK_SL_ATR_MULT * atr_val

    # Symbol-specific minimum SL floor — prevents M5 ATR from producing
    # absurdly tight stops on volatile symbols (BTC, JPY pairs).
    floor = MIN_SL_FLOOR.get(sym, 0.0)
    if floor > 0 and stop < floor:
        stop = floor

    # Spread buffer: add half the current spread to the SL distance so the
    # actual SL price accounts for the cost of exiting through the spread.
    # (Full spread is already in sizing math; this protects the SL level itself.)
    try:
        _c = get_client()
        info = _c.symbol_info(sym)
        if info:
            sp_pts = float(info.get("spread") or 0.0)
            pt = float(info.get("point") or 0.0)
            if sp_pts > 0 and pt > 0:
                stop += sp_pts * pt * 0.5  # half-spread buffer on SL price
    except Exception:
        pass

    # structural invalidation can widen the stop (never narrow below 3xATR)
    invalidation = pat.get("invalidation")
    if invalidation is not None:
        invalidation = float(invalidation)
        struct_stop = abs(price - invalidation)
        if struct_stop > stop:
            stop = struct_stop

    # TP calculated after all stop adjustments to maintain correct R:R
    tp_dist = DESK_TP_R_MULT * stop

    if direction == "BUY":
        sl = price - stop
        tp = price + tp_dist
    elif direction == "SELL":
        sl = price + stop
        tp = price - tp_dist
    else:
        return None
    if strict:
        target = float(pat["target"])
        tp = min(tp, target) if direction == "BUY" else max(tp, target)
        if not pattern_entry_check(pat, direction, price, sl=sl, tp=tp)["allowed"]:
            return None
    return {"entry": price, "sl": sl, "tp": tp, "atr": atr_val}


def execute_pick(client, entry: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """Compliance gate + sizing + order for one desk pick."""
    sym = entry["symbol"]
    direction = entry.get("direction")
    levels = _plan_levels(entry)
    if not levels:
        return {"symbol": sym, "status": "skipped", "reason": "no levels"}
    sl = levels["sl"]
    tp = levels["tp"]
    # SL distance in price -> pips for the gate + sizing
    try:
        info = client.symbol_info(sym)
        point = float(info.get("point", 0) or 0)
        digits = int(info.get("digits", 5) or 5)
        pip = point * (10 ** (digits - 4)) if digits >= 4 else point * 10
        sl_pips = abs(levels["entry"] - sl) / pip if pip else 0.0
    except Exception:
        sl_pips = 0.0

    # unified gate: consensus (fail-open) + compliance (hard veto)
    from mt5_mcp.pipeline import run_gates
    gate_verdict = run_gates(
        symbol=sym, side=str(direction), lots=0.01,
        sl_price=sl, confidence=float(entry.get("confluence") or 0),
        source="desk", score=float(entry.get("confluence") or 0),
        setup=f"desk:{entry.get('pattern') or 'confluence'}",
        tp_price=tp,
        entry_price=levels["entry"],
    )
    if not gate_verdict.allowed:
        return {"symbol": sym, "status": "blocked", "reason": "; ".join(gate_verdict.violations)}

    # size by risk percent using proper pip value
    lots = _size_position(client, sym, sl_pips, levels["entry"], sl)
    if not lots:
        return {"symbol": sym, "status": "skipped", "reason": "sizing failed"}

    if dry_run:
        return {"symbol": sym, "status": "planned", "side": direction, "lots": lots,
                "entry": levels["entry"], "sl": sl, "tp": tp,
                "confluence": entry.get("confluence"),
                "note": "dry-run: no order sent"}

    # margin pre-check: don't send orders the broker must reject
    ok_margin, margin_reason = _margin_ok(client, sym, direction, lots)
    if not ok_margin:
        _cooldown_symbol(sym, f"margin: {margin_reason}")
        return {"symbol": sym, "status": "rejected", "side": direction, "lots": lots,
                "sl": sl, "tp": tp, "ticket": None,
                "error": margin_reason, "confluence": entry.get("confluence")}

    # rejection cooldown: skip symbols that failed execution recently
    cd_until = _cooldown_remaining(sym)
    if cd_until > 0:
        return {"symbol": sym, "status": "skipped",
                "reason": f"in rejection cooldown {cd_until:.0f}m "
                          f"({_cooldown_reason(sym)})"}

    result = open_market(symbol=sym, side=str(direction), lots=lots, sl=sl, tp=tp,
                         comment=f"desk-{entry.get('pattern', 'confluence')[:18]}",
                         confidence=float(entry.get("confluence") or 0))
    ok = bool(getattr(result, "accepted", False))
    payload = getattr(result, "model_dump", lambda: {})() or {}
    error = str(payload.get("error", "") or "")
    if not ok:
        # any broker rejection puts the symbol on cooldown so one scan
        # can't burn the whole cycle retrying the same dead pick
        _cooldown_symbol(sym, error[:120])
    out = {"symbol": sym, "status": "filled" if ok else "rejected",
           "side": direction, "lots": lots, "sl": sl, "tp": tp,
           "ticket": payload.get("ticket"), "error": error,
           "confluence": entry.get("confluence")}
    if ok:
        journal = get_journal()
        # Calculate RR if we have levels
        rr = 0.0
        if levels.get("entry") and levels.get("sl") and levels.get("tp"):
            risk = abs(levels["entry"] - levels["sl"])
            reward = abs(levels["tp"] - levels["entry"])
            rr = reward / risk if risk > 0 else 0.0
        
        journal.log_signal(
            signal_id=f"desk-{sym}-{int(time.time())}",
            symbol=sym, side=direction, setup=f"desk:{entry.get('pattern', 'confluence')}",
            entry=levels["entry"], sl=sl, tp=tp, lots=lots,
            confidence=float(entry.get("confluence") or 0),
            sl_pips=sl_pips,
            rr=rr,
            filled=True,
            trade_time=datetime.now(timezone.utc).isoformat(),
        )
        # Record trade with available features (desk doesn't have trend/confirmation confidence)
        journal.record_trade(
            ticket=int(payload.get("ticket") or 0),
            symbol=sym, side=direction, setup=f"desk:{entry.get('pattern', 'confluence')}",
            entry=levels["entry"], sl=sl, tp=tp, lots=lots,
            open_time=datetime.now(timezone.utc).isoformat(),
            confidence=float(entry.get("confluence") or 0),
            trade_score=float(entry.get("confluence") or 0),
            rr=rr,
            sl_pips=sl_pips,
            session_hour=datetime.now(timezone.utc).hour,
        )
        state = _load_state()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.setdefault("daily_trades", {}).setdefault(today, []).append(sym)
        state.setdefault("positions", {})[str(payload.get("ticket"))] = {
            "symbol": sym, "side": direction, "entry": levels["entry"],
            "sl": sl, "tp": tp, "lots": lots, "opened": datetime.now(timezone.utc).isoformat(),
            "risk_r": abs(levels["entry"] - sl),
            "setup": f"desk:{entry.get('pattern') or 'confluence'}",
        }
        _save_state(state)
    return out


def _size_position(client, symbol: str, sl_pips: float, entry: float, sl: float) -> float | None:
    """Risk-based lots using tick-value math (contract-aware).

    Rules:
    - Outcome policy overrides the fixed-dollar budget when enabled.
    - Round DOWN to volume step (never round up past the risk cap).
    - Skip the trade if broker minimum volume exceeds the budget.
    - Consistency guard: reduce lot size if best day approaches 40% of total profit
      (FundingPips consistency rule).
    """
    if sl_pips <= 0:
        return None

    # Consistency risk check — reduce lot size if best day is too large
    try:
        from mt5_mcp.challenge_tracker import ChallengeTracker
        ct = ChallengeTracker()
        multiplier = ct.get_consistency_multiplier()
        if multiplier <= 0.0:
            logger.info(
                "consistency block: best day >= 40%% of total profit, skipping %s",
                symbol,
            )
            return None
        if multiplier < 1.0:
            logger.info(
                "consistency reduction: applying %.0f%% lot size for %s (best day %.0f%% of total)",
                multiplier * 100, symbol,
                ct.get_status().get("consistency_ratio", 0),
            )
    except Exception as exc:
        logger.warning("consistency check failed: %s", exc)
        multiplier = 1.0

    try:
        info = client.symbol_info(symbol)
        tick_value = float(info.get("trade_tick_value") or 0.0)
        tick_size = float(info.get("trade_tick_size") or 0.0)
        contract = float(info.get("trade_contract_size", 1.0) or 1.0)
        # money per 1.0 lot per price-unit of SL distance
        if tick_value and tick_size:
            per_unit = tick_value / tick_size
        else:
            per_unit = contract
        risk_money = (
            DESK_RISK_FIXED
            if DESK_RISK_FIXED > 0
            else _equity() * (DESK_RISK_PERCENT / 100.0)
        )
        from mt5_mcp import performance_risk
        policy_enabled = performance_risk.enabled()
        base_percent = DESK_RISK_PERCENT
        if policy_enabled:
            base_percent = performance_risk.effective_percent(client,DESK_RISK_PERCENT)
            risk_money = float(client.account_info().get("equity") or 0) * base_percent / 100
        risk_money *= multiplier  # apply consistency multiplier

        # Drawdown recovery: reduce risk when account drawdown exceeds threshold
        try:
            from mt5_mcp.drawdown_recovery import drawdown_recovery_risk_percent
            equity = _equity()
            dd_result = drawdown_recovery_risk_percent(
                equity=equity,
                max_risk_percent=base_percent,
                enabled=True,
            )
            if dd_result["mode"] == "reduced":
                # Scale risk_money proportionally: reduced_risk / max_risk
                dd_scale = min(1.0, dd_result["risk_percent"] / max(base_percent, 0.01))
                risk_money *= dd_scale
                logger.info(
                    "drawdown recovery: scaling risk by %.0f%% for %s (dd %.1f%%)",
                    dd_scale * 100, symbol, dd_result["drawdown_percent"],
                )
        except Exception as exc:
            logger.debug("drawdown recovery check failed in desk: %s", exc)

        # Equity curve sizing: adjust risk based on recent win/loss streak
        try:
            from mt5_mcp.pipeline import equity_curve_sizing_multiplier
            ec = {"multiplier":1.0} if policy_enabled else equity_curve_sizing_multiplier()
            risk_money *= ec["multiplier"]
            if ec["multiplier"] != 1.0:
                logger.info(
                    "equity curve sizing: multiplier %.2f for %s (streak=%d %s)",
                    ec["multiplier"], symbol, abs(ec["streak"]), ec["streak_type"],
                )
        except Exception as exc:
            logger.debug("equity curve sizing failed in desk: %s", exc)

        risk_money = performance_risk.limit_budget(client,risk_money)
        if risk_money <= 0:
            return None
        sl_distance = abs(entry - sl)
        if sl_distance <= 0:
            return None
        # Spread never blocks, but it is real risk: add live spread to the
        # SL distance so wide-spread fills size down instead of being invisible.
        spread_points = float(info.get("spread") or 0.0)
        point = float(info.get("point") or 0.0)
        if spread_points > 0 and point > 0:
            sl_distance += spread_points * point
        per_lot_risk = sl_distance * per_unit
        if per_lot_risk <= 0:
            return None

        step = float(info.get("volume_step", 0.01) or 0.01)
        vmin = float(info.get("volume_min", 0.01) or 0.01)
        vmax = float(info.get("volume_max", 100.0) or 100.0)

        if not all(math.isfinite(v) and v > 0 for v in (risk_money,per_lot_risk,step,vmin,vmax)):
            return None
        lots = round(math.floor(min(risk_money / per_lot_risk,vmax) / step + 1e-10) * step,8)
        if lots < vmin or lots * per_lot_risk > risk_money + 1e-8:
            logger.info("sizing skip %s: minimum lot exceeds current risk budget", symbol)
            return None
        return lots
    except Exception as exc:
        logger.warning("sizing failed for %s: %s", symbol, exc)
        return None


def _margin_ok(client, symbol: str, direction: str, lots: float) -> tuple[bool, str]:
    """Pre-check free margin before sending an order the broker will reject.

    Also compares vs a hard headroom factor so correlated groups can't
    eat the whole free margin in one scan.
    """
    try:
        acct = client.account_info()
        free = float(acct.get("margin_free") or 0.0)
        action = 1 if str(direction).upper() == "BUY" else -1
        price = _last_price(client, symbol)
        if not price:
            return True, ""  # can't estimate; let the broker decide
        need = client.order_calc_margin(action, symbol, lots, price)
        if need and need > free * 0.9:
            return False, (
                f"insufficient free margin: need {need:.2f}, free {free:.2f}"
            )
        return True, ""
    except Exception:
        return True, ""  # never block on check failure


def _last_price(client, symbol: str) -> float:
    try:
        t = client.tick(symbol)
        ask = float(t.get("ask") or 0.0)
        bid = float(t.get("bid") or ask)
        return ask or bid
    except Exception:
        return 0.0


def _equity() -> float:
    try:
        acct = get_client().account_info()
        return float(acct.get("equity") or 0.0)
    except Exception:
        return 0.0


# ── position management ─────────────────────────────────────────────

def _resolve_closed_position(client, ticket: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    """Find the closing deal for a tracked position that vanished.

    Returns {"summary": {...}, "html": str} or None when the deal can't be
    recovered (e.g. history API hiccup). Uses completed broker positions,
    including partial exits, entry/exit commissions, swap and fees.
    """
    symbol = str(meta.get("symbol", ""))
    side = str(meta.get("side", ""))
    entry = float(meta.get("entry") or 0)
    try:
        from mt5_mcp.journal_reconcile import closed_positions
        opened = datetime.fromisoformat(meta["opened"])
        age_days = max(0, (datetime.now(timezone.utc) - opened).days)
        records = closed_positions(client=client, days=max(3, age_days + 2))
        record = records.get(int(ticket)) or next(
            (row for row in records.values() if row.get("entry_order") == int(ticket)), None)
    except Exception as exc:
        logger.warning("Cannot resolve closed position %s: %s", ticket, exc)
        return None
    if record is None:
        return None
    pnl = record["pnl"]
    close_price = record["exit_price"]
    reason = "broker-confirmed close (all deals and costs included)"
    symbol = str(record["symbol"] or symbol)
    summary = {
        "ticket": int(ticket),
        "symbol": symbol,
        "side": side,
        "action": "closed",
        "pnl": round(pnl, 2),
        "entry": entry,
        "close": close_price,
        "reason": reason,
        "outcome": record["outcome"],
        "close_time": min(datetime.fromtimestamp(record["close_time"], timezone.utc), datetime.now(timezone.utc)).isoformat(),
    }
    html = fmt_position_update(
        symbol=symbol,
        ticket=int(ticket),
        action=f"CLOSED — {reason}",
        entry=entry or None,
        new_sl=None,
        pnl=pnl,
        reason=f"exit {close_price:g}" if close_price else "",
    )
    return {"summary": summary, "html": html}


def manage_positions(client, dry_run: bool) -> list[dict[str, Any]]:
    """Break-even, trail, time-stop the desk's open positions."""
    actions: list[dict[str, Any]] = []
    state = _load_state()
    tracked: dict[str, Any] = state.get("positions", {})
    try:
        positions = client.positions()
    except Exception:
        return actions
    live = {str(p.get("ticket")): p for p in (positions or [])}

    for ticket, meta in list(tracked.items()):
        pos = live.get(ticket)
        if pos is None:
            # Position closed at the broker (SL/TP hit or manual close).
            # Recover the closing deal so the trader gets a notification
            # and the journal gets a record — a silent stop-out is the
            # one thing a notification system must never do.
            closed = _resolve_closed_position(client, ticket, meta)
            if not closed:
                continue
            tracked.pop(ticket)
            if closed:
                actions.append(closed["summary"])
                _log_event("position_closed", closed["summary"])
                notify(
                    "closed_" + str(ticket),
                    f"{closed['summary'].get('symbol')} closed "
                    f"{closed['summary'].get('pnl')}",
                    html=closed["html"],
                )
                try:
                    get_journal().record_trade(
                        symbol=closed["summary"].get("symbol", ""),
                        ticket=int(ticket),
                        side=closed["summary"].get("side", ""),
                        pnl=float(closed["summary"].get("pnl") or 0),
                        setup=meta.get("setup", "desk_managed"),
                        open_time=meta.get("opened"),
                        close_time=closed["summary"]["close_time"],
                        exit_price=closed["summary"].get("close"),
                        outcome=closed["summary"]["outcome"],
                        reason=closed["summary"].get("reason", ""),
                        lessons="closed while desk was tracking",
                    )
                except Exception:
                    pass
            continue
        symbol = pos.get("symbol", meta.get("symbol"))
        entry = float(pos.get("price_open") or meta.get("entry") or 0)
        sl = float(pos.get("sl") or 0)
        tp = float(pos.get("tp") or 0)
        price = float(pos.get("price_current") or 0)
        side = str(pos.get("type", meta.get("side", "BUY"))).upper()
        is_buy = side in ("BUY", "0", "2") or str(pos.get("type")) == "0"
        if not entry or not price:
            continue

        risk_r = abs(entry - float(meta.get("sl", entry)))
        if risk_r <= 0:
            continue
        move = (price - entry) if is_buy else (entry - price)
        r_multiple = move / risk_r

        opened = str(meta.get("opened", ""))
        age_h = 0.0
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(opened)).total_seconds() / 3600
        except Exception:
            pass

        # 1) partial exit at +PARTIAL_CLOSE_R_TRIGGER R: bank a fraction, let the runner run.
        #    A winner that round-trips to entry is the most expensive mistake
        #    a desk can make — bank some, let the rest pay for the risk.
        if PARTIAL_CLOSE_ENABLED and r_multiple >= PARTIAL_CLOSE_R_TRIGGER and not meta.get("partial_taken"):
            volume = float(pos.get("volume") or 0)
            info = None
            try:
                info = client.symbol_info(symbol)
            except Exception:
                pass
            vmin = float((info or {}).get("volume_min", 0.01) or 0.01)
            step = float((info or {}).get("volume_step", 0.01) or 0.01)
            remaining_min = max(vmin, step)
            take = math.floor((volume * PARTIAL_CLOSE_FRACTION) / step) * step
            if not dry_run and volume - take >= remaining_min and take >= vmin:
                try:
                    res = close_position(int(ticket), volume=round(take, 2))
                    if getattr(res, "accepted", False):
                        meta["partial_taken"] = True
                        actions.append({
                            "ticket": ticket, "symbol": symbol,
                            "action": "partial-exit",
                            "closed_lots": round(take, 2),
                            "remaining_lots": round(volume - take, 2),
                            "r_multiple": round(r_multiple, 2),
                        })
                        _log_event("partial_exit", {
                            "ticket": ticket, "symbol": symbol,
                            "closed_lots": round(take, 2),
                            "remaining_lots": round(volume - take, 2),
                            "r_multiple": round(r_multiple, 2),
                        })
                        html = fmt_position_update(
                            symbol=symbol, ticket=int(ticket),
                            action=f"PARTIAL EXIT @ +{r_multiple:.1f}R",
                            entry=entry,
                            reason=f"closed {round(take, 2)} lots, {round(volume - take, 2)} running",
                        )
                        notify(
                            f"partial_{ticket}",
                            f"{symbol} partial exit: banked {round(take, 2)} lots at +{r_multiple:.1f}R",
                            html=html,
                        )
                    else:
                        # partial failed (e.g. stops level) — don't retry every
                        # cycle; mark taken so BE/trail still proceed
                        meta["partial_taken"] = True
                        logger.warning("partial exit rejected %s ticket %s", symbol, ticket)
                except Exception as exc:
                    logger.warning("partial exit failed %s: %s", symbol, exc)
            elif dry_run and not meta.get("partial_taken"):
                actions.append({"ticket": ticket, "symbol": symbol,
                                "action": "partial-exit (dry-run)",
                                "closed_lots": round(take, 2) if take >= vmin else 0})

        # 1b) break-even at +PARTIAL_CLOSE_R_TRIGGER R: move SL to entry+buffer.
        #     Independent of partial close — can be enabled alone.
        if BREAKEVEN_ENABLED and r_multiple >= PARTIAL_CLOSE_R_TRIGGER and not meta.get("be_armed"):
            buf = BREAKEVEN_BUFFER_POINTS * _point(client, symbol)
            be_price = entry + buf if is_buy else entry - buf
            ok = (not dry_run) and _safe_modify(client, int(ticket), be_price, tp)
            if ok or dry_run:
                meta["be_armed"] = True
                actions.append({"ticket": ticket, "symbol": symbol,
                                "action": "break-even", "sl": be_price,
                                "entry": entry, "r_multiple": round(r_multiple, 2)})
                _log_event("breakeven", {
                    "ticket": ticket, "symbol": symbol,
                    "sl": be_price, "entry": entry,
                    "r_multiple": round(r_multiple, 2),
                })
                html = fmt_position_update(
                    symbol=symbol, ticket=int(ticket),
                    action=f"BREAKEVEN @ +{r_multiple:.1f}R",
                    entry=entry, new_sl=be_price,
                    reason="SL moved to entry (risk-free)",
                )
                notify(
                    f"breakeven_{ticket}",
                    f"{symbol} breakeven: SL → {be_price:g} at +{r_multiple:.1f}R",
                    html=html,
                )

        # 2) trail after +1.5R at 1R distance
        if r_multiple >= 1.5 and meta.get("be_armed"):
            try:
                df = client.rates(symbol, "H1", 20)
                atr = float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])
            except Exception:
                atr = risk_r * 0.5
            if is_buy:
                new_sl = price - max(atr, risk_r)
                if new_sl > sl:
                    ok = (not dry_run) and _safe_modify(client, int(ticket), round(new_sl, 10), tp)
                    if ok or dry_run:
                        actions.append({"ticket": ticket, "symbol": symbol, "action": "trail", "sl": round(new_sl, 10)})
            else:
                new_sl = price + max(atr, risk_r)
                if not sl or new_sl < sl:
                    ok = (not dry_run) and _safe_modify(client, int(ticket), round(new_sl, 10), tp)
                    if ok or dry_run:
                        actions.append({"ticket": ticket, "symbol": symbol, "action": "trail", "sl": round(new_sl, 10)})
        # 2b) early loss cut — dead trade detection
        # Data: 1-4h holds have 25% WR. If a trade is losing >0.5R after 2h,
        # it's probably dead. Cut early to recycle capital.
        if EARLY_LOSS_CUT_ENABLED and age_h >= EARLY_LOSS_CUT_HOURS and r_multiple <= EARLY_LOSS_CUT_R:
            if not meta.get("early_cut"):
                if not dry_run:
                    res = close_position(int(ticket))
                    accepted = bool(getattr(res, "accepted", False))
                    actions.append({"ticket": ticket, "symbol": symbol,
                                    "action": "early-loss-cut",
                                    "r_multiple": round(r_multiple, 2),
                                    "age_h": round(age_h, 1),
                                    "result": accepted})
                    if accepted:
                        meta["early_cut"] = True
                        _log_event("early_loss_cut", {
                            "ticket": ticket, "symbol": symbol,
                            "r_multiple": round(r_multiple, 2),
                            "age_h": round(age_h, 1),
                        })
                        html = fmt_position_update(
                            symbol=symbol, ticket=int(ticket),
                            action=f"EARLY CUT @ {r_multiple:.2f}R",
                            entry=entry,
                            reason=f"dead trade: {age_h:.1f}h old, cutting loss",
                        )
                        notify(
                            f"early_cut_{ticket}",
                            f"{symbol} early loss cut: {r_multiple:.2f}R after {age_h:.1f}h",
                            html=html,
                        )
                else:
                    actions.append({"ticket": ticket, "symbol": symbol,
                                    "action": "early-loss-cut (dry-run)",
                                    "r_multiple": round(r_multiple, 2)})
        # 3) time-stop
        if age_h > MAX_HOLD_HOURS and r_multiple < 1.0:
            if not dry_run:
                res = close_position(int(ticket))
                actions.append({"ticket": ticket, "symbol": symbol, "action": "time-stop",
                                "result": bool(getattr(res, "accepted", False))})
            else:
                actions.append({"ticket": ticket, "symbol": symbol, "action": "time-stop (dry-run)"})

    state["positions"] = tracked
    _save_state(state)
    return actions


def _point(client, symbol: str) -> float:
    try:
        return float(client.symbol_info(symbol).get("point", 0) or 0)
    except Exception:
        return 0.0


def _safe_modify(client, ticket: int, sl: float, tp: float | None) -> bool:
    try:
        res = modify_sl_tp(ticket, sl=sl, tp=tp if tp else None)
        return bool(getattr(res, "accepted", False))
    except Exception as exc:
        logger.warning("modify %s failed: %s", ticket, exc)
        return False


# ── guards ──────────────────────────────────────────────────────────

def desk_guards() -> str | None:
    if KILL_PATH.exists():
        return "kill-switch present"
    if not DESK_ENABLED:
        return "MT5_DESK_ENABLED off"
    acct_server = ""
    try:
        acct = get_client().account_info()
        acct_server = str(acct.get("server", "") or "")
    except Exception:
        return "account info unavailable"
    if DESK_MODE == "SIM" and "SIM" not in acct_server.upper():
        return f"mode SIM but server is {acct_server!r}"
    return None


# ── cycles ──────────────────────────────────────────────────────────

def scan_cycle(client, dry_run: bool) -> dict[str, Any]:
    """Full universe scan -> portfolio selection -> execution."""
    from mt5_mcp.paper_learning import schedule_advance
    schedule_advance(client)
    started = time.time()
    sheet = build_desk_sheet(client, UNIVERSE)
    from mt5_mcp import entry_quality
    if entry_quality.enabled():
        for candidate in sheet:
            side = candidate.get("direction")
            levels = _plan_levels(candidate) if side in ("BUY", "SELL") else None
            candidate["entry_plan"] = levels
            if not levels:
                continue
            request = {"symbol": candidate["symbol"], "type": 0 if side == "BUY" else 1, "action": 1,
                       "price": levels["entry"], "sl": levels["sl"], "tp": levels["tp"],
                       "comment": f"desk:{candidate.get('pattern') or 'confluence'}",
                       "confidence": float(candidate.get("confluence") or 0)}
            quality = entry_quality.check(request, client)
            reasons = quality.get("violations", []) + (["desk: below selection threshold"] if candidate["confluence"] < DESK_MIN_CONFLUENCE else [])
            if float(candidate.get("health") or 0) < DESK_MIN_HEALTH:
                reasons.append("desk: below health threshold")
            try:
                entry_quality.capture(request, quality, reasons)
            except Exception as exc:
                logger.warning("Cannot record paper candidate: %s", exc)
    picks = portfolio_select(sheet, _load_state())
    executions = []
    if picks:
        status = eval_compliance()
        free_slots = int(status.get("max_positions", 3)) - int(status.get("open_positions", 0))
        # ── correlation-aware risk caps ─────────────────────────────
        # Open positions already carry risk per group; count it so we never
        # stack correlated trades (e.g. USOIL BUY + XAU SELL + EUR BUY are
        # all effectively one USD-short trade).
        from mt5_mcp.compliance import (MAX_GROUP_RISK_USD, MAX_USD_EXPOSURE_RISK,
                                        USD_EXPOSURE_MEMBERS)
        group_risk: dict[str, float] = {}
        usd_risk = 0.0
        try:
            for pos in client.positions() or []:
                p = pos if isinstance(pos, dict) else {}
                grp = CORRELATION_GROUPS.get(str(p.get("symbol", "")).upper(), "")
                risk = DESK_RISK_FIXED or 25.0  # each open position ≈ one unit of risk
                if grp:
                    group_risk[grp] = group_risk.get(grp, 0) + risk
                if str(p.get("symbol", "")).upper() in USD_EXPOSURE_MEMBERS:
                    usd_risk += risk
        except Exception:
            pass
        usd_dir: str = ""  # net USD direction of open exposure
        for pick in picks[: max(0, free_slots)]:
            sym = pick["symbol"].upper()
            grp = CORRELATION_GROUPS.get(sym, "other:" + sym)
            risk = DESK_RISK_FIXED or 25.0
            # group cap
            if group_risk.get(grp, 0) + risk > MAX_GROUP_RISK_USD:
                executions.append({"symbol": pick["symbol"], "status": "skipped",
                                   "reason": f"group {grp} at ${group_risk.get(grp,0):.0f} cap "
                                             f"${MAX_GROUP_RISK_USD:.0f}"})
                continue
            # USD-exposure cap
            if sym in USD_EXPOSURE_MEMBERS:
                pick_dir = pick.get("direction", "")
                # treat as same USD bet if direction matches net open USD bias
                same_side = (usd_risk > 0 and not usd_dir) or usd_dir == pick_dir
                if usd_risk + risk > MAX_USD_EXPOSURE_RISK and same_side:
                    executions.append({"symbol": pick["symbol"], "status": "skipped",
                                       "reason": f"USD exposure at ${usd_risk:.0f} cap "
                                                 f"${MAX_USD_EXPOSURE_RISK:.0f}"})
                    continue
            res = execute_pick(client, pick, dry_run)
            executions.append(res)
            _log_event("execute", res)
            if res.get("status") == "filled":
                if grp:
                    group_risk[grp] = group_risk.get(grp, 0) + risk
                if sym in USD_EXPOSURE_MEMBERS:
                    usd_risk += risk
                    usd_dir = usd_dir or pick.get("direction", "")
    summary = {
        "scanned": len(sheet),
        "top": [
            {"symbol": e["symbol"], "confluence": e["confluence"], "dir": e["direction"],
             "health": e["health"], "pattern": e.get("pattern")}
            for e in sheet[:5]
        ],
        "picks": [{"symbol": p["symbol"], "dir": p["direction"], "confluence": p["confluence"]} for p in picks],
        "executions": executions,
        "elapsed_s": round(time.time() - started, 1),
        "learning": learning_snapshot(),
        "selection": selection_snapshot(sheet),
    }
    _log_event("scan", summary)
    # publish desk stances to the consensus board (top-sheet directional reads)
    try:
        from mt5_mcp.pipeline import publish_stance
        for t in summary.get("top", []):
            publish_stance("desk", t.get("symbol", ""), t.get("dir", "NONE"),
                           float(t.get("confluence", 0)))
    except Exception:
        pass
    if executions:
        note = "; ".join(
            f"{e.get('symbol')} {e.get('status')} {e.get('side', '')} {e.get('lots', '')}".strip()
            for e in executions
        )
        # Build rich HTML card for Telegram
        filled = [e for e in executions if e.get("status") == "filled"]
        if filled:
            for e in filled:
                html = fmt_trade_executed(
                    symbol=e.get("symbol", "?"),
                    direction=e.get("side", "?").upper(),
                    entry=float(e.get("entry", 0)),
                    lots=float(e.get("lots", 0)),
                    sl=float(e.get("sl", 0)),
                    tp=float(e.get("tp", 0)),
                    note=f"confluence {int(e.get('confluence', 0))}",
                )
                notify("desk_filled_" + e.get("symbol", ""), note, html=html)
        else:
            # No fills — send scan summary instead
            top3 = [
                {"symbol": t["symbol"], "direction": t["dir"], "confluence": t["confluence"]}
                for t in summary.get("top", [])[:3]
            ]
            html = fmt_scan_summary(scanned=len(sheet), top3=top3, picks=len(picks))
            notify("desk_scan", note, html=html)
    return summary


def manage_cycle(client, dry_run: bool) -> list[dict[str, Any]]:
    actions = manage_positions(client, dry_run)
    from mt5_mcp.paper_learning import schedule_advance
    schedule_advance(client)
    if actions:
        _log_event("manage", actions)
        for a in actions:
            action_kind = a.get("action", "?")
            # partial-exit and break-even already send their own rich
            # notifications inside manage_positions — skip the generic
            # manage_cycle card to avoid duplicate Telegram messages.
            if action_kind in ("partial-exit", "break-even"):
                continue
            html = fmt_position_update(
                symbol=a.get("symbol", "?"),
                ticket=int(a.get("ticket", 0)),
                action=action_kind,
                entry=float(a["entry"]) if a.get("entry") else None,
                new_sl=float(a["sl"]) if a.get("sl") else (
                    float(a["new_sl"]) if a.get("new_sl") else None),
                reason=a.get("reason", ""),
            )
            notify("desk_manage_" + str(a.get("ticket", "")),
                   f"{a.get('symbol')} {action_kind}", html=html)
    return actions


# ── main ────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="single scan + manage cycle")
    parser.add_argument("--scan", action="store_true", help="analysis only: print desk sheet")
    parser.add_argument("--dry-run", action="store_true", help="no orders sent")
    args = parser.parse_args()

    _setup_logging()
    logger.info("trade desk starting (enabled=%s mode=%s risk=%.2f%%)",
                DESK_ENABLED, DESK_MODE, DESK_RISK_PERCENT)
    client = get_client()
    client.ensure_connected()

    if args.scan:
        sheet = build_desk_sheet(client, UNIVERSE)
        for e in sheet:
            print("%-8s conv=%5.1f dir=%-4s agree=%-4.0f health=%5.1f pattern=%-18s(%s) zone=%.0f trend=%.0f mom=%.0f" % (
                e["symbol"], float(e.get("confluence") or 0), e.get("direction") or "-",
                float(e.get("agreement") or 0), float(e.get("health") or 0),
                str(e.get("pattern") or "-"), str(e.get("health_verdict") or "-"),
                float(e.get("zone_score") or 0), float(e.get("trend_score") or 0),
                float(e.get("momentum_score") or 0)))
        picks = portfolio_select(sheet, _load_state())
        print("\nPortfolio picks:")
        for p in picks:
            print("  %s %s conv=%.1f" % (p["symbol"], p["direction"], p["confluence"]))
        return 0

    dry_run = args.dry_run or not DESK_ENABLED

    if args.once:
        summary = scan_cycle(client, dry_run)
        manage_cycle(client, dry_run)
        logger.info("cycle: scanned=%s picks=%s executions=%s",
                    summary["scanned"], len(summary["picks"]), len(summary["executions"]))
        return 0

    # continuous mode: scan every SCAN_INTERVAL_MIN, manage every MANAGE_INTERVAL_SEC
    last_scan = 0.0
    while True:
        try:
            client.ensure_connected()
            now = time.time()
            if now - last_scan >= SCAN_INTERVAL_MIN * 60:
                blocked = desk_guards()
                if blocked:
                    logger.info("scan skipped: %s", blocked)
                else:
                    scan_cycle(client, dry_run)
                last_scan = now
            if desk_guards() is None or True:
                manage_cycle(client, dry_run if not DESK_ENABLED else False)
        except Exception as exc:
            logger.warning("cycle error: %s", exc, exc_info=True)
        time.sleep(max(10, MANAGE_INTERVAL_SEC))


if __name__ == "__main__":
    raise SystemExit(main())
