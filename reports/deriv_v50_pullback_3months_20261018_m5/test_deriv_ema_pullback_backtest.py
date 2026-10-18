import importlib.util
import unittest
import numpy as np
import pandas as pd
from pathlib import Path
spec=importlib.util.spec_from_file_location('bt',Path(__file__).parents[1]/'scripts/backtest_deriv_ema_pullback.py');bt=importlib.util.module_from_spec(spec);spec.loader.exec_module(bt)
class BacktestChecks(unittest.TestCase):
 def test_ambiguous_bar_is_stop_first(self):
  z=bt.fill_exit([dict(time=0,open=100,high=104,low=98,close=103)],100,99,102.05,1)
  self.assertEqual(z['exit_reason'],'stop');self.assertTrue(z['ambiguous']);self.assertAlmostEqual(z['net_r'],-1.05)
 def test_adverse_stop_gap(self):
  z=bt.fill_exit([dict(time=0,open=97,high=98,low=96,close=97)],100,99,102.05,1)
  self.assertEqual(z['exit_price'],97);self.assertAlmostEqual(z['net_r'],-3.05)
 def test_target_gap_capped(self):
  z=bt.fill_exit([dict(time=0,open=105,high=106,low=104,close=105)],100,99,102.05,1)
  self.assertEqual(z['exit_price'],102.05);self.assertAlmostEqual(z['net_r'],2)
 def test_boundary_close_and_time(self):
  z=bt.fill_exit([dict(time=0,open=100,high=101,low=99.5,close=100.5)],100,99,102.05,1)
  self.assertEqual(z['exit_time'],300);self.assertEqual(z['held_minutes'],5);self.assertAlmostEqual(z['net_r'],.45)
 def test_costs_and_nearest_resistance(self):
  self.assertEqual(bt.geometry(100,99,[101,110],.01)[1],'insufficient_net_rr')
  z,_=bt.geometry(100,99,[103,110],.01);self.assertAlmostEqual(z['tp'],102.05);self.assertLessEqual(z['tp'],z['ceiling'])
 def test_target_respects_tick_size_and_minimum_net_reward(self):
  z,_=bt.geometry(100.0013,99.6654,[102.5],.01)
  self.assertAlmostEqual(z['tp']/.01,round(z['tp']/.01));self.assertGreaterEqual((z['tp']-z['entry'])/z['price_risk']-.05,2)
 def test_missing_target_cannot_extrapolate(self):
  self.assertEqual(bt.geometry(100,99,[98,99],.01)[1],'no_overhead_reference')
 def test_pivot_only_confirmed_after_right_bars(self):
  f=pd.DataFrame({'low':[5,4,2,4,5,3,1,3,4]})
  self.assertNotIn(6,bt.pivots_low(f,7));self.assertIn(6,bt.pivots_low(f,8))
 def test_future_changes_do_not_repaint_past_indicators(self):
  f=pd.DataFrame({'open':np.arange(1.,301),'high':np.arange(1.,301)+1,'low':np.arange(1.,301)-.5,'close':np.arange(1.,301)+.2})
  before=bt.indicators(f);f.loc[250:,'close']=50000;f.loc[250:,'high']=60000;after=bt.indicators(f)
  np.testing.assert_allclose(before.ema20.iloc[:250],after.ema20.iloc[:250]);np.testing.assert_allclose(before.atr.iloc[:250],after.atr.iloc[:250])
 def test_no_trade_summary_is_not_invented_edge(self):
  s=bt.stats([]);self.assertEqual(s['trades'],0);self.assertIsNone(s['profit_factor']);self.assertIsNone(s['win_rate_percent'])
 @unittest.skipUnless(Path(r'E:\Project\Mt5 Ea\reports\deriv_v50_pullback_3months_20261018_m5/manifest.json').exists(),'Optional saved broker evidence')
 def test_full_signal_is_prefix_invariant(self):
  p=Path(r'E:\Project\Mt5 Ea\reports\deriv_v50_pullback_3months_20261018_m5');frames,_=bt.load(p)
  a,b,c=[frames[k] for k in ['M15','H1','H4']];i=len(a)-4;t=int(a.time.iloc[i])+900;j=int(np.searchsorted(b.time+3600,t,side='right')-1);k=int(np.searchsorted(c.time+14400,t,side='right')-1)
  first=bt.candidate(a,i,b,j,c,k,.0001);prefix=bt.candidate(a.iloc[:i+1],i,b.iloc[:j+1],j,c.iloc[:k+1],k,.0001)
  self.assertEqual(first,prefix)
if __name__=='__main__':unittest.main()
