"""Bounded forward-test mode: record rule violations and permit measured sends.

Every test runs with reject_orders pinned true by conftest; these tests flip
it to false (the live forward-test setting) and assert that:

* execution_gate.require audits (event=violation, mode=log, send_permitted=true) and
  does not raise, while enforce mode still raises (event=blocked),
* compliance._verdict keeps the violation evidence but reports allowed=True,
* pipeline.run_gates forces GateVerdict.allowed=True with log_mode=True and
  keeps every flag/violation for the record,
* auto_trader._status_guard returns instead of raising,
* tools.trades._preflight warns instead of raising on a wide spread,
* the record-only filters (allowed_hours_utc, blocked_setup_prefixes) are
  produced by check() in both modes (enforcement is what changes).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import client as client_module
from config import get_settings

EURUSD = "EURUSD"


@pytest.fixture(autouse=True)
def _use_fake(fake_client):
    client_module.set_client(fake_client)
    yield
    client_module.reset_client()


@pytest.fixture(autouse=True)
def _fake_state(tmp_path, monkeypatch):
    import compliance

    monkeypatch.setattr(compliance, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(compliance, "_record_event", lambda kind, message: None)


def _log_mode(risk, monkeypatch):
    from mt5_mcp.config import ForwardTestConfig
    import mt5_mcp.config as config
    monkeypatch.setattr(config, "BASE_DIR", Path(config.BASE_DIR) / "test-unused-stop-dir")
    monkeypatch.setattr(get_settings(), "forward_test", ForwardTestConfig(
        test_id="unit", starts_at=time.time()-60, expires_at=time.time()+3600))
    monkeypatch.setattr(risk, "reject_orders", False)


def _bad_request(**overrides) -> dict:
    req = {
        "action": 1,
        "type": 0,
        "symbol": "USOIL",
        "volume": 0.10,
        "sl": 70.0,
        "magic": 20260601,
        "comment": "test-setup",
    }
    req.update(overrides)
    return req


def _audit_lines(eg) -> list[dict]:
    path = Path(eg.GATE_LOG)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestRequireModes:
    def test_log_mode_audits_and_sends(self, permissive_execution_gate, monkeypatch):
        eg = permissive_execution_gate
        monkeypatch.setattr(eg, "check", lambda req, client=None, source="": ["not in allowlist"])
        _log_mode(get_settings().risk, monkeypatch)

        eg.require(_bad_request(), source="test")  # must not raise

        entry = _audit_lines(eg)[-1]
        assert entry["event"] == "violation"
        assert entry["mode"] == "log"
        assert entry["sent"] is None
        assert entry["send_permitted"] is True
        assert entry["violations"] == ["not in allowlist"]

    def test_enforce_mode_still_blocks(self, permissive_execution_gate, monkeypatch):
        eg = permissive_execution_gate
        monkeypatch.setattr(eg, "check", lambda req, client=None, source="": ["not in allowlist"])

        with pytest.raises(eg.OrderBlocked):
            eg.require(_bad_request(), source="test")

        entry = _audit_lines(eg)[-1]
        assert entry["event"] == "blocked"
        assert entry["mode"] == "enforce"
        assert entry["sent"] is False

    def test_clean_request_audits_attempt(self, permissive_execution_gate, monkeypatch):
        eg = permissive_execution_gate
        monkeypatch.setattr(eg, "check", lambda req, client=None, source="": [])
        _log_mode(get_settings().risk, monkeypatch)

        eg.require(_bad_request(), source="test")
        assert _audit_lines(eg)[-1]["event"] == "allowed"
        assert _audit_lines(eg)[-1]["rules_allowed"] is True


class TestComplianceVerdict:
    def test_log_mode_keeps_evidence_but_allows(self, monkeypatch):
        from mt5_mcp.compliance import _verdict

        _log_mode(get_settings().risk, monkeypatch)
        v = _verdict(
            ["daily trade limit reached (3)"],
            [],
            {"mode": "halted", "status": "halted"},
        )
        assert v["allowed"] is True
        assert v["trading_allowed"] is True
        assert v["violations"] == ["daily trade limit reached (3)"]
        assert any("log-only" in w for w in v["warnings"])

    def test_enforce_mode_blocks(self, monkeypatch):
        from mt5_mcp.compliance import _verdict

        v = _verdict(["daily trade limit reached (3)"], [], {"mode": "normal", "status": "active"})
        assert v["allowed"] is False
        assert v["violations"] == ["daily trade limit reached (3)"]


class TestRunGates:
    @staticmethod
    def _stub_vetoes(monkeypatch):
        import mt5_mcp.compliance as compliance_mod
        import mt5_mcp.consensus as consensus_mod

        monkeypatch.setattr(
            consensus_mod,
            "check",
            lambda *a, **kw: {"allowed": False, "violations": ["symbol not in allowlist"]},
        )
        monkeypatch.setattr(
            compliance_mod,
            "gate",
            lambda *a, **kw: {"allowed": False, "violations": ["daily trade limit reached"]},
        )

    def test_log_mode_forces_allowed_and_keeps_evidence(self, monkeypatch):
        from mt5_mcp.pipeline import run_gates

        self._stub_vetoes(monkeypatch)
        _log_mode(get_settings().risk, monkeypatch)

        verdict = run_gates(
            "USOIL", "BUY", 0.10, enable_llm_devil_advocate=False, source="test"
        )
        assert verdict.allowed is True
        assert verdict.log_mode is True
        assert verdict.consensus_allowed is False
        assert verdict.compliance_allowed is False
        assert "symbol not in allowlist" in verdict.violations
        assert "daily trade limit reached" in verdict.violations

    def test_enforce_mode_still_vetoes(self, monkeypatch):
        from mt5_mcp.pipeline import run_gates

        self._stub_vetoes(monkeypatch)

        verdict = run_gates(
            "USOIL", "BUY", 0.10, enable_llm_devil_advocate=False, source="test"
        )
        assert verdict.allowed is False
        assert verdict.log_mode is False


class TestStatusGuard:
    def test_log_mode_returns_instead_of_raising(self, monkeypatch):
        import mt5_mcp.auto_trader as at

        status = {
            "mode": "halted",
            "trading_allowed": False,
            "news_blocked": True,
            "liquidity_blocked": False,
        }
        monkeypatch.setattr(at, "evaluate_compliance", lambda: status)
        _log_mode(get_settings().risk, monkeypatch)

        assert at._status_guard() is status

    def test_enforce_mode_raises(self, monkeypatch):
        import mt5_mcp.auto_trader as at

        status = {
            "mode": "halted",
            "trading_allowed": False,
            "news_blocked": False,
            "liquidity_blocked": False,
        }
        monkeypatch.setattr(at, "evaluate_compliance", lambda: status)
        with pytest.raises(RuntimeError):
            at._status_guard()


class TestPreflightSpread:
    def test_wide_spread_rejected_in_enforce_mode(self, fake_client, monkeypatch):
        from tools.trades import open_market

        fake_client.bid, fake_client.ask = 1.2000, 1.20030
        monkeypatch.setattr(get_settings().risk, "max_spread_points", 1)
        result = open_market(EURUSD, "BUY", 0.01, sl=1.19500)
        assert not result.accepted
        assert "spread" in result.error.lower()

    def test_wide_spread_warns_and_sends_in_log_mode(self, fake_client, monkeypatch):
        from tools.trades import open_market

        fake_client.bid, fake_client.ask = 1.2000, 1.20030
        monkeypatch.setattr(get_settings().risk, "max_spread_points", 1)
        _log_mode(get_settings().risk, monkeypatch)
        result = open_market(EURUSD, "BUY", 0.01, sl=1.19500)
        assert result.accepted
        assert any("WARNING" in c and "spread" in c for c in result.checks)


class TestRecordOnlyFilters:
    """check() is mode-agnostic: violations are always produced, only
    enforcement changes.  strict_gate keeps consensus/compliance stubbed
    open so only the record-only filters can fire."""

    def test_out_of_hours_and_desk_setup_recorded(self, strict_gate, monkeypatch):
        risk = get_settings().risk
        now_hour = datetime.now(timezone.utc).hour
        monkeypatch.setattr(risk, "allowed_hours_utc", [(now_hour + 1) % 24])
        monkeypatch.setattr(risk, "blocked_setup_prefixes", ["desk-", "desk:"])

        violations = strict_gate.check(
            _bad_request(comment="desk:double_top"), source="test"
        )
        joined = " | ".join(violations)
        assert "allowed trading hours" in joined
        assert "blocked setup prefix" in joined
        assert "desk:double_top" in joined

    def test_in_hours_and_custom_setup_clean(self, strict_gate, monkeypatch):
        risk = get_settings().risk
        monkeypatch.setattr(
            risk, "allowed_hours_utc", [datetime.now(timezone.utc).hour]
        )
        monkeypatch.setattr(risk, "blocked_setup_prefixes", ["desk-"])

        violations = strict_gate.check(_bad_request(), source="test")
        assert violations == []


class TestEnvOverride:
    def test_env_false_forces_log_mode(self, monkeypatch):
        import mt5_mcp.config as config_mod

        monkeypatch.setenv("MT5_MCP_REJECT_ORDERS", "0")
        data = {"risk": {"reject_orders": True}}
        out = config_mod._apply_env_overrides(data)
        assert out["risk"]["reject_orders"] is False

    def test_env_true_forces_enforcement(self, monkeypatch):
        import mt5_mcp.config as config_mod

        monkeypatch.setenv("MT5_MCP_REJECT_ORDERS", "1")
        data = {"risk": {"reject_orders": False}}
        out = config_mod._apply_env_overrides(data)
        assert out["risk"]["reject_orders"] is True
