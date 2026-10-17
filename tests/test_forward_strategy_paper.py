from scripts.forward_strategy_paper import exit_crossing, sizing, Feed, entry_cursor
import pytest

def pos(**kw):
    return {'side':'BUY','sl':95.,'tp':110.,'last_tick_msc':1000,**kw}

def tick(stamp,bid,ask): return {'time_msc':stamp,'bid':bid,'ask':ask}

def test_old_ticks_cannot_close_new_entry():
    assert exit_crossing(pos(),[tick(999,90,91),tick(1000,90,91)]) is None

def test_recent_quote_never_allows_exit_before_decision_time():
    assert entry_cursor(10999000,200,10800)==11000000

def test_stop_gap_uses_adverse_quote():
    assert exit_crossing(pos(),[tick(1001,92,93)])=={'price':92.,'reason':'stop','time_msc':1001}

def test_target_does_not_credit_favorable_gap():
    assert exit_crossing(pos(),[tick(1001,114,115)])['price']==110.

def test_sell_stop_uses_ask():
    result=exit_crossing(pos(side='SELL',sl=105.,tp=90.),[tick(1001,104,106)])
    assert result['price']==106. and result['reason']=='stop'

def test_first_crossing_wins_and_invalid_quotes_skipped():
    result=exit_crossing(pos(),[tick(1001,90,0),tick(1002,111,112),tick(1003,90,91)])
    assert result['reason']=='target' and result['time_msc']==1002

def test_position_size_never_rounds_up_past_budget():
    info={'volume_min':.01,'volume_max':100.,'volume_step':.01}
    assert sizing(9.39,1000,info)==0
    assert sizing(19.99,1000,info)==.01
    assert sizing(20,1000,info)==.02

def test_feed_exposes_no_order_methods():
    feed=Feed(None,{'starting_balance':10000},None)
    with pytest.raises(AttributeError): feed.order_send({})

def test_stale_quote_cannot_create_paper_entry(monkeypatch):
    from types import SimpleNamespace
    from scripts import forward_strategy_paper as module
    monkeypatch.setattr(module.time,'time',lambda:1000)
    feed=Feed(SimpleNamespace(symbol_info_tick=lambda symbol:SimpleNamespace(_asdict=lambda:{'time':100,'bid':1.,'ask':1.1})),{'starting_balance':10000,'offset':0},None)
    with pytest.raises(ValueError,match='Stale'): feed.tick('EURUSD')
