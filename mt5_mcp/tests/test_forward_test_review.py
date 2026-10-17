"""Offline measurement/rollback checks; no services, broker, or notifier calls."""
import json
import time

import pytest

from mt5_mcp import config, execution_gate, settings_guard
from mt5_mcp.forward_test_review import summarize, write_rollback


@pytest.fixture
def window(monkeypatch, tmp_path):
    settings = config.get_settings()
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings.risk, "reject_orders", False)
    monkeypatch.setattr(settings, "forward_test", config.ForwardTestConfig(
        test_id="measurement", starts_at=100, expires_at=200))
    monkeypatch.setattr(config.time, "time", lambda: 150)
    return settings


@pytest.mark.parametrize("now,expected", [(99, True), (100, False), (199, False), (200, True)])
def test_window_boundaries(window, monkeypatch, now, expected):
    monkeypatch.setattr(config.time, "time", lambda: now)
    assert config.rejections_enabled() is expected


def test_window_required_and_bounded(window, monkeypatch):
    for test in [config.ForwardTestConfig(), config.ForwardTestConfig(test_id=" ", starts_at=100, expires_at=200),
                 config.ForwardTestConfig(test_id="long", starts_at=100, expires_at=100+8*86400)]:
        monkeypatch.setattr(window, "forward_test", test)
        assert config.rejections_enabled()


@pytest.mark.parametrize("policy", ["quality", "performance_risk", "prop_risk"])
def test_newer_strict_policies_win(window, monkeypatch, policy):
    monkeypatch.setattr(getattr(window, policy), "enabled", True)
    assert config.rejections_enabled()


def test_hot_stop_marker_without_reload(window, tmp_path):
    assert not config.rejections_enabled()
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "forward_test.stop").touch()
    assert config.rejections_enabled()


def test_config_error_fails_closed(monkeypatch):
    def broken():
        raise ValueError("bad configuration")
    monkeypatch.setattr(config, "get_settings", broken)
    assert config.rejections_enabled()


def test_audit_failure_blocks_log_mode(window, permissive_execution_gate, monkeypatch):
    monkeypatch.setattr(execution_gate, "_audit", lambda *a, **kw: False)
    with pytest.raises(execution_gate.OrderBlocked, match="audit unavailable"):
        execution_gate.require({"action": 1, "symbol": "USOIL"})
    # Exit remains available even if the test audit is broken.
    execution_gate.require({"action": 1, "position": 42})


def test_learning_remains_hard_in_log_mode(window, permissive_execution_gate, monkeypatch):
    monkeypatch.setattr(execution_gate, "check", lambda *a, **kw: ["learning: paused"])
    with pytest.raises(execution_gate.OrderBlocked, match="learning"):
        execution_gate.require({"action": 1, "symbol": "USOIL"})


def test_offline_rollbacks_preserve_user_settings(tmp_path):
    original = {"risk": {"reject_orders": False, "max_spread_points": 0},
                "quality": {"enabled": True}, "custom": {"keep": [1, 2]},
                "deriv": {"api_token": "fake"}, "forward_test": {"test_id": "old", "expires_at": 123}}
    source = tmp_path / "settings.json"
    source.write_text(json.dumps(original), encoding="utf-8")
    before = source.read_bytes()
    output = tmp_path / "rollback.candidate.json"
    write_rollback(source, output)
    candidate = json.loads(output.read_text())
    expected = settings_guard.rollback_candidate(original)
    assert candidate == expected
    assert original["risk"]["reject_orders"] is False
    assert candidate["risk"]["reject_orders"] is True
    assert candidate["forward_test"]["expires_at"] == 0
    assert candidate["custom"] == original["custom"]
    assert candidate["quality"] == original["quality"]
    assert candidate["deriv"] == original["deriv"]
    assert source.read_bytes() == before
    with pytest.raises(FileExistsError):
        write_rollback(source, output)
    with pytest.raises(ValueError):
        write_rollback(source, source)


def test_summary_distinguishes_attempts_from_legacy_and_fills(tmp_path):
    row = dict(schema_version=2, epoch=150, attempt_id="a", test_id="test", symbol="USOIL",
               setup="desk-x", source="desk", mode="log", event="violation", rules_allowed=False,
               send_permitted=True, sent=None, violations=["blocked setup", "blocked setup"])
    path = tmp_path / "audit.jsonl"
    rows = [row, row, {**row, "attempt_id": "b", "epoch": 200},
            {**row, "attempt_id": "c", "test_id": "other"}, {"epoch": 150, "sent": True}]
    path.write_text("\n".join(json.dumps(r) for r in rows)+"\nbroken\n", encoding="utf-8")
    report = summarize(path, 100, 200, "test")
    assert report["counts"]["attempts"] == 1
    assert report["counts"]["duplicate_rows"] == 1
    assert report["counts"]["malformed_rows"] == 1
    assert report["violations"] == {"blocked setup": 1}
    assert summarize(path, 100, 200)["counts"]["legacy_rows_not_attempt_denominator"] == 1
