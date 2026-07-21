"""Invariant tests for Detailed OOS Evidence v2."""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import detailed_oos_analysis as core


def protocol_file(root: Path) -> Path:
    payload={
      'mode':'RESEARCH_ONLY_NON_PROMOTABLE','promotion_evidence_allowed':False,
      'automatic_strategy_change':False,
      'evaluation':{'horizons':[1,5,20],'top_sizes':[10,30,100],'round_trip_cost_bps':20,'random_placebo_repetitions':20},
      'evidence_gates':{'primary_horizons':[5,20],'primary_top_sizes':[10,30],'minimum_years_positive':1,
                        'minimum_rank_ic_positive_rate':0.5,'minimum_leave_one_sector_positive_rate':0.5},
    }
    path=root/'protocol.yaml'; path.write_text(yaml.safe_dump(payload),encoding='utf-8'); return path


def synthetic_ranking() -> pd.DataFrame:
    records=[]
    for day in pd.to_datetime(['2024-01-05','2024-01-12','2024-01-19']):
      for i in range(1,61):
        reason=''
        if i>45: reason='TWENTY_DAY_OVERHEATED'
        elif i>35: reason='VOLUME_SPIKE_OVERHEATED'
        records.append({
          'date':day,'code':str(i).zfill(4),'name':f'S{i}','sector33':f'Sector{i%3}',
          'rank':i,'score':101-i,'healthy_rank':i if i<=35 else np.nan,
          'healthy_selection_score':105-i,'healthy_eligible':i<=35,
          'healthy_v2_rank':i if i<=35 else np.nan,'healthy_v2_selection_score':104-i,
          'healthy_v2_eligible':i<=35,'healthy_exclusion_reasons':reason,
          'return_5d':(61-i)/1000,'return_20d':(61-i)/500,
          'healthy_relative_strength_score':101-i,'ytd_high_streak':61-i,
          'volume_ratio':1+i/100,'ma20_deviation':0.04+i/10000,'above_ma20':True,
        })
    return pd.DataFrame(records)


def synthetic_outcomes(ranking: pd.DataFrame) -> pd.DataFrame:
    records=[]
    for row in ranking.itertuples(index=False):
      for horizon in (1,5,20):
        value=(61-row.rank)/1000*horizon/5
        records.append({
          'signal_date':row.date,'code':row.code,'sector33':row.sector33,'horizon_sessions':horizon,
          'rank':row.rank,'score':row.score,'healthy_rank':row.healthy_rank,
          'healthy_selection_score':row.healthy_selection_score,'healthy_v2_rank':row.healthy_v2_rank,
          'healthy_v2_selection_score':row.healthy_v2_selection_score,'net_return':value,
          'market_excess_net':value-0.01,'mfe':value+0.02,'mae':-0.01,
        })
    return pd.DataFrame(records)


def synthetic_selections(ranking: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    frames=[]
    for method,rank_col,score_col in [
      ('production','rank','score'),('healthy_v1','healthy_rank','healthy_selection_score'),
      ('balanced_v2','healthy_v2_rank','healthy_v2_selection_score')]:
      selected=ranking[pd.to_numeric(ranking[rank_col],errors='coerce').le(30)][['date','code','name','sector33',rank_col,score_col]].copy()
      selected=selected.rename(columns={'date':'signal_date',rank_col:'method_rank',score_col:'method_score'})
      selected['method']=method
      selected=selected.merge(outcomes[['signal_date','code','horizon_sessions','net_return','market_excess_net','mfe','mae']],on=['signal_date','code'])
      selected['sector_excess_net']=selected['market_excess_net']
      selected['entry_date']=selected['signal_date']+pd.Timedelta(days=1)
      selected['exit_date']=selected['entry_date']+pd.to_timedelta(selected['horizon_sessions'],unit='D')
      frames.append(selected)
    return pd.concat(frames,ignore_index=True)


def test_all() -> None:
    ranking=synthetic_ranking(); outcomes=synthetic_outcomes(ranking); selections=synthetic_selections(ranking,outcomes)
    with tempfile.TemporaryDirectory() as tmp:
      protocol,raw=core.load_protocol(protocol_file(Path(tmp)))
      assert protocol.horizons==(1,5,20)
      assert raw['promotion_evidence_allowed'] is False
    daily,summary=core.rank_ic_from_outcomes(outcomes)
    assert len(daily)==27
    assert summary['mean_rank_ic'].gt(0.99).all()
    monotonic=core.rank_monotonicity_from_outcomes(outcomes)
    assert not monotonic.empty
    top=monotonic[(monotonic.method=='production')&(monotonic.horizon_sessions==5)]
    assert top.loc[top.rank_band=='1-10','mean_net_return'].iloc[0] > top.loc[top.rank_band=='31-50','mean_net_return'].iloc[0]
    calibration=core.score_calibration_from_outcomes(outcomes)
    assert set(calibration.score_decile)==set(range(1,11))
    selections['eligible']=True; selections['year']=2024; selections['max_close_drawdown']=np.nan
    method_summary=core.method_summary(selections,(10,30))
    assert set(method_summary.method)==set(core.METHODS)
    detail,lifecycle=core.signal_lifecycle(selections,(10,30))
    assert 'FIRST_PICK' in set(detail.lifecycle_state)
    assert not lifecycle.empty
    ablation=core.ablation_summary(ranking,outcomes,(10,30),(1,5,20))
    assert 'ORIGINAL_V1' in set(ablation.ablation_variant)
    assert any(v.startswith('REMOVE_') for v in set(ablation.ablation_variant))
    baselines=core.summarize_candidate_methods(core.baseline_candidates(ranking),outcomes,(10,30))
    assert {'baseline_return_5d','baseline_return_20d','baseline_simple_balanced'}.issubset(set(baselines.method))
    loso=core.leave_one_sector_out(selections,(10,30))
    placebo=core.random_placebo(ranking,outcomes,method_summary,(10,30),(1,5,20),10)
    assert not loso.empty and not placebo.empty
    assert placebo.one_sided_empirical_p.between(0,1).all()
    dates=pd.date_range('2024-01-01',periods=100,freq='B')
    panel=[]
    for code in ranking.code.unique():
      for j,date in enumerate(dates):
        price=100+j
        panel.append({'date':date,'code':code,'adjusted_open':price,'adjusted_high':price*1.01,'adjusted_low':price*0.995,'adjusted_close':price*1.005})
    candidates=core.top_method_candidates(ranking,10)
    path_detail,path_summary=core.path_quality(candidates,core.price_lookup(pd.DataFrame(panel)),20)
    assert not path_detail.empty and not path_summary.empty
    assert path_detail.first_touch_5pct.isin({'UP_5_FIRST','NEITHER'}).all()


if __name__=='__main__':
    test_all(); print('Detailed OOS invariant tests passed')
