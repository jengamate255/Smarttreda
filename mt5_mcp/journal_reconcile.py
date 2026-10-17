"""Reconcile the trade journal with broker history (post-mortem 2026-10-02).

The journal held 90 of 213 closed broker positions.  The 123 positions it
never saw were -527.98 - 95% of the entire account loss - so every report
built on the journal (win rate, expectancy, learning, the weekly research
report) was reporting a *profitable* system while the account bled.

Broker history is the source of truth: deal history includes commission and
swap, which the journal's own P&L never counted.

What this module does
---------------------
1. ``closed_positions()`` - rebuild closed positions from MT5 IN/OUT deals.
2. ``reconcile()``       - insert journal rows that are missing, correct
                           pnl/exit/close_time/outcome where the journal
                           disagrees with the broker, and never touch a row
                           the broker has not closed yet.
3. ``refresh_live_stats()`` - write ``memory/live_stats.json`` so
                           ``mt5_mcp.execution_gate`` can suspend symbols
                           whose live sample has gone negative.

CLI:
    python -X utf8 -m mt5_mcp.journal_reconcile --days 200
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("mt5_mcp.journal_reconcile")

MODULE_DIR = Path(__file__).resolve().parent
LIVE_STATS_PATH = MODULE_DIR / "memory" / "live_stats.json"
STATE_PATH = MODULE_DIR / "memory" / "journal_reconcile_state.json"

DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1

#: Two closes in this window with the same symbol/lots are the same position
#: (a journal ticket is the *order* ticket, which is not always the position id).
MATCH_WINDOW_SECONDS = 180

#: Cover the whole account (live since 2026-03-31) so the journal holds every
#: closed position and the gate's suspension stats use the full live sample.
RECONCILE_DAYS = 200

RECONCILE_SETUP = "reconcile"


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def journal_path() -> Path:
    """Absolute path of the live journal DB (cwd-independent)."""
    try:
        from mt5_mcp.config import get_settings

        raw = Path(get_settings().memory.db_path)
    except Exception:  # noqa: BLE001 - fall back to the canonical location
        raw = Path("memory/journal.db")
    if raw.is_absolute():
        return raw
    local = MODULE_DIR / raw
    if local.exists():
        return local
    return Path.cwd() / raw


def closed_positions(client: Any = None, days: int = 45) -> dict[int, dict]:
    """Closed positions from broker deal history, keyed by position id.

    Net P&L is the sum over every deal of the position (profit + swap +
    commission + fee), so commission charged on the entry leg is included -
    exactly what the account statement shows.
    """
    if client is None:
        from mt5_mcp.client import get_client

        client = get_client()

    deals = client.deals(days=days) or []
    positions: dict[int, dict] = {}
    for deal in deals:
        entry = deal.get("entry")
        try:
            entry = int(entry if entry is not None else -1)
        except (TypeError, ValueError):
            continue
        if entry not in (DEAL_ENTRY_IN, DEAL_ENTRY_OUT):
            continue
        # NOTE: ``type`` 0 is BUY, so it must not be coerced with ``or`` -
        # a BUY deal is a real value, not a missing one.
        raw_type = deal.get("type")
        try:
            dtype = int(raw_type) if raw_type is not None else -1
        except (TypeError, ValueError):
            continue
        if dtype not in (DEAL_TYPE_BUY, DEAL_TYPE_SELL):
            continue
        pid = int(deal.get("position_id") or 0)
        if not pid:
            continue

        rec = positions.get(pid)
        if rec is None:
            rec = {
                "ticket": pid,
                "symbol": str(deal.get("symbol") or ""),
                "side": "BUY" if dtype == DEAL_TYPE_BUY else "SELL",
                "lots": 0.0,
                "entry": 0.0,
                "exit_price": 0.0,
                "open_time": int(deal.get("time") or 0),
                "close_time": 0,
                "pnl": 0.0,
                "entry_volume": 0.0,
                "exit_volume": 0.0,
                "entry_order": 0,
                "magic": int(deal.get("magic") or 0),
                "comment": str(deal.get("comment") or ""),
            }
            positions[pid] = rec

        rec["pnl"] += float(deal.get("profit") or 0.0)
        rec["pnl"] += float(deal.get("swap") or 0.0)
        rec["pnl"] += float(deal.get("fee") or 0.0)
        rec["pnl"] += float(deal.get("commission") or 0.0)

        stamp = int(deal.get("time") or 0)
        if entry == DEAL_ENTRY_IN:
            if not rec["entry_order"]:
                rec["entry_order"] = int(deal.get("order") or 0)
            rec["entry_volume"] += float(deal.get("volume") or 0.0)
            rec["side"] = "BUY" if dtype == DEAL_TYPE_BUY else "SELL"
            rec["entry"] = float(deal.get("price") or 0.0) or rec["entry"]
            rec["lots"] = float(deal.get("volume") or 0.0) or rec["lots"]
            rec["open_time"] = min(rec["open_time"] or stamp, stamp) or stamp
            if not rec["comment"]:
                rec["comment"] = str(deal.get("comment") or "")
            if not rec["magic"]:
                rec["magic"] = int(deal.get("magic") or 0)
        else:
            rec["exit_volume"] += float(deal.get("volume") or 0.0)
            rec["exit_price"] = float(deal.get("price") or 0.0) or rec["exit_price"]
            rec["close_time"] = max(rec["close_time"], stamp)

    closed = {pid: rec for pid, rec in positions.items() if rec["close_time"]
              and rec["entry_volume"] > 0 and rec["exit_volume"] + 1e-8 >= rec["entry_volume"]}
    for rec in closed.values():
        rec["pnl"] = round(rec["pnl"], 2)
        rec["outcome"] = (
            "win" if rec["pnl"] > 0.01 else "loss" if rec["pnl"] < -0.01 else "breakeven"
        )
        rec["duration_seconds"] = max(0, rec["close_time"] - rec["open_time"])
        rec["session_hour"] = (
            datetime.fromtimestamp(rec["open_time"], tz=timezone.utc).hour
        )
    return closed


def _connect() -> sqlite3.Connection:
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _journal_rows(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    rows = conn.execute(
        "SELECT id, ticket, symbol, side, lots, open_time, close_time, pnl, outcome "
        "FROM trades WHERE ticket IS NOT NULL"
    ).fetchall()
    return {int(r["ticket"]): r for r in rows}


def _match(
    rec: dict, rows_by_ticket: dict[int, sqlite3.Row], unmatched: list[sqlite3.Row]
) -> sqlite3.Row | None:
    row = rows_by_ticket.get(int(rec["ticket"]))
    if row is not None:
        return row
    # Fallback: journal ticket is the order ticket, position id may differ.
    for cand in unmatched:
        if cand["symbol"] != rec["symbol"]:
            continue
        if abs(float(cand["lots"] or 0.0) - rec["lots"]) > 1e-9:
            continue
        try:
            cand_open = datetime.fromisoformat(str(cand["open_time"])).timestamp()
        except (TypeError, ValueError):
            continue
        if abs(cand_open - rec["open_time"]) <= MATCH_WINDOW_SECONDS:
            return cand
    return None


def reconcile(
    days: int = RECONCILE_DAYS,
    client: Any = None,
    write_live_stats: bool = True,
) -> dict[str, Any]:
    """Insert missing journal rows and correct journal/broker disagreements."""
    closed = closed_positions(client=client, days=days)
    conn = _connect()
    inserted = corrected = 0
    corrections: list[dict[str, Any]] = []
    try:
        rows_by_ticket = _journal_rows(conn)
        rows = conn.execute(
            "SELECT id, ticket, symbol, side, lots, open_time, close_time, pnl, outcome "
            "FROM trades WHERE ticket IS NOT NULL"
        ).fetchall()
        unmatched = list(rows)
        now_iso = datetime.now(timezone.utc).isoformat()

        for rec in sorted(closed.values(), key=lambda r: r["close_time"]):
            row = _match(rec, rows_by_ticket, unmatched)
            if row is None:
                conn.execute(
                    """
                    INSERT INTO trades (
                        ticket, symbol, side, setup, timeframe, entry, exit_price,
                        lots, open_time, close_time, pnl, outcome, reason,
                        session_hour, duration_seconds, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        rec["ticket"],
                        rec["symbol"],
                        rec["side"],
                        RECONCILE_SETUP,
                        None,
                        rec["entry"],
                        rec["exit_price"],
                        rec["lots"],
                        _iso(rec["open_time"]),
                        _iso(rec["close_time"]),
                        rec["pnl"],
                        rec["outcome"],
                        "auto-reconciled from broker deal history "
                        "(commission and swap included)",
                        rec["session_hour"],
                        rec["duration_seconds"],
                        now_iso,
                        now_iso,
                    ),
                )
                inserted += 1
                continue

            unmatched = [r for r in unmatched if r["id"] != row["id"]]
            journal_pnl = row["pnl"]
            needs = (
                journal_pnl is None
                or abs(float(journal_pnl) - rec["pnl"]) > 0.01
                or row["outcome"] != rec["outcome"]
                or not row["close_time"]
            )
            if not needs:
                continue
            conn.execute(
                "UPDATE trades SET pnl=?, outcome=?, exit_price=?, close_time=?, "
                "session_hour=?, duration_seconds=?, updated_at=? WHERE id=?",
                (
                    rec["pnl"],
                    rec["outcome"],
                    rec["exit_price"],
                    _iso(rec["close_time"]),
                    rec["session_hour"],
                    rec["duration_seconds"],
                    now_iso,
                    row["id"],
                ),
            )
            corrected += 1
            corrections.append(
                {
                    "ticket": int(row["ticket"]),
                    "symbol": row["symbol"],
                    "journal_pnl": journal_pnl,
                    "broker_pnl": rec["pnl"],
                }
            )
        conn.commit()
    finally:
        conn.close()

    stats: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "days": days,
        "positions_from_broker": len(closed),
        "inserted": inserted,
        "corrected": corrected,
        "corrections": corrections[:50],
    }
    if write_live_stats:
        stats["live_stats"] = refresh_live_stats(closed, days=days)
    _save_state(stats)
    logger.info(
        "journal reconcile: %s broker positions, %s inserted, %s corrected",
        len(closed),
        inserted,
        corrected,
    )
    return stats


def live_stats_from(positions: dict[int, dict]) -> dict[str, dict]:
    """Per-symbol win rate / net P&L from closed broker positions."""
    out: dict[str, dict] = {}
    for rec in positions.values():
        sym = rec["symbol"].upper()
        if not sym:
            continue
        stat = out.setdefault(
            sym,
            {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "net": 0.0,
                "gross_win": 0.0,
                "gross_loss": 0.0,
                "last_close": 0,  # epoch while accumulating, ISO at the end
            },
        )
        stat["trades"] += 1
        pnl = float(rec["pnl"])
        stat["net"] += pnl
        if pnl > 0.01:
            stat["wins"] += 1
            stat["gross_win"] += pnl
        elif pnl < -0.01:
            stat["losses"] += 1
            stat["gross_loss"] += -pnl
        stamp = int(rec["close_time"] or 0)
        if stamp > stat["last_close"]:
            stat["last_close"] = stamp

    for stat in out.values():
        stat["net"] = round(stat["net"], 2)
        stat["last_close"] = _iso(stat["last_close"]) if stat["last_close"] else None
        stat["win_rate"] = (
            round(100.0 * stat["wins"] / stat["trades"], 1) if stat["trades"] else 0.0
        )
        stat["pf"] = (
            round(stat["gross_win"] / stat["gross_loss"], 2)
            if stat["gross_loss"] > 0
            else (99.0 if stat["gross_win"] else 0.0)
        )
    return out


def refresh_live_stats(
    positions: dict[int, dict] | None = None,
    *,
    days: int = RECONCILE_DAYS,
    client: Any = None,
) -> dict[str, dict]:
    """Write memory/live_stats.json (consumed by the execution gate)."""
    if positions is None:
        positions = closed_positions(client=client, days=days)
    symbols = live_stats_from(positions)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "days": days,
        "positions": len(positions),
        "symbols": symbols,
    }
    LIVE_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LIVE_STATS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(LIVE_STATS_PATH)
    return symbols


def _save_state(stats: dict[str, Any]) -> None:
    try:
        STATE_PATH.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - state is advisory
        logger.debug("could not persist reconcile state: %s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile memory/journal.db with MT5 deal history."
    )
    parser.add_argument("--days", type=int, default=RECONCILE_DAYS)
    parser.add_argument("--no-live-stats", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    started = time.time()
    stats = reconcile(
        days=args.days, write_live_stats=not args.no_live_stats
    )
    print(json.dumps(stats, indent=2))
    print(f"completed in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
