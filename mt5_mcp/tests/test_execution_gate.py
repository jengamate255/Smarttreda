"""Execution gate: opening orders are policed, exits never are."""

from __future__ import annotations

import json

import pytest
from mt5_mcp.execution_gate import OrderBlocked, classify, side_of

# TRADE_ACTION_DEAL / ORDER_TYPE_BUY, fully tagged opening request
OPEN = {
    "action": 1,
    "symbol": "USOIL",
    "type": 0,
    "volume": 0.10,
    "price": 70.0,
    "sl": 69.0,
    "tp": 72.0,
    "magic": 20260602,
    "comment": "gate-test",
}


def _violations(gate, request):
    return gate.check(request, source="test")


def test_classify_open_close_modify_cancel():
    assert classify({"action": 1}) == "open"          # DEAL
    assert classify({"action": 5}) == "open"          # PENDING
    assert classify({"action": 1, "position": 77}) == "close"
    assert classify({"action": 6}) == "modify"        # SLTP
    assert classify({"action": 7}) == "modify"        # MODIFY
    assert classify({"action": 8}) == "cancel"        # REMOVE
    assert classify({"action": 10}) == "close"        # CLOSE_BY
    assert classify({"action": "junk"}) == "open"     # malformed -> gated


def test_side_of():
    assert side_of({"type": 0}) == "BUY"
    assert side_of({"type": 4}) == "BUY"   # BUY_STOP
    assert side_of({"type": 1}) == "SELL"
    assert side_of({}) == "BUY"


def test_exits_are_never_blocked(strict_gate):
    exits = [
        {"action": 6, "symbol": "USOIL", "position": 1, "sl": 69.0},  # SLTP
        {"action": 7, "symbol": "USOIL", "position": 1, "sl": 69.0},  # MODIFY
        {"action": 8, "symbol": "USOIL", "order": 5},                  # REMOVE
        {"action": 10, "symbol": "USOIL", "position": 1, "position_by": 2},
        {"action": 1, "symbol": "USOIL", "type": 1, "volume": 0.1,
         "position": 999, "price": 71.0},                              # close
    ]
    for request in exits:
        assert _violations(strict_gate, request) == [], request


def test_allowed_symbol_with_full_tagging_passes(strict_gate):
    assert _violations(strict_gate, dict(OPEN)) == []


def test_missing_magic_blocks(strict_gate):
    request = dict(OPEN)
    request.pop("magic")
    joined = "; ".join(_violations(strict_gate, request))
    assert "magic id" in joined


def test_missing_comment_blocks(strict_gate):
    request = dict(OPEN)
    request["comment"] = "   "
    joined = "; ".join(_violations(strict_gate, request))
    assert "no comment" in joined


def test_symbol_outside_allowlist_blocks(strict_gate):
    # EURUSD is not on the live-positive allowlist (and not blocked outright)
    joined = "; ".join(_violations(strict_gate, {**OPEN, "symbol": "EURUSD"}))
    assert "not in allowlist" in joined


def test_empty_allowlist_fails_closed(strict_gate, monkeypatch):
    from mt5_mcp import consensus

    monkeypatch.setattr(consensus, "_cfg", lambda: {"allowed_symbols": []})
    joined = "; ".join(_violations(strict_gate, dict(OPEN)))
    assert "allowlist is empty" in joined


def test_blocked_symbol_list(strict_gate, monkeypatch):
    from mt5_mcp import config, consensus

    monkeypatch.setattr(config.get_settings().risk, "blocked_symbols", ["XAUUSD"])
    monkeypatch.setattr(consensus, "_cfg", lambda: {"allowed_symbols": ["USOIL"]})
    # XAUUSD is on risk.blocked_symbols in settings.json
    joined = "; ".join(_violations(strict_gate, {**OPEN, "symbol": "XAUUSD"}))
    assert "blocked list" in joined
    assert "not in allowlist" in joined  # it is off the allowlist too


def test_live_stats_suspension(strict_gate, tmp_path, monkeypatch):
    stats = tmp_path / "live_stats.json"
    monkeypatch.setattr(strict_gate, "LIVE_STATS_PATH", stats)

    stats.write_text(
        json.dumps({"symbols": {"USOIL": {"trades": 25, "net": -120.5}}}),
        encoding="utf-8",
    )
    joined = "; ".join(_violations(strict_gate, dict(OPEN)))
    assert "suspended by live stats" in joined

    # A small negative sample is not enough to suspend a symbol.
    stats.write_text(
        json.dumps({"symbols": {"USOIL": {"trades": 5, "net": -30.0}}}),
        encoding="utf-8",
    )
    assert _violations(strict_gate, dict(OPEN)) == []


def test_positive_live_stats_never_suspend(strict_gate, tmp_path, monkeypatch):
    stats = tmp_path / "live_stats.json"
    monkeypatch.setattr(strict_gate, "LIVE_STATS_PATH", stats)
    stats.write_text(
        json.dumps({"symbols": {"USOIL": {"trades": 40, "net": 12.0}}}),
        encoding="utf-8",
    )
    assert _violations(strict_gate, dict(OPEN)) == []


def test_sub_check_exception_is_a_violation(strict_gate, monkeypatch):
    from mt5_mcp import consensus

    def _boom(*args, **kwargs):
        raise RuntimeError("consensus down")

    monkeypatch.setattr(consensus, "check", _boom)
    joined = "; ".join(_violations(strict_gate, dict(OPEN)))
    assert "fail-closed" in joined


def test_require_raises_and_writes_audit(strict_gate):
    with pytest.raises(OrderBlocked) as excinfo:
        strict_gate.require({**OPEN, "symbol": "EURUSD"}, source="unit")
    assert "not in allowlist" in str(excinfo.value)

    lines = [
        json.loads(line)
        for line in strict_gate.GATE_LOG.read_text(encoding="utf-8").splitlines()
    ]
    assert lines, "blocked orders must land in the audit log"
    assert lines[-1]["symbol"] == "EURUSD"
    assert lines[-1]["source"] == "unit"
    assert lines[-1]["event"] == "blocked"


def test_mt5_client_order_send_runs_the_gate(strict_gate, mt5_client, monkeypatch):
    from mt5_mcp.mt5_client import MT5Error

    seen: list[dict] = []
    monkeypatch.setattr(mt5_client._mt5, "order_send", seen.append)

    request = {
        **OPEN,
        "symbol": "EURUSD",  # off the allowlist
        "price": 1.1,
        "sl": 1.099,
        "tp": 1.102,
    }
    with pytest.raises(MT5Error, match="execution gate"):
        mt5_client.order_send(request)
    assert seen == [], "a blocked order must never reach MetaTrader5"


def test_permissive_default_keeps_untagged_requests_flowable(mt5_client):
    """Ordinary tests run with the gate stubbed open (see conftest)."""
    from common import RETCODE_DONE

    request = {"action": 1, "symbol": "EURUSD", "volume": 0.1, "type": 0,
               "price": 1.1}
    result = mt5_client.order_send(request)
    assert result["retcode"] == RETCODE_DONE
