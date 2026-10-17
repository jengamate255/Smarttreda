"""Independent replay checks use synthetic candles, without terminal access."""
import numpy as np
import pandas as pd
import pytest
from types import SimpleNamespace
from scripts import backtest_entry_selection as replay
from scripts.verify_entry_backtest import verify_trade
from scripts.verify_entry_backtest import verify
import hashlib
import json


def rates(**changes):
    return {"time":1000,"open":100.,"high":101.,"low":99.,"close":100.,"spread":1.,"tick_volume":10.,**changes}


@pytest.mark.parametrize("changes",[{"high":98.},{"low":102.},{"close":np.nan},{"spread":-1.},{"open":0.}])
def test_invalid_market_history_cannot_supply_backtest_fills(changes):
    with pytest.raises(ValueError):
        replay.clean_rates([rates(**changes)])


def test_duplicate_history_fails_instead_of_selecting_arbitrary_bar():
    with pytest.raises(ValueError,match="Duplicate"):
        replay.clean_rates([rates(),rates(close=100.5)])


def saved(side="BUY",**changes):
    return {"side":side,"entry":100.1 if side=="BUY" else 100.,"sl":99. if side=="BUY" else 101.,
            "tp":103. if side=="BUY" else 97.,"decision_time":970,"entry_time":1000,"exit_time":1300,
            "h1_last_closed":900,"h4_last_closed":900,"net_r":-1.05,"ambiguous":0,**changes}


def frame(rows):
    data=pd.DataFrame(rows)
    data["time"]=pd.to_datetime(data.time,unit="s",utc=True)
    return data


@pytest.mark.parametrize("side",["BUY","SELL"])
def test_independent_verifier_recomputes_spread_stop_and_cost(side):
    row=rates(high=101. if side=="BUY" else 100.95,low=98.9 if side=="BUY" else 99.5)
    assert verify_trade(saved(side),frame([row]),.1,0,.05,72)==pytest.approx(-1.05)


def test_same_bar_stop_and_target_is_adverse_and_labelled_ambiguous():
    assert verify_trade(saved(ambiguous=1),frame([rates(high=104.,low=98.)]),.1,0,.05,72)==pytest.approx(-1.05)


def test_independent_verifier_accounts_for_gap_beyond_stop():
    rows=[rates(low=99.5),rates(time=1300,open=98.,high=98.5,low=97.,close=98.)]
    assert verify_trade(saved(entry=100.1,exit_time=1600,net_r=-2.1/1.1-.05),frame(rows),.1,0,.05,72)==pytest.approx(-2.1/1.1-.05)


def test_future_candles_cannot_verify_a_trade():
    with pytest.raises(AssertionError,match="Lookahead"):
        verify_trade(saved(h1_last_closed=1000),frame([rates(low=98.)]),.1,0,.05,72)


def test_wrong_published_pnl_is_detected_independently():
    with pytest.raises(AssertionError,match="Net R mismatch"):
        verify_trade(saved(net_r=2.95),frame([rates(low=98.)]),.1,0,.05,72)


def test_zero_trades_are_not_reported_as_profitable():
    assert replay.stats([])["profit_factor"] is None
    assert not replay.stats([])["no_losses"]


def test_matched_replay_changes_only_the_structural_entry_rule(monkeypatch):
    base=1760000400//1800*1800
    def history(times):
        return frame([rates(time=int(t),high=103.,low=99.5,spread=1.) for t in times])
    frames={"M5":history(base+np.arange(-120,110)*300),
            "H1":history(base+np.arange(-250,1)*3600),
            "H4":history(base+np.arange(-250,1)*14400)}
    policy=SimpleNamespace(risk=SimpleNamespace(allowed_hours_utc=[],min_risk_reward=2.),
        quality=SimpleNamespace(max_zone_distance_atr=.5,max_entry_drift_atr=.25,shadow_cost_r=.05,shadow_horizon_bars=12))
    monkeypatch.setattr(replay.entry_quality,"get_settings",lambda:policy)
    monkeypatch.setattr(replay.paper_learning,"get_settings",lambda:policy)
    monkeypatch.setattr(replay,"analyze_trend",lambda f:SimpleNamespace(direction="bullish"))
    monkeypatch.setattr(replay,"analyze_chart",lambda *a,**kw:{"patterns":[
        {"pattern":"double_bottom","direction":"bullish","status":"confirmed","ai_score":90,"target":101.5,"invalidation":99.9}]})
    monkeypatch.setattr(replay,"detect_zones",lambda *a,**kw:[SimpleNamespace(lower=99.9,upper=100.1,fresh=True,broken=False,type="demand")])
    monkeypatch.setattr(replay,"detect_patterns",lambda *a,**kw:[SimpleNamespace(direction="bullish",confidence=.8)])
    monkeypatch.setattr(replay,"atr",lambda f:pd.Series(np.ones(len(f))))
    original_check=replay.entry_quality.pattern_entry_check
    result=replay.compare_symbol("TEST",frames,.01,policy,base,base+30000,0,1.,2.)
    assert result["previous"]["full"]["trades"]==1
    assert result["updated"]["full"]["trades"]==0
    assert result["previous"]["full"]["net_r"]==pytest.approx(1.95)
    assert replay.entry_quality.pattern_entry_check is original_check


@pytest.mark.parametrize("seed",[1,5,19])
def test_fast_structure_is_exactly_equal_to_production_pivot_comparisons(seed):
    from mt5_mcp.strategy import market_structure
    rng=np.random.default_rng(seed)
    close=100+np.cumsum(rng.normal(size=400))
    candles=pd.DataFrame({"high":close+rng.uniform(0,2,400),"low":close-rng.uniform(0,2,400)})
    for length in (10,60,210,400):
        for left,right in ((3,3),(5,5)):
            prefix=candles.iloc[:length]
            assert replay.fast_market_structure(prefix,left,right)==market_structure(prefix,left,right)


def empty_result(directory):
    manifest={"symbols":["TEST"],"frames":{},"point":{"TEST":.1},"offset":0,"end":5000}
    for tf in ("M5","H1","H4"):
        path=directory/f"TEST_{tf}.parquet"
        data=frame([rates()])
        data.to_parquet(path,index=False)
        manifest["frames"][f"TEST:{tf}"]={"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"rows":1}
    report={"manifest":manifest,"failures":[],"results":[{"symbol":"TEST","comparison":{
        name:{"trades":[],"full":replay.stats([])} for name in ("previous","updated")}}],
        "aggregate":{name:{"full":replay.stats([])} for name in ("previous","updated")}}
    (directory/"results.json").write_text(json.dumps(report),encoding="utf-8")
    return report


def test_zero_fill_verification_reports_only_data_integrity(tmp_path):
    empty_result(tmp_path)
    result=verify(tmp_path)
    assert result["passed"] and result["verified_trades"]==0 and result["history_files_checked"]==3


def test_modified_history_cannot_pass_independent_verification(tmp_path):
    empty_result(tmp_path)
    frame([rates(high=102.)]).to_parquet(tmp_path/"TEST_M5.parquet",index=False)
    result=verify(tmp_path)
    assert not result["passed"] and any("hash differs" in error for error in result["errors"])


def test_missing_symbol_results_cannot_pass_independent_verification(tmp_path):
    report=empty_result(tmp_path)
    report["manifest"]["symbols"].append("OTHER")
    (tmp_path/"results.json").write_text(json.dumps(report),encoding="utf-8")
    assert not verify(tmp_path)["passed"]
