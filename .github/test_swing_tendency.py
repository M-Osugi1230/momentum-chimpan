"""Invariant tests for Swing Tendency Study v1."""
from __future__ import annotations
import sys, tempfile
from pathlib import Path
import numpy as np, pandas as pd, yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_detailed_historical_oos as strict_outcomes
import swing_tendency_analysis as swing


def protocol(path: Path) -> Path:
 payload={
  'mode':'RESEARCH_ONLY_NON_PROMOTABLE','promotion_evidence_allowed':False,
  'automatic_strategy_change':False,'automatic_exit_rule_change':False,
  'automatic_threshold_optimization':False,'production_ranking_change':False,
  'evaluation':{'top_sizes':[10,30],'horizons':[5,10,20,40,60],'focus_horizons':[20,40,60],
   'marginal_intervals':[[0,20],[20,40],[40,60]],'round_trip_cost_bps':[20,50,100],
   'robust_statistics':{'trim_fraction':.05,'paired_bootstrap_iterations':20}},
  'price_path':{'upside_thresholds':[.05,.10,.15],'downside_thresholds':[-.05,-.08,-.10],
   'principal_first_touch_pairs':[[.05,-.05],[.10,-.08],[.15,-.10]],
   'maximum_entry_gap_days':7,'maximum_session_gap_days':14,'maximum_adjacent_price_multiplier':4.0},
  'signal_states':{'rank_improvement_minimum':5},
  'regimes':{'breadth_weak_max':.33,'breadth_strong_min':.67},
  'interpretation':{'minimum_years_same_direction':1,'minimum_observations_per_cell':1,'minimum_signal_dates_per_cell':1},
 }
 path.write_text(yaml.safe_dump(payload),encoding='utf-8'); return path


def candidates() -> pd.DataFrame:
 rows=[]
 for date in pd.to_datetime(['2018-01-05','2018-01-12','2018-01-19']):
  for method in swing.METHODS:
   for rank in range(1,11):
    rows.append({'signal_date':date,'code':f'{rank:04d}','name':f'S{rank}','sector33':f'G{rank%2}',
     'method':method,'method_rank':rank,'method_score':100-rank,'return_5d':.01,'return_20d':.03,
     'return_60d':.05,'ma20_deviation':.04,'volume_ratio':1.2,'trading_value':1e8+rank,
     'above_ma20':True,'healthy_drawdown_from_recent_high':-.02})
 return pd.DataFrame(rows)


def events(c: pd.DataFrame) -> pd.DataFrame:
 rows=[]
 for r in c.itertuples(index=False):
  for h in (5,10,20,40,60):
   value=(11-r.method_rank)*h/10000
   rows.append({'signal_date':r.signal_date,'code':r.code,'name':r.name,'sector33':r.sector33,
    'horizon_sessions':h,'net_return':value-.002,'market_excess_net':value-.004,'mfe':value+.03,
    'mae':-.02,'method_rank':r.method_rank,'method_score':r.method_score,'method':r.method,
    'year':2018,'gross_return':value})
 return pd.DataFrame(rows)


def test_positive_volume_session_contract() -> None:
 prices=pd.DataFrame({
  'date':pd.to_datetime(['2019-04-26','2019-04-29','2019-05-07']),
  'adjusted_open':[100.,999.,102.],
  'adjusted_high':[101.,1000.,103.],
  'adjusted_low':[99.,998.,101.],
  'adjusted_close':[100.,999.,102.],
  'volume':[1000,0,1000],
 })
 result=strict_outcomes.one_outcome_strict(prices,pd.Timestamp('2019-04-25'),2)
 assert result is not None and result['exit_date']==pd.Timestamp('2019-05-07')
 assert result['max_session_gap_days']==11
 assert strict_outcomes.MAX_SESSION_GAP_DAYS==14


def test_all() -> None:
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp); _,cfg=swing.load_protocol(protocol(root/'p.yaml'))
  assert cfg['session_gap']==14
  c=swing.states(candidates(),cfg['rank_move'])
  c['breadth_regime']='MIXED'; c['trend_regime']='UP'; c['vol_regime']='MID'
  c['liquidity_band']='MID'; c['ma20_band']='MODERATE'
  assert {'FIRST_PICK','STABLE_REPEAT'}.issubset(set(c.signal_state))
  e=events(c)
  h=swing.summaries(e,cfg); assert not h.empty and set(h.horizon_sessions)=={5,10,20,40,60}
  detail,m=swing.marginal(e,cfg); assert not detail.empty and set(zip(m.interval_start,m.interval_end))=={(0,20),(20,40),(40,60)}
  dates=pd.date_range('2018-01-08',periods=100,freq='B'); panel=[]
  for code in c.code.unique():
   for i,date in enumerate(dates):
    price=100+i*.3
    panel.append({'date':date,'code':code,'adjusted_open':price,'adjusted_high':price*1.01,
     'adjusted_low':price*.995,'adjusted_close':price*1.005,'volume':1000})
  price_path=root/'prices.csv'; pd.DataFrame(panel).to_csv(price_path,index=False)
  p,t,a,skip=swing.paths(c,price_path,cfg)
  assert not p.empty and not t.empty and not a.empty
  assert p.path_data_quality.eq('OK').all()
  assert set(t.first_touch).issubset({'UP_FIRST','DOWN_FIRST','NEITHER','BOTH'})
  sc=swing.stability(h,m,cfg); assert not sc.empty
  assert swing.tmean(pd.Series([1,2,3,100]),.25)==2.5
 test_positive_volume_session_contract()


if __name__=='__main__':
 test_all(); print('Swing tendency invariant tests passed')
