"""Frozen read-only Volatility50 EMA-pullback research replay.
No strategy, execution, notification, journal or configuration imports.
"""
from __future__ import annotations
import argparse,hashlib,json,math,time
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
import numpy as np
import pandas as pd

EAT=timezone(timedelta(hours=3))
RULES={
 'version':2,'symbol':'Volatility 50 Index','side':'BUY',
 'start_nairobi':'2026-07-18T00:00:00+03:00','end_nairobi':'2026-10-18T04:00:00+03:00',
 'warmup_days':45,'ema_fast':20,'ema_slow':50,'atr_period':14,
 'trend':'Latest fully closed H1 and H4: close>EMA20>EMA50; M15 EMA20>EMA50 and close>EMA20.',
 'rejection':'M15 candle bullish, closes in top third, lower wick >= body; low touches previous-bar EMA20 within +0.10 previous ATR and high reaches that EMA.',
 'structure':'Last two M15 2-left/2-right confirmed swing lows (last40 bars) must rise. Signal low >= latest swing low -0.25 previous ATR.',
 'stop':'min(signal low, latest confirmed swing low)-0.25 previous M15 ATR, rounded down to current tick size.',
 'ceiling':'Nearest above entry among prior20 closed-bar highs on M15/H1/H4; cap at one current tick below. If any ceiling equals/below entry, only ceilings above entry are relevant; no new-high extrapolated target.',
 'entry':'First M5 open at M15 close plus most recent strictly earlier positive M5 historical spread; spread is causal recorded-bar proxy, not tick-verified executable ask.',
 'net_target_r':2.0,'extra_cost_r':0.05,'risk_percent':0.1,'reference_balance':10000.0,'sizing':'Current broker tick value, minimum/maximum and downward volume step rounding. Fixed10USD budget including0.05R reserve. Reject minimum volume above budget.',
 'max_entries_per_nairobi_day':2,'max_open_positions':1,'horizon_m5_bars':288,
 'exit':'M5 bid OHLC: stops before targets when ambiguous; adverse stop gap at opening bid; target fills capped at target. Exit at 24h, segment end or unavailable data; unavailable data is unresolved, not an invented close.',
 'split_fraction':0.70,'split':'Chronological elapsed-time70/30; independent flat train/holdout books, force close at split and end. No parameter tuning. Market selection used current scan, so holdout is retrospective temporal evaluation, not genuinely unseen selection evidence.',
 'cost_stress':'Repeat same rules with1.5x recorded spread; do not choose best variant.',
 'limitations':'Mechanical interpretation of discretionary chart idea, NOT exact full desk pattern/zone/quality gate or an execution-approved strategy. Fees/slippage/swap use an assumed0.05R reserve, not reconstructed broker charges. Current contract metadata may differ historically. Closed drawdown excludes intratrade floating risk.'
}

def stamp(s):return int(datetime.fromisoformat(s).timestamp())
def iso(t):return datetime.fromtimestamp(t,EAT).isoformat()
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')
def indicators(frame):
 f=frame.copy();c=f.close
 f['ema20']=c.ewm(span=20,adjust=False).mean();f['ema50']=c.ewm(span=50,adjust=False).mean()
 tr=pd.concat([f.high-f.low,(f.high-c.shift()).abs(),(f.low-c.shift()).abs()],axis=1).max(axis=1)
 f['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
 return f

def pivots_low(bars,last,left=2,right=2,lookback=40):
 result=[]
 for j in range(max(left,last-lookback+1),last-right+1):
  value=float(bars.low.iloc[j]);other=bars.low.iloc[j-left:j+right+1]
  if value==float(other.min()) and int((other==value).sum())==1:result.append(j)
 return result

def candidate(m15,i,h1,j,h4,k,tick):
 """Use only slices ending at supplied fully closed bar indices."""
 if min(i,j,k)<199:return None,'warmup'
 row=m15.iloc[i];prev=m15.iloc[i-1];a=float(prev.atr)
 if not (a>0 and h1.close.iloc[j]>h1.ema20.iloc[j]>h1.ema50.iloc[j] and h4.close.iloc[k]>h4.ema20.iloc[k]>h4.ema50.iloc[k]):return None,'h1_h4_trend'
 if not row.close>row.ema20>row.ema50:return None,'m15_trend'
 body=float(row.close-row.open);rng=float(row.high-row.low);wick=float(min(row.open,row.close)-row.low)
 if not (body>0 and rng>0 and row.close>=row.low+2*rng/3 and wick>=body and row.low<=prev.ema20+.10*a and row.high>=prev.ema20):return None,'rejection'
 lows=pivots_low(m15,i)
 if len(lows)<2 or not m15.low.iloc[lows[-1]]>m15.low.iloc[lows[-2]]:return None,'higher_low'
 swing=float(m15.low.iloc[lows[-1]])
 if row.low<swing-.25*a:return None,'structure_broken'
 stop=math.floor((min(float(row.low),swing)-.25*a)/tick)*tick
 ceilings=[float(m15.high.iloc[i-19:i+1].max()),float(h1.high.iloc[j-19:j+1].max()),float(h4.high.iloc[k-19:k+1].max())]
 return {'sl':stop,'ceilings':ceilings,'atr':a,'signal_low':float(row.low),'swing_low':swing,'swing_index':lows[-1],'m15_last_open':int(row.time),'h1_last_close':int(h1.time.iloc[j])+3600,'h4_last_close':int(h4.time.iloc[k])+14400},'candidate'

def geometry(entry,sl,ceilings,tick,cost=.05):
 risk=entry-sl
 if risk<=0:return None,'invalid_risk'
 above=[c for c in ceilings if c>entry]
 if not above:return None,'no_overhead_reference'
 cap=min(above)-tick
 tp=math.ceil((entry+(2+cost)*risk)/tick-1e-10)*tick
 if tp>cap+1e-10:return None,'insufficient_net_rr'
 return {'entry':entry,'sl':sl,'tp':tp,'price_risk':risk,'ceiling':cap},'rr_pass'

def fill_exit(rows,entry,sl,tp,risk,cost=.05):
 """M5 arrays/dicts, with full [entry, exit) bar-window evidence."""
 for n,row in enumerate(rows):
  o,h,l,c=[float(row[k]) for k in ('open','high','low','close')]
  stopped=l<=sl;targeted=h>=tp
  if stopped:
   exit_price=min(sl,o);reason='stop';ambiguous=bool(targeted)
  elif targeted:exit_price=tp;reason='target';ambiguous=False
  elif n==len(rows)-1:exit_price=c;reason='time_or_boundary';ambiguous=False
  else:continue
  return {'exit_time':int(row['time'])+300,'exit_price':exit_price,'net_r':(exit_price-entry)/risk-cost,'exit_reason':reason,'ambiguous':ambiguous,'held_minutes':(n+1)*5}
 raise ValueError('No exit rows')

def stats(trades):
 r=np.array([z['net_r'] for z in trades],float);win=float(r[r>0].sum());loss=float(-r[r<0].sum());cum=np.r_[0.,np.cumsum(r)]
 return {'trades':len(r),'wins':int((r>0).sum()),'losses':int((r<0).sum()),'win_rate_percent':100*float((r>0).mean()) if len(r) else None,'net_r':float(r.sum()),'mean_r':float(r.mean()) if len(r) else None,'profit_factor':win/loss if loss else None,'max_closed_drawdown_r':float((np.maximum.accumulate(cum)-cum).max()),'modeled_net_usd':sum(z.get('modeled_usd_net',0.) for z in trades),'reference_return_percent':sum(z.get('modeled_usd_net',0.) for z in trades)/100,'ambiguous_stop_first':sum(z['ambiguous'] for z in trades)}

def capture(out):
 import MetaTrader5 as mt
 assert mt.initialize(path=r'C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe',timeout=15000),mt.last_error()
 selected=False;restore=False
 try:
  account=mt.account_info();assert account and account.login==41204968 and 'Deriv' in account.server,'Unexpected terminal identity'
  name=RULES['symbol'];info=mt.symbol_info(name);assert info is not None
  selected=info.select
  if not selected:assert mt.symbol_select(name,True);restore=True
  tick=mt.symbol_info_tick(name);assert tick and abs(time.time()-tick.time)<120,'Unverified broker clock/freshness'
  start,end=stamp(RULES['start_nairobi']),stamp(RULES['end_nairobi']);warm=start-RULES['warmup_days']*86400
  meta={k:getattr(info,k) for k in ['name','point','digits','trade_tick_size','trade_tick_value','trade_contract_size','volume_min','volume_max','volume_step','trade_stops_level','swap_mode','swap_long','swap_short','swap_rollover3days']}
  meta.update(currency=account.currency,captured_nairobi=datetime.now(EAT).isoformat(),terminal_maxbars=mt.terminal_info().maxbars)
  dump(out/'metadata.json',meta)
  manifest={'rules_sha256':digest(out/'rules.json'),'source_sha256':digest(__file__),'frames':{},'capture_errors':[]}
  for label,tf,sec,beg in [('M15',mt.TIMEFRAME_M15,900,warm),('H1',mt.TIMEFRAME_H1,3600,warm),('H4',mt.TIMEFRAME_H4,14400,warm),('M5',mt.TIMEFRAME_M5,300,start-3600)]:
   pieces=[]
   # Weekly chunks limit download size and expose missing ranges.
   ranges=list(range(beg,end,7*86400))
   for chunk,left in enumerate(ranges):
    right=min(end,left+7*86400)
    data=None
    for attempt in range(3):
     data=mt.copy_rates_range(name,tf,datetime.fromtimestamp(left,timezone.utc),datetime.fromtimestamp(right,timezone.utc))
     if data is not None and len(data) and int(data[0]['time'])<=left+sec and int(data[-1]['time'])>=right-sec:break
     time.sleep(1)
    if data is None or not len(data):manifest['capture_errors'].append({'tf':label,'start':left,'end':right,'error':str(mt.last_error())});continue
    pieces.append(pd.DataFrame(data))
   if not pieces:raise ValueError('No '+label+' history')
   f=pd.concat(pieces,ignore_index=True).drop_duplicates('time').sort_values('time');f=f.loc[(f.time>=beg)&(f.time+sec<=end)].reset_index(drop=True)
   path=out/(label+'.parquet');f.to_parquet(path,index=False)
   gaps=f.time.diff();bad=f.loc[gaps>sec,['time']]
   manifest['frames'][label]={'sha256':digest(path),'rows':len(f),'expected_start':beg,'first':int(f.time.iloc[0]),'last':int(f.time.iloc[-1]),'gap_count':len(bad),'missing_bars':int(((gaps.fillna(sec)/sec)-1).clip(lower=0).sum()),'zero_spread_bars':int((f.spread==0).sum()),'duration_seconds':sec,'expected_rows':end//sec-((beg+sec-1)//sec)}
   print('CAPTURE',label,json.dumps(manifest['frames'][label]),flush=True)
  dump(out/'manifest.json',manifest)
 finally:
  if restore:assert mt.symbol_select(RULES['symbol'],False),'Temporary symbol selection restore failed'
  mt.shutdown()

def load(out):
 manifest=json.loads((out/'manifest.json').read_text());frames={}
 assert manifest['rules_sha256']==digest(out/'rules.json'),'Changed frozen rules'
 assert json.loads((out/'rules.json').read_text())==RULES,'Runtime rules differ from frozen rules'
 for label,sec in [('M5',300),('M15',900),('H1',3600),('H4',14400)]:
  path=out/(label+'.parquet');assert digest(path)==manifest['frames'][label]['sha256'],'Tampered '+label
  f=pd.read_parquet(path)
  assert f.time.is_monotonic_increasing and not f.time.duplicated().any(),'Timestamp integrity'
  assert np.isfinite(f[['open','high','low','close','spread']].to_numpy()).all(),'Nonfinite data'
  assert (f[['open','high','low','close']]>0).all().all() and (f.spread>=0).all(),'Invalid price/spread'
  assert (f.high>=f[['open','close','low']].max(axis=1)).all() and (f.low<=f[['open','close','high']].min(axis=1)).all(),'Invalid OHLC'
  # Require complete continuous in-window history; never silently shorten three months.
  expected=manifest['frames'][label]['expected_rows']
  assert len(f)==expected and (f.time.diff().dropna()==sec).all(),f'{label}: incomplete requested history ({len(f)}/{expected})'
  frames[label]=indicators(f) if label!='M5' else f
 return frames,manifest

def replay(frames,meta,start,end,spread_factor=1.):
 m15,h1,h4,m1=[frames[k] for k in ['M15','H1','H4','M5']]
 ts={k:frames[k].time.to_numpy(dtype=np.int64) for k in frames};m1rec=m1.to_dict('records');cost=RULES['extra_cost_r'];tick=meta['trade_tick_size'];point=meta['point']
 spreads=m1.spread.replace(0,np.nan).ffill().to_numpy()*point*spread_factor
 until=start;counts=Counter();funnel=Counter();trades=[]
 for i in range(200,len(m15)):
  decision=int(ts['M15'][i])+900
  if not start<=decision<end:continue
  funnel['closed_m15_decisions']+=1
  if decision<until:funnel['position_open']+=1;continue
  day=datetime.fromtimestamp(decision,EAT).date().isoformat()
  if counts[day]>=2:funnel['daily_limit']+=1;continue
  j=int(np.searchsorted(ts['H1']+3600,decision,side='right')-1);k=int(np.searchsorted(ts['H4']+14400,decision,side='right')-1)
  signal,reason=candidate(m15,i,h1,j,h4,k,tick);funnel[reason]+=1
  if signal is None:continue
  idx=int(np.searchsorted(ts['M5'],decision));prev=idx-1
  if idx>=len(m1) or int(ts['M5'][idx])!=decision or prev<0 or not np.isfinite(spreads[prev]):funnel['missing_entry_quote_proxy']+=1;continue
  entry=float(m1.open.iloc[idx])+float(spreads[prev]);geom,reason=geometry(entry,signal['sl'],signal['ceilings'],tick,cost);funnel[reason]+=1
  if geom is None:continue
  # Respect current minimum stop distance without narrowing a structural stop.
  if min(entry-float(spreads[prev])-geom['sl'],geom['tp']-entry)<meta['trade_stops_level']*point:funnel['broker_min_stop']+=1;continue
  perlot=geom['price_risk']/meta['trade_tick_size']*meta['trade_tick_value']
  if perlot<=0:raise ValueError('Invalid broker risk value')
  volume=math.floor(min(10/(1+cost)/perlot,meta['volume_max'])/meta['volume_step'])*meta['volume_step']
  if volume+1e-9<meta['volume_min']:funnel['minimum_volume_over_budget']+=1;continue
  riskmoney=perlot*volume
  stopidx=min(idx+RULES['horizon_m5_bars'],int(np.searchsorted(ts['M5'],end)))
  rows=m1rec[idx:stopidx]
  if not rows:continue
  exit=fill_exit(rows,entry,geom['sl'],geom['tp'],geom['price_risk'],cost)
  tr={'side':'BUY','decision_time':decision,'entry_time':decision,'entry_nairobi':iso(decision),'exit_nairobi':iso(exit['exit_time']),**signal,**geom,**exit,'spread':float(spreads[prev]),'spread_source_time':int(ts['M5'][prev]),'volume':volume,'price_risk_usd':riskmoney,'planned_risk_with_reserve_usd':riskmoney*(1+cost),'modeled_usd_net':exit['net_r']*riskmoney,'modeled_extra_cost_r':cost}
  trades.append(tr);counts[day]+=1;until=exit['exit_time']
 return {'stats':stats(trades),'funnel':dict(funnel),'trades':trades}

def run(out):
 frames,manifest=load(out);meta=json.loads((out/'metadata.json').read_text());start,end=stamp(RULES['start_nairobi']),stamp(RULES['end_nairobi']);split=start+int((end-start)*.7)//900*900
 result={'rules':RULES,'replay_source_sha256':digest(__file__),'split_nairobi':iso(split),'cases':{},'manifest':manifest}
 for name,factor in [('base',1.),('spread_stress_1_5x',1.5)]:
  train=replay(frames,meta,start,split,factor);holdout=replay(frames,meta,split,end,factor)
  result['cases'][name]={'train':train,'holdout':holdout,'combined_stats':stats(train['trades']+holdout['trades'])}
  print('RESULT',name,json.dumps({k:v for k,v in result['cases'][name].items() if k=='combined_stats'}),'TRAIN',json.dumps(train['stats']),'HOLDOUT',json.dumps(holdout['stats']),flush=True)
 dump(out/'results.json',result)
 return result

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--cached',action='store_true');args=ap.parse_args();out=args.output
 if not args.cached:
  out.mkdir(exist_ok=False);dump(out/'rules.json',RULES);capture(out)
 result=run(out)
 print('OUTPUT',str(out),flush=True)
if __name__=='__main__':main()
