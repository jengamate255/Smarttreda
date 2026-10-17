"""Routing evidence: thin backtests must not steer live strategy choice."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from mt5_mcp import routing_evidence as ev

PROMOTE = {"trades": 60, "pf": 1.31, "verdict": "PROMOTE", "routing_family": True}


@pytest.fixture
def atlas(tmp_path, monkeypatch) -> SimpleNamespace:
    monkeypatch.setattr(ev, "_LAB_DIR", tmp_path)
    monkeypatch.setattr(ev, "_cache", {"ts": 0.0, "symbols": None, "path": None})
    ev._logged.clear()

    def write(payload: dict, name: str = "atlas_2026-10-05.json") -> None:
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    def row(**metrics) -> dict:
        merged = dict(PROMOTE)
        merged.update(metrics)
        return merged

    def with_rows(symbol: str, strategies: dict) -> dict:
        return {"symbols": {symbol: {"strategies": strategies}}}

    return SimpleNamespace(write=write, row=row, with_rows=with_rows, dir=tmp_path)


def test_walk_forward_promote_is_routed(atlas):
    atlas.write(atlas.with_rows("SPX500", {"ema_crossover": atlas.row()}))
    assert ev.route("SPX500", candidate="ema_crossover") == "ema_crossover"
    assert ev.allowed("SPX500", "ema_crossover") is True


def test_thin_backtest_sample_is_rejected(atlas):
    atlas.write(
        atlas.with_rows("EURJPY", {"mean_reversion": atlas.row(trades=6, pf=2.65)})
    )
    assert ev.route("EURJPY", candidate="mean_reversion") == ev.DEFAULT_STRATEGY


def test_low_profit_factor_is_rejected(atlas):
    atlas.write(
        atlas.with_rows("USDCAD", {"momentum_breakout": atlas.row(pf=1.05)})
    )
    assert ev.route("USDCAD", candidate="momentum_breakout") == ev.DEFAULT_STRATEGY


def test_failed_walk_forward_is_rejected(atlas):
    atlas.write(
        atlas.with_rows(
            "BTCUSD", {"btc_trend_following": atlas.row(verdict="FAIL")}
        )
    )
    assert ev.route("BTCUSD", candidate="btc_trend_following") == ev.DEFAULT_STRATEGY


def test_paper_grade_only_allowed_on_the_paper_path(atlas):
    atlas.write(
        atlas.with_rows(
            "NDX100", {"ema_crossover": atlas.row(verdict="PAPER", trades=425, pf=1.153)}
        )
    )
    assert ev.route("NDX100", candidate="ema_crossover") == ev.DEFAULT_STRATEGY
    assert (
        ev.route("NDX100", candidate="ema_crossover", allow_paper=True)
        == "ema_crossover"
    )


def test_missing_atlas_entry_falls_back(atlas):
    atlas.write(atlas.with_rows("SPX500", {"ema_crossover": atlas.row()}))
    assert ev.route("ETHUSD", candidate="btc_trend_following") == ev.DEFAULT_STRATEGY


def test_no_atlas_file_fails_closed(atlas):
    # _LAB_DIR exists but holds no atlas_*: no evidence, no specialisation.
    assert ev.route("SPX500", candidate="ema_crossover") == ev.DEFAULT_STRATEGY


def test_default_strategy_short_circuits(atlas):
    assert ev.route("EURUSD", candidate=ev.DEFAULT_STRATEGY) == ev.DEFAULT_STRATEGY
    assert ev.allowed("EURUSD", ev.DEFAULT_STRATEGY) is True


def test_newest_atlas_wins(atlas):
    atlas.write(
        atlas.with_rows(
            "SPX500", {"ema_crossover": atlas.row(verdict="FAIL")}
        ),
        name="atlas_2026-10-01.json",
    )
    atlas.write(
        atlas.with_rows("SPX500", {"ema_crossover": atlas.row()}),
        name="atlas_2026-10-05.json",
    )
    assert ev.route("SPX500", candidate="ema_crossover") == "ema_crossover"


def test_non_routing_family_is_rejected(atlas):
    atlas.write(
        atlas.with_rows(
            "EURUSD", {"daybreak": atlas.row(routing_family=False)}
        )
    )
    assert ev.route("EURUSD", candidate="daybreak") == ev.DEFAULT_STRATEGY


def test_routing_status_reports_every_symbol(atlas):
    atlas.write(atlas.with_rows("SPX500", {"ema_crossover": atlas.row()}))
    status = ev.routing_status()
    assert status["default_strategy"] == ev.DEFAULT_STRATEGY
    assert status["atlas"].endswith("atlas_2026-10-05.json")
    spx = status["symbols"]["SPX500"]
    assert spx["candidate"] == "ema_crossover"
    assert spx["routed"] == "ema_crossover"  # shipped row is a clean PROMOTE
    assert spx["validated"] is True


def test_regime_router_calls_the_evidence_gate(monkeypatch):
    """get_regime_aware_strategy must route through routing_evidence."""
    from mt5_mcp import multi_strategy as ms

    calls: list[tuple] = []

    def _recorder(symbol, candidate=None, **kwargs):
        calls.append((symbol, candidate, kwargs.get("allow_paper")))
        return "mean_reversion"

    monkeypatch.setattr(ms, "route_strategy", _recorder)
    out = ms.get_regime_aware_strategy("EURJPY", enable_llm_regime=False)
    assert out == "mean_reversion"
    assert calls and calls[0][0] == "EURJPY"


def test_real_atlas_never_routes_below_threshold():
    """Whatever the shipped atlas says, live routing stays threshold-clean."""
    for symbol in ("SPX500", "NDX100", "EURJPY", "USDCAD", "BTCUSD", "USOIL"):
        routed = ev.route(symbol)
        if routed == ev.DEFAULT_STRATEGY:
            continue
        res = ev.evidence(symbol, routed)
        assert res is not None, f"{symbol} routed to {routed} without evidence"
        assert res.get("verdict") == "PROMOTE"
        assert int(res.get("trades") or 0) >= ev.ROUTING_MIN_TRADES
        assert float(res.get("pf") or 0.0) >= ev.ROUTING_MIN_PF
