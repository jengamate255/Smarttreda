"""Daily reset, full-account costs and stop/pending loss-floor reservations."""
from types import SimpleNamespace
import pytest
from mt5_mcp import config, prop_risk

NOW = 1760000000
DAY = int((NOW+10800)//86400)*86400-10800


def deal(ticket,position,entry,volume=1,profit=0,**kw):
    return {"ticket":ticket,"position_id":position,"type":0,"entry":entry,"volume":volume,
            "profit":profit,"time":DAY+10800+100,"commission":0,**kw}


class Client:
    def __init__(self):
        self.account = {"login":123,"server":"TEST","balance":10000,"equity":10000}
        self.open,self.orders,self.history = [],[],[]
    def account_info(self): return dict(self.account)
    def positions(self): return self.open
    def pending_orders(self): return self.orders
    def deals_since(self,_): return self.history
    def account_deals_since(self,_):
        funding = {"ticket":999,"type":2,"profit":10000,"time":DAY+10800-86400}
        return [funding, *self.history] if _ == 0 and self.history is not None else self.history
    def symbol_info(self,_):
        return {"trade_tick_size":1,"trade_tick_value_loss":1,"point":1,"spread":0}


@pytest.fixture
def setup(monkeypatch,tmp_path):
    cfg = SimpleNamespace(prop_risk=config.PropRiskConfig(enabled=True,account_login=123,server="TEST",initial_balance=10000),
                          risk=config.RiskConfig(max_daily_loss_percent=4,max_drawdown_percent=8),
                          quality=SimpleNamespace(broker_time_offsets={"TEST":10800}))
    monkeypatch.setattr(prop_risk,"get_settings",lambda:cfg)
    monkeypatch.setattr(prop_risk,"ADMISSION_PATH",tmp_path/"admissions.db")
    return cfg,Client(),tmp_path/"prop.json"


def check(setup):
    _,client,path = setup
    return prop_risk.snapshot(client,state_path=path,now=NOW)


def test_static_initial_floor_and_daily_reset(setup):
    verdict = check(setup)
    assert verdict["daily_floor"] == 9600 and verdict["overall_floor"] == 9200
    assert verdict["new_trade_budget"] == 390
    assert verdict["reset_utc"].endswith("21:00:00+00:00")


def test_entry_and_exit_commissions_reduce_daily_buffer(setup):
    _,client,_ = setup
    client.account.update(balance=9800,equity=9800)
    client.history = [deal(1,10,0,commission=-2),deal(2,10,1,profit=-196,commission=-2)]
    verdict = check(setup)
    assert verdict["opening_baseline"] == 10000
    assert verdict["new_trade_budget"] == 190


def test_pending_order_reserves_budget_before_filling(setup):
    _,client,_ = setup
    check(setup)  # verify the flat reset before reserving pending entry risk
    client.orders = [{"symbol":"X","type":4,"price_open":1000,"sl":800,"volume_current":1}]
    assert check(setup)["new_trade_budget"] == 190
    client.orders[0]["sl"] = 0
    assert check(setup)["new_trade_budget"] == 0


def test_stop_risk_is_incremental_from_mark_not_double_counted():
    info = {"trade_tick_size":1,"trade_tick_value_loss":1,"point":1,"spread":0}
    assert prop_risk.stop_risk(info,900,800,1,0) == 100
    assert prop_risk.stop_risk(info,900,800,1,1) == 0


def test_unknown_overnight_baseline_cannot_allow_entries(setup):
    _,client,_ = setup
    client.history = [deal(2,10,1,profit=-100)]
    assert check(setup)["state"] == "unavailable"
    client.history = []
    client.open = [{"ticket":10,"symbol":"X","type":0,"volume":1,"time":DAY+10800-100,
                    "price_current":1000,"sl":900}]
    assert check(setup)["new_trade_budget"] == 0


def test_verified_day_baseline_is_preserved_but_never_reused_after_reset(setup):
    _,client,path = setup
    check(setup)
    client.open = [{"ticket":10,"symbol":"X","type":0,"volume":1,"time":DAY+10800+100,
                    "price_current":1000,"sl":900}]
    assert check(setup)["new_trade_budget"] == 290
    assert prop_risk.snapshot(client,state_path=path,now=NOW+86400)["new_trade_budget"] == 0


def test_account_switch_or_unverified_initial_balance_is_closed(setup):
    cfg,client,_ = setup
    client.account["login"] = 999
    assert check(setup)["new_trade_budget"] == 0
    client.account["login"] = 123
    cfg.prop_risk.initial_balance = None
    assert check(setup)["new_trade_budget"] == 0


def test_loss_floor_uses_lower_of_balance_and_equity(setup):
    _,client,_ = setup
    check(setup)
    client.account.update(balance=9500,equity=10100)
    assert check(setup)["new_trade_budget"] == 0


def test_missing_exposure_or_cash_transfer_is_closed(setup):
    _,client,_ = setup
    client.orders = None
    assert check(setup)["new_trade_budget"] == 0
    client.orders = []
    client.history = [deal(1,0,0,type=2,profit=500)]
    assert check(setup)["new_trade_budget"] == 0


def test_concurrent_admissions_and_uncertain_send_are_durable(setup):
    _,client,_ = setup
    import threading
    entered,release = threading.Event(),threading.Event()

    @prop_risk.serialized_admission
    def send(client,request):
        prop_risk.mark_sending()
        entered.set()
        assert release.wait(5)
        prop_risk.record_result({"retcode":10009})
        return {"retcode":10009}

    worker = threading.Thread(target=send,args=(client,{"action":1}))
    worker.start()
    assert entered.wait(5)
    try:
        with pytest.raises(ValueError,match="reconciliation"):
            send(client,{"action":1})
        assert check(setup)["new_trade_budget"] == 0
    finally:
        release.set()
        worker.join(5)
    assert check(setup)["new_trade_budget"] == 390

    @prop_risk.serialized_admission
    def timeout(client,request):
        prop_risk.mark_sending()
        raise TimeoutError("Unknown execution outcome")

    with pytest.raises(TimeoutError):
        timeout(client,{"action":1})
    assert check(setup)["new_trade_budget"] == 0
    with pytest.raises(ValueError,match="reconciliation"):
        send(client,{"action":1})


def test_gate_rejection_releases_admission_and_exits_stay_executable(setup):
    _,client,_ = setup
    @prop_risk.serialized_admission
    def rejected(client,request):
        raise ValueError("Entry rule failed before send")
    with pytest.raises(ValueError,match="Entry rule"):
        rejected(client,{"action":1})
    assert check(setup)["new_trade_budget"] == 390
    with prop_risk._ledger() as conn:
        conn.execute("INSERT INTO admissions VALUES ('123:TEST','old','uncertain')")
    @prop_risk.serialized_admission
    def exit_order(client,request): return "closed"
    assert exit_order(client,{"action":1,"position":10}) == "closed"
    assert exit_order(client,{"action":8,"order":10}) == "closed"


@pytest.mark.parametrize("side,old_stop,tighter,wider",[(0,900,950,850),(1,1100,1050,1150)])
def test_stop_tightening_allowed_widening_and_removal_blocked(setup,side,old_stop,tighter,wider):
    _,client,_ = setup
    client.open = [{"ticket":10,"type":side,"sl":old_stop}]
    assert prop_risk.modification_violations({"position":10,"sl":tighter},client) == []
    assert prop_risk.modification_violations({"position":10,"tp":1200},client) == []
    assert prop_risk.modification_violations({"position":10,"sl":wider},client)
    assert prop_risk.modification_violations({"position":10,"sl":0},client)


def test_corrupt_daily_state_fails_closed(setup):
    _,_,path = setup
    path.write_text("{broken",encoding="utf-8")
    assert check(setup)["new_trade_budget"] == 0


def test_strategy_lots_cannot_round_up_above_budget():
    from mt5_mcp.strategy import _calc_lots
    info = {"volume_min":.01,"volume_step":.01,"volume_max":100}
    lots,_,_ = _calc_lots("TEST",10000,.1,1100,1,info)
    assert lots is None
    info.update(volume_min=.001,volume_step=.001)
    lots,_,_ = _calc_lots("TEST",10000,.1,1100,1,info)
    assert lots == pytest.approx(.009)


def test_desk_levels_use_saved_executable_quote(monkeypatch):
    import trade_desk as desk
    monkeypatch.setattr(desk,"get_client",lambda:SimpleNamespace(symbol_info=lambda _:{}))
    for side,expected in (("BUY",102),("SELL",100)):
        levels = desk._plan_levels({"symbol":"TEST","direction":side,"price":100,"entry_bid":100,"entry_ask":102,
                                   "analysts":{"atr":1,"patterns":{"direction":side,"status":"confirmed",
                                       "target":150 if side == "BUY" else 50,
                                       "invalidation":99 if side == "BUY" else 103}}})
        assert levels["entry"] == expected
        assert abs(levels["tp"]-expected)/abs(expected-levels["sl"]) == pytest.approx(desk.DESK_TP_R_MULT)


def test_raw_fallback_uses_admission_latch(setup,monkeypatch):
    from mt5_mcp import execution_gate
    monkeypatch.setattr(execution_gate,"check",lambda *a,**kw:[])
    raw = SimpleNamespace(order_send=lambda request:{"retcode":10009})
    assert execution_gate.send_raw(raw,{"action":1})["retcode"] == 10009
    assert check(setup)["new_trade_budget"] == 390
    raw.order_send = lambda request:None
    assert execution_gate.send_raw(raw,{"action":1}) is None
    assert check(setup)["new_trade_budget"] == 0


def test_recovery_waits_for_flat_even_for_intraday_positions(setup):
    _,client,path = setup
    client.history = [deal(1,10,0)]
    client.open = [{"ticket":10,"time":DAY+10800+100,"volume":1}]
    verdict = check(setup)
    assert verdict["entry_admission"] == "paused"
    assert verdict["recovery_status"] == "waiting_for_flat"
    assert not path.exists()
    client.open = []
    client.account.update(balance=9890,equity=12345)
    client.history += [deal(2,10,1,profit=-100,commission=-5,swap=-3,fee=-2)]
    verdict = check(setup)
    assert verdict["opening_baseline"] == 10000  # never the supplied equity
    assert verdict["baseline_source"] == "verified_flat_broker_history"
    assert verdict["new_trade_budget"] == 280
    import json
    assert json.loads(path.read_text())["history_deals"] == 3


def test_closed_overnight_still_needs_later_flat_reset(setup):
    _,client,path = setup
    client.account.update(balance=9900,equity=9900)
    client.history = [deal(1,10,0,time=DAY+10800-100),deal(2,10,1,profit=-100)]
    verdict = check(setup)
    assert verdict["recovery_status"] == "waiting_for_flat_reset"
    assert "overnight" in verdict["reason"].lower()
    assert not path.exists()
    verdict = prop_risk.snapshot(client,state_path=path,now=NOW+86400)
    assert verdict["opening_baseline"] == 9900
    assert verdict["available"]


@pytest.mark.parametrize("history,balance",[
    ([deal(2,10,1,profit=-100)],9900),  # missing prior entry
    ([deal(1,10,0),deal(2,10,1,volume=.5)],10000),  # omitted partial exit
    ([deal(1,10,0),deal(2,10,1,profit=-100)],9950),  # balance cannot reconcile
    ([deal(1,10,2)],10000),  # reversal
    ([deal(1,10,0),deal(1,10,1)],10000),  # conflicting duplicate
    ([deal(1,0,0,type=4)],10000),  # unsupported cash operation
    ([deal(1,10,0,time=NOW+10800+100)],10000),  # future stamp
    (None,10000),
])
def test_incomplete_or_ambiguous_broker_history_never_writes_baseline(setup,history,balance):
    _,client,path = setup
    client.history = history
    client.account.update(balance=balance,equity=balance)
    assert check(setup)["new_trade_budget"] == 0
    assert not path.exists()


def test_missing_funding_anchor_is_not_complete_history(setup):
    _,client,path = setup
    client.account_deals_since = lambda _: []
    assert "initial funding" in check(setup)["reason"]
    assert not path.exists()


def test_identical_deal_duplicates_do_not_double_count_costs(setup):
    _,client,_ = setup
    client.history = [deal(1,10,0),deal(2,10,1,profit=-100),deal(2,10,1,profit=-100)]
    client.account.update(balance=9900,equity=9900)
    assert check(setup)["opening_baseline"] == 10000


@pytest.mark.parametrize("change",["balance","identity","positions","pending"])
def test_collection_race_cannot_initialize_baseline(setup,change):
    _,client,path = setup
    original = client.account_deals_since
    def changed(start):
        rows = original(start)
        if change == "balance": client.account["balance"] -= 10
        if change == "identity": client.account["login"] = 999
        if change == "positions": client.open = [{"ticket":10}]
        if change == "pending": client.orders = [{"ticket":10}]
        return rows
    client.account_deals_since = changed
    assert check(setup)["new_trade_budget"] == 0
    assert not path.exists()


def test_reset_during_collection_does_not_write_stale_baseline(setup,monkeypatch):
    _,client,path = setup
    moments = iter([DAY+86399,DAY+86401])
    monkeypatch.setattr(prop_risk.time,"time",lambda:next(moments))
    verdict = prop_risk.snapshot(client,state_path=path)
    assert "Reset changed" in verdict["reason"]
    assert not path.exists()


def test_pending_entry_prevents_recovery_but_existing_baseline_reserves_it(setup):
    _,client,path = setup
    client.orders = [{"symbol":"X","type":4,"price_open":1000,"sl":800,"volume_current":1}]
    assert check(setup)["recovery_status"] == "waiting_for_flat"
    assert not path.exists()
    client.orders = []
    assert check(setup)["available"]
    client.orders = [{"symbol":"X","type":4,"price_open":1000,"sl":800,"volume_current":1}]
    assert check(setup)["new_trade_budget"] == 190
