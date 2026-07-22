"""Research-only 20/40/60-session swing tendency study."""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np, pandas as pd, yaml
import healthy_rank_v3

VERSION='2026-07-22-swing-tendency-v1'; METHODS=('production','healthy_v1','healthy_v3'); COST=.002

def sha(path):
 d=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): d.update(b)
 return d.hexdigest()

def load_protocol(path):
 raw=yaml.safe_load(Path(path).read_text()) or {}
 assert raw.get('mode')=='RESEARCH_ONLY_NON_PROMOTABLE'
 for k in ['promotion_evidence_allowed','automatic_strategy_change','automatic_exit_rule_change','automatic_threshold_optimization','production_ranking_change']: assert raw.get(k) is False
 e=raw['evaluation']; p=raw['price_path']; s=raw['signal_states']; r=raw['regimes']; i=raw['interpretation']
 return raw,dict(tops=tuple(e['top_sizes']), horizons=tuple(e['horizons']), focus=tuple(e['focus_horizons']), intervals=tuple(map(tuple,e['marginal_intervals'])), costs=tuple(e['round_trip_cost_bps']), trim=e['robust_statistics']['trim_fraction'], boots=e['robust_statistics']['paired_bootstrap_iterations'], ups=tuple(p['upside_thresholds']), downs=tuple(p['downside_thresholds']), pairs=tuple(map(tuple,p['principal_first_touch_pairs'])), entry_gap=p['maximum_entry_gap_days'], session_gap=p['maximum_session_gap_days'], jump=p['maximum_adjacent_price_multiplier'], rank_move=s['rank_improvement_minimum'], weak=r['breadth_weak_max'], strong=r['breadth_strong_min'], minyears=i['minimum_years_same_direction'], minobs=i['minimum_observations_per_cell'], mindates=i['minimum_signal_dates_per_cell'])

def tmean(s,f=.05):
 a=pd.to_numeric(s,errors='coerce').dropna().sort_values().to_numpy(float); n=int(len(a)*f)
 return np.nan if not len(a) else float(a[n:-n].mean() if n and 2*n<len(a) else a.mean())

def summarize(g,cfg):
 out={'observations':len(g),'stocks':g.code.nunique(),'dates':g.signal_date.nunique(),'mean_20bps':g.groupby('signal_date').net_return.mean().mean(),'median_20bps':g.net_return.median(),'trimmed_mean_20bps':tmean(g.net_return,cfg['trim']),'win_rate_20bps':g.net_return.gt(0).mean(),'market_excess_20bps':g.market_excess_net.mean(),'mean_mfe':g.mfe.mean(),'mean_mae':g.mae.mean()}
 for b in cfg['costs']:
  x=g.net_return-(b-20)/10000; out[f'mean_{b}bps']=x.groupby(g.signal_date).mean().mean(); out[f'median_{b}bps']=x.median(); out[f'win_rate_{b}bps']=x.gt(0).mean()
 return out

def load_events(path,cfg):
 cols=['signal_date','code','name','sector33','horizon_sessions','net_return','market_excess_net','mfe','mae','method_rank','method_score','method']
 x=pd.read_csv(path,usecols=lambda c:c in cols,dtype={'code':str},low_memory=False); x.code=x.code.astype(str).str.split('.').str[0].str.zfill(4); x.signal_date=pd.to_datetime(x.signal_date).dt.normalize()
 for c in ['horizon_sessions','net_return','market_excess_net','mfe','mae','method_rank','method_score']: x[c]=pd.to_numeric(x[c],errors='coerce')
 x=x[x.method.isin(METHODS)&x.horizon_sessions.isin(cfg['horizons'])&x.net_return.notna()].copy(); x['year']=x.signal_date.dt.year; x['gross_return']=x.net_return+COST
 return x

def load_candidates(path,cfg):
 cols=['date','code','name','sector33','rank','score','healthy_rank','healthy_selection_score','healthy_eligible','return_5d','return_20d','return_60d','healthy_relative_strength_score','ma20_deviation','volume_ratio','trading_value','above_ma20','healthy_drawdown_from_recent_high']
 r=pd.read_csv(path,usecols=lambda c:c in cols,dtype={'code':str},low_memory=False); r.code=r.code.astype(str).str.split('.').str[0].str.zfill(4); r.date=pd.to_datetime(r.date).dt.normalize(); r=healthy_rank_v3.attach(r)
 specs=[('production','rank','score'),('healthy_v1','healthy_rank','healthy_selection_score'),('healthy_v3','healthy_v3_rank','healthy_v3_selection_score')]; fs=[]
 base=['date','code','name','sector33','return_5d','return_20d','return_60d','ma20_deviation','volume_ratio','trading_value','above_ma20','healthy_drawdown_from_recent_high']
 for m,rc,sc in specs:
  f=r[[c for c in base+[rc,sc] if c in r]].copy(); f[rc]=pd.to_numeric(f[rc],errors='coerce'); f=f[f[rc].notna()&f[rc].le(max(cfg['tops']))].rename(columns={'date':'signal_date',rc:'method_rank',sc:'method_score'}); f['method']=m; fs.append(f)
 c=pd.concat(fs,ignore_index=True); c=states(c,cfg['rank_move']); c=regimes(r,c,cfg)
 return r,c

def states(c,move):
 outs=[]
 for m,f in c.groupby('method',sort=False):
  f=f.sort_values(['signal_date','method_rank','code']); last={},{}; seen,rank=last; prev=None; streak={}; chunks=[]
  for d,g in f.groupby('signal_date',sort=True):
   g=g.copy(); st=[]; pr=[]; cs=[]
   for row in g.itertuples():
    code=str(row.code); old=rank.get(code,np.nan); ld=seen.get(code)
    if ld is None: z='FIRST_PICK'; n=1
    elif prev is None or ld!=prev: z='REENTRY'; n=1
    elif old-row.method_rank>=move: z='IMPROVING'; n=streak.get(code,0)+1
    elif row.method_rank-old>=move: z='DETERIORATING'; n=streak.get(code,0)+1
    else: z='STABLE_REPEAT'; n=streak.get(code,0)+1
    st.append(z); pr.append(old); cs.append(n); seen[code]=d; rank[code]=row.method_rank; streak[code]=n
   g['signal_state']=st; g['previous_rank']=pr; g['selection_streak']=cs; g['rank_change']=g.previous_rank-g.method_rank; chunks.append(g); prev=d
  outs.append(pd.concat(chunks,ignore_index=True))
 return pd.concat(outs,ignore_index=True)

def regimes(r,c,cfg):
 b=r.above_ma20.astype(str).str.lower().isin(['true','1','yes','y']); d=r.assign(_b=b).groupby('date').agg(breadth=('_b','mean'),market_return20=('return_20d','median'),market_vol20=('return_20d','std')).reset_index().rename(columns={'date':'signal_date'}); d['breadth_regime']=np.select([d.breadth.le(cfg['weak']),d.breadth.ge(cfg['strong'])],['WEAK','STRONG'],default='MIXED'); d['trend_regime']=np.where(d.market_return20.gt(0),'UP','DOWN'); q=d.market_vol20.quantile([1/3,2/3]).values; d['vol_regime']=np.select([d.market_vol20.le(q[0]),d.market_vol20.ge(q[1])],['LOW','HIGH'],default='MID')
 c=c.merge(d,on='signal_date',how='left'); c['liquidity_pct']=pd.to_numeric(c.trading_value,errors='coerce').groupby(c.signal_date).rank(pct=True); c['liquidity_band']=pd.cut(c.liquidity_pct,[0,.33,.67,1],labels=['LOW','MID','HIGH'],include_lowest=True); c['ma20_band']=pd.cut(pd.to_numeric(c.ma20_deviation,errors='coerce'),[-np.inf,.03,.08,np.inf],labels=['LOW_OR_NEAR','MODERATE','EXTENDED']); return c

def summaries(e,cfg):
 rec=[]
 for top in cfg['tops']:
  z=e[e.method_rank.le(top)]
  for keys,g in z.groupby(['year','method','horizon_sessions']): rec.append(dict(year=int(keys[0]),method=keys[1],top_size=top,horizon_sessions=int(keys[2]),**summarize(g,cfg)))
  for keys,g in z.groupby(['method','horizon_sessions']): rec.append(dict(year='ALL',method=keys[0],top_size=top,horizon_sessions=int(keys[1]),**summarize(g,cfg)))
 return pd.DataFrame(rec)

def marginal(e,cfg):
 keys=['signal_date','code','method','method_rank','year']; p=e.pivot_table(index=keys,columns='horizon_sessions',values='gross_return',aggfunc='first').reset_index(); det=[]
 for a,b in cfg['intervals']:
  if b not in p: continue
  v=p[b] if a==0 else (1+p[b])/(1+p[a])-1
  z=p[keys].copy(); z['interval_start']=a; z['interval_end']=b; z['interval_return']=v; det.append(z.dropna(subset=['interval_return']))
 det=pd.concat(det,ignore_index=True); rec=[]
 for top in cfg['tops']:
  z=det[det.method_rank.le(top)]
  for k,g in z.groupby(['year','method','interval_start','interval_end']): rec.append({'year':int(k[0]),'method':k[1],'top_size':top,'interval_start':k[2],'interval_end':k[3],'observations':len(g),'dates':g.signal_date.nunique(),'mean_interval':g.groupby('signal_date').interval_return.mean().mean(),'median_interval':g.interval_return.median(),'trimmed_interval':tmean(g.interval_return,cfg['trim']),'win_rate':g.interval_return.gt(0).mean()})
 return det,pd.DataFrame(rec)

def context(e,c,cfg,groups):
 cols=['signal_date','code','method','signal_state','selection_streak','rank_change','breadth_regime','trend_regime','vol_regime','liquidity_band','ma20_band']; x=e.merge(c[[k for k in cols if k in c]].drop_duplicates(['signal_date','code','method']),on=['signal_date','code','method'],how='left'); rec=[]
 for top in cfg['tops']:
  z=x[x.method_rank.le(top)&x.horizon_sessions.isin(cfg['focus'])]
  for k,g in z.groupby(['year','method','horizon_sessions']+groups,observed=True):
   if not isinstance(k,tuple): k=(k,)
   d=dict(zip(['year','method','horizon_sessions']+groups,k)); d['top_size']=top; d.update(summarize(g,cfg)); rec.append(d)
 return x,pd.DataFrame(rec)

def paths(c,price_path,cfg):
 cols=['date','code','adjusted_open','adjusted_high','adjusted_low','adjusted_close','volume']; p=pd.read_csv(price_path,usecols=lambda x:x in cols,dtype={'code':str},low_memory=False); p.code=p.code.astype(str).str.split('.').str[0].str.zfill(4); p.date=pd.to_datetime(p.date).dt.normalize()
 for x in cols[2:]: p[x]=pd.to_numeric(p[x],errors='coerce')
 p=p.dropna().query('volume>0'); look={k:(g.date.to_numpy('datetime64[ns]'),g.adjusted_open.to_numpy(float),g.adjusted_high.to_numpy(float),g.adjusted_low.to_numpy(float),g.adjusted_close.to_numpy(float)) for k,g in p.sort_values(['code','date']).groupby('code',sort=False)}; del p
 pr=[]; tr=[]; ar=[]; skips={}; D=np.timedelta64(1,'D')
 for row in c.drop_duplicates(['method','signal_date','code']).itertuples():
  a=look.get(str(row.code)); reason='OK'
  if a is None: reason='NO_CODE'
  else:
   dates,o,h,l,cl=a; pos=np.searchsorted(dates,np.datetime64(row.signal_date,'ns'),side='right'); end=min(pos+60,len(dates))
   if pos>=len(dates): reason='NO_ENTRY'
   else:
    wd=dates[pos:end]; wo=o[pos:end]; wh=h[pos:end]; wl=l[pos:end]; wc=cl[pos:end]; gap=int((wd[0]-np.datetime64(row.signal_date,'ns'))/D)
    if gap<1 or gap>cfg['entry_gap']: reason='STALE_ENTRY'
    elif len(wd)>1 and int((np.diff(wd)/D).max())>cfg['session_gap']: reason='SESSION_GAP'
    elif len(wc)>1 and (np.any(wc[1:]/wc[:-1]>cfg['jump']) or np.any(wc[1:]/wc[:-1]<1/cfg['jump'])): reason='PRICE_JUMP'
    elif not np.isfinite(np.r_[wo,wh,wl,wc]).all(): reason='INVALID'
  if reason!='OK': skips[reason]=skips.get(reason,0)+1; continue
  ep=wo[0]; hi=wh/ep-1; lo=wl/ep-1; close=wc/ep-1; run=np.maximum.accumulate(np.r_[0,close]); dd=(1+np.r_[0,close])/(1+run)-1
  base={'method':row.method,'signal_date':row.signal_date,'year':row.signal_date.year,'code':row.code,'name':row.name,'sector33':row.sector33,'method_rank':row.method_rank,'signal_state':row.signal_state,'breadth_regime':row.breadth_regime,'trend_regime':row.trend_regime,'vol_regime':row.vol_regime,'liquidity_band':str(row.liquidity_band),'path_data_quality':'OK','mfe60':hi.max(),'mae60':lo.min(),'time_mfe':hi.argmax()+1,'time_mae':lo.argmin()+1,'max_dd60':dd.min()}
  for q in cfg['focus']: base[f'return{q}']=close[q-1] if len(close)>=q else np.nan
  pr.append(base); uh={}
  for u in cfg['ups']:
   hit=np.flatnonzero(hi>=u); sess=int(hit[0]+1) if len(hit) else None; uh[u]=sess; ar.append({**{k:base[k] for k in ['method','signal_date','year','code','method_rank','signal_state']},'upside_threshold':u,'reached':sess is not None,'first_reach_session':sess,'pre_profit_mae':np.min(lo if sess is None else lo[:sess])})
  dh={d:(int(x[0]+1) if len(x:=np.flatnonzero(lo<=d)) else None) for d in cfg['downs']}
  for u,d in cfg['pairs']:
   us,ds=uh[u],dh[d]; state='NEITHER' if us is None and ds is None else 'BOTH' if us==ds else 'UP_FIRST' if ds is None or (us is not None and us<ds) else 'DOWN_FIRST'; tr.append({**{k:base[k] for k in ['method','signal_date','year','code','method_rank','signal_state','breadth_regime','trend_regime','vol_regime','liquidity_band']},'upside_threshold':u,'downside_threshold':d,'up_session':us,'down_session':ds,'first_touch':state})
 return pd.DataFrame(pr),pd.DataFrame(tr),pd.DataFrame(ar),pd.DataFrame([{'reason':k,'count':v} for k,v in skips.items()])

def stability(h,m,cfg):
 y=h[h.year.astype(str)!='ALL'].copy(); rec=[]
 for method in METHODS:
  for top in cfg['tops']:
   for q in cfg['focus']:
    z=y[(y.method==method)&(y.top_size==top)&(y.horizon_sessions==q)]; rec.append({'method':method,'top_size':top,'period':str(q),'years':z.year.nunique(),'positive_mean_years':z.mean_20bps.gt(0).sum(),'positive_trimmed_years':z.trimmed_mean_20bps.gt(0).sum(),'positive_median_years':z.median_20bps.gt(0).sum(),'positive_market_excess_years':z.market_excess_20bps.gt(0).sum(),'consistent':len(z)>0 and z.mean_20bps.gt(0).sum()>=cfg['minyears'] and z.trimmed_mean_20bps.gt(0).sum()>=cfg['minyears'] and z.observations.min()>=cfg['minobs'] and z.dates.min()>=cfg['mindates'],'status':'DESCRIPTIVE_ONLY'})
   for a,b in cfg['intervals']:
    z=m[(m.method==method)&(m.top_size==top)&(m.interval_start==a)&(m.interval_end==b)]; rec.append({'method':method,'top_size':top,'period':f'{a}-{b}','years':z.year.nunique(),'positive_mean_years':z.mean_interval.gt(0).sum(),'positive_trimmed_years':z.trimmed_interval.gt(0).sum(),'positive_median_years':z.median_interval.gt(0).sum(),'positive_market_excess_years':np.nan,'consistent':len(z)>0 and z.mean_interval.gt(0).sum()>=cfg['minyears'] and z.trimmed_interval.gt(0).sum()>=cfg['minyears'],'status':'DESCRIPTIVE_ONLY'})
 return pd.DataFrame(rec)

def report(h,m,sc):
 z=h[(h.year.astype(str)=='ALL')&h.top_size.isin([10,30])&h.horizon_sessions.isin([20,40,60])]; L=['# Swing Tendency Study v1','', '> 株価予測や売買推奨ではなく、複数年で方向がそろう傾向の研究です。','', '## 20・40・60営業日','', '|手法|Top|期間|平均|中央値|5%トリム|市場超過|勝率|','|---|---:|---:|---:|---:|---:|---:|---:|']
 for r in z.sort_values(['method','top_size','horizon_sessions']).itertuples(): L.append(f'|{r.method}|{r.top_size}|{r.horizon_sessions}日|{r.mean_20bps:.2%}|{r.median_20bps:.2%}|{r.trimmed_mean_20bps:.2%}|{r.market_excess_20bps:.2%}|{r.win_rate_20bps:.1%}|')
 L+=['','## 複数年で方向がそろったセル','']; ok=sc[sc.consistent==True]; L+=['- 固定条件を満たすセルはありません。'] if ok.empty else [f'- {r.method} Top{r.top_size} {r.period}日' for r in ok.itertuples()]; L+=['','## 注意','- 現在上場銘柄を過去へ遡るためサバイバーシップ・構成銘柄バイアスがあります。','- 5取引日間隔、重複保有を含む記述統計であり、独立取引の損益曲線ではありません。','- 本番ルール、出口、ペーパー取引、実注文は変更していません。']; return '\n'.join(L)

def main():
 a=argparse.ArgumentParser(); a.add_argument('--events',required=True); a.add_argument('--enriched-ranking',required=True); a.add_argument('--prices',required=True); a.add_argument('--protocol',required=True); a.add_argument('--output-dir',required=True); a.add_argument('--strict',action='store_true'); x=a.parse_args(); out=Path(x.output_dir); out.mkdir(parents=True,exist_ok=True); raw,cfg=load_protocol(x.protocol)
 e=load_events(x.events,cfg); r,c=load_candidates(x.enriched_ranking,cfg); h=summaries(e,cfg); md,m=marginal(e,cfg); ex,state=context(e,c,cfg,['signal_state']); _,reg=context(e,c,cfg,['breadth_regime','trend_regime','vol_regime']); _,liq=context(e,c,cfg,['liquidity_band','ma20_band']); pd_,touch,adv,skip=paths(c,x.prices,cfg); sc=stability(h,m,cfg)
 files={'swing_horizon_summary.csv':h,'marginal_holding_return.csv':m,'marginal_holding_detail.csv':md,'signal_state_swing_summary.csv':state,'regime_swing_summary.csv':reg,'liquidity_swing_summary.csv':liq,'swing_path_detail.csv':pd_,'threshold_first_touch.csv':touch,'pre_profit_adverse_excursion.csv':adv,'path_skip_audit.csv':skip,'swing_stability_scorecard.csv':sc}
 for n,f in files.items(): f.to_csv(out/n,index=False)
 (out/'swing_tendency_report_ja.md').write_text(report(h,m,sc),encoding='utf-8'); man={'version':VERSION,'generated_at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),'years':sorted(map(int,e.year.unique())),'methods':sorted(e.method.unique()),'horizons':sorted(map(int,e.horizon_sessions.unique())),'event_rows':len(e),'candidate_rows':len(c),'path_rows':len(pd_),'research_only':True,'promotion_evidence_allowed':False,'automatic_strategy_change':False,'automatic_exit_rule_change':False,'production_state_mutations':[],'healthy_v1_eligibility_mutations':[],'events_sha256':sha(x.events),'ranking_sha256':sha(x.enriched_ranking),'prices_sha256':sha(x.prices),'protocol_sha256':sha(x.protocol)}; (out/'manifest.json').write_text(json.dumps(man,ensure_ascii=False,indent=2))
 if x.strict:
  assert set(man['years'])=={2018,2019,2020,2021}; assert set(man['methods'])==set(METHODS); assert not any(f.empty for f in [h,m,state,reg,liq,pd_,touch,adv,sc]); assert set(pd_.year.unique())=={2018,2019,2020,2021}; assert pd_.path_data_quality.eq('OK').all(); assert e.net_return.abs().max()<20; assert not man['production_state_mutations'] and not man['healthy_v1_eligibility_mutations']
 print(json.dumps(man,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
