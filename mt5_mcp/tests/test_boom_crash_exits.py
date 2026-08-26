"""Tests for the chandelier trailing exit manager (boom_crash_exits)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import client as client_module
from tools.boom_crash_exits import (
    ExitConfig,
    ExitState,
    _is_watcher_position,
    get_exit_config,
    manage_position,
    run_exit_management,
)
from tools.trades import MAGIC

BOOM = "Boom 300 Index"
CRASH = "Crash 500 Index"


@pytest.fixture(autouse=True)
def _use_fake(fake_client):
    client_module.set_client(fake_client)
    yield
    client_module.reset_client()


def _m1_frame(
    n: int = 120,
    spike_high: float = 107.0,
    spike_low: float = 93.0,
    close: float = 106.0,
    base: float = 100.0,
) -> pd.DataFrame:
    """M1 candles ending now; a high/low spike sits in the last bars."""
    now = int(time.time())
    rows = []
    price = base
    for i in range(n):
        t = now - (n - i) * 60
        high = price + 0.1
        low = price - 0.1
        if i == n - 2:
            high = spike_high
        if i == n - 3:
            low = spike_low
        rows.append(
            {
                "time": t,
                "open": price,
                "high": high,
                "low": low,
                "close": close if i == n - 1 else price,
            }
        )
        price = close if i == n - 1 else price
    return pd.DataFrame(rows)


def _long_position(**overrides) -> dict:
    pos = {
        "ticket": 101,
        "symbol": BOOM,
        "type": 0,  # ORDER_TYPE_BUY
        "volume": 1.0,
        "price_open": 100.0,
        "sl": 95.0,
        "tp": 0.0,
        "time": float(int(time.time()) - 300),
        "magic": MAGIC,
        "comment": "BOOM300 spike-drift 38.2%",
    }
    pos.update(overrides)
    return pos


def _short_position(**overrides) -> dict:
    pos = {
        "ticket": 102,
        "symbol": CRASH,
        "type": 1,  # ORDER_TYPE_SELL
        "volume": 1.0,
        "price_open": 100.0,
        "sl": 105.0,
        "tp": 0.0,
        "time": float(int(time.time()) - 300),
        "magic": MAGIC,
        "comment": "CRASH500 spike-drift 50%",
    }
    pos.update(overrides)
    return pos


def _state(tmp_path) -> ExitState:
    return ExitState(tmp_path / "exit_state.json")


def _sltp_requests(fake_client, position_id: int) -> list[dict]:
    return [
        r
        for r in fake_client.requests
        if r.get("action") == 6 and r.get("position") == position_id
    ]


def _deal_requests(fake_client, position_id: int) -> list[dict]:
    return [
        r
        for r in fake_client.requests
        if r.get("action") == 1 and r.get("position") == position_id
    ]


class TestPositionFilter:
    def test_watcher_position_accepted(self):
        assert _is_watcher_position(_long_position())

    def test_non_boom_symbol_rejected(self):
        assert not _is_watcher_position(_long_position(symbol="EURUSD"))

    def test_foreign_magic_rejected(self):
        pos = _long_position(magic=999, comment="manual trade")
        assert not _is_watcher_position(pos)

    def test_comment_fallback_accepted(self):
        pos = _long_position(magic=0, comment="BOOM300 spike-drift 38.2%")
        assert _is_watcher_position(pos)


class TestArmAndTrail:
    def test_long_not_armed_below_1r(self, fake_client, tmp_path):
        fake_client._positions.append(_long_position())
        fake_client.frames[(BOOM.upper(), "M1")] = _m1_frame()
        fake_client.bid = 102.0  # below arm level 105.0

        result = manage_position(
            fake_client, _long_position(), config=ExitConfig(), state=_state(tmp_path)
        )

        assert result["status"] in ("watching", "armed")
        assert not _sltp_requests(fake_client, 101)

    def test_long_arms_at_1r_and_trails_sl(self, fake_client, tmp_path):
        fake_client._positions.append(_long_position())
        fake_client.frames[(BOOM.upper(), "M1")] = _m1_frame(spike_high=107.0)
        fake_client.bid = 106.9  # >= arm level (entry 100 + risk 5), above chandelier line

        result = manage_position(
            fake_client, _long_position(), config=ExitConfig(), state=_state(tmp_path)
        )

        assert "armed at +1R" in result["actions"]
        sl_requests = _sltp_requests(fake_client, 101)
        assert sl_requests, "trail must modify the SL once armed"
        new_sl = sl_requests[-1]["sl"]
        # Chandelier: highest high (107) - 1.0 ATR (small) -> between 1R and high.
        assert 105.0 < new_sl < 107.0
        assert new_sl > 95.0  # only ever moved toward profit
        assert new_sl < fake_client.bid  # SL must sit below the market for a long

    def test_long_breached_line_closes_at_market(self, fake_client, tmp_path):
        fake_client._positions.append(_long_position())
        fake_client.frames[(BOOM.upper(), "M1")] = _m1_frame(spike_high=107.0)
        fake_client.bid = 105.0  # at +1R but below the chandelier line: exit triggered

        config = ExitConfig(partial_exit_enabled=False)
        result = manage_position(
            fake_client, _long_position(), config=config, state=_state(tmp_path)
        )

        assert any(a.startswith("chandelier exit") for a in result["actions"])
        assert result["status"] == "exited"

    def test_short_breached_line_closes_at_market(self, fake_client, tmp_path):
        fake_client._positions.append(_short_position())
        fake_client.frames[(CRASH.upper(), "M1")] = _m1_frame(spike_low=93.0)
        fake_client.ask = 95.0  # at -1R but above the chandelier line: exit triggered

        config = ExitConfig(partial_exit_enabled=False)
        result = manage_position(
            fake_client, _short_position(), config=config, state=_state(tmp_path)
        )

        assert any(a.startswith("chandelier exit") for a in result["actions"])
        assert result["status"] == "exited"

    def test_short_arms_and_trails_sl(self, fake_client, tmp_path):
        fake_client._positions.append(_short_position())
        fake_client.frames[(CRASH.upper(), "M1")] = _m1_frame(spike_low=93.0)
        fake_client.ask = 93.2  # <= arm level (entry 100 - risk 5), below chandelier line

        result = manage_position(
            fake_client, _short_position(), config=ExitConfig(), state=_state(tmp_path)
        )

        assert "armed at +1R" in result["actions"]
        sl_requests = _sltp_requests(fake_client, 102)
        assert sl_requests
        new_sl = sl_requests[-1]["sl"]
        assert 93.0 < new_sl < 95.0  # lowest low (93) + ATR, below entry
        assert new_sl > fake_client.ask  # SL must sit above the market for a short

    def test_sl_never_moves_backward(self, fake_client, tmp_path):
        fake_client._positions.append(_long_position(sl=106.8))
        fake_client.frames[(BOOM.upper(), "M1")] = _m1_frame(spike_high=107.0)
        fake_client.bid = 106.9

        result = manage_position(
            fake_client, _long_position(sl=106.8), config=ExitConfig(), state=_state(tmp_path)
        )

        # candidate ~106.4 is not better than the current 106.8 -> no modify.
        assert not _sltp_requests(fake_client, 101)
        assert "trail SL" not in " ".join(result["actions"])

    def test_trail_distance_clamped_to_research_range(self):
        config = ExitConfig(trail_atr_distance=9.9)
        assert config.trail_distance() == 1.5
        config = ExitConfig(trail_atr_distance=0.1)
        assert config.trail_distance() == 1.0
        assert ExitConfig().trail_distance() == 1.0


class TestPartialExit:
    def test_partial_fires_once_at_1r(self, fake_client, tmp_path):
        state = _state(tmp_path)
        pos = _long_position()
        fake_client._positions.append(pos)
        fake_client.frames[(BOOM.upper(), "M1")] = _m1_frame(spike_high=107.0)
        fake_client.bid = 106.0

        first = manage_position(fake_client, pos, config=ExitConfig(), state=state)
        assert any(a.startswith("partial +1R") for a in first["actions"])

        # Position volume shrunk after the partial; a second pass must not fire again.
        pos["volume"] = 0.5
        fake_client._positions[0]["volume"] = 0.5
        second = manage_position(fake_client, pos, config=ExitConfig(), state=state)
        assert not any(a.startswith("partial +1R") for a in second["actions"])
        assert len(_deal_requests(fake_client, 101)) == 1

    def test_partial_not_fired_below_1r(self, fake_client, tmp_path):
        fake_client._positions.append(_long_position())
        fake_client.frames[(BOOM.upper(), "M1")] = _m1_frame()
        fake_client.bid = 102.0

        manage_position(fake_client, _long_position(), config=ExitConfig(), state=_state(tmp_path))

        assert not _deal_requests(fake_client, 101)

    def test_partial_dedup_survives_restart(self, fake_client, tmp_path):
        state_path = tmp_path / "exit_state.json"
        state = ExitState(state_path)
        pos = _long_position()
        fake_client._positions.append(pos)
        fake_client.frames[(BOOM.upper(), "M1")] = _m1_frame(spike_high=107.0)
        fake_client.bid = 106.0

        manage_position(fake_client, pos, config=ExitConfig(), state=state)
        assert len(_deal_requests(fake_client, 101)) == 1

        # Simulate a watcher restart: fresh state object from the same file.
        state.save()
        state2 = ExitState(state_path)
        pos["volume"] = 0.5
        fake_client._positions[0]["volume"] = 0.5
        manage_position(fake_client, pos, config=ExitConfig(), state=state2)

        assert len(_deal_requests(fake_client, 101)) == 1, "restart must not double-fire the partial"


class TestConfig:
    def test_settings_defaults(self):
        config = get_exit_config()
        assert config.trail_arm_r == 1.0
        assert config.trail_atr_distance == pytest.approx(1.0)
        assert config.partial_exit_fraction == pytest.approx(0.5)
        assert config.partial_exit_enabled is True


class TestRunExitManagement:
    def test_checks_open_watcher_positions(self, fake_client, tmp_path):
        fake_client._positions.append(_long_position())
        fake_client.frames[(BOOM.upper(), "M1")] = _m1_frame()
        fake_client.bid = 102.0  # not yet at +1R

        summary = run_exit_management(
            fake_client,
            config=ExitConfig(),
            state=ExitState(tmp_path / "state.json"),
        )

        assert summary["checked"] == 1
        assert summary["statuses"] == {"101": "watching"}

    def test_ignores_foreign_positions(self, fake_client, tmp_path):
        fake_client._positions.append(_long_position(magic=999, comment="manual"))
        fake_client._positions.append(_short_position())
        fake_client.frames[(BOOM.upper(), "M1")] = _m1_frame()
        fake_client.frames[(CRASH.upper(), "M1")] = _m1_frame()
        fake_client.bid = 93.2
        fake_client.ask = 93.2

        summary = run_exit_management(
            fake_client,
            config=ExitConfig(partial_exit_enabled=False),
            state=ExitState(tmp_path / "state.json"),
        )

        assert summary["checked"] == 1  # only the watcher short position