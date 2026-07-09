import pandas as pd

from main import (
    compare_priority_candidates,
    build_plain_email,
    build_html_email,
    excel_report,
)


def row(code, rank, *, score=75, ytd=True, new=False, rising=False, top30=0, volume=2.0, trading=1_000_000_000, above=True, date=None):
    data = {
        "rank": rank,
        "code": code,
        "name": f"Test{code}",
        "score": score,
        "close": 1000,
        "return_5d": 0.05,
        "return_20d": 0.20,
        "return_60d": 0.30,
        "volume_ratio": volume,
        "trading_value": trading,
        "ytd_high_count": 10,
        "ytd_high_streak": 3,
        "score_ytd_high": 30,
        "score_ytd_streak": 12,
        "score_return_20d": 15,
        "score_volume_ratio": 10,
        "score_ma": 10,
        "score_trading_value": 5,
        "reason": "年初来高値更新、20日線上、60日線上、売買代金1億円以上",
        "is_new_entry": new,
        "is_rising_fast": rising,
        "is_best_rank": False,
        "rank_change": 5,
        "top30_streak": top30,
        "top30_streak_days": top30,
        "price_date": "2026-07-10",
        "ytd_high_flag": ytd,
        "ma20_deviation": 0.10,
        "above_ma20": above,
        "above_ma60": above,
    }
    if date is not None:
        data["date"] = date
    return data


current = pd.DataFrame([
    row("1001", 5, top30=5, trading=6_000_000_000),
    row("1002", 40, ytd=False, top30=0, volume=1.0, trading=500_000_000, above=False),
    row("1003", 15, new=True, ytd=True, volume=2.0, trading=1_000_000_000),
    row("1004", 20, rising=False, top30=4, ytd=True, volume=2.0, trading=1_000_000_000),
])

history = pd.DataFrame([
    row("1001", 6, top30=4, trading=6_000_000_000, date="2026-07-09"),
    row("1002", 10, top30=4, trading=1_000_000_000, date="2026-07-09"),
    row("1004", 45, rising=True, top30=0, ytd=False, trading=1_000_000_000, date="2026-07-09"),
    row("1005", 1, top30=5, trading=6_000_000_000, date="2026-07-10"),
])
for col in ["is_new_entry", "is_rising_fast", "is_best_rank", "ytd_high_flag", "above_ma20", "above_ma60"]:
    history[col] = history[col].map(lambda value: "True" if value else "False")

changes = compare_priority_candidates(current, history, "2026-07-10", 100)
print("comparison date:", changes["previous_date"])
print("counts:", {key: len(changes[key]) for key in ["new", "continued", "dropped", "label_changed"]})
print(changes["table"].to_string(index=False))
assert changes["previous_date"] == "2026-07-09", changes["previous_date"]
assert len(changes["new"]) == 1, changes["table"]
assert len(changes["continued"]) == 2, changes["table"]
assert len(changes["dropped"]) == 1, changes["table"]
assert len(changes["label_changed"]) == 1, changes["table"]
assert changes["new"].iloc[0]["code"] == "1003"
assert changes["dropped"].iloc[0]["code"] == "1002"
changed = changes["label_changed"].iloc[0]
assert changed["code"] == "1004"
assert "加速" in changed["previous_labels"]
assert "継続" in changed["current_labels"]

temperature = pd.DataFrame([{
    "date": "2026-07-10",
    "ytd_high_count": 50,
    "delta_ytd_high_count": 1,
    "top100_avg_score": 70,
    "delta_top100_avg_score": 1,
    "top100_avg_return_20d": 0.15,
    "delta_top100_avg_return_20d": 0.01,
    "top100_avg_volume_ratio": 2.0,
    "delta_top100_avg_volume_ratio": 0.1,
    "market_regime": "強気",
    "market_regime_score": 80,
    "previous_market_regime": "やや強気",
    "previous_market_regime_score": 70,
    "previous_market_regime_date": "2026-07-09",
    "regime_changed": True,
    "regime_transition": "やや強気 → 強気",
    "regime_transition_type": "改善",
    "regime_score_delta": 10,
    "regime_streak": 1,
}])
summary = {
    "実行日": "2026-07-10",
    "新規ランクイン": 1,
    "急上昇": 1,
    "TOP30継続10日以上": 0,
    "年初来高値更新": 3,
    "取得失敗": 0,
}
cfg = {"ranking": {"email_top_n": 10}}
empty = current.iloc[0:0].copy()
plain = build_plain_email(summary, current, empty, empty, current, current, temperature, changes, cfg)
html = build_html_email(summary, current, empty, empty, current, current, temperature, changes, cfg)
print("plain has change section:", "【重点候補の変化】" in plain)
print("plain summary line present:", "新規 1件 / 継続 2件 / 脱落 1件 / タグ変化 1件" in plain)
print("plain tag change present:", "加速 → 継続" in plain)
assert "【重点候補の変化】" in plain
assert "新規 1件 / 継続 2件 / 脱落 1件 / タグ変化 1件" in plain
assert "1003" in plain and "1002" in plain
assert "加速 → 継続" in plain
assert "重点候補の変化" in html
assert "今日から重点候補" in html
assert "重点候補から脱落" in html

report_path = "/tmp/priority_changes_test.xlsx"
excel_report(
    report_path,
    summary,
    current,
    empty,
    empty,
    current,
    current,
    changes["table"],
    temperature,
    [],
    pd.DataFrame([{"code": "1001"}]),
)
print("excel sheets:", pd.ExcelFile(report_path).sheet_names)
assert "Priority Changes" in pd.ExcelFile(report_path).sheet_names
print("priority candidate change tracking smoke ok")
