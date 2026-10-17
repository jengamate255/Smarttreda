"""Selection regressions using synthetic evidence; no terminal or order calls."""
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from mt5_mcp import entry_quality


@pytest.fixture
def desk(monkeypatch):
    import trade_desk

    cfg = SimpleNamespace(quality=SimpleNamespace(enabled=True),
                          risk=SimpleNamespace(min_risk_reward=2.0))
    monkeypatch.setattr(entry_quality, "get_settings", lambda: cfg)
    monkeypatch.setattr(trade_desk, "DESK_MIN_CONFLUENCE", 75)
    monkeypatch.setattr(trade_desk, "DESK_MIN_HEALTH", 55)
    monkeypatch.setattr(trade_desk, "DESK_SL_ATR_MULT", 3)
    monkeypatch.setattr(trade_desk, "DESK_TP_R_MULT", 3)
    monkeypatch.setattr(trade_desk, "MIN_SL_FLOOR", {})
    monkeypatch.setattr(trade_desk, "CORRELATION_GROUPS", {})
    monkeypatch.setattr(trade_desk, "_frame", lambda *a, **kw: pd.DataFrame({"close": [100.] * 70}))
    monkeypatch.setattr(trade_desk, "adjust_pattern_score", lambda pattern, score, **kw: (score, 1.0))
    fake = SimpleNamespace(symbol_info=lambda symbol: {"spread": 0, "point": .01},
                           tick=lambda symbol: {"bid": 100., "ask": 100.01})
    monkeypatch.setattr(trade_desk, "get_client", lambda: fake)
    trade_desk._test_settings = cfg
    yield trade_desk
    del trade_desk._test_settings


def pattern(**updates):
    return {"pattern": "double_bottom", "direction": "bullish", "status": "confirmed",
            "ai_score": 85, "target": 110., "invalidation": 99., **updates}


def candidate(**updates):
    return {"symbol": "TEST", "direction": "BUY", "confluence": 90., "health": 90.,
            "price": 100., "entry_bid": 100., "entry_ask": 100.,
            "analysts": {"atr": 1., "patterns": pattern(direction="BUY"),
                         "zones": {"direction": "BUY", "fresh": True, "broken": False},
                         "trend": {"mtf": [{"tf": "H1", "direction": "BUY"},
                                           {"tf": "H4", "direction": "BUY"}]}}, **updates}


@pytest.mark.parametrize("changes", [
    {"status": "emerging"}, {"target": 99.}, {"invalidation": 101.},
    {"target": 100.005}, {"target": None},
])
def test_pattern_ranking_excludes_unexecutable_evidence(desk, monkeypatch, changes):
    monkeypatch.setattr(desk, "analyze_chart", lambda *a, **kw: {"patterns": [pattern(**changes)]})
    result = desk.analyst_patterns(desk.get_client(), "TEST")
    assert result["direction"] is None and result["score"] == 0


def test_learned_scores_choose_the_best_remaining_pattern(desk, monkeypatch):
    choices = [pattern(pattern="double_bottom", ai_score=90),
               pattern(pattern="rectangle_breakout", ai_score=75)]
    monkeypatch.setattr(desk, "analyze_chart", lambda *a, **kw: {"patterns": choices})
    monkeypatch.setattr(desk, "adjust_pattern_score",
                        lambda name, score, **kw: (score * .5, .5) if name == "double_bottom" else (score, 1.))
    result = desk.analyst_patterns(desk.get_client(), "TEST")
    assert result["pattern"] == "rectangle_breakout"
    assert result["raw_score"] == pytest.approx(75 * 1.15)
    assert result["score"] == result["raw_score"] and result["learned_factor"] == 1


def test_sell_pattern_uses_bid_when_ask_is_still_below_invalidation(desk, monkeypatch):
    # The sell is exhausted at bid, although ask remains above its target.
    monkeypatch.setattr(desk, "analyze_chart", lambda *a, **kw: {
        "patterns": [pattern(direction="bearish", target=100., invalidation=101.)]})
    assert desk.analyst_patterns(desk.get_client(), "TEST")["direction"] is None


@pytest.mark.parametrize("bad", [{"broken": True}, {"fresh": False}])
def test_zone_ranking_discards_unusable_high_scores(desk, monkeypatch, bad):
    unusable = SimpleNamespace(lower=99., upper=101., type="demand", strength=100., fresh=True, broken=False)
    for key, value in bad.items():
        setattr(unusable, key, value)
    usable = SimpleNamespace(lower=99.5, upper=100.5, type="demand", strength=60., fresh=True, broken=False)
    monkeypatch.setattr(desk, "detect_zones", lambda *a, **kw: [unusable, usable])
    result = desk.analyst_zones(desk.get_client(), "TEST")
    assert result["zone_lower"] == 99.5 and result["fresh"] and not result["broken"]


@pytest.mark.parametrize("component,changes", [
    ("patterns", {"direction": "SELL"}), ("patterns", {"status": "emerging"}),
    ("zones", {"direction": "SELL"}), ("zones", {"broken": True}),
    ("zones", {"fresh": False}),
    ("trend", {"mtf": [{"tf": "H1", "direction": "BUY"}, {"tf": "H4", "direction": "SELL"}]}),
])
def test_portfolio_requires_the_selected_evidence_to_agree(desk, component, changes):
    entry = candidate()
    assert desk.portfolio_select([entry], {}) == [entry]
    entry["analysts"][component].update(changes)
    assert desk.portfolio_select([entry], {}) == []


def test_opposing_analyst_cannot_increase_conviction(desk):
    analyses = {"health": {"score": 100}, "patterns": {"score": 100, "direction": "BUY"},
                "zones": {"score": 0, "direction": "SELL"},
                "trend": {"score": 100, "direction": "BUY"},
                "momentum": {"score": 100, "direction": "BUY"}}
    baseline = desk.confluence(analyses)
    analyses["zones"]["score"] = 100
    conflicted = desk.confluence(analyses)
    assert baseline["direction"] == conflicted["direction"] == "BUY"
    assert conflicted["conviction"] < baseline["conviction"]
    assert conflicted["conviction"] < desk.DESK_MIN_CONFLUENCE


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_levels_cap_reward_to_structure_without_shrinking_stop(desk, side):
    entry = candidate(direction=side)
    entry["analysts"]["patterns"].update(direction=side, target=107. if side == "BUY" else 93.,
                                          invalidation=99. if side == "BUY" else 101.)
    levels = desk._plan_levels(entry)
    assert levels["tp"] == (107. if side == "BUY" else 93.)
    assert abs(levels["entry"] - levels["sl"]) == 3.
    assert abs(levels["tp"] - levels["entry"]) / abs(levels["entry"] - levels["sl"]) >= 2


def test_capping_target_cannot_weaken_required_rr(desk):
    entry = candidate()
    entry["analysts"]["patterns"]["target"] = 105.
    assert desk._plan_levels(entry) is None


@pytest.mark.parametrize("changes", [{"target": 99.}, {"invalidation": 101.}, {"target": None}])
def test_levels_reject_exhausted_invalidated_or_unknown_structure(desk, changes):
    entry = candidate()
    entry["analysts"]["patterns"].update(changes)
    assert desk._plan_levels(entry) is None


def test_levels_keep_wider_structural_stop(desk):
    entry = candidate()
    entry["analysts"]["patterns"].update(invalidation=95., target=112.)
    levels = desk._plan_levels(entry)
    assert levels["sl"] == 95. and levels["tp"] == 112.


def test_quality_requires_measured_atr(desk):
    entry = candidate()
    entry["analysts"]["atr"] = 0
    assert desk._plan_levels(entry) is None


def test_legacy_mode_retains_pattern_fallback_and_score(desk, monkeypatch):
    desk._test_settings.quality.enabled = False
    monkeypatch.setattr(desk, "analyze_chart", lambda *a, **kw: {"patterns": [pattern(status="emerging")]})
    assert desk.analyst_patterns(desk.get_client(), "TEST")["pattern"] == "double_bottom"
    entry = candidate()
    entry["analysts"]["patterns"].update(target=99.)
    assert desk._plan_levels(entry)["tp"] == 109.
    analyses = {name: {"score": 100., "direction": "SELL" if name == "zones" else "BUY"}
                for name in desk.WEIGHTS}
    analyses["health"].pop("direction")
    assert desk.confluence(analyses)["conviction"] == 88.2


def test_selection_records_explicit_rejection_reasons(desk):
    entry = candidate()
    entry["analysts"]["zones"]["direction"] = "SELL"
    desk.portfolio_select([entry], {})
    assert not entry["selection"]["eligible"] and not entry["selection"]["picked"]
    assert "selected zone direction disagrees with entry" in entry["selection"]["reasons"]


def test_correlation_ranking_is_distinct_from_rejected_evidence(desk, monkeypatch):
    monkeypatch.setattr(desk, "CORRELATION_GROUPS", {"TEST":"same", "OTHER":"same"})
    stronger, weaker = candidate(), candidate(symbol="OTHER",confluence=80.)
    assert desk.portfolio_select([weaker,stronger], {}) == [stronger]
    assert weaker["selection"]["eligible"] and not weaker["selection"]["picked"]
    assert weaker["selection"]["reasons"] == ["lower-ranked candidate in same correlation group"]


def test_unplannable_candidate_cannot_take_a_portfolio_slot(desk):
    entry = candidate(entry_plan=None)
    assert desk.portfolio_select([entry], {}) == []
    assert "no measured stop/target plan supports required RR" in entry["selection"]["reasons"]


def test_selection_snapshot_preserves_evidence_without_extra_fields(desk):
    entry = candidate(entry_plan={"entry":100.,"sl":97.,"tp":109.,"atr":1.})
    entry["analysts"]["patterns"]["secret"] = "private"
    desk.portfolio_select([entry], {})
    snapshot = desk.selection_snapshot([entry])
    row = snapshot["candidates"][0]
    assert snapshot["schema"] == 1 and snapshot["quality_enabled"]
    assert row["selection"]["picked"] and row["entry_plan"]["sl"] == 97
    assert row["analysts"]["patterns"]["target"] == 110.
    assert "private" not in str(snapshot)
