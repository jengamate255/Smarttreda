"""Tests: telegram_bot interaction layer (mocked network/MT5)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import telegram_bot as tb


class _Res:
    def __init__(self, accepted: bool, error: str = ""):
        self.accepted = accepted
        self.error = error


def _install_fake_trades(close=True, be=False, trail=True):
    mod = types.ModuleType("fake_trades")
    mod.close_position = lambda t: _Res(close, "" if close else "rejected")
    mod.break_even = lambda t: _Res(be, "" if be else "not beyond entry")
    mod.trail_stop = lambda t, p: _Res(trail, "" if trail else "backwards")
    sys.modules["mt5_mcp.tools.trades"] = mod


def _capture_api():
    calls: list = []
    patcher = patch.object(
        tb, "api_call",
        lambda token, method, payload=None, timeout=35: calls.append((method, payload)) or {"ok": True},
    )
    patcher.start()
    return calls, patcher


def _methods(calls):
    return [m for m, _ in calls]


def test_handle_message_triple_and_unknown():
    reply, png, markup = tb.handle_message("/bogus")
    assert reply.startswith("Unknown") and png is None and markup is None
    reply, png, markup = tb.handle_message("/help")
    assert png is None and markup is None and len(reply) > 0


def test_close_callback_accept_and_reject():
    _install_fake_trades(close=True)
    calls, p = _capture_api()
    try:
        tb.handle_callback("t", 1, {"id": "c", "data": "close:7"})
        assert _methods(calls) == ["answerCallbackQuery", "sendMessage"]
        _install_fake_trades(close=False)
        del calls[:]
        tb.handle_callback("t", 1, {"id": "c", "data": "close:7"})
        assert _methods(calls) == ["answerCallbackQuery", "sendMessage"]
        assert "rejected" in calls[0][1]["text"]
    finally:
        p.stop()


def test_be_trail_callbacks_surface_rejections():
    _install_fake_trades(be=False, trail=True)
    calls, p = _capture_api()
    try:
        tb.handle_callback("t", 1, {"id": "c", "data": "be:9"})
        assert _methods(calls) == ["answerCallbackQuery", "sendMessage"]
        del calls[:]
        tb.handle_callback("t", 1, {"id": "c", "data": "trail:9:40"})
        assert _methods(calls) == ["answerCallbackQuery", "sendMessage"]
    finally:
        p.stop()


def test_bad_ticket_and_unknown_action_still_answer():
    _install_fake_trades()
    calls, p = _capture_api()
    try:
        tb.handle_callback("t", 1, {"id": "c", "data": "close:abc"})
        assert _methods(calls) == ["answerCallbackQuery", "sendMessage"]
        del calls[:]
        tb.handle_callback("t", 1, {"id": "c", "data": "frobnicate"})
        assert _methods(calls) == ["answerCallbackQuery"]
    finally:
        p.stop()


def test_build_position_keyboard_shape():
    kb = tb._build_position_keyboard(123, "EURUSD")
    rows = kb["inline_keyboard"]
    assert len(rows) == 2 and all(len(r) == 2 for r in rows)
    assert rows[0][0]["callback_data"] == "close:123"
