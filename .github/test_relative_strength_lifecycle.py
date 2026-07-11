from __future__ import annotations
import inspect,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pandas as pd
import main
import relative_strength_lifecycle as rs
TODAY="2026-07-10";DATES=["2026-07-06","2026-07-07","2026-07-08","2026-07-09"];history_rows=[]
def add_history(code,scores,ranks,duals):
 for date,score,rank,dual in zip(DATES,scores,ranks,duals):history_rows.append({"date":date,"code":code,"relative_strength_score":score,"relative_strength_rank":rank,"relative_strength_grade":"A" if score>=70 else "B" if score>=55 else "C","dual_outperformer":dual})
add_history("1001",[58,61,65,66],[70,60,48,40],[False]*4);add_history("1002",[82,80,78,76],[10,12,14,15],[True]*4);add_history("1003",[44,46,48,50],[90,85,82,80],[False]*4);add_history("1004",[72,73,74,75],[25,24,23,22],[True]*4);add_history("1005",[78,79,80,78],[18,17,16,15],[True]*4);add_history("1007",[55,57,59,61],[60,56,52,48],[False]*4)
history=pd.DataFrame(history_rows)
current=pd.DataFrame([
{"rank":11,"code":"1001","name":"急加速","sector33":"情報・通信業","score":80,"relative_strength_score":78,"relative_strength_rank":18,"relative_strength_grade":"A","dual_outperformer":True,"market_relative_20d":.12,"sector_relative_20d":.08,"market_relative_60d":.18,"sector_relative_60d":.10},
{"rank":12,"code":"1002","name":"崩れ","sector33":"電気機器","score":76,"relative_strength_score":52,"relative_strength_rank":60,"relative_strength_grade":"C","dual_outperformer":False},
{"rank":13,"code":"1003","name":"再浮上","sector33":"サービス業","score":74,"relative_strength_score":68,"relative_strength_rank":38,"relative_strength_grade":"B","dual_outperformer":True},
{"rank":14,"code":"1004","name":"主導継続","sector33":"機械","score":73,"relative_strength_score":76,"relative_strength_rank":20,"relative_strength_grade":"A","dual_outperformer":True},
{"rank":15,"code":"1005","name":"失速警戒","sector33":"化学","score":72,"relative_strength_score":68,"relative_strength_rank":35,"relative_strength_grade":"B","dual_outperformer":False},
{"rank":16,"code":"1006","name":"初登場","sector33":"小売業","score":71,"relative_strength_score":64,"relative_strength_rank":45,"relative_strength_grade":"B","dual_outperformer":False},
{"rank":17,"code":"1007","name":"加速","sector33":"卸売業","score":70,"relative_strength_score":67,"relative_strength_rank":39,"relative_strength_grade":"B","dual_outperformer":False}])
current["trading_value"]=1_000_000_000;current["volume_ratio"]=2.0
original=current.set_index("code")["rank"].to_dict();enriched=rs.attach(current,history,TODAY);status=enriched.set_index("code")["relative_strength_lifecycle"].to_dict()
assert status=={"1001":"急加速","1002":"崩れ","1003":"再浮上","1004":"主導継続","1005":"失速警戒","1006":"初登場","1007":"加速"},status
assert enriched.set_index("code")["rank"].to_dict()==original
assert int(enriched.set_index("code").loc["1004","relative_strength_strong_streak"])==5
assert int(enriched.set_index("code").loc["1004","dual_outperformer_streak"])==5
assert rs.count(enriched,"急加速")==1 and rs.count(enriched,"崩れ")==1
table=rs.build_table(enriched);assert table.iloc[0]["relative_strength_lifecycle"]=="急加速";assert "relative_strength_trajectory_score" in table.columns
plain="\n".join(rs.plain_section(table));assert "相対強度ライフサイクル" in plain and "失速・崩れ警戒" in plain;assert "相対強度ライフサイクル" in rs.html_section(table)
assert main.APP_VERSION=="2026-07-11-dashboard-relative-strength-lifecycle-v19"
source=Path("main.py").read_text(encoding="utf-8");assert "rs_lifecycle.attach(all_ranked, history, today)" in source;assert 'sheet_name="RS Lifecycle"' in source
with tempfile.TemporaryDirectory() as td:
 output=str(Path(td)/"report.xlsx");kwargs={}
 for name in inspect.signature(main.excel_report).parameters:
  if name=="path":kwargs[name]=output
  elif name=="summary":kwargs[name]={"実行日":TODAY}
  elif name=="errors":kwargs[name]=[]
  elif name=="relative_strength_lifecycle":kwargs[name]=table
  elif name=="relative_strength":kwargs[name]=enriched
  else:kwargs[name]=pd.DataFrame()
 main.excel_report(**kwargs);sheets=pd.ExcelFile(output).sheet_names;assert "RS Lifecycle" in sheets and "Relative Strength" in sheets
print("relative strength lifecycle validation passed")
