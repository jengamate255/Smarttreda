"""Journal reconcile: broker history is the source of truth."""

from __future__ import annotations

import json
import sqlite3

import pytest
from mt5_mcp import journal_reconcile as jr

SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER,
    symbol TEXT,
    side TEXT,
    setup TEXT,
    timeframe TEXT,
    entry REAL,
    exit_price REAL,
    lots REAL,
    open_time TEXT,
    close_time TEXT,
    pnl REAL,
    outcome TEXT,
    reason TEXT,
    session_hour INTEGER,
    duration_seconds INTEGER,
    created_at TEXT,
    updated_at TEXT
)
"""


@pytest.fixture
def journal(tmp_path, monkeypatch) -> sqlite3.Connection:
    db = tmp_path / "journal.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.commit()
    monkeypatch.setattr(jr, "journal_path", lambda: db)
    monkeypatch.setattr(jr, "LIVE_STATS_PATH", tmp_path / "live_stats.json")
    monkeypatch.setattr(jr, "STATE_PATH", tmp_path / "reconcile_state.json")
    yield conn
    conn.close()


def _deal(**overrides) -> dict:
    deal = {
        "position_id": 9001,
        "symbol": "USOIL",
        "type": 0,          # BUY (0 must never be treated as "missing")
        "entry": 0,         # IN
        "time": 1_759_000_000,
        "price": 70.0,
        "volume": 0.10,
        "profit": 0.0,
        "swap": 0.0,
        "fee": 0.0,
        "commission": 0.0,
        "magic": 20260602,
        "comment": "reconcile-test",
    }
    deal.update(overrides)
    return deal


def _client(deals: list[dict]):
    class _Client:
        def deals(self, days: int = 30, symbol: str = "") -> list[dict]:
            return list(deals)

    return _Client()


def test_closed_positions_sums_every_cost_leg():
    deals = [
        _deal(entry=0, time=1_759_000_000, price=70.0,
              commission=-3.50, swap=0.0, profit=0.0),
        _deal(entry=1, time=1_759_003_600, price=71.50, type=1,
              profit=15.0, swap=-0.42, fee=-0.10, commission=-3.50),
    ]
    closed = jr.closed_positions(client=_client(deals), days=45)

    assert list(closed) == [9001]
    rec = closed[9001]
    assert rec["symbol"] == "USOIL"
    assert rec["side"] == "BUY"          # side comes from the IN leg
    assert rec["entry"] == 70.0
    assert rec["exit_price"] == 71.50
    assert rec["pnl"] == pytest.approx(15.0 - 0.42 - 0.10 - 7.0)
    assert rec["outcome"] == "win"
    assert rec["duration_seconds"] == 3600


def test_positions_without_a_close_are_ignored():
    deals = [_deal(entry=0)]  # still open at the broker
    assert jr.closed_positions(client=_client(deals), days=45) == {}


def test_partial_close_is_not_a_finished_outcome():
    deals = [_deal(entry=0, volume=0.10),
             _deal(entry=1, type=1, volume=0.04, profit=10, time=1_759_003_600)]
    assert jr.closed_positions(client=_client(deals)) == {}
    deals.append(_deal(entry=1, type=1, volume=0.06, profit=-20, time=1_759_007_200))
    closed = jr.closed_positions(client=_client(deals))
    assert closed[9001]["pnl"] == -10
    assert closed[9001]["outcome"] == "loss"


def test_reconcile_inserts_missing_positions(journal):
    deals = [
        _deal(entry=0, time=1_759_000_000, commission=-3.5),
        _deal(entry=1, time=1_759_003_600, price=71.5, profit=15.0,
              commission=-3.5),
    ]
    stats = jr.reconcile(days=45, client=_client(deals))

    assert stats["positions_from_broker"] == 1
    assert stats["inserted"] == 1
    assert stats["corrected"] == 0

    row = journal.execute(
        "SELECT symbol, side, setup, pnl, outcome, exit_price FROM trades"
    ).fetchone()
    assert dict(row)["symbol"] == "USOIL"
    assert dict(row)["setup"] == jr.RECONCILE_SETUP
    assert dict(row)["pnl"] == pytest.approx(8.0)
    assert dict(row)["outcome"] == "win"

    # Idempotent: a second pass finds nothing to change.
    again = jr.reconcile(days=45, client=_client(deals))
    assert again["inserted"] == 0 and again["corrected"] == 0


def test_reconcile_corrects_journal_pnl_and_outcome(journal):
    journal.execute(
        "INSERT INTO trades (ticket, symbol, side, setup, lots, open_time, "
        "close_time, pnl, outcome) VALUES (?,?,?,?,?,?,?,?,?)",
        (9001, "USOIL", "BUY", "manual", 0.10,
         "2026-09-27T00:00:00+00:00", "2026-09-27T01:00:00+00:00",
         100.0, "win"),
    )
    journal.commit()

    deals = [
        _deal(entry=0, time=1_759_000_000, commission=-3.5),
        _deal(entry=1, time=1_759_003_600, price=68.0, profit=-30.0,
              commission=-3.5),
    ]
    stats = jr.reconcile(days=45, client=_client(deals))

    assert stats["inserted"] == 0
    assert stats["corrected"] == 1
    row = dict(journal.execute("SELECT pnl, outcome FROM trades").fetchone())
    assert row["pnl"] == pytest.approx(-37.0)  # journal ignored commission
    assert row["outcome"] == "loss"


def test_reconcile_matches_by_symbol_lots_and_time(journal):
    """A journal ticket is the order ticket, not always the position id."""
    from datetime import datetime, timezone

    open_iso = datetime.fromtimestamp(1_759_000_000, tz=timezone.utc).isoformat(
        timespec="seconds"
    )
    journal.execute(
        "INSERT INTO trades (ticket, symbol, side, setup, lots, open_time, "
        "close_time, pnl, outcome) VALUES (?,?,?,?,?,?,?,?,?)",
        (111, "USOIL", "BUY", "manual", 0.10, open_iso, None, None, None),
    )
    journal.commit()

    deals = [
        _deal(entry=0, time=1_759_000_000),
        _deal(entry=1, time=1_759_003_600, price=71.0, profit=10.0),
    ]
    stats = jr.reconcile(days=45, client=_client(deals))

    assert stats["inserted"] == 0 and stats["corrected"] == 1
    row = dict(journal.execute("SELECT ticket, pnl, outcome FROM trades").fetchone())
    assert row["ticket"] == 111  # the journal row was updated, not duplicated
    assert row["outcome"] == "win"


def test_live_stats_reflect_broker_history(journal, tmp_path):
    deals = [
        _deal(entry=0, time=1_759_000_000),
        _deal(entry=1, time=1_759_003_600, price=71.0, profit=20.0),
        _deal(position_id=9002, entry=0, time=1_759_100_000, price=71.0),
        _deal(position_id=9002, entry=1, time=1_759_103_600, price=70.0,
              type=1, profit=-15.0),
    ]
    stats = jr.reconcile(days=45, client=_client(deals))

    assert stats["live_stats"]["USOIL"]["trades"] == 2
    assert stats["live_stats"]["USOIL"]["net"] == pytest.approx(5.0)
    assert stats["live_stats"]["USOIL"]["win_rate"] == 50.0
    assert stats["live_stats"]["USOIL"]["pf"] == pytest.approx(1.33)  # rounded

    written = json.loads(
        (tmp_path / "live_stats.json").read_text(encoding="utf-8")
    )
    assert written["symbols"]["USOIL"]["trades"] == 2


def test_execution_gate_suspends_a_losing_symbol(journal, strict_gate, monkeypatch):
    """The gate consumes what reconcile writes: a losing sample suspends."""
    deals = [
        _deal(position_id=9100 + i, entry=0, time=1_759_000_000 + i * 1000)
        for i in range(1, 26)
    ]
    deals += [
        _deal(position_id=9100 + i, entry=1, time=1_759_003_600 + i * 1000,
              price=69.0, profit=-10.0)
        for i in range(1, 26)
    ]
    jr.reconcile(days=45, client=_client(deals))

    monkeypatch.setattr(strict_gate, "LIVE_STATS_PATH", jr.LIVE_STATS_PATH)
    request = {
        "action": 1,
        "symbol": "USOIL",
        "type": 0,
        "volume": 0.10,
        "price": 70.0,
        "sl": 69.0,
        "tp": 72.0,
        "magic": 20260602,
        "comment": "reconcile-suspension",
    }
    joined = "; ".join(strict_gate.check(request, source="test"))
    assert "suspended by live stats" in joined
    assert "-250.00" in joined  # 25 losing positions, commission included
