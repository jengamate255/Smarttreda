"""Pattern detection + confluence analysis + annotated chart generation.

Detects: double top/bottom, head & shoulders, falling/rising wedge.
Computes confluence: trend, RSI, S/R proximity, multi-pattern.
Generates annotated chart snapshots for Telegram signals.

Used by telegram_bot.py via _generate_and_send_chart -> generate_chart_with_patterns
and by the autochartist watcher for periodic pattern scans.
"""

from __future__ import annotations

import io
import logging

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

def detect_patterns(df: pd.DataFrame) -> dict:
    """Detect chart patterns: double top/bottom, head & shoulders, falling wedge.

    Returns dict with pattern list, each entry has name, indices, prices, confidence.
    """
    result: dict = {"patterns": [], "confidence": 0.0}
    if len(df) < 20:
        return result

    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    n = len(df)

    # -- peaks (local maxima) --
    peak_idxs: list[int] = []
    for i in range(1, n - 1):
        if closes[i] > closes[i - 1] and closes[i] > closes[i + 1]:
            peak_idxs.append(i)

    # -- valleys (local minima) --
    valley_idxs: list[int] = []
    for i in range(1, n - 1):
        if closes[i] < closes[i - 1] and closes[i] < closes[i + 1]:
            valley_idxs.append(i)

    # --- Double Top ---
    if len(peak_idxs) >= 2:
        p1, p2 = peak_idxs[-2], peak_idxs[-1]
        if closes[p1] > 0 and abs(closes[p1] - closes[p2]) / closes[p1] < 0.03:
            conf = min(0.85, 0.5 + 0.35 * (1 - abs(closes[p1] - closes[p2]) / closes[p1]))
            result["patterns"].append({
                "name": "double_top",
                "peak1_idx": p1, "peak2_idx": p2,
                "peak1_price": closes[p1], "peak2_price": closes[p2],
                "confidence": conf,
            })
            result["confidence"] = max(result["confidence"], conf)

    # --- Double Bottom ---
    if len(valley_idxs) >= 2:
        v1, v2 = valley_idxs[-2], valley_idxs[-1]
        ref = abs(lows[v1]) if lows[v1] != 0 else 1
        if abs(lows[v1] - lows[v2]) / ref < 0.03:
            conf = min(0.85, 0.5 + 0.35 * (1 - abs(lows[v1] - lows[v2]) / ref))
            result["patterns"].append({
                "name": "double_bottom",
                "valley1_idx": v1, "valley2_idx": v2,
                "valley1_price": lows[v1], "valley2_price": lows[v2],
                "confidence": conf,
            })
            result["confidence"] = max(result["confidence"], conf)

    # --- Head & Shoulders ---
    if len(peak_idxs) >= 3:
        recent_peaks = sorted(peak_idxs)[-3:]
        if len(recent_peaks) == 3:
            left, head, right = recent_peaks
            if (closes[head] > closes[left] and closes[head] > closes[right]
                    and abs(closes[left] - closes[right]) / closes[head] < 0.05):
                # neckline = lowest low between shoulders
                neck_candidates = []
                if right > left:
                    neck_candidates = lows[left:right]
                if neck_candidates:
                    neck_low = min(neck_candidates)
                    result["patterns"].append({
                        "name": "head_and_shoulders",
                        "left_shoulder": {"idx": left, "price": closes[left]},
                        "head": {"idx": head, "price": closes[head]},
                        "right_shoulder": {"idx": right, "price": closes[right]},
                        "neckline_price": neck_low,
                        "confidence": 0.72,
                    })
                    result["confidence"] = max(result["confidence"], 0.72)

    # --- Falling Wedge (simplified) ---
    if n >= 20:
        # lower highs and lower lows over last 20 bars
        last20_highs = highs[-20:]
        last20_lows = lows[-20:]
        lower_highs = all(last20_highs[i] <= last20_highs[i - 1] + 1e-9 for i in range(1, 20))
        lower_lows = all(last20_lows[i] <= last20_lows[i - 1] + 1e-9 for i in range(1, 20))
        # check convergence: recent swing range tighter than earlier
        if lower_highs and lower_lows and n >= 10:
            early_range = max(highs[:5]) - min(lows[:5]) if n >= 5 else 0
            late_range = max(highs[-5:]) - min(lows[-5:]) if n >= 5 else 0
            if early_range > late_range * 1.4 and late_range > 0:
                result["patterns"].append({"name": "falling_wedge", "confidence": 0.58})
                result["confidence"] = max(result["confidence"], 0.58)

    return result


# ---------------------------------------------------------------------------
# Confluence / enter-vs-wait
# ---------------------------------------------------------------------------

def compute_confluence(df: pd.DataFrame, patterns: dict) -> dict:
    """Compute confluence factors and enter/wait rationale.

    Returns dict with trend, key_levels, rationale, enter_wait, confluence_factors.
    """
    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    n = len(df)

    # -- trend (20-bar) --
    trend = "unclear"
    if n > 20:
        if closes[-1] > closes[-20]:
            trend = "bullish"
        elif closes[-1] < closes[-20]:
            trend = "bearish"

    recent_high = max(highs[-20:]) if n >= 20 else max(highs)
    recent_low = min(lows[-20:]) if n >= 20 else min(lows)
    last_price = closes[-1] if closes else 0

    # -- RSI (14) --
    rsi = 50.0
    if n >= 15:
        gains: list[float] = []
        losses: list[float] = []
        for i in range(max(1, n - 14), n):
            ch = closes[i] - closes[i - 1]
            if ch > 0:
                gains.append(ch)
            else:
                losses.append(-ch)
        avg_gain = sum(gains) / 14 if gains else 0
        avg_loss = sum(losses) / 14 if losses else 0
        if avg_loss > 0:
            rs = avg_gain / avg_loss if avg_loss else 0
            rsi = 100 - (100 / (1 + rs))
        elif avg_gain > 0:
            rsi = 100.0

    # -- confluence factors --
    factors: list[str] = []
    pc = len(patterns.get("patterns", []))
    if pc >= 2:
        factors.append("multiple_patterns")
    elif pc == 1:
        factors.append("single_pattern")
    if trend != "unclear":
        factors.append(f"trend_{trend}")
    if rsi > 70:
        factors.append("rsi_overbought")
    elif rsi < 30:
        factors.append("rsi_oversold")
    if recent_high and abs(last_price - recent_high) / recent_high < 0.02:
        factors.append("near_resistance")
    if recent_low and abs(last_price - recent_low) / recent_low < 0.02:
        factors.append("near_support")

    # -- rationale / decision --
    names = [p["name"] for p in patterns.get("patterns", [])]
    rationale_parts: list[str] = []

    if "double_top" in names and "near_resistance" in factors:
        rationale_parts.append("Double top at resistance -> WAIT for breakdown confirmation")
    elif "double_bottom" in names and "near_support" in factors:
        rationale_parts.append("Double bottom at support -> watch for bullish reversal confirmation")
    elif "head_and_shoulders" in names:
        rationale_parts.append("Head & shoulders -> WAIT for neckline break, enter on retest")
    elif "falling_wedge" in names:
        rationale_parts.append("Falling wedge (compression) -> prepare for breakout, WAIT for close outside wedge")

    if not rationale_parts:
        if trend == "bullish":
            rationale_parts.append("Bullish structure -> ENTER on pullback to support / bullish engulfing")
        elif trend == "bearish":
            rationale_parts.append("Bearish structure -> ENTER on rally to resistance / bearish rejection")
        else:
            rationale_parts.append("No clear edge -> WAIT for structure to develop")

    has_enter = any("ENTER" in r for r in rationale_parts)
    decision = "enter" if has_enter else "wait"

    return {
        "trend": trend,
        "rsi": round(rsi, 1),
        "key_levels": {"support": recent_low, "resistance": recent_high},
        "rationale": " | ".join(rationale_parts),
        "enter_wait": decision,
        "confluence_factors": sorted(set(factors)),
        "pattern_confidence": patterns.get("confidence", 0),
    }


# ---------------------------------------------------------------------------
# Annotated chart generation
# ---------------------------------------------------------------------------

def generate_chart_with_patterns(symbol: str, df: pd.DataFrame) -> tuple[bytes | None, dict]:
    """Generate annotated chart PNG + confluence dict.

    Returns (png_bytes or None, confluence_dict).
    Falls back to plain chart if pattern logic fails.
    """
    try:
        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        n = len(df)
        opens = df["open"].tolist() if "open" in df.columns else closes

        patterns = detect_patterns(df)
        confluence = compute_confluence(df, patterns)

        fig, ax = plt.subplots(1, 1, figsize=(11, 6.2))

        # candles
        for i in range(n):
            col = "green" if closes[i] >= opens[i] else "red"
            ax.bar(i, closes[i] - opens[i], bottom=opens[i], width=0.62, color=col, zorder=2)
            ax.plot([i, i], [highs[i], lows[i]], color="black", linewidth=0.9, zorder=2)

        # EMA20
        ema20 = pd.Series(closes).ewm(span=20, adjust=False).mean()
        ax.plot(range(n), ema20, color="gold", linewidth=1.6, label="EMA 20", zorder=3)

        # -- pattern annotations --
        for p in patterns.get("patterns", []):
            name = p["name"]
            if name == "double_top":
                p1, p2 = p["peak1_idx"], p["peak2_idx"]
                ax.plot([p1, p2], [p["peak1_price"], p["peak2_price"]],
                        color="magenta", linewidth=2.2, linestyle="--", zorder=4)
                ax.scatter([p1, p2], [p["peak1_price"], p["peak2_price"]],
                           color="magenta", s=55, zorder=5, edgecolors="white", linewidths=0.8)
                ax.text((p1 + p2) / 2, max(p["peak1_price"], p["peak2_price"]) * 1.002,
                        "Double Top", color="magenta", fontsize=8.5, ha="center",
                        bbox=dict(facecolor="white", alpha=0.85, edgecolor="magenta", boxstyle="round,pad=0.25"),
                        zorder=6)

            elif name == "double_bottom":
                v1, v2 = p["valley1_idx"], p["valley2_idx"]
                ax.plot([v1, v2], [p["valley1_price"], p["valley2_price"]],
                        color="cyan", linewidth=2.2, linestyle="--", zorder=4)
                ax.scatter([v1, v2], [p["valley1_price"], p["valley2_price"]],
                           color="cyan", s=55, zorder=5, edgecolors="white", linewidths=0.8)
                ax.text((v1 + v2) / 2, min(p["valley1_price"], p["valley2_price"]) * 0.998,
                        "Double Bottom", color="cyan", fontsize=8.5, ha="center",
                        bbox=dict(facecolor="white", alpha=0.85, edgecolor="cyan", boxstyle="round,pad=0.25"),
                        zorder=6)

            elif name == "head_and_shoulders":
                ls, hd, rs = p["left_shoulder"], p["head"], p["right_shoulder"]
                neck = p["neckline_price"]
                ax.plot([ls["idx"], hd["idx"]], [ls["price"], hd["price"]],
                        color="magenta", linewidth=1.8, zorder=4)
                ax.plot([hd["idx"], rs["idx"]], [hd["price"], rs["price"]],
                        color="magenta", linewidth=1.8, zorder=4)
                ax.axhline(neck, color="magenta", linestyle=":", linewidth=1.6, alpha=0.75, zorder=3)
                ax.text(hd["idx"], hd["price"] * 1.003, "Head", color="magenta",
                        fontsize=8, ha="center",
                        bbox=dict(facecolor="white", alpha=0.85, edgecolor="magenta", boxstyle="round,pad=0.2")),
                ax.text((ls["idx"] + rs["idx"]) / 2, neck * 1.001, "Neckline",
                        color="magenta", fontsize=7.5, ha="center",
                        bbox=dict(facecolor="white", alpha=0.85, edgecolor="magenta", boxstyle="round,pad=0.2")),

            elif name == "falling_wedge":
                ax.text(n - 2, max(highs) * 0.998, "Falling Wedge (compression)",
                        color="orange", fontsize=8.5, ha="right",
                        bbox=dict(facecolor="white", alpha=0.85, edgecolor="orange", boxstyle="round,pad=0.25"))

        # -- confluence banner --
        banner_color = "#16a34a" if confluence["enter_wait"] == "enter" else "#dc2626"
        banner_text = " ENTER  |  pullback entry" if confluence["enter_wait"] == "enter" else " WAIT  |  confirmation needed"
        ax.text(0.02, 0.96, banner_text, transform=ax.transAxes, fontsize=9.5, color="white",
                ha="left", va="top", weight="bold",
                bbox=dict(facecolor=banner_color, alpha=0.92, edgecolor="white", boxstyle="round,pad=0.35"),
                zorder=7)

        # -- S/R footer --
        sr = confluence["key_levels"]
        ax.text(0.98, 0.02, f"S {sr['support']:.2f}  |  R {sr['resistance']:.2f}  |  RSI {confluence['rsi']:.0f}",
                transform=ax.transAxes, fontsize=7.5, color="white", ha="right", va="bottom",
                bbox=dict(facecolor="black", alpha=0.55, boxstyle="round,pad=0.25")), 

        # title
        pat_names = [p["name"] for p in patterns.get("patterns", [])]
        title = f"{symbol} M5  |  {confluence['trend'].upper()}  |  RSI {confluence['rsi']:.0f}"
        if pat_names:
            title += f"  |  {', '.join(pat_names)}"
        ax.set_title(title, fontsize=10, pad=10)
        ax.set_ylabel("Price", fontsize=9)
        ax.legend(loc="upper left", fontsize=7.5, framealpha=0.85)
        ax.tick_params(labelsize=7.5)
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

        plt.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=130)
        buf.seek(0)
        plt.close(fig)
        return buf.getvalue(), confluence

    except Exception as exc:
        logger.warning("generate_chart_with_patterns failed: %s", exc)
        return None, {}


def build_signal_caption(symbol: str, confluence: dict, patterns: dict) -> str:
    """Build the Telegram caption for a pattern signal.

    Shows full structure, confluence, and enter/wait guidance like a trader would.
    """
    trend_emoji = {"bullish": "🟢", "bearish": "🔴", "unclear": "⚪"}.get(confluence.get("trend", "unclear"), "⚪")
    decision = confluence.get("enter_wait", "wait")
    dec_emoji = "✅ ENTER" if decision == "enter" else "⏳ WAIT"
    dec_color = "🟢" if decision == "enter" else "🟡"

    pats = patterns.get("patterns", []) if isinstance(patterns, dict) else []
    if pats:
        pat_lines = "\n".join(
            f"  • <b>{p['name']}</b>  conf {p.get('confidence', 0):.0%}" for p in pats
        )
    else:
        pat_lines = "  • <i>no classic pattern — price action only</i>"

    factors = confluence.get("confluence_factors", [])
    factor_str = ", ".join(factors) if factors else "—"

    sr = confluence.get("key_levels", {})
    rsi = confluence.get("rsi", 50)

    lines = [
        f"📊 <b>{symbol} M5  —  Autochartist Snapshot</b>",
        "",
        f"{trend_emoji} <b>Structure:</b> {confluence.get('trend', 'unclear').upper()}  |  RSI {rsi:.0f}",
        f"  S <code>{sr.get('support', 0):.2f}</code>  |  R <code>{sr.get('resistance', 0):.2f}</code>",
        "",
        f"<b>Patterns detected:</b>",
        pat_lines,
        "",
        f"<b>Confluence:</b> {factor_str}",
        "",
        f"{dec_color} <b>Decision: {dec_emoji}</b>",
        f"  <i>{confluence.get('rationale', '—')}</i>",
        "",
    ]

    if decision == "enter":
        lines += [
            "<b>How to enter (full confluence):</b>",
            "  1. Wait for bullish/bearish confirmation candle (engulfing or rejection wick)",
            "  2. Enter on pullback to S/R or after neckline retest",
            "  3. SL beyond recent swing (pattern invalidation)",
            "  4. TP 1: recent swing high/low  |  TP 2: measured move (pattern height)",
            "  5. Risk ≤1% — position size from SL distance",
        ]
    else:
        lines += [
            "<b>Why WAIT:</b>",
            "  • Pattern not yet confirmed (needs breakout close outside level)",
            "  • Enter only after confirmation candle + retest",
            "  • Chasing now = poor R:R and high fake-out risk",
        ]

    lines += [
        "",
        f"<i>Snapshot: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M UTC')}  |  conf {confluence.get('pattern_confidence', 0):.0%}</i>",
    ]
    return "\n".join(lines)
