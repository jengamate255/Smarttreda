"""Separate saved-data checks; does not import the replay engine or broker."""
import json,hashlib,math
from pathlib import Path
from datetime import datetime,timezone,timedelta
import numpy as np
import pandas as pd
p=Path(r'E:\Project\Mt5 Ea\reports\deriv_v50_pullback_3months_20261018_m5')
s=json.loads((p/'results.json').read_text());manifest=json.loads((p/'manifest.json').read_text());rules=json.loads((p/'rules.json').read_text());meta=json.loads((p/'metadata.json').read_text())
errors=[];checked=0;datasets={}
try:
 assert hashlib.sha256((p/'rules.json').read_bytes()).hexdigest()==manifest['rules_sha256']
 assert s['rules']==rules
 for lab,sec in [('M5',300),('M15',900),('H1',3600),('H4',14400)]:
  raw=(p/(lab+'.parquet')).read_bytes();assert hashlib.sha256(raw).hexdigest()==manifest['frames'][lab]['sha256']
  f=pd.read_parquet(p/(lab+'.parquet'));assert len(f)==manifest['frames'][lab]['expected_rows'];assert (f.time.diff().dropna()==sec).all()
  assert f.time.is_monotonic_increasing and not f.time.duplicated().any()
  assert np.isfinite(f[['open','high','low','close','spread']]).all().all();assert (f.spread>=0).all()
  assert (f.high>=f[['open','close','low']].max(axis=1)).all() and (f.low<=f[['open','close','high']].min(axis=1)).all()
  datasets[lab]=f
 for case,factor in [('base',1.),('spread_stress_1_5x',1.5)]:
  alltr=[]
  for part in ['train','holdout']:
   last=0;days={}
   for tr in s['cases'][case][part]['trades']:
    t=tr['entry_time'];assert tr['decision_time']==t and t>=last and tr['h1_last_close']<=t and tr['h4_last_close']<=t
    a=datasets['M15'];ind=int(np.searchsorted(a.time.to_numpy(),t-900));assert int(a.time.iloc[ind])==t-900
    # Independent prefix EMA and Wilder ATR computation.
    prefix=a.iloc[:ind+1];fast=prefix.close.ewm(span=20,adjust=False).mean();slow=prefix.close.ewm(span=50,adjust=False).mean()
    trange=np.maximum(prefix.high-prefix.low,np.maximum(abs(prefix.high-prefix.close.shift()),abs(prefix.low-prefix.close.shift()))).fillna(prefix.high-prefix.low)
    vol=float(trange.ewm(alpha=1/14,adjust=False).mean().iloc[-2]);bar=prefix.iloc[-1];prior=float(fast.iloc[-2])
    assert bar.close>fast.iloc[-1]>slow.iloc[-1];assert bar.close>bar.open and bar.close>=bar.low+2*(bar.high-bar.low)/3
    assert min(bar.open,bar.close)-bar.low>=bar.close-bar.open;assert bar.low<=prior+.1*vol and bar.high>=prior
    lows=[]
    for j in range(max(2,ind-39),ind-1):
     vals=a.low.iloc[j-2:j+3];v=float(a.low.iloc[j])
     if v==float(vals.min()) and int((vals==v).sum())==1:lows.append(j)
    assert len(lows)>=2 and a.low.iloc[lows[-1]]>a.low.iloc[lows[-2]]
    assert bar.low>=a.low.iloc[lows[-1]]-.25*vol
    expectedsl=math.floor((min(bar.low,a.low.iloc[lows[-1]])-.25*vol)/meta['trade_tick_size'])*meta['trade_tick_size']
    assert abs(expectedsl-tr['sl'])<1e-9
    for lab,seconds in [('H1',3600),('H4',14400)]:
     ht=datasets[lab];ht=ht.loc[ht.time+seconds<=t]
     assert ht.close.iloc[-1]>ht.close.ewm(span=20,adjust=False).mean().iloc[-1]>ht.close.ewm(span=50,adjust=False).mean().iloc[-1]
    minute=datasets['M5'];idx=int(np.searchsorted(minute.time.to_numpy(),t));assert int(minute.time.iloc[idx])==t
    spread=float(minute.spread.iloc[idx-1])*meta['point']*factor;entry=float(minute.open.iloc[idx])+spread
    assert tr['spread_source_time']==int(minute.time.iloc[idx-1])<t and abs(entry-tr['entry'])<1e-9
    risk=entry-tr['sl'];expectedtp=math.ceil((entry+2.05*risk)/meta['trade_tick_size']-1e-10)*meta['trade_tick_size']
    assert abs(tr['tp']-expectedtp)<1e-9
    ceilings=[float(prefix.high.iloc[-20:].max())]
    for lab,seconds in [('H1',3600),('H4',14400)]:
     ht=datasets[lab];ht=ht.loc[ht.time+seconds<=t];ceilings.append(float(ht.high.iloc[-20:].max()))
    assert np.allclose(ceilings,tr['ceilings'])
    cap=min(v for v in ceilings if v>entry)-meta['trade_tick_size']
    assert abs(cap-tr['ceiling'])<1e-9 and tr['tp']<=cap
    perlot=risk/meta['trade_tick_size']*meta['trade_tick_value'];lots=math.floor(min(10/1.05/perlot,meta['volume_max'])/meta['volume_step'])*meta['volume_step']
    assert abs(tr['volume']-lots)<1e-9 and lots>=meta['volume_min'] and perlot*lots*1.05<=10+1e-8
    window=minute.loc[(minute.time>=t)&(minute.time<tr['exit_time'])]
    assert len(window) and len(window)<=288 and int(window.time.iloc[-1])+300==tr['exit_time']
    expected=None
    for n,row in enumerate(window.itertuples()):
     sl=row.low<=tr['sl'];tp=row.high>=tr['tp']
     if sl or tp or n==len(window)-1:
      px=min(tr['sl'],row.open) if sl else tr['tp'] if tp else row.close
      reason='stop' if sl else 'target' if tp else 'time_or_boundary'
      assert n==len(window)-1 and reason==tr['exit_reason'];assert bool(sl and tp)==tr['ambiguous']
      expected=(px-entry)/risk-.05;assert abs(px-tr['exit_price'])<1e-9;break
    assert expected is not None and abs(expected-tr['net_r'])<1e-9
    assert abs(expected*perlot*lots-tr['modeled_usd_net'])<1e-7
    day=datetime.fromtimestamp(t,timezone(timedelta(hours=3))).date().isoformat();days[day]=days.get(day,0)+1;assert days[day]<=2
    last=tr['exit_time'];checked+=1;alltr.append(tr)
   assert len(s['cases'][case][part]['trades'])==s['cases'][case][part]['stats']['trades']
  assert abs(sum(x['net_r'] for x in alltr)-s['cases'][case]['combined_stats']['net_r'])<1e-8
  assert abs(sum(x['modeled_usd_net'] for x in alltr)-s['cases'][case]['combined_stats']['modeled_net_usd'])<1e-7
except (AssertionError,ValueError,KeyError) as exc:
 import traceback
 errors.append(type(exc).__name__+': '+str(exc));errors.append(traceback.format_exc())
result={'passed':not errors,'checked_trade_cases':checked,'checked_history_files':len(datasets),'errors':errors,'scope':'Independent saved history hashes/continuity/OHLC, executed signal prefixes, entry/stop/target/exit/cost/size/accounting and daily/nonoverlap checks. Not tick-verified execution or independent exhaustive detection-completeness proof.'}
(p/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
raise SystemExit(0 if not errors else 1)
