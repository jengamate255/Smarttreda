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
DESK_RISK_FIXED = float(os.getenv("MT5_DESK_RISK_FIXED", "75"))
SCAN_INTERVAL_MIN = float(os.getenv("MT5_DESK_SCAN_MINUTES", "30"))   # full scan cadence
MANAGE_INTERVAL_SEC = float(os.getenv("MT5_DESK_MANAGE_SECONDS", "60"))  # mgmt cadence
DESK_MIN_CONFLUENCE = float(os.getenv("MT5_DESK_MIN_CONFLUENCE", "75"))
DESK_MIN_HEALTH = float(os.getenv("MT5_DESK_MIN_HEALTH", "55"))
MAX_HOLD_HOURS = float(os.getenv("MT5_DESK_MAX_HOLD_HOURS", "48"))
DESK_MAGIC = int(os.getenv("MT5_DESK_MAGIC", str(MAGIC + 7)))  # distinguish desk orders

# Champion-combo levels (Colab exhaustive sweep 2026-09-12, 136k combos):
# M5 entry, H4+H6 gates, SL 3.0xATR, TP 1.5R, partial exit at +1R.
# Bootstrap: P(profit)=100%, exp_p5=+$6.94, PF floor 1.90 at $25 risk.
DESK_ENTRY_TF = os.getenv("MT5_DESK_ENTRY_TF", "M5")
DESK_SL_ATR_MULT = float(os.getenv("MT5_DESK_SL_ATR", "3.0"))
DESK_TP_R_MULT = float(os.getenv("MT5_DESK_TP_R", "1.5"))

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
        return client.rates(symbol, tf, count)
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
        # confirmed patterns score higher than emerging
        best, best_score, best_dir = None, 0.0, None
        for p in patterns:
            if p.get("direction") not in ("bullish", "bearish"):
                continue
            score = float(p.get("ai_score", p.get("quality_score", 0)) or 0)
            if str(p.get("status")) == "confirmed":
                score = min(100.0, score * 1.15)
            if score > best_score:
                best, best_score, best_dir = p, score, p["direction"]
        if not best:
            return {"score": 0.0, "direction": None}
        # Learning loop: journal win rate per setup adjusts trust.
        raw_score = best_score
        best_score, learned_factor = adjust_pattern_score(best.get("pattern"), best_score)
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
        for z in zones:
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
    if hit and now - hit[0] < _MTF_TTL.get(tf, 300):
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
        df = client.rates(symbol, "H1", 60)
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
        except Exception:
            pass
        sheet.append(entry)
    sheet.sort(key=lambda e: float(e.get("confluence") or 0), reverse=True)
    return sheet


def portfolio_select(sheet: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    """Pick the best symbol per correlation group; drop weak confluence."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    traded_today: set[str] = set(state.get("daily_trades", {}).get(today, []))

    picks: dict[str, dict[str, Any]] = {}
    for entry in sheet:
        sym = entry["symbol"]
        if float(entry.get("confluence") or 0) < DESK_MIN_CONFLUENCE:
            continue
        if entry.get("direction") not in ("BUY", "SELL"):
            continue
        if float(entry.get("health") or 0) < DESK_MIN_HEALTH:
            continue
        if sym in traded_today:
            continue  # one shot per symbol per day
        grp = correlation_group(sym)
        cur = picks.get(grp)
        if cur is None or float(entry["confluence"]) > float(cur["confluence"]):
            picks[grp] = entry
    return list(picks.values())


# ── execution ──────────────────────────────────────────────────────

def _plan_levels(entry: dict[str, Any]) -> dict[str, float] | None:
    """Champion-combo levels (Colab sweep 2026-09-12): SL 3.0xATR, TP 1.5R.

    Pattern invalidation/target are still respected when they give a WIDER
    stop than 3.0xATR (structure matters), but the floor/ceiling is ATR
    math: never tighter than 3.0xATR stop, target never beyond 1.5R.
    """
    analysts = entry.get("analysts", {})
    pat = analysts.get("patterns", {})
    zones = analysts.get("zones", {})
    price = float(entry.get("price") or 0.0)
    if not price:
        return None
    direction = entry.get("direction")
    sym = entry.get("symbol", "")

    # ATR from the entry frame (M5) — the sweep's engine measured ATR(14)
    atr_val = float(analysts.get("atr") or 0.0)
    if atr_val <= 0:
        try:
            client = get_client()
            df = client.rates(sym, DESK_ENTRY_TF, 30)
            h, l, c = df["high"], df["low"], df["close"]
            prev = c.shift(1)
            tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
            atr_val = float(tr.rolling(14).mean().iloc[-1])
        except Exception:
            atr_val = 0.0
    if atr_val <= 0:
        atr_val = price * 0.001  # last-resort: 0.1% of price

    stop = DESK_SL_ATR_MULT * atr_val
    tp_dist = DESK_TP_R_MULT * stop

    # structural invalidation can widen the stop (never narrow below 3xATR)
    invalidation = pat.get("invalidation")
    if invalidation is not None:
        invalidation = float(invalidation)
        struct_stop = abs(price - invalidation)
        if struct_stop > stop:
            stop = struct_stop

    if direction == "BUY":
        sl = price - stop
        tp = price + tp_dist
    elif direction == "SELL":
        sl = price + stop
        tp = price - tp_dist
    else:
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

    verdict = gate(sym, str(direction), 0.01, sl_price=sl,
                   confidence=float(entry.get("confluence") or 0))
    if not verdict.get("allowed"):
        return {"symbol": sym, "status": "blocked", "reason": "; ".join(verdict.get("violations", []))}

    # consensus layer: allowlist + cross-engine veto (fail-open inside)
    try:
        from mt5_mcp.consensus import check as consensus_check
        cver = consensus_check(sym, str(direction).upper(), "desk",
                               score=float(entry.get("confluence") or 0))
        if not cver.get("allowed"):
            return {"symbol": sym, "status": "blocked",
                    "reason": "; ".join(cver.get("violations", []))}
    except Exception:
        pass

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
        journal.log_signal(
            signal_id=f"desk-{sym}-{int(time.time())}",
            symbol=sym, side=direction, setup=f"desk:{entry.get('pattern', 'confluence')}",
            entry=levels["entry"], sl=sl, tp=tp, lots=lots,
            confidence=float(entry.get("confluence") or 0),
        )
        state = _load_state()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.setdefault("daily_trades", {}).setdefault(today, []).append(sym)
        state.setdefault("positions", {})[str(payload.get("ticket"))] = {
            "symbol": sym, "side": direction, "entry": levels["entry"],
            "sl": sl, "tp": tp, "lots": lots, "opened": datetime.now(timezone.utc).isoformat(),
            "risk_r": abs(levels["entry"] - sl),
        }
        _save_state(state)
    return out


def _size_position(client, symbol: str, sl_pips: float, entry: float, sl: float) -> float | None:
    """Risk-based lots using tick-value math (contract-aware).

    Rules (audit 2026-09-11):
    - Risk exactly DESK_RISK_FIXED ($25) using live tick-value math.
    - Round DOWN to volume step (never round up past the risk cap).
    - Min-lot risk guard: if even volume_min risks more than 1.5x the cap,
      skip the trade (wide-SL symbols like USOIL are untradeable at $25).
    """
    if sl_pips <= 0:
        return None
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
        if risk_money <= 0:
            return None
        sl_distance = abs(entry - sl)
        if sl_distance <= 0:
            return None
        per_lot_risk = sl_distance * per_unit
        if per_lot_risk <= 0:
            return None

        step = float(info.get("volume_step", 0.01) or 0.01)
        vmin = float(info.get("volume_min", 0.01) or 0.01)
        vmax = float(info.get("volume_max", 100.0) or 100.0)

        # floor to step — never exceed the risk cap
        lots = math.floor((risk_money / per_lot_risk) / step) * step
        if lots <= 0:
            # even one step over-risks: check the min-lot guard
            min_risk = vmin * per_lot_risk
            if min_risk <= risk_money * 1.5:
                lots = vmin  # within 1.5x cap: accept min lot
            else:
                logger.info(
                    "sizing skip %s: min lot %.2f risks $%.2f > 1.5x cap $%.2f",
                    symbol, vmin, min_risk, risk_money,
                )
                return None
        lots = min(lots, vmax)
        return round(lots, 2)
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
    recovered (e.g. history API hiccup). Falls back to tracked entry for
    PnL estimation when the deal lacks one.
    """
    symbol = str(meta.get("symbol", ""))
    side = str(meta.get("side", ""))
    entry = float(meta.get("entry") or 0)
    try:
        deals = client.deals(days=3) or []
    except Exception:
        deals = []
    best = None
    for d in deals:
        if not isinstance(d, dict):
            continue
        if str(d.get("position_id") or d.get("ticket")) != str(ticket):
            continue
        if float(d.get("entry", -1)) != 1:  # DEAL_ENTRY_OUT
            continue
        best = d
        break
    if best is not None:
        pnl = float(best.get("profit", 0) or 0) + float(best.get("swap", 0) or 0) + float(best.get("fee", 0) or 0)
        close_price = float(best.get("price") or 0)
        reason = "SL hit" if pnl < 0 else "TP hit" if pnl > 0 else "manual/flat close"
        symbol = str(best.get("symbol") or symbol)
    else:
        pnl = 0.0
        close_price = 0.0
        reason = "closed (details unavailable)"
    summary = {
        "ticket": int(ticket),
        "symbol": symbol,
        "side": side,
        "action": "closed",
        "pnl": round(pnl, 2),
        "entry": entry,
        "close": close_price,
        "reason": reason,
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
                        setup="desk_managed",
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

        # 1) partial exit + break-even at +1R: bank 50%, SL to entry+buffer.
        #    A winner that round-trips to entry is the most expensive mistake
        #    a desk can make — bank half, let the runner pay for the risk.
        if r_multiple >= 1.0 and not meta.get("be_armed"):
            volume = float(pos.get("volume") or 0)
            info = None
            try:
                info = client.symbol_info(symbol)
            except Exception:
                pass
            vmin = float((info or {}).get("volume_min", 0.01) or 0.01)
            step = float((info or {}).get("volume_step", 0.01) or 0.01)
            remaining_min = max(vmin, step)
            take = math.floor((volume / 2) / step) * step
            if not dry_run and volume - take >= remaining_min and take >= vmin and not meta.get("partial_taken"):
                try:
                    res = close_position(int(ticket), volume=round(take, 2))
                    if getattr(res, "accepted", False):
                        meta["partial_taken"] = True
                        actions.append({
                            "ticket": ticket, "symbol": symbol,
                            "action": "partial-exit",
                            "closed_lots": round(take, 2),
                            "remaining_lots": round(volume - take, 2),
                        })
                        _log_event("partial_exit", {
                            "ticket": ticket, "symbol": symbol,
                            "closed_lots": round(take, 2),
                            "r_multiple": round(r_multiple, 2),
                        })
                        notify(
                            f"partial_{ticket}",
                            f"{symbol} partial exit: banked {round(take, 2)} lots at +{r_multiple:.1f}R",
                        )
                    else:
                        # partial failed (e.g. stops level) — don't retry every
                        # cycle; mark taken so BE/trail still proceed
                        meta["partial_taken"] = True
                except Exception as exc:
                    logger.warning("partial exit failed %s: %s", symbol, exc)
            elif dry_run and not meta.get("partial_taken"):
                actions.append({"ticket": ticket, "symbol": symbol,
                                "action": "partial-exit (dry-run)",
                                "closed_lots": round(take, 2) if take >= vmin else 0})
            be_price = entry + (2 * _point(client, symbol)) if is_buy else entry - (2 * _point(client, symbol))
            ok = (not dry_run) and _safe_modify(client, int(ticket), be_price, tp)
            if ok or dry_run:
                meta["be_armed"] = True
                actions.append({"ticket": ticket, "symbol": symbol, "action": "break-even", "sl": be_price})
        # 2) trail after +1.5R at 1R distance
        elif r_multiple >= 1.5 and meta.get("be_armed"):
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
    started = time.time()
    sheet = build_desk_sheet(client, UNIVERSE)
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
    }
    _log_event("scan", summary)
    # publish desk stances to the consensus board (top-sheet directional reads)
    try:
        from mt5_mcp.consensus import publish as consensus_publish
        for t in summary.get("top", []):
            consensus_publish("desk", t.get("symbol", ""), t.get("dir", "NONE"),
                              t.get("confluence", 0))
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
    if actions:
        _log_event("manage", actions)
        for a in actions:
            html = fmt_position_update(
                symbol=a.get("symbol", "?"),
                ticket=int(a.get("ticket", 0)),
                action=a.get("action", "?"),
                new_sl=float(a.get("new_sl")) if a.get("new_sl") else None,
                reason=a.get("reason", ""),
            )
            notify("desk_manage_" + str(a.get("ticket", "")),
                   f"{a.get('symbol')} {a.get('action')}", html=html)
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
