"""Prospective, isolated paper comparison. No order API or live state writes.

Each family has an independent fixed-balance paper account. Signals failing
live admission are still paper research and are clearly tagged as such.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAMILIES = ('trend_zone','mean_reversion','momentum_breakout','btc_trend_following','ema_crossover','bb_mr_fade')
CODE = ['scripts/forward_strategy_paper.py','mt5_mcp/strategy.py','mt5_mcp/multi_strategy.py',
        'mt5_mcp/common.py','mt5_mcp/indicators.py','mt5_mcp/supply_demand.py',
        'mt5_mcp/price_action.py','mt5_mcp/entry_quality.py','mt5_mcp/models/signal_ranker.json']

def save(path, value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,indent=2,allow_nan=False,default=str),encoding='utf-8')
    temp.replace(path)

def hashes():
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in CODE}

def append(path, value):
    with path.open('a',encoding='utf-8') as f: f.write(json.dumps(value,default=str,allow_nan=False)+'\n')

def iso(stamp): return datetime.fromtimestamp(stamp,timezone.utc).isoformat()

def entry_cursor(tick_msc, entry_time, offset):
    # A recent-but-older quote cannot make pre-decision ticks eligible exits.
    return max(int(tick_msc),int((entry_time+offset)*1000))

def exit_crossing(position, ticks):
    """First observed bid/ask crossing; stop gaps adverse, TP capped at target."""
    buy=position['side']=='BUY'
    for tick in ticks:
        stamp=int(tick['time_msc'])
        if stamp<=position['last_tick_msc']: continue
        bid,ask=float(tick['bid']),float(tick['ask'])
        if not (math.isfinite(bid) and math.isfinite(ask) and ask>=bid>0): continue
        price=bid if buy else ask
        stop=price<=position['sl'] if buy else price>=position['sl']
        target=price>=position['tp'] if buy else price<=position['tp']
        if stop or target:
            return {'price':price if stop else position['tp'],'reason':'stop' if stop else 'target','time_msc':stamp}
    return None

def sizing(budget, loss_per_lot, info):
    if not math.isfinite(loss_per_lot) or loss_per_lot<=0: return 0.
    step=float(info['volume_step']); minimum=float(info['volume_min'])
    lots=round(math.floor(min(budget/loss_per_lot,float(info['volume_max']))/step)*step,8)
    return lots if lots>=minimum and lots*loss_per_lot<=budget+1e-8 else 0.

class Feed:
    """Explicit read-only facade; unknown methods, including order_send, fail."""
    def __init__(self, mt5, manifest, directory):
        self.mt5=mt5; self.manifest=manifest; self.directory=directory; self.cache={}
        self.virtual_balance=manifest['starting_balance']
    def ensure_connected(self):
        a=self.mt5.account_info()
        if a is None or (a.login,a.server)!=(self.manifest['account'],self.manifest['server']):
            raise RuntimeError('Account identity changed')
    def account_info(self):
        return {'login':self.manifest['account'],'server':self.manifest['server'],
                'balance':self.virtual_balance,'equity':self.virtual_balance,'margin_free':self.virtual_balance,
                'currency':self.manifest['currency']}
    def symbol_info(self,symbol):
        value=self.mt5.symbol_info(symbol)
        if value is None: raise ValueError('Missing symbol metadata: '+symbol)
        return value._asdict()
    def tick(self,symbol):
        tick=self.mt5.symbol_info_tick(symbol)
        if tick is None: raise ValueError('Tick unavailable')
        result=tick._asdict()
        age=time.time()-(result['time']-self.manifest['offset'])
        if not -10<=age<=120 or result['ask']<result['bid'] or result['bid']<=0:
            raise ValueError('Stale/invalid quote')
        return result
    def rates(self,symbol,timeframe,count):
        import pandas as pd
        if timeframe=='W1':
            raise ValueError('Optional weekly advisory unavailable in this paper feed')
        key=(symbol,timeframe)
        if key not in self.cache:
            seconds=(int(timeframe[1:])*60 if timeframe.startswith('M') else int(timeframe[1:])*3600 if timeframe.startswith('H') else int(timeframe[1:])*86400 if timeframe.startswith('D') else int(timeframe[1:])*604800)
            end=time.time()+self.manifest['offset']
            raw=self.mt5.copy_rates_range(symbol,getattr(self.mt5,'TIMEFRAME_'+timeframe),
                 datetime.fromtimestamp(end-seconds*(count+5)*3,timezone.utc),datetime.fromtimestamp(end,timezone.utc))
            if raw is None or not len(raw): raise ValueError('History unavailable')
            frame=pd.DataFrame(raw)
            frame=frame.loc[frame.time+seconds<=time.time()+self.manifest['offset']].copy()
            if len(frame)<min(count,50): raise ValueError('Insufficient closed history')
            self.cache[key]=frame.tail(max(count,400)).copy()
            archive=self.directory/'bars'; archive.mkdir(exist_ok=True)
            path=archive/f'{symbol}_{timeframe}_{int(frame.time.iloc[-1])}.parquet'
            if not path.exists(): frame.to_parquet(path,index=False)
        frame=self.cache[key].tail(count).copy().reset_index(drop=True)
        frame['time']=pd.to_datetime(frame.time,unit='s',utc=True)
        return frame
    def order_calc_margin(self,action,symbol,lots,price):
        margin=self.mt5.order_calc_margin(action,symbol,lots,price)
        if margin is None: raise ValueError('Margin estimate unavailable')
        return margin

def render(directory,manifest,state):
    lines=['# Prospective strategy paper test','',f"UTC window: {iso(manifest['start'])} to {iso(manifest['end'])}.",
      f"Status: **{state['status']}**. Latest run: {iso(state['last_run'])}.",
      f"Reference balance: {manifest['starting_balance']:.2f} {manifest['currency']}; fixed risk {manifest['risk_percent']}% per paper entry.",
      '', 'These are independent research accounts, not additive portfolio returns or predicted earnings. No broker orders are sent.',
      'All six families are evaluated on all six allowed symbols; candidate routes are not promoted. Closed bars only; fresh tick entries and bid/ask tick exits.',
      'Strategy signals are tested even when static live eligibility fails. Eligibility reasons are logged. Full desk, news, consensus, prop and portfolio admission are not reproduced.',
      'Base strategy thresholds and low performance-risk budget are frozen; adaptive updates are disabled. Optional W1 advisory is omitted. Source/model changes pause the test. Stops/targets only; no desk early cuts, partials or trailing.',
      f"Spread is included via executable bid/ask; additional cost reserve {manifest['cost_r']}R per round trip is an assumption, not measured commission/swap. Overnight costs and execution latency can differ.",
      'A missing tick-history request flags an error; an unobserved outage cannot be guaranteed complete by the broker. Results require coverage review.',
      '', '| Strategy | Closed | Wins | Estimated net | Open | Floating estimate | Paper entries satisfying static screen |',
      '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name,book in state['books'].items():
        closed=book['closed']; pnl=sum(t['net_pnl'] for t in closed)
        lines.append(f"| {name} | {len(closed)} | {sum(t['net_pnl']>0 for t in closed)} | {pnl:+.2f} | {len(book['positions'])} | {book.get('floating',0):+.2f} | {sum(t['static_screen_pass'] for t in closed+book['positions'])} |")
    lines+=['','Initial numbers are zero until prospective signals complete. No earnings estimate is established by zero or a few trades.',
      'Each strategy has at most three open paper positions, three entries/day, and one position per symbol. A 4% daily or 8% total equity loss stops new paper entries.',
      'At expiry, open paper positions are marked and reported, not recorded as closed wins/losses. Do not combine strategy books as if the same capital funded them concurrently.',
      '', '## Recent errors','',json.dumps(state.get('errors',[])[-10:],indent=2),'']
    (directory/'report.md').write_text('\n'.join(lines),encoding='utf-8')

def run(directory,days=None):
    import MetaTrader5 as mt5
    manifest_path=directory/'manifest.json'
    disk=json.loads((ROOT/'mt5_mcp/settings.json').read_text(encoding='utf-8'))
    if not mt5.initialize(path=disk['mt5']['terminal_path'],timeout=15000): raise RuntimeError(str(mt5.last_error()))
    try:
        account=mt5.account_info()
        if account is None or (account.login,account.server)!=(disk['prop_risk']['account_login'],disk['prop_risk']['server']):
            raise RuntimeError('Wrong existing terminal account; no account switching permitted')
        now=time.time()
        if not manifest_path.exists():
            if days is None: raise RuntimeError('Initialize explicitly with --init-days')
            from mt5_mcp.config import get_settings
            cfg=get_settings()
            manifest={'schema':1,'start':now,'end':now+days*86400,'account':account.login,'server':account.server,
              'currency':account.currency,'starting_balance':account.balance,'offset':cfg.quality.broker_time_offsets[account.server],
              'symbols':disk['consensus']['allowed_symbols'],'risk_percent':min(cfg.performance_risk.losing_percent,cfg.risk.max_risk_percent),
              'cost_r':cfg.quality.shadow_cost_r,'settings':cfg.model_dump(mode='json',include={'risk','strategy','quality','performance_risk'}),
              'adaptive':json.loads((ROOT/'mt5_mcp/memory/adaptive_state.json').read_text(encoding='utf-8')) if (ROOT/'mt5_mcp/memory/adaptive_state.json').exists() else {},
              'hashes':hashes(),'terminal_path':disk['mt5']['terminal_path']}
            save(manifest_path,manifest)
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        state_path=directory/'state.json'
        state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {
          'status':'running','last_run':now,'books':{n:{'positions':[],'closed':[],'seen':{},'days':{}} for n in FAMILIES},'errors':[]}
        state['last_run']=now
        if manifest['hashes']!=hashes():
            state['status']='paused_code_changed';save(state_path,state);render(directory,manifest,state);return
        if (account.login,account.server)!=(manifest['account'],manifest['server']): raise RuntimeError('Frozen account mismatch')
        save(state_path,state)
        # Inject only into this short-lived paper process. No production module is edited.
        import mt5_mcp.config as config
        frozen=config.Settings(**manifest['settings']); config.get_settings=lambda:frozen
        from mt5_mcp import strategy, multi_strategy, performance_risk, entry_quality
        from scripts.backtest_entry_selection import fast_market_structure
        strategy.market_structure=fast_market_structure
        feed=Feed(mt5,manifest,directory)
        strategy.get_client=lambda:feed;multi_strategy.get_client=lambda:feed
        # Feed.rates already removes the forming candle; do not drop a second bar.
        strategy._strategy_rates=lambda client,symbol,timeframe,count:client.rates(symbol,timeframe,count)
        strategy.get_adaptive_thresholds=lambda:manifest['adaptive'].get('thresholds',{})
        performance_risk.effective_percent=lambda client,fallback:manifest['risk_percent']
        planners={'trend_zone':lambda symbol:strategy.evaluate_strategy(symbol,risk_percent=manifest['risk_percent'],closed_confirmation=False,adaptive=False)}
        planners.update({n:getattr(multi_strategy,n+'_strategy') for n in FAMILIES if n!='trend_zone'})
        tick_cache={}
        for name in sorted(state['books'],key=lambda name:name=='trend_zone'):
            book=state['books'][name]
            remaining=[];floating=0.
            for p in book['positions']:
                try:
                    feed.ensure_connected()
                    key=(p['symbol'],p['last_tick_msc'])
                    if key not in tick_cache:
                        end=min(now,manifest['end'])+manifest['offset']
                        start=p['last_tick_msc']/1000
                        raw=mt5.copy_ticks_range(p['symbol'],datetime.fromtimestamp(start,timezone.utc),datetime.fromtimestamp(end,timezone.utc),mt5.COPY_TICKS_ALL)
                        if raw is None: raise ValueError('Missing tick history')
                        tick_cache[key]=raw
                        if len(raw):
                            import pandas as pd
                            path=directory/'ticks';path.mkdir(exist_ok=True)
                            pd.DataFrame(raw).to_parquet(path/f"{p['symbol']}_{p['last_tick_msc']}_{int(end*1000)}.parquet",index=False)
                    ticks=tick_cache[key]; hit=exit_crossing(p,ticks)
                    if hit:
                        pnl=mt5.order_calc_profit(0 if p['side']=='BUY' else 1,p['symbol'],p['lots'],p['entry'],hit['price'])
                        if pnl is None: raise ValueError('P&L conversion unavailable')
                        closed={**p,'exit':hit['price'],'exit_time':hit['time_msc']/1000-manifest['offset'],'exit_reason':hit['reason'],
                                'gross_pnl':pnl,'cost_reserve':p['risk_amount']*manifest['cost_r'],'net_pnl':pnl-p['risk_amount']*manifest['cost_r']}
                        book['closed'].append(closed);append(directory/'trades.jsonl',{'family':name,**closed});continue
                    if len(ticks):
                        p['last_tick_msc']=max(p['last_tick_msc'],int(ticks[-1]['time_msc']))
                        tick=ticks[-1]; price=float(tick['bid'] if p['side']=='BUY' else tick['ask'])
                        if price>0:
                            value=mt5.order_calc_profit(0 if p['side']=='BUY' else 1,p['symbol'],p['lots'],p['entry'],price)
                            if value is not None: floating+=value-p['risk_amount']*manifest['cost_r']
                except Exception as exc:
                    state['errors'].append({'utc':iso(now),'family':name,'symbol':p['symbol'],'phase':'settlement','error':str(exc)})
                remaining.append(p)
            book['positions']=remaining;book['floating']=floating
            if now>=manifest['end']: continue
            realized=sum(p['net_pnl'] for p in book['closed']);base=manifest['starting_balance'];day=iso(now)[:10]
            day_start=book.setdefault('day_equity',{}).setdefault(day,base+realized+floating)
            equity=base+realized+floating
            if equity<=base*.92 or equity<=day_start-base*.04: continue
            feed.virtual_balance=base
            for symbol in manifest['symbols']:
                if len(book['positions'])>=3 or book['days'].get(day,0)>=3 or any(p['symbol']==symbol for p in book['positions']): continue
                try:
                    feed.ensure_connected()
                    print(json.dumps({'scan':name,'symbol':symbol}),flush=True)
                    tf='H4' if name=='btc_trend_following' else manifest['settings']['strategy']['confirmation_timeframe'] if name=='trend_zone' else 'H1'
                    frame=feed.rates(symbol,tf,250);bar=int(frame.time.iloc[-1].timestamp())
                    if book['seen'].get(symbol)==bar: continue
                    feed.tick(symbol)
                    plan=planners[name](symbol) if name=='trend_zone' else planners[name](symbol,risk_percent=manifest['risk_percent'])
                    record={'utc':iso(time.time()),'family':name,'symbol':symbol,'signal_bar_broker':bar,'plan':plan.model_dump(mode='json')}
                    book['seen'][symbol]=bar
                    if not plan.valid or plan.action not in ('BUY','SELL'):
                        append(directory/'observations.jsonl',record);continue
                    tick=feed.tick(symbol);buy=plan.action=='BUY';entry=float(tick['ask'] if buy else tick['bid']);sl=float(plan.sl);tp=float(plan.tp)
                    risk=(entry-sl) if buy else (sl-entry);reward=(tp-entry) if buy else (entry-tp)
                    if risk<=0 or reward<=0: raise ValueError('Actual fill has invalid stop/target geometry')
                    loss=mt5.order_calc_profit(0 if buy else 1,symbol,1.,entry,sl)
                    if loss is None or loss>=0: raise ValueError('Cannot value stop risk')
                    budget=base*manifest['risk_percent']/100
                    info=feed.symbol_info(symbol);lots=sizing(budget,abs(loss),info)
                    if lots<=0: record['paper_rejection']='lot minimum exceeds frozen risk';append(directory/'observations.jsonl',record);continue
                    reasons=[];risk_cfg=manifest['settings']['risk']
                    if plan.confidence<risk_cfg['confidence_floor_percent']: reasons.append('confidence below current floor')
                    if reward/risk+1e-8<risk_cfg['min_risk_reward']: reasons.append('actual-fill RR below current floor')
                    if risk_cfg['allowed_hours_utc'] and datetime.now(timezone.utc).hour not in risk_cfg['allowed_hours_utc']: reasons.append('outside allowed hours')
                    if symbol in risk_cfg['blocked_symbols']: reasons.append('blocked symbol')
                    if any(('desk:'+name).startswith(x) for x in risk_cfg['blocked_setup_prefixes']): reasons.append('desk setup prefix blocked if executed through desk')
                    entered=time.time()
                    position={'symbol':symbol,'side':plan.action,'entry':entry,'sl':sl,'tp':tp,'lots':lots,'risk_amount':abs(loss)*lots,
                      'entry_time':entered,'entry_tick_msc':int(tick['time_msc']),'last_tick_msc':entry_cursor(tick['time_msc'],entered,manifest['offset']),
                      'signal_bar_broker':bar,'actual_rr':reward/risk,'static_screen_pass':not reasons,'static_screen_reasons':reasons}
                    book['positions'].append(position);book['days'][day]=book['days'].get(day,0)+1
                    record['paper_entry']=position;append(directory/'observations.jsonl',record)
                except Exception as exc:
                    state['errors'].append({'utc':iso(now),'family':name,'symbol':symbol,'phase':'scan','error':str(exc)})
            # Persist each completed family so a later error does not lose earlier observations.
            save(state_path,state)
        state['status']='complete' if now>=manifest['end'] else 'running'
        state['errors']=state['errors'][-100:]
        save(state_path,state);render(directory,manifest,state)
        print(json.dumps({'status':state['status'],'end_utc':iso(manifest['end']),'balance':manifest['starting_balance'],
                          'risk_percent':manifest['risk_percent'],'closed':sum(len(b['closed']) for b in state['books'].values()),
                          'open':sum(len(b['positions']) for b in state['books'].values()),'recent_errors':state['errors'][-3:]}),flush=True)
    finally: mt5.shutdown()

def main():
    import faulthandler
    faulthandler.enable()
    faulthandler.dump_traceback_later(60,repeat=False)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--init-days',type=int,choices=(7,30))
    args=parser.parse_args();args.directory.mkdir(parents=True,exist_ok=True)
    # OS lock automatically releases on process exit, including a forced termination.
    import msvcrt
    with (args.directory/'run.lock').open('a+b') as lock:
        if lock.tell()==0: lock.write(b'0');lock.flush()
        lock.seek(0)
        try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError: print('A paper scan is already running');return
        try: run(args.directory,args.init_days)
        finally: lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
    faulthandler.cancel_dump_traceback_later()

if __name__=='__main__': main()
