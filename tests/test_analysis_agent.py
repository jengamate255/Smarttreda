"""Tests: analysis_agent pure helpers (no MT5 connection needed)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis_agent import is_available, should_notify, _short_plan


def good_plan():
    return {
        "action": "BUY", "valid": True, "confidence": 85.0,
        "trade_score": 75.0, "risk_reward": 3.5, "reason": "",
    }


def test_is_available_pass():
    ok, reasons = is_available(good_plan(), 80.0)
    assert ok and reasons == []


def test_is_available_rejects_each_gate():
    ok, _ = is_available({**good_plan(), "valid": False, "reason": "x"}, 80.0)
    assert not ok
    ok, _ = is_available({**good_plan(), "action": "NONE"}, 80.0)
    assert not ok
    ok, _ = is_available({**good_plan(), "confidence": 10.0}, 80.0)
    assert not ok
    ok, _ = is_available({**good_plan(), "trade_score": 10.0}, 80.0)
    assert not ok
    ok, _ = is_available({**good_plan(), "risk_reward": 1.0}, 80.0)
    assert not ok
    ok, reasons = is_available(good_plan(), 10.0)
    assert not ok and any("health" in r for r in reasons)


def test_should_notify_new_available():
    state = {"last_plan": {}, "last_notify": {}}
    assert should_notify("EURUSD", True, "BUY", state, time.time()) is True
    assert should_notify("EURUSD", False, "BUY", state, time.time()) is False


def test_should_notify_snoozes_repeat_same_direction():
    now = time.time()
    state = {
        "last_plan": {"EURUSD": {"plan": {"action": "BUY"}, "available": True}},
        "last_notify": {"EURUSD": {"ts": now, "msg": "x"}},
    }
    assert should_notify("EURUSD", True, "BUY", state, now, snooze_minutes=90) is False
    # direction flip re-alerts immediately
    assert should_notify("EURUSD", True, "SELL", state, now, snooze_minutes=90) is True


def test_short_plan_accepts_dict_and_object():
    short = _short_plan(good_plan())
    assert short["action"] == "BUY" and short["confidence"] == 85.0

    class P:
        action = "SELL"; valid = True; confidence = 90.0; trade_score = 80.0
        entry = 1.0; sl = 0.9; tp = 1.3; sl_pips = 20.0; tp_pips = 90.0
        risk_reward = 4.0; zone_strength = 70.0; confirmation = "BOS"; reason = ""

    short = _short_plan(P())
    assert short["action"] == "SELL" and short["risk_reward"] == 4.0
