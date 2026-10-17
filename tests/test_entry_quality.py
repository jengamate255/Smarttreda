"""Strict entry controls and prospective paper requalification never send orders."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import time

import pandas as pd
import pytest

from mt5_mcp import config, entry_quality as quality, paper_learning as paper


def test_quality_profile_loads_and_cannot_be_disabled_by_log_mode_env(tmp_path,monkeypatch):
    (tmp_path/"settings.json").write_text(json.dumps({"risk":{"reject_orders":False},"quality":{"enabled":True}}))
    monkeypatch.setattr(config,"BASE_DIR",tmp_path)
    monkeypatch.setenv("MT5_MCP_REJECT_ORDERS","false")
    config.get_settings.cache_clear()
    try:
        cfg = config.get_settings()
        assert cfg.quality.enabled and cfg.risk.reject_orders
    finally:
        config.get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path, monkeypatch):
    cfg = SimpleNamespace(quality=config.QualityConfig(enabled=True, shadow_db_path=str(tmp_path / "paper.db")),
                          risk=SimpleNamespace(min_risk_reward=2, blocked_symbols=[], allowed_hours_utc=[], blocked_setup_prefixes=[]),
                          memory=SimpleNamespace(db_path=tmp_path / "journal.db"))
    for module in (config,quality,paper):
        monkeypatch.setattr(module,"get_settings",lambda:cfg)
    return cfg


def context():
    current = int(time.time()) // 300 * 300
    return {"symbol":"TEST","side":"BUY","trends":{"H1":"BUY","H4":"BUY"},
            "patterns":[{"pattern":"double_bottom","status":"confirmed","direction":"bullish",
                         "target":104,"invalidation":99}],"confirmation":True,
            "zones":[{"side":"BUY","fresh":True,"broken":False,"lower":99.8,"upper":100.2}],
            "atr":1.0,"signal_bar":current-300,"next_bar":current,"timeframe":"M5"}


def request():
    return {"symbol":"TEST","type":0,"action":1,"price":100.01,"sl":99,"tp":103,
            "comment":"desk-double_bottom","magic":1,"volume":0.1}


def tick():
    return {"bid":100,"ask":100.01,"time":int(time.time())}


def test_closed_frame_excludes_forming_candle(settings):
    frame = pd.DataFrame({"close":[100,101,999]})
    assert quality.closed_frame(frame)["close"].tolist() == [100,101]
    assert len(frame) == 3


@pytest.mark.parametrize("change,reason", [
    ({"confirmation":False},"confirmation"),({"patterns":[]},"confirmed"),
    ({"trends":{"H1":"BUY","H4":"SELL"}},"H1/H4"),
    ({"zones":[{"side":"BUY","fresh":False,"broken":False,"lower":99,"upper":101}]},"fresh"),
])
def test_every_critical_condition_is_required(settings,change,reason):
    result = quality.evaluate(request(),{**context(),**change},tick())
    assert not result["allowed"] and reason in ";".join(result["violations"])


def test_good_context_passes_and_drift_is_rechecked(settings):
    assert quality.evaluate(request(),context(),tick())["allowed"]
    result = quality.evaluate({**request(),"price":98},context(),tick())
    assert not result["allowed"] and "ATR drift" in ";".join(result["violations"])


def test_clock_offset_does_not_hide_stale_quotes(settings):
    ctx = {**context(),"broker_time_offset_seconds":10800,"next_bar":context()["next_bar"]+10800}
    quote = {**tick(),"time":int(time.time())+10800}
    assert quality.evaluate(request(),ctx,quote)["allowed"]
    with pytest.raises(ValueError,match="fresh quote"):
        quality.evaluate(request(),ctx,{**quote,"time":quote["time"]-3600})


def test_current_reward_to_risk_and_stop_geometry(settings):
    assert not quality.evaluate({**request(),"tp":101},context(),tick())["allowed"]
    assert not quality.evaluate({**request(),"sl":102},context(),tick())["allowed"]


@pytest.mark.parametrize("side,entry,sl,tp,target,invalidation",[
    ("BUY",100,99,102,104,98), ("SELL",100,101,98,96,102),
])
def test_pattern_reward_is_directional_and_supports_planned_trade(settings,side,entry,sl,tp,target,invalidation):
    pattern = {"direction":side,"status":"confirmed","target":target,"invalidation":invalidation}
    result = quality.pattern_entry_check(pattern,side,entry,sl=sl,tp=tp)
    assert result["allowed"] and result["remaining_rr"] == 4
    past_target = target + (1 if side == "BUY" else -1)
    assert not quality.pattern_entry_check(pattern,side,past_target)["allowed"]
    assert not quality.pattern_entry_check(pattern,side,target)["allowed"]
    assert not quality.pattern_entry_check(pattern,side,invalidation)["allowed"]
    widened_stop = entry + (-3 if side == "BUY" else 3)
    assert "remaining pattern RR" in ";".join(quality.pattern_entry_check(pattern,side,entry,sl=widened_stop)["reasons"])
    assert "beyond pattern target" in ";".join(quality.pattern_entry_check(pattern,side,entry,sl=sl,tp=past_target)["reasons"])


@pytest.mark.parametrize("change",[
    {"target":None},{"invalidation":float("nan")},{"target":float("inf")},
    {"target":-1},{"direction":"bearish"},{"status":"emerging"},
])
def test_unusable_pattern_cannot_become_entry_evidence(settings,change):
    pattern = {**context()["patterns"][0],**change}
    assert not quality.pattern_entry_check(pattern,"BUY",100,sl=99,tp=103)["allowed"]


def test_fresh_ask_crossing_target_cannot_be_rescued_by_generated_tp(settings):
    ctx = context()
    ctx["patterns"][0]["target"] = 100.005
    result = quality.evaluate(request(),ctx,tick())
    assert not result["allowed"]
    assert "target already reached" in ";".join(result["violations"])


def test_other_pattern_cannot_rescue_expired_selected_setup(settings):
    ctx = context()
    ctx["patterns"][0]["target"] = 99.9
    ctx["patterns"].append({"pattern":"bull_flag","direction":"bullish","status":"confirmed","target":110,"invalidation":98})
    result = quality.evaluate(request(),ctx,tick())
    assert not result["allowed"] and len(result["pattern_checks"]) == 1


def test_remaining_structural_reward_uses_actual_stop_not_pattern_invalidation(settings):
    ctx = context()
    # Still before the target, with a mathematically valid generated 3R target.
    ctx["patterns"][0]["target"] = 101
    result = quality.evaluate(request(),ctx,tick())
    assert not result["allowed"] and "remaining pattern RR" in ";".join(result["violations"])


def test_pending_entry_is_checked_at_its_trigger_price(settings):
    ctx = context()
    ctx["patterns"][0]["target"] = 104
    result = quality.evaluate({**request(),"action":5,"type":4,"price":105,"sl":104,"tp":108},ctx,tick())
    assert not result["allowed"] and "target already reached" in ";".join(result["violations"])


def test_valid_alternative_in_same_selected_family_can_support_entry(settings):
    ctx = context()
    ctx["patterns"].append({**ctx["patterns"][0],"target":99.9})
    assert quality.evaluate(request(),ctx,tick())["allowed"]


def test_paper_registration_is_deduplicated_and_nonoverlapping(settings):
    result = quality.evaluate(request(),context(),tick())
    a = paper.record_candidate(request(),result,["learning: paused"])
    assert paper.record_candidate(request(),result,["learning: paused"]) == a
    paper.record_candidate(request(),{**result,"context":{**context(),"signal_bar":context()["signal_bar"]+300,"next_bar":context()["next_bar"]+300}},["learning: paused"])
    snapshot = paper.snapshot()
    assert snapshot["counts"] == {"waiting":1,"observed":1}
    with paper._connect() as conn:
        first = dict(conn.execute("SELECT * FROM candidates WHERE id=?",(a,)).fetchone())
    assert first["target_bar"] == context()["next_bar"]+300, "only a future full candle can start the paper position"


def candidate(**changes):
    return {"status":"waiting","side":"BUY","entry":100,"sl":99,"tp":103,"spread":0,"entry_atr":1,
            "target_bar":600,"entered_bar":None,**changes}


def bar(**changes):
    return {"time":600,"open":100,"high":101,"low":99.5,"close":100.5,"spread":0,**changes}


def test_ambiguous_bar_is_conservative_and_cannot_qualify(settings):
    result = paper.settle_candidate(candidate(),[bar(high=104,low=98)],0.01,settings.quality)
    assert result["status"] == "closed" and result["ambiguous"] == 1
    assert result["r_multiple"] == pytest.approx(-1.05)


def test_sell_stop_uses_ask_and_costs(settings):
    result = paper.settle_candidate(candidate(side="SELL",sl=101,tp=97),
                                    [bar(high=100.95,spread=10)],0.01,settings.quality)
    assert result["r_multiple"] == pytest.approx(-1.05)


def test_gap_through_stop_is_not_capped_at_one_risk(settings):
    result = paper.settle_candidate(candidate(status="open",entered_bar=600),
                                    [bar(),bar(time=900,open=98,low=97,high=98.5)],0.01,settings.quality)
    assert result["r_multiple"] == pytest.approx(-2.05)


def test_repeated_observations_preserve_original_prices_and_merge_reasons(settings):
    result = quality.evaluate(request(),context(),tick())
    identifier = paper.record_candidate(request(),result,["learning: paused"])
    paper.record_candidate(request(),{**result,"entry":200},["desk: below health threshold"])
    with paper._connect() as conn:
        row = dict(conn.execute("SELECT * FROM candidates WHERE id=?",(identifier,)).fetchone())
    assert row["entry"] == result["entry"] and row["qualified"] == 0
    assert len(json.loads(row["reasons"])) == 2


def test_out_of_hours_observations_are_not_requalification_evidence(settings):
    settings.risk.allowed_hours_utc = [(datetime.now(timezone.utc).hour + 1) % 24]
    paper.record_candidate(request(),quality.evaluate(request(),context(),tick()),[])
    assert paper.snapshot()["recent"][0]["qualified"] == 0


def test_feed_status_is_shared_with_dashboard_process(settings):
    saved = dict(paper._advance_status)
    try:
        paper._advance_status.update(running=False,last_completed_at="2026-10-13T01:00:00+00:00")
        paper._save_feed_status()
        paper._advance_status.update(last_completed_at=None)
        assert paper.snapshot()["feed"]["last_completed_at"] == "2026-10-13T01:00:00+00:00"
    finally:
        paper._advance_status.update(saved)


def test_paper_entry_gap_and_missing_history_do_not_get_success_labels(settings):
    assert paper.settle_candidate(candidate(),[bar(open=104)],0.01,settings.quality)["status"] == "missed"
    assert paper.settle_candidate(candidate(),[bar(time=900)],0.01,settings.quality)["status"] == "missed"
    assert paper.settle_candidate(candidate(),[bar(time=300,high=104)],0.01,settings.quality) is None
    assert paper.settle_candidate(candidate(sl=95,tp=120),[bar(open=100.5)],0.01,settings.quality)["status"] == "missed"


def bad_evidence(until=None):
    return {"trades":8,"consecutive_losses":4,"net_pnl":-80,"profit_factor":0.2,"paused_until":until}


def seed_paper(settings,start,count=20,qualified=1,ambiguous=0):
    with paper._connect() as conn:
        for i in range(count):
            created = start + timedelta(hours=i+1)
            conn.execute("INSERT INTO candidates (id,account,symbol,side,setup,created_at,timeframe,reasons,qualified,status,closed_at,r_multiple,ambiguous) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"seed-{start.isoformat()}-{i}",paper.account_scope(),"TEST","BUY","desk:double_bottom",created.isoformat(),
                          "M5","[]",qualified,"closed",(created+timedelta(minutes=30)).isoformat(),1.5,ambiguous))


def test_expiry_alone_never_requalifies_and_state_survives_lookback(settings):
    now = datetime.now(timezone.utc)
    state = paper.requalification("TEST","BUY","desk:double_bottom",bad_evidence((now+timedelta(hours=6)).isoformat()),now,now)
    assert state["required"]
    assert paper.requalification("TEST","BUY","desk:double_bottom",bad_evidence(),now,now+timedelta(days=40))["required"]


@pytest.mark.parametrize("qualified,ambiguous,start_offset",[(0,0,0),(1,1,0),(1,0,-5),(1,0,5)])
def test_invalid_old_and_future_paper_results_cannot_requalify(settings,qualified,ambiguous,start_offset):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=2)
    paper.requalification("TEST","BUY","desk:double_bottom",bad_evidence(),since,since)
    seed_paper(settings,since+timedelta(days=start_offset),qualified=qualified,ambiguous=ambiguous)
    assert paper.requalification("TEST","BUY","desk:double_bottom",bad_evidence(),since,now)["required"]


def test_prospective_results_can_requalify_but_new_loss_requires_fresh_evidence(settings):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=2)
    paper.requalification("TEST","BUY","desk:double_bottom",bad_evidence(),since,since)
    seed_paper(settings,since)
    assert not paper.requalification("TEST","BUY","desk:double_bottom",bad_evidence(),since,now)["required"]
    assert not paper.requalification("TEST","BUY","desk:double_bottom",bad_evidence(),since,now)["required"]
    assert paper.requalification("TEST","BUY","desk:double_bottom",bad_evidence(),now,now)["required"]


def test_new_real_loss_during_quarantine_invalidates_earlier_paper_recovery(settings):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=2)
    paper.requalification("TEST","BUY","desk:double_bottom",bad_evidence(),since,since)
    seed_paper(settings,since)
    result = paper.requalification("TEST","BUY","desk:double_bottom",bad_evidence(),since+timedelta(days=1),now)
    assert result["required"] and result["paper_trades"] == 0


def test_exit_and_stop_changes_bypass_quality(settings,monkeypatch):
    from mt5_mcp import execution_gate as gate
    monkeypatch.setattr(quality,"check",lambda *a,**kw:(_ for _ in ()).throw(AssertionError("entry check on exit")))
    for change in ({"position":42},{"action":6},{"action":8}):
        gate.require({**request(),**change})


def test_unavailable_quality_fails_closed_even_in_log_mode(settings,monkeypatch,tmp_path):
    from mt5_mcp import execution_gate as gate, consensus, compliance, learning_loop
    monkeypatch.setattr(config,"rejections_enabled",lambda:False)
    monkeypatch.setattr(quality,"check",lambda *a,**kw:{"allowed":False,"violations":["quality: stale"]})
    monkeypatch.setattr(quality,"capture",lambda *a:None)
    monkeypatch.setattr(learning_loop,"check_entry",lambda *a,**kw:{"allowed":True,"violations":[]})
    monkeypatch.setattr(consensus,"_cfg",lambda:{"allowed_symbols":["TEST"]})
    monkeypatch.setattr(consensus,"check",lambda *a,**kw:{"violations":[]})
    monkeypatch.setattr(compliance,"gate",lambda *a,**kw:{"violations":[]})
    monkeypatch.setattr(gate,"live_stats",lambda:{})
    monkeypatch.setattr(gate,"GATE_LOG",tmp_path/"audit.jsonl")
    with pytest.raises(gate.OrderBlocked,match="quality: stale"):
        gate.require(request())


def test_stale_candles_are_rejected_even_with_fresh_quotes(settings):
    result = quality.evaluate(request(),{**context(),"next_bar":context()["next_bar"]-10800},tick())
    assert not result["allowed"] and "candles are stale" in ";".join(result["violations"])


def test_newest_rates_anchor_to_broker_epoch(monkeypatch):
    from mt5_mcp.mt5_client import MT5Client

    client = MT5Client()
    stamp = int(time.time())+10800
    calls = []
    def call(name,*args):
        if name == "symbol_info_tick":
            return SimpleNamespace(time=stamp)
        if name == "copy_rates_range":
            calls.append(args)
            return [1]
        return True
    monkeypatch.setattr(client,"_call",call)
    assert client._rates_newest("TEST",5,"M5",20) == [1]
    assert calls[0][-1].timestamp() == stamp
    assert (calls[0][-1]-calls[0][-2]).total_seconds() == 20*5*60


def test_native_timeframe_ids_do_not_change_history_window_minutes(monkeypatch):
    from mt5_mcp.mt5_client import MT5Client
    client = MT5Client()
    client._mt5 = SimpleNamespace(TIMEFRAME_H1=16385)
    stamp = int(time.time())+10800
    calls = []
    def call(name,*args):
        if name == "symbol_info_tick":
            return SimpleNamespace(time=stamp)
        if name == "copy_rates_range":
            calls.append(args)
            return [1]
        return True
    monkeypatch.setattr(client,"_call",call)
    assert client._rates_newest("TEST",60,"H1",20) == [1]
    assert calls[0][1] == 16385
    assert (calls[0][-1]-calls[0][-2]).total_seconds() == 20*60*60
