# -*- coding: utf-8 -*-
"""Trading consensus layer (added 2026-09-26).

Every engine publishes its per-symbol stance to memory/consensus.json;
before executing, engines call check() which enforces one shared policy:

1. allowlist   - symbols without backtested edge are not executable
2. veto        - a fresh confident opposite stance blocks the entry
3. corroboration (optional) - consensus.min_agree > 0 requires N agreeing votes
4. risk cap    - unified risk_percent every executor must size under

Config: settings.json -> "consensus" (safe defaults if block absent).
Fail-open by design: on any internal error consensus ALLOWS and logs -
compliance.gate() remains the hard veto layer.

CLI (part B of the consensus workflow - session routine):
    python -X utf8 mt5_mcp/consensus.py                 # board report
    python -X utf8 mt5_mcp/consensus.py check USDJPY BUY
    python -X utf8 mt5_mcp/consensus.py publish desk EURUSD SELL 76.4
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("consensus")

MODULE_DIR = Path(__file__).resolve().parent
BOARD_PATH = MODULE_DIR / "memory" / "consensus.json"
SETTINGS_PATH = MODULE_DIR / "settings.json"

KNOWN_SOURCES = ("desk", "confirm", "breakout", "analysis")

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "vote_ttl_sec": 1800,
    "veto_score": 75.0,
    "min_agree": 0,
    "agree_min_score": 60.0,
    "risk_percent": 0.25,
    # Backtested tradable set (2026-08-01, 42 terminal symbols) + explicit
    # approvals. Anything not listed here is not executable by consensus.
    "allowed_symbols": [
        "SPX500", "XAGUSD", "AUDUSD", "USDJPY", "EURUSD",
        "XAUUSD", "JP225", "ETHUSD", "EURJPY",
    ],
}


# ── config ───────────────────────────────────────────────────────────

def _cfg() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        block = data.get("consensus") or {}
        cfg.update({k: v for k, v in block.items() if v is not None})
    except Exception as exc:
        logger.warning("consensus config read failed (%s) - using defaults", exc)
    return cfg


def risk_cap() -> float:
    """Unified max risk percent for every executor (fail-open to default)."""
    try:
        return float(_cfg().get("risk_percent", DEFAULTS["risk_percent"]))
    except Exception:
        return float(DEFAULTS["risk_percent"])


def allowed_symbols() -> set[str]:
    try:
        return {str(s).upper() for s in _cfg().get("allowed_symbols", [])}
    except Exception:
        return set()


# ── board IO (multi-process safe: atomic replace) ────────────────────

def _read_board() -> dict[str, Any]:
    try:
        board = json.loads(BOARD_PATH.read_text(encoding="utf-8"))
        if isinstance(board, dict) and isinstance(board.get("votes"), dict):
            return board
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("board read failed (%s)", exc)
    return {"updated_at": None, "votes": {}}


def _write_board(board: dict[str, Any]) -> None:
    board["updated_at"] = datetime.now(timezone.utc).isoformat()
    BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(BOARD_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(board, fh, indent=1, default=str)
        os.replace(tmp, BOARD_PATH)  # atomic on Windows
    except Exception as exc:
        logger.warning("board write failed (%s)", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── API used by engines ──────────────────────────────────────────────

def publish(source: str, symbol: str, side: Any, score: Any, note: str = "") -> None:
    """Record one engine's stance. side: BUY/SELL/NONE (case-insensitive)."""
    try:
        sym = str(symbol).upper().strip()
        side_u = str(side or "NONE").upper().strip()
        if side_u not in ("BUY", "SELL", "NONE"):
            side_u = "NONE"
        board = _read_board()
        votes = board.setdefault("votes", {})
        # prune dead votes so the board stays small
        ttl = float(_cfg().get("vote_ttl_sec", 1800))
        cutoff = time.time() - ttl * 2
        for s in list(votes):
            votes[s] = {k: v for k, v in votes[s].items()
                        if float((v or {}).get("ts", 0)) >= cutoff}
            if not votes[s]:
                votes.pop(s, None)
        votes.setdefault(sym, {})[str(source).lower()] = {
            "side": side_u,
            "score": round(float(score or 0), 1),
            "note": str(note or "")[:80],
            "ts": time.time(),
        }
        _write_board(board)
    except Exception as exc:
        logger.warning("publish failed %s/%s: %s", source, symbol, exc)


def check(symbol: str, side: str, source: str, score: float = 0.0) -> dict[str, Any]:
    """Consensus verdict for an entry. Fail-open: internal errors allow.

    Returns {"allowed": bool, "violations": [..], "agree": n, "oppose": n,
             "risk_percent": float}.
    """
    out: dict[str, Any] = {"allowed": True, "violations": [],
                           "agree": 0, "oppose": 0, "risk_percent": None}
    try:
        cfg = _cfg()
        out["risk_percent"] = float(cfg["risk_percent"])
        if not cfg.get("enabled", True):
            return out

        sym = str(symbol).upper().strip()
        side_u = str(side).upper().strip()

        # 1. allowlist (additive to compliance's blocklist)
        allow = {str(s).upper() for s in cfg.get("allowed_symbols", [])}
        if allow and sym not in allow:
            out["violations"].append(
                f"consensus: {sym} not in allowlist (no backtested edge)")

        # gather fresh votes from OTHER sources
        now = time.time()
        ttl = float(cfg.get("vote_ttl_sec", 1800))
        votes = _read_board().get("votes", {}).get(sym, {}) or {}
        fresh: list[tuple[str, str, float]] = []
        for src, v in votes.items():
            if src == str(source).lower():
                continue
            v = v or {}
            if now - float(v.get("ts", 0)) > ttl:
                continue
            fresh.append((src, str(v.get("side", "NONE")), float(v.get("score", 0))))

        oppose = [(s, sd, sc) for s, sd, sc in fresh
                  if sd in ("BUY", "SELL") and sd != side_u]
        agree = [(s, sc) for s, sd, sc in fresh
                 if sd == side_u and sc >= float(cfg.get("agree_min_score", 60))]
        out["oppose"] = len(oppose)
        out["agree"] = len(agree)

        # 2. veto: fresh confident opposite stance
        veto_score = float(cfg.get("veto_score", 75))
        hot = [f"{s} {sd} {sc:.0f}" for s, sd, sc in oppose if sc >= veto_score]
        if hot:
            out["violations"].append(
                f"consensus: opposite stance ({', '.join(hot)} >= {veto_score:.0f})")

        # 3. corroboration (optional, default off)
        need = int(cfg.get("min_agree", 0))
        if need and out["agree"] < need:
            out["violations"].append(
                f"consensus: need {need} agreeing vote(s), got {out['agree']}")

        out["allowed"] = not out["violations"]
        return out
    except Exception as exc:
        logger.warning("check failed (fail-open) %s/%s: %s", symbol, side, exc)
        return out


# ── reporting (session routine / part B) ─────────────────────────────

def _age(ts: float, now: float) -> str:
    m = max(0, (now - ts) / 60)
    return f"{int(m)}m" if m < 120 else f"{m / 60:.1f}h"


def report_text() -> str:
    cfg = _cfg()
    board = _read_board()
    votes = board.get("votes", {})
    now = time.time()
    ttl = float(cfg.get("vote_ttl_sec", 1800))
    sources = list(KNOWN_SOURCES)
    for sym_votes in votes.values():
        for src in sym_votes:
            if src not in sources:
                sources.append(src)

    lines = [
        "CONSENSUS BOARD  (updated %s, ttl %.0fm, veto >= %.0f, min_agree %d,"
        " risk <= %.2f%%)" % (
            board.get("updated_at") or "never", ttl / 60,
            float(cfg.get("veto_score", 75)), int(cfg.get("min_agree", 0)),
            float(cfg.get("risk_percent", 0.25))),
        "",
    ]
    allow = sorted(str(s).upper() for s in cfg.get("allowed_symbols", []))
    lines.append("allowlist: " + (", ".join(allow) or "(empty = nothing tradable)"))
    lines.append(f"enabled: {cfg.get('enabled', True)}")
    lines.append("")
    header = f"{'SYMBOL':<9}" + "".join(f"{s:<12}" for s in sources)
    lines.append(header)
    lines.append("-" * len(header))
    for sym in sorted(votes):
        row = f"{sym:<9}"
        for src in sources:
            v = votes[sym].get(src)
            if not v:
                row += f"{'-':<12}"
            elif now - float(v.get("ts", 0)) > ttl:
                row += f"{'stale':<12}"
            else:
                mark = {"BUY": "+", "SELL": "-", "NONE": "."}.get(v["side"], "?")
                row += f"{mark + str(v['score']):<12}"
        lines.append(row)
    lines.append("")
    lines.append("legend: +buy  -sell  .none/reading  stale=expired  '-'=no vote")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(prog="consensus", description="consensus board")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("report", help="print the board (default)")
    p_chk = sub.add_parser("check", help="ask for a verdict")
    p_chk.add_argument("symbol")
    p_chk.add_argument("side")
    p_chk.add_argument("--source", default="cli")
    p_chk.add_argument("--score", type=float, default=0.0)
    p_pub = sub.add_parser("publish", help="post a stance")
    p_pub.add_argument("source")
    p_pub.add_argument("symbol")
    p_pub.add_argument("side")
    p_pub.add_argument("score", type=float, nargs="?", default=0.0)
    args = ap.parse_args(argv)

    if args.cmd == "check":
        v = check(args.symbol, args.side, args.source, args.score)
        print(json.dumps(v, indent=2))
        return 0 if v["allowed"] else 2
    if args.cmd == "publish":
        publish(args.source, args.symbol, args.side, args.score)
        print("published")
        return 0
    print(report_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
