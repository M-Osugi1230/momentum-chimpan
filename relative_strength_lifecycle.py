from __future__ import annotations
import html
from typing import Any
import pandas as pd

LIFECYCLE_COLUMNS=[
"previous_relative_strength_date","previous_relative_strength_score","previous_relative_strength_rank","previous_relative_strength_grade","previous_dual_outperformer","relative_strength_score_delta","relative_strength_rank_change","relative_strength_direction","relative_strength_strong_streak","dual_outperformer_streak","relative_strength_total_strong_days","relative_strength_run_count","relative_strength_first_date","relative_strength_best_score","relative_strength_best_rank","relative_strength_new_high","relative_strength_lifecycle","relative_strength_alert","relative_strength_trajectory_score","relative_strength_lifecycle_reason"]
LIFECYCLE_ORDER={"急加速":0,"再浮上":1,"加速":2,"主導継続":3,"主導":4,"継続":5,"初登場":6,"失速警戒":7,"崩れ":8,"低位":9}

def code(v:Any)->str:return str(v).strip().split('.')[0].zfill(4)
def num(v:Any)->float|None:
 x=pd.to_numeric(pd.Series([v]),errors='coerce').iloc[0];return None if pd.isna(x) else float(x)
def rn(r:pd.Series,c:str,d:float=0.)->float:
 x=num(r.get(c));return d if x is None else x
def txt(v:Any)->str:
 if v is None:return ''
 try:
  if pd.isna(v):return ''
 except (TypeError,ValueError):pass
 s=str(v).strip();return '' if s.lower() in {'','nan','none'} else s
def flag(v:Any)->bool:
 if isinstance(v,bool):return v
 if v is None:return False
 try:
  if pd.isna(v):return False
 except (TypeError,ValueError):pass
 return str(v).strip().lower() in {'true','1','yes','y'}

def prior(history:pd.DataFrame,today:str)->pd.DataFrame:
 cols=['date','code','relative_strength_score','relative_strength_rank','relative_strength_grade','dual_outperformer']
 if history is None or history.empty or not {'date','code'}.issubset(history.columns):return pd.DataFrame(columns=cols+['date_sort'])
 w=history.copy()
 for c in cols:
  if c not in w.columns:w[c]=None
 w['code']=w['code'].map(code);w['date_sort']=pd.to_datetime(w['date'],errors='coerce')
 w['relative_strength_score']=pd.to_numeric(w['relative_strength_score'],errors='coerce');w['relative_strength_rank']=pd.to_numeric(w['relative_strength_rank'],errors='coerce');w['dual_outperformer']=w['dual_outperformer'].map(flag)
 return w.dropna(subset=['date_sort','code']).loc[w['date'].astype(str)!=str(today),cols+['date_sort']].sort_values(['date_sort','code'])

def values(row:pd.Series,previous:pd.DataFrame,h:pd.DataFrame,dates:list[str],today:str)->dict[str,Any]:
 c=code(row.get('code'));score=rn(row,'relative_strength_score',50);rank=int(rn(row,'relative_strength_rank',9999));grade=txt(row.get('relative_strength_grade')) or 'C';dual=flag(row.get('dual_outperformer'))
 p=previous.loc[c] if c in previous.index else None
 if isinstance(p,pd.DataFrame):p=p.iloc[-1]
 pdate=txt(p.get('date')) if p is not None else '';ps=num(p.get('relative_strength_score')) if p is not None else None;prv=num(p.get('relative_strength_rank')) if p is not None else None;pr=int(prv) if prv is not None else None;pg=txt(p.get('relative_strength_grade')) if p is not None else '';pdul=flag(p.get('dual_outperformer')) if p is not None else False
 ds=None if ps is None else score-ps;dr=None if pr is None else pr-rank
 states={}
 if h is not None and not h.empty:
  for _,x in h.iterrows():states[pd.Timestamp(x['date_sort']).date().isoformat()]=(rn(x,'relative_strength_score')>=70,flag(x.get('dual_outperformer')))
 states[str(today)]=(score>=70,dual)
 def streak(i:int)->int:
  n=0
  for d in reversed(dates):
   if states.get(d,(False,False))[i]:n+=1
   else:break
  return n
 ss,sd=streak(0),streak(1);strong=[states.get(d,(False,False))[0] for d in dates];total=int(sum(strong));runs=0;active=False
 for q in strong:
  if q and not active:runs+=1
  active=q
 hs=pd.to_numeric(h.get('relative_strength_score',pd.Series(dtype=float)),errors='coerce').dropna() if h is not None and not h.empty else pd.Series(dtype=float);hr=pd.to_numeric(h.get('relative_strength_rank',pd.Series(dtype=float)),errors='coerce').dropna() if h is not None and not h.empty else pd.Series(dtype=float)
 bs=float(hs.max()) if not hs.empty else None;br=int(hr.min()) if not hr.empty else None;new=bs is None or score>bs or br is None or rank<br;best_s=score if bs is None else max(score,bs);best_r=rank if br is None else min(rank,br);hd=h['date_sort'].dropna().sort_values() if h is not None and not h.empty else pd.Series(dtype='datetime64[ns]');first=hd.iloc[0].date().isoformat() if not hd.empty else str(today)
 if ps is None:life='初登場'
 elif ps>=70 and score<55 and ds<=-12:life='崩れ'
 elif ds<=-8 or (dr is not None and dr<=-15):life='失速警戒'
 elif ps<55 and score>=65 and ds>=8:life='再浮上'
 elif score>=70 and (ds>=8 or (dr is not None and dr>=15)):life='急加速'
 elif score>=65 and (ds>=4 or (dr is not None and dr>=8)):life='加速'
 elif score>=70 and ss>=5:life='主導継続'
 elif score>=70:life='主導'
 elif score>=55:life='継続'
 else:life='低位'
 alert='調査優先' if life in {'急加速','再浮上'} else '継続確認' if life in {'加速','主導継続','主導'} else '警戒' if life in {'失速警戒','崩れ'} else '観察';direction='履歴開始' if ds is None else '改善' if ds>=4 or (dr is not None and dr>=8) else '悪化' if ds<=-4 or (dr is not None and dr<=-8) else '横ばい'
 trajectory=score+(0 if ds is None else min(max(ds,-15),15)*1.2)+(0 if dr is None else min(max(dr,-30),30)*.35)+min(ss,10)*1.2+min(sd,10)*.8;trajectory=round(min(max(trajectory,0),100),1)
 why=[f'相対強度{score:.1f}点・{grade}']+([] if ds is None else [f'前回比{ds:+.1f}点'])+([] if dr is None else [f'順位{dr:+d}'])+([f'A以上{ss}日'] if ss else [])+([f'市場・同業双方超過{sd}日'] if sd else [])+(['過去最高水準更新'] if new else [])
 return dict(previous_relative_strength_date=pdate,previous_relative_strength_score=ps,previous_relative_strength_rank=pr,previous_relative_strength_grade=pg,previous_dual_outperformer=pdul,relative_strength_score_delta=ds,relative_strength_rank_change=dr,relative_strength_direction=direction,relative_strength_strong_streak=ss,dual_outperformer_streak=sd,relative_strength_total_strong_days=total,relative_strength_run_count=runs,relative_strength_first_date=first,relative_strength_best_score=best_s,relative_strength_best_rank=best_r,relative_strength_new_high=bool(new),relative_strength_lifecycle=life,relative_strength_alert=alert,relative_strength_trajectory_score=trajectory,relative_strength_lifecycle_reason=' / '.join(why))

def attach(frame:pd.DataFrame,history:pd.DataFrame,today:str)->pd.DataFrame:
 if frame is None or frame.empty:
  r=frame.copy() if frame is not None else pd.DataFrame()
  for c in LIFECYCLE_COLUMNS:
   if c not in r.columns:r[c]=pd.Series(dtype='object')
  return r
 r=frame.copy();r['code']=r['code'].map(code);p=prior(history,today);dates=sorted(set(p.get('date',pd.Series(dtype=str)).astype(str))|{str(today)},key=pd.Timestamp);latest=p.sort_values('date_sort').drop_duplicates('code',keep='last');previous=latest.set_index('code',drop=False) if not latest.empty else pd.DataFrame();groups={c:g.sort_values('date_sort') for c,g in p.groupby('code')};v=r.apply(lambda x:pd.Series(values(x,previous,groups.get(code(x.get('code')),pd.DataFrame()),dates,today)),axis=1)
 for c in LIFECYCLE_COLUMNS:r[c]=v[c].values
 return r

def build_table(frame:pd.DataFrame)->pd.DataFrame:
 cols=['relative_strength_lifecycle','relative_strength_alert','relative_strength_trajectory_score','relative_strength_rank','rank','code','name','sector33','score','relative_strength_score','relative_strength_grade','dual_outperformer','previous_relative_strength_date','previous_relative_strength_score','relative_strength_score_delta','previous_relative_strength_rank','relative_strength_rank_change','relative_strength_direction','relative_strength_strong_streak','dual_outperformer_streak','relative_strength_total_strong_days','relative_strength_run_count','relative_strength_first_date','relative_strength_best_score','relative_strength_best_rank','relative_strength_new_high','market_relative_20d','sector_relative_20d','market_relative_60d','sector_relative_60d','relative_strength_lifecycle_reason','trading_value','volume_ratio']
 if frame is None or frame.empty:return pd.DataFrame(columns=cols)
 r=frame.copy();r['_order']=r.get('relative_strength_lifecycle',pd.Series(index=r.index,dtype=str)).map(LIFECYCLE_ORDER).fillna(99);r=r.sort_values(['_order','relative_strength_trajectory_score','relative_strength_score','rank'],ascending=[True,False,False,True]).drop(columns='_order');return r[[c for c in cols if c in r.columns]].reset_index(drop=True)
def count(frame:pd.DataFrame,status:str)->int:return 0 if frame is None or frame.empty or 'relative_strength_lifecycle' not in frame.columns else int((frame['relative_strength_lifecycle']==status).sum())
def delta(v:Any,d:int=1)->str:
 x=num(v);return '-' if x is None else (f'{x:+.{d}f}' if d else f'{int(x):+d}')

def plain_section(frame:pd.DataFrame,positive_limit:int=8,warning_limit:int=5)->list[str]:
 if frame is None or frame.empty:return ['【相対強度ライフサイクル】','比較可能な相対強度履歴がありません。','']
 pos=['急加速','再浮上','加速','主導継続','主導'];warn=['失速警戒','崩れ'];counts={s:count(frame,s) for s in pos+warn};lines=['【相対強度ライフサイクル】','前回差・順位変化・継続日数から強さの推移を判定します。売買推奨ではありません。',' / '.join(f'{s} {counts[s]}件' for s in pos+warn if counts[s]) or '大きな変化なし']
 for title,statuses,limit in [('強さが改善・継続',pos,positive_limit),('失速・崩れ警戒',warn,warning_limit)]:
  sub=frame[frame['relative_strength_lifecycle'].isin(statuses)].head(limit)
  if sub.empty:continue
  lines.append('■ '+title)
  for _,r in sub.iterrows():lines.append(f"{txt(r.get('relative_strength_lifecycle'))}｜#{int(rn(r,'relative_strength_rank'))} {r['code']} {r['name']}｜{rn(r,'relative_strength_score'):.1f}点｜前回比 {delta(r.get('relative_strength_score_delta'))}点｜順位 {delta(r.get('relative_strength_rank_change'),0)}｜A以上 {int(rn(r,'relative_strength_strong_streak'))}日｜双方超過 {int(rn(r,'dual_outperformer_streak'))}日")
 lines.append('');return lines

def html_section(frame:pd.DataFrame,positive_limit:int=8,warning_limit:int=5)->str:
 if frame is None or frame.empty:return '<div><b>相対強度ライフサイクル</b><div>比較可能な相対強度履歴がありません。</div></div>'
 pos=['急加速','再浮上','加速','主導継続','主導'];warn=['失速警戒','崩れ'];colors={'急加速':'#b45309','再浮上':'#7c3aed','加速':'#15803d','主導継続':'#1d4ed8','主導':'#0369a1','失速警戒':'#c2410c','崩れ':'#b91c1c'};counts={s:count(frame,s) for s in pos+warn};groups=[]
 for title,statuses,limit in [('強さが改善・継続',pos,positive_limit),('失速・崩れ警戒',warn,warning_limit)]:
  items=[]
  for _,r in frame[frame['relative_strength_lifecycle'].isin(statuses)].head(limit).iterrows():
   s=txt(r.get('relative_strength_lifecycle'));items.append(f'<div style="border-top:1px solid #e5e7eb;padding:9px 0"><b>{html.escape(s)}｜#{int(rn(r,"relative_strength_rank"))} {html.escape(str(r["code"]))} {html.escape(str(r["name"]))}</b><span style="float:right;color:{colors.get(s,"#475569")}">{rn(r,"relative_strength_score"):.1f}点</span><div style="clear:both;font-size:11px">前回比 {delta(r.get("relative_strength_score_delta"))}点 ・ 順位 {delta(r.get("relative_strength_rank_change"),0)} ・ A以上 {int(rn(r,"relative_strength_strong_streak"))}日 ・ 双方超過 {int(rn(r,"dual_outperformer_streak"))}日</div></div>')
  if items:groups.append(f'<div style="font-weight:900;margin-top:10px">{html.escape(title)}</div>'+''.join(items))
 summary=' ・ '.join(f'{s} {counts[s]}件' for s in pos+warn if counts[s]) or '大きな変化なし';return f'<div style="background:#fff;border:2px solid #7c3aed;border-radius:18px;padding:16px;margin-top:14px"><div style="font-size:18px;font-weight:900;color:#581c87">相対強度ライフサイクル</div><div style="font-size:12px;color:#64748b">前回差・順位変化・継続日数から強さの推移を判定します。売買推奨ではありません。</div><div style="font-size:12px;font-weight:800">{html.escape(summary)}</div>{"".join(groups)}</div>'
