"""Walk-forward evidence gate for symbol -> strategy routing.

STRATEGY_ROUTING / BACKTEST_PF were built from the 2026-09-27 backtest whose
per-(symbol, strategy) samples were 0-6 trades — far too thin to steer live
money. This module refuses to route on thin evidence: a symbol-specific
strategy is used only when the research atlas
(memory/research_lab/atlas_*.json) reports it walk-forward validated
(PROMOTE: n>=50, PF>=1.25, thirds>=1.0, bootstrap>=0.80, holdout n>=15
pf>=1.0 — see research/atlas.py gates). Everything else falls back to the
house default strategy (trend_zone).

- Live/auto paths: PROMOTE only.
- Paper/analysis path: PAPER rows (n>=40, PF>=1.10) are also allowed, which
  is what the atlas gates label them for.

No atlas file, an unreadable file or a missing entry fails closed to the
default strategy.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("routing_evidence")

ATLAS_GLOB = "atlas_*.json"
DEFAULT_STRATEGY = os.getenv("ROUTING_DEFAULT_STRATEGY", "trend_zone")
ROUTING_MIN_TRADES = int(os.getenv("ROUTING_MIN_TRADES", "50"))
ROUTING_MIN_PF = float(os.getenv("ROUTING_MIN_PF", "1.25"))
PAPER_MIN_TRADES = int(os.getenv("ROUTING_PAPER_MIN_TRADES", "40"))
PAPER_MIN_PF = float(os.getenv("ROUTING_PAPER_MIN_PF", "1.10"))
CACHE_TTL_SECONDS = float(os.getenv("ROUTING_EVIDENCE_TTL", "300"))

_LAB_DIR = Path(__file__).resolve().parent / "memory" / "research_lab"
_cache: dict = {"ts": 0.0, "symbols": None, "path": None}
_logged: set = set()


def _atlas() -> dict:
    """Newest research atlas as {symbol: {"strategies": {name: metrics}}}."""
    now = time.time()
    if _cache["symbols"] is not None and now - _cache["ts"] < CACHE_TTL_SECONDS:
        return _cache["symbols"]

    newest: Path | None = None
    try:
        for path in sorted(_LAB_DIR.glob(ATLAS_GLOB)):
            newest = path
    except OSError as exc:
        logger.warning("research atlas unreadable: %s", exc)

    symbols: dict = {}
    if newest is not None:
        try:
            data = json.loads(newest.read_text(encoding="utf-8"))
            block = data.get("symbols")
            if isinstance(block, dict):
                symbols = block
            else:
                logger.warning("research atlas %s has no symbols block", newest.name)
        except (OSError, ValueError) as exc:
            logger.warning("research atlas %s unreadable: %s", newest, exc)

    _cache.update(ts=now, symbols=symbols, path=str(newest) if newest else None)
    return symbols


def evidence(symbol: str, strategy: str) -> dict | None:
    """Walk-forward metrics for (symbol, strategy), or None when unevidenced."""
    if not strategy:
        return None
    block = _atlas().get(symbol)
    if not isinstance(block, dict):
        return None
    res = block.get("strategies", {}).get(strategy)
    return res if isinstance(res, dict) else None


def _passes(res: dict, allow_paper: bool) -> tuple[bool, str]:
    verdict = str(res.get("verdict") or "")
    n = int(res.get("trades") or 0)
    pf = float(res.get("pf") or 0.0)
    if verdict == "PROMOTE":
        need_n, need_pf = ROUTING_MIN_TRADES, ROUTING_MIN_PF
    elif verdict == "PAPER" and allow_paper:
        need_n = min(ROUTING_MIN_TRADES, PAPER_MIN_TRADES)
        need_pf = min(ROUTING_MIN_PF, PAPER_MIN_PF)
    else:
        return False, f"verdict={verdict or 'none'}"
    if not res.get("routing_family", True):
        return False, "not a routing family"
    if n < need_n:
        return False, f"trades {n}<{need_n}"
    if pf < need_pf:
        return False, f"pf {pf:.3f}<{need_pf}"
    return True, f"{verdict} n={n} pf={pf:.3f}"


def allowed(symbol: str, strategy: str, *, allow_paper: bool = False) -> bool:
    """True when *strategy* may be used live (or on paper) for *symbol*."""
    if strategy == DEFAULT_STRATEGY:
        return True
    res = evidence(symbol, strategy)
    if res is None:
        return False
    ok, _ = _passes(res, allow_paper)
    return ok


def route(
    symbol: str,
    candidate: str | None = None,
    *,
    default: str = DEFAULT_STRATEGY,
    allow_paper: bool = False,
) -> str:
    """Strategy to run for *symbol*: candidate only if walk-forward validated.

    ``candidate`` defaults to STRATEGY_ROUTING[symbol] (imported lazily to
    avoid a cycle). Falls back to *default* when evidence is missing or thin.
    """
    if candidate is None:
        try:
            from mt5_mcp.multi_strategy import STRATEGY_ROUTING

            candidate = STRATEGY_ROUTING.get(symbol, default)
        except Exception as exc:  # noqa: BLE001 - fail closed to default
            logger.warning("routing table unavailable for %s: %s", symbol, exc)
            candidate = default
    if candidate == default:
        return default

    res = evidence(symbol, candidate)
    ok, why = _passes(res, allow_paper) if res else (False, "no atlas entry")
    if ok:
        return candidate

    key = (symbol, candidate, allow_paper)
    if key not in _logged:
        _logged.add(key)
        logger.info(
            "routing neutralised for %s: %s not validated (%s) -> %s",
            symbol, candidate, why, default,
        )
    return default


def routing_status() -> dict:
    """Diagnostic: atlas source plus the routing verdict for every symbol."""
    atlas = _atlas()
    out: dict = {
        "atlas": _cache.get("path"),
        "atlas_symbols": len(atlas),
        "generated_at": _cache.get("ts"),
        "default_strategy": DEFAULT_STRATEGY,
        "min_trades": ROUTING_MIN_TRADES,
        "min_pf": ROUTING_MIN_PF,
        "symbols": {},
    }
    try:
        from mt5_mcp.multi_strategy import STRATEGY_ROUTING

        table = dict(STRATEGY_ROUTING)
    except Exception:  # noqa: BLE001
        table = {}
    for symbol, candidate in sorted(table.items()):
        res = evidence(symbol, candidate)
        ok, why = _passes(res, False) if res else (False, "no atlas entry")
        out["symbols"][symbol] = {
            "candidate": candidate,
            "routed": route(symbol, candidate, allow_paper=False),
            "validated": ok,
            "why": why,
        }
    return out
