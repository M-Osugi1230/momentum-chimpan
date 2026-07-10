import pandas as pd

from main import (
    attach_priority_expectancy,
    build_tag_expectancy,
    expectancy_detail,
    excel_report,
)

rows = []
def add(group, horizon, count, win, average):
    rows.append({
        "group": group,
        "horizon": horizon,
        "count": count,
        "win_rate": win,
        "average_return": average,
        "median_return": average,
        "best_return": average + 0.10,
        "worst_return": average - 0.10,
    })

for horizon, average in [(5, 0.02), (10, 0.04), (20, 0.06)]:
    add("全重点候補", horizon, 30, 0.60, average)
    add("初動", horizon, 20, 0.75, average + 0.08)
    add("大型資金", horizon, 12, 0.68, average + 0.04)
    add("過熱注意", horizon, 2, 0.40, average - 0.08)

summary = pd.DataFrame(rows)
tag_table = build_tag_expectancy(summary)
assert set(tag_table["tag"]) == {"初動", "大型資金", "過熱注意"}
assert tag_table.set_index("tag").loc["初動", "expectancy_score"] > tag_table.set_index("tag").loc["大型資金", "expectancy_score"]
assert tag_table.set_index("tag").loc["初動", "confidence"] == "高"
assert tag_table.set_index("tag").loc["過熱注意", "confidence"] == "蓄積中"

current = pd.DataFrame([
    {"code": "1001", "name": "Strong", "rank": 10, "score": 75, "priority_signal_count": 1, "priority_labels": ["初動"], "trading_value": 1_000_000_000},
    {"code": "1002", "name": "Medium", "rank": 5, "score": 90, "priority_signal_count": 1, "priority_labels": ["大型資金"], "trading_value": 8_000_000_000},
    {"code": "1003", "name": "Unknown", "rank": 1, "score": 95, "priority_signal_count": 1, "priority_labels": ["未検証"], "trading_value": 1_000_000_000},
])
base_table = current[["code", "name", "rank", "score"]].copy()
changes = {"current": current, "table": base_table, "lifecycle": base_table.copy()}
enriched = attach_priority_expectancy(changes, summary)
ranked = enriched["current"].reset_index(drop=True)
assert ranked.loc[0, "code"] == "1001"
assert ranked.loc[0, "expectancy_confidence"] == "高"
assert ranked.loc[2, "expectancy_evidence_count"] == 0
assert ranked.loc[2, "expectancy_score"] == 50.0
assert "期待値" in expectancy_detail(ranked.loc[0])
assert "実績蓄積中" in expectancy_detail(ranked.loc[2])
assert "expectancy_score" in enriched["table"].columns

report_path = "/tmp/expectancy_test.xlsx"
empty = pd.DataFrame()
excel_report(
    report_path,
    {"実行日": "2026-07-10"},
    current,
    empty,
    empty,
    empty,
    empty,
    enriched["table"],
    enriched["lifecycle"],
    enriched["expectancy"],
    empty,
    summary,
    empty,
    [],
    pd.DataFrame([{"code": "1001"}]),
)
assert "Priority Expectancy" in pd.ExcelFile(report_path).sheet_names
print("priority expectancy score smoke ok")
