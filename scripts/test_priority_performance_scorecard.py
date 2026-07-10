import pandas as pd

from main import (
    build_signal_performance_summary,
    calculate_priority_performance,
    combined_ranking_history,
    excel_report,
    html_performance_scorecard,
    overall_performance_stats,
    plain_performance_scorecard,
)


def row(date, code, rank, close, *, score=75, ytd=True, volume=2.0, trading=1_000_000_000, top30=4):
    return {
        "date": date,
        "rank": rank,
        "code": code,
        "name": f"Test{code}",
        "close": close,
        "score": score,
        "reason": "年初来高値更新、20日線上、60日線上、売買代金1億円以上",
        "score_ytd_high": 30,
        "score_ytd_streak": 12,
        "score_return_20d": 15,
        "score_volume_ratio": 10,
        "score_ma": 10,
        "score_trading_value": 5,
        "ytd_high_flag": ytd,
        "ytd_high_streak": 3,
        "ytd_high_count": 10,
        "return_5d": 0.05,
        "return_20d": 0.20,
        "return_60d": 0.30,
        "volume_ratio": volume,
        "trading_value": trading,
        "ma20": close * 0.9,
        "ma60": close * 0.8,
        "ma20_deviation": 0.10,
        "above_ma20": True,
        "above_ma60": True,
        "price_date": date,
        "is_top100": True,
        "is_new_entry": False,
        "rank_change": 0,
        "is_rising_fast": False,
        "is_best_rank": False,
        "top30_streak": top30,
        "top30_streak_days": top30,
    }


rows = []
for day in range(1, 26):
    date = f"2026-06-{day:02d}"
    rows.append(row(date, "1001", 5, 100 + day * 2, trading=6_000_000_000))
    rows.append(row(date, "1002", 10, 200 - day, trading=6_000_000_000))
    rows.append(row(date, "9000", 90, 50, score=40, ytd=False, volume=0.8, trading=100_000_000, top30=0))

history = pd.DataFrame(rows)
performance = calculate_priority_performance(history, 100)
assert not performance.empty
assert {"return_5d_after", "return_10d_after", "return_20d_after"}.issubset(performance.columns)

first_1001 = performance[(performance["signal_date"] == "2026-06-01") & (performance["code"] == "1001")].iloc[0]
assert round(first_1001["return_5d_after"], 6) == round(110 / 102 - 1, 6)
assert round(first_1001["return_20d_after"], 6) == round(140 / 102 - 1, 6)
assert first_1001["max_return_20d_after"] > 0

summary = build_signal_performance_summary(performance)
stats5 = overall_performance_stats(summary, 5)
stats20 = overall_performance_stats(summary, 20)
assert stats5["count"] > 0
assert stats20["count"] > 0
assert 0 <= stats5["win_rate"] <= 1
assert "全重点候補" in set(summary["group"])
assert "大型資金" in set(summary["group"])

plain = "\n".join(plain_performance_scorecard(summary))
html = html_performance_scorecard(summary)
assert "【シグナル実績】" in plain
assert "5日後" in plain and "20日後" in plain
assert "シグナル実績" in html

combined = combined_ranking_history(history, history[history["date"] == "2026-06-25"], "2026-06-25")
assert combined.duplicated(["date", "code"]).sum() == 0

report_path = "/tmp/performance_scorecard.xlsx"
empty = pd.DataFrame()
excel_report(
    report_path,
    {"実行日": "2026-06-25"},
    history.tail(3),
    empty,
    empty,
    empty,
    empty,
    empty,
    empty,
    performance,
    summary,
    empty,
    [],
    pd.DataFrame([{"code": "1001"}]),
)
sheets = pd.ExcelFile(report_path).sheet_names
assert "Priority Performance" in sheets
assert "Signal Performance" in sheets
print("priority performance scorecard smoke ok")
