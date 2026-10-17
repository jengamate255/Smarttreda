"""Learning must abstain on known losses without inventing outcomes or blocking exits."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from mt5_mcp import learning_loop as learning

NOW = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def standalone_learning_policy(monkeypatch):
    from mt5_mcp import entry_quality
    monkeypatch.setattr(entry_quality,"enabled",lambda:False)


def trade(index, pnl=-10, **changes):
    close = NOW - timedelta(minutes=10 + index * 30)
    return {"ticket": index + 1, "symbol": "BTCUSD", "side": "SELL",
            "setup": "desk:double_top", "open_time": (close - timedelta(minutes=15)).isoformat(),
            "close_time": close.isoformat(), "pnl": pnl,
            "outcome": "win" if pnl is not None and pnl > 0 else "loss" if pnl is not None and pnl < 0 else "breakeven", **changes}


def check(rows, **changes):
    return learning.check_entry("BTCUSD", "SELL", "desk-double_top", rows=rows, now=NOW, **changes)


def test_three_losses_pause_and_expire():
    rows = [trade(i) for i in range(3)] + [trade(i, 50) for i in range(3, 8)]
    verdict = check(rows)
    assert not verdict["allowed"] and verdict["consecutive_losses"] == 3
    assert verdict["paused_until"]
    later = learning.check_entry("BTCUSD", "SELL", "desk:double_top", rows=rows,
                                 now=NOW + timedelta(hours=6))
    assert later["allowed"], "pause expires from the loss timestamp, not the scan timestamp"


def test_recent_win_breaks_loss_streak():
    assert check([trade(0, 20), *[trade(i) for i in range(1, 4)]])["allowed"]


def test_small_loss_streak_is_evidence_not_a_veto():
    verdict = check([trade(i) for i in range(3)])
    assert verdict["allowed"] and verdict["status"] == "insufficient_sample"


def test_negative_edge_pauses_high_win_rate_with_large_losses():
    rows = [trade(i, 2 if i < 6 else -50) for i in range(8)]
    verdict = check(rows)
    assert not verdict["allowed"] and verdict["wins"] == 6
    assert verdict["profit_factor"] < 0.8
    assert learning._factor(8, 75, verdict["net_pnl"]) < 1


def test_profitable_low_win_rate_is_not_penalized():
    rows = [trade(i, 100 if i % 3 == 0 else -10) for i in range(9)]
    assert check(rows)["allowed"]
    assert learning._factor(9, 33.3, 240) == 1


@pytest.mark.parametrize("changes", [
    {"symbol": "ETHUSD"}, {"side": "BUY"}, {"setup": "desk:double_bottom"},
    {"setup": "manual"}, {"setup": "reconcile"}, {"pnl": None},
    {"outcome": None}, {"outcome": "win"}, {"pnl": float("nan")},
    {"close_time": None}, {"close_time": (NOW + timedelta(hours=1)).isoformat()},
])
def test_unrelated_unfinished_or_future_outcomes_cannot_block(changes):
    assert check([trade(i, **changes) for i in range(8)])["allowed"]


def test_duplicate_tickets_do_not_create_a_loss_streak():
    assert check([trade(0)] * 8)["allowed"]


def test_loader_failure_abstains(monkeypatch):
    monkeypatch.setattr(learning, "_closed_rows", lambda: (_ for _ in ()).throw(OSError("offline")))
    assert not learning.check_entry("BTCUSD", "SELL", "desk:double_top")["allowed"]


def test_sqlite_close_invalidates_history_cache(tmp_path, monkeypatch):
    from mt5_mcp import config
    from mt5_mcp.memory.trade_journal import Journal

    path = tmp_path / "journal.db"
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(memory=SimpleNamespace(db_path=path)))
    journal = Journal(path)
    try:
        journal.record_trade(ticket=1, symbol="BTCUSD", side="SELL", setup="desk:double_top",
                             open_time=(NOW - timedelta(hours=1)).isoformat())
        assert journal.get_trade(1)["close_time"] is None
        assert learning._closed_rows() == []
        journal.record_trade(**trade(0))
        assert len(learning._closed_rows()) == 1, "new outcomes cannot stay hidden behind cached factors"
    finally:
        journal.close()


def test_pattern_factor_uses_normalized_setup_and_symbol(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [trade(i, -10, close_time=(now - timedelta(minutes=10 + i * 30)).isoformat(),
                  open_time=(now - timedelta(minutes=25 + i * 30)).isoformat()) for i in range(8)]
    monkeypatch.setattr(learning, "_closed_rows", lambda **_: rows)
    score, factor = learning.adjust_pattern_score("double_top", 80, symbol="BTCUSD")
    assert factor < 1 and score < 80
    assert learning.adjust_pattern_score("double_top", 80, symbol="ETHUSD") == (80, 1)


def test_learning_blocks_even_in_execution_log_mode(monkeypatch, tmp_path):
    from mt5_mcp import compliance, config, consensus, execution_gate as gate

    risk = SimpleNamespace(blocked_symbols=[], allowed_hours_utc=[], blocked_setup_prefixes=[])
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(risk=risk))
    monkeypatch.setattr(config, "rejections_enabled", lambda: False)
    monkeypatch.setattr(consensus, "_cfg", lambda: {"allowed_symbols": ["BTCUSD"]})
    monkeypatch.setattr(consensus, "check", lambda *a, **kw: {"violations": []})
    monkeypatch.setattr(compliance, "gate", lambda *a, **kw: {"allowed": True, "violations": []})
    monkeypatch.setattr(learning, "_closed_rows", lambda: [trade(i, close_time=(datetime.now(timezone.utc) - timedelta(minutes=i+1)).isoformat(),
        open_time=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()) for i in range(8)])
    monkeypatch.setattr(gate, "live_stats", lambda: {})
    monkeypatch.setattr(gate, "GATE_LOG", tmp_path / "gate.jsonl")
    request = {"action": 1, "type": 1, "symbol": "BTCUSD", "volume": 0.1,
               "magic": 1, "comment": "desk-double_top", "sl": 90000}
    with pytest.raises(gate.OrderBlocked, match="learning:"):
        gate.require(request)
    gate.require({**request, "position": 42})
    gate.require({**request, "action": 6})


def test_pipeline_cannot_override_learning_in_log_mode(monkeypatch):
    from mt5_mcp import compliance, config, consensus, pipeline

    monkeypatch.setattr(config, "rejections_enabled", lambda: False)
    monkeypatch.setattr(consensus, "check", lambda *a, **kw: {"allowed": True})
    monkeypatch.setattr(compliance, "gate", lambda *a, **kw: {"allowed": True})
    monkeypatch.setattr(learning, "check_entry", lambda *a, **kw: {"allowed": False, "violations": ["learning: pause"]})
    verdict = pipeline.run_gates("BTCUSD", "SELL", 0.1, setup="desk:double_top", enable_llm_devil_advocate=False)
    assert not verdict.allowed and not verdict.learning_allowed and not verdict.log_mode


def test_inactivity_does_not_lower_floors():
    from mt5_mcp.adaptive_thresholds import _calculate_adjustments

    assert all(value >= 1 for value in _calculate_adjustments({"total_trades": 0}).values())
    assert all(value >= 1 for value in _calculate_adjustments({"total_trades": 10, "hours_since_last_trade": 1,
        "win_rate": 45, "avg_pnl": 4, "profit_factor": 1.5}).values())
