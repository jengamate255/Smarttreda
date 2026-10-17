"""Risk increases require verified completed outcomes; lot rounding cannot exceed budgets."""
from datetime import datetime, timezone
from types import SimpleNamespace
import time

import pytest

from mt5_mcp import config, performance_risk as policy


@pytest.fixture
def settings(monkeypatch):
    cfg = SimpleNamespace(performance_risk=config.PerformanceRiskConfig(enabled=True),
                          quality=SimpleNamespace(broker_time_offsets={}))
    monkeypatch.setattr(policy,"get_settings",lambda:cfg)
    policy._cache.clear()
    return cfg


def outcomes(values):
    return [{"net_pnl":v,"closed_at":i,"position":i} for i,v in enumerate(values)]


@pytest.mark.parametrize("values,expected",[([],0.1),([1]*19,0.1),([-1]*20,0.1),([1]*20,1),
    ([1,-1]*10,0.1),([2]*17+[-1]*3,0.1),([-100]*30+[1]*20,1)])
def test_rolling_switch_requires_positive_recent_evidence(settings,values,expected):
    assert policy.decide(outcomes(values),settings.performance_risk)["risk_percent"] == expected


def test_one_win_does_not_override_a_losing_window(settings):
    assert policy.decide(outcomes([-2]*19+[1]),settings.performance_risk)["risk_percent"] == 0.1


def test_last_trade_option_uses_net_outcome(settings):
    cfg = settings.performance_risk
    cfg.mode = "last_trade"
    assert policy.decide(outcomes([-10,1]),cfg)["risk_percent"] == 1
    assert policy.decide(outcomes([10,-1]),cfg)["risk_percent"] == 0.1
    assert policy.decide(outcomes([0]),cfg)["risk_percent"] == 0.1


def deal(ticket,position,entry,profit=0,volume=1,**changes):
    return {"ticket":ticket,"position_id":position,"entry":entry,"type":0,"profit":profit,"volume":volume,
            "time":int(time.time())-100,"commission":0,"swap":0,"fee":0,"magic":0,
            "comment":"desk-test",**changes}


def test_costs_and_partial_legs_form_one_completed_outcome(settings):
    deals = [deal(1,10,0,commission=-2),deal(2,10,1,profit=5,volume=.4,fee=-1),
             deal(3,10,1,profit=6,volume=.6,swap=-2)]
    rows = policy.completed_outcomes(deals+[deals[0]],set(),0,time.time())
    assert len(rows) == 1 and rows[0]["net_pnl"] == 6
    assert policy.completed_outcomes(deals,{10},0,time.time()) == []
    assert policy.completed_outcomes(deals[:2],set(),0,time.time()) == []


def test_manual_and_incomplete_history_cannot_increase_risk(settings):
    manual = [deal(1,10,0,comment=""),deal(2,10,1,profit=100,comment="")]
    assert policy.completed_outcomes(manual,set(),0,time.time()) == []
    assert policy.completed_outcomes([deal(2,10,1,profit=100)],set(),0,time.time()) == []


def test_reversals_and_bad_clock_are_not_winning_evidence(settings):
    assert policy.completed_outcomes([deal(1,10,0),deal(2,10,2,profit=100)],set(),0,time.time()) == []
    with pytest.raises(ValueError,match="timestamps"):
        policy.completed_outcomes([deal(1,10,0),deal(2,10,1,profit=100,time=int(time.time())+10800)],set(),0,time.time())


class Client:
    def __init__(self,login,values):
        self.login,self.balance = login,10000
        self.deals = [d for i,v in enumerate(values) for d in (deal(i*2+1,i+1,0),deal(i*2+2,i+1,1,profit=v))]
    def account_info(self):
        return {"login":self.login,"server":"TEST","equity":10000,"balance":self.balance}
    def deals_since(self,_):
        return self.deals
    def positions(self):
        return []


def test_account_identity_is_part_of_the_cache_key(settings):
    assert policy.snapshot(Client(1,[1]*20))["risk_percent"] == 1
    assert policy.snapshot(Client(2,[-1]*20))["risk_percent"] == .1


def test_missing_evidence_defaults_to_low_risk(settings):
    assert policy.snapshot(object())["risk_percent"] == .1
    client = Client(1,[1]*20)
    client.deals = None
    assert policy.snapshot(client)["risk_percent"] == .1


def test_balance_change_during_collection_cannot_raise_risk(settings):
    client = Client(1,[1]*20)
    def collect(_):
        client.balance -= 100
        return client.deals
    client.deals_since = collect
    assert policy.snapshot(client)["risk_percent"] == .1


def test_budget_never_exceeds_current_account_fraction(settings):
    assert policy.limit_budget(Client(1,[-1]*20),90) == 10
    assert policy.limit_budget(Client(2,[1]*20),500) == 100
    assert policy.limit_budget(Client(2,[1]*20),5) == 5


def test_pipeline_rounds_down_and_skips_minimum_lot_oversizing(settings,monkeypatch):
    from mt5_mcp import pipeline
    client = Client(1,[-1]*20)
    client.symbol_info = lambda _: {"trade_tick_value":1,"trade_tick_size":1,"volume_step":.001,
        "volume_min":.001,"volume_max":100,"point":1,"spread":0}
    assert pipeline.size_position("TEST",1100,90,client=client) == pytest.approx(.009)
    client.symbol_info = lambda _: {"trade_tick_value":1,"trade_tick_size":1,"volume_step":.01,
        "volume_min":.01,"volume_max":100,"point":1,"spread":0}
    assert pipeline.size_position("TEST",1100,90,client=client) is None
