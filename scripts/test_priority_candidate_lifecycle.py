import pandas as pd

from main import (
    attach_priority_candidate_lifecycle,
    build_html_email,
    build_plain_email,
    compare_priority_candidates,
    excel_report,
)


def row(
    code: str,
    rank: int,
    *,
    date: str | None = None,
    score: int = 75,
    ytd: bool = True,
    new: bool = False,
    rising: bool = False,
    top30: int = 0,
    volume: float = 2.0,
    trading: float = 1_000_000_000,
    above: bool = True,
) -> dict:
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


history_rows: list[dict] = []
for day in range(1, 10):
    report_date = f"2026-07-{day:02d}"
    history_rows.append(row("9000", 99, date=report_date, score=40, ytd=False, volume=0.8, trading=100_000_000, above=False))
    history_rows.append(row("1005", 5, date=report_date, trading=6_000_000_000))
    if day >= 5:
        history_rows.append(row("1004", 10, date=report_date, top30=day - 2))
    if day >= 7:
        history_rows.append(row("1001", 15, date=report_date, top30=day - 4))
    if day == 1:
        history_rows.append(row("1002", 20, date=report_date, trading=6_000_000_000))

# Same-day history must be ignored when rebuilding lifecycle.
history_rows.append(row("7777", 1, date="2026-07-10", trading=6_000_000_000))
history = pd.DataFrame(history_rows)
for column in ["is_new_entry", "is_rising_fast", "is_best_rank", "ytd_high_flag", "above_ma20", "above_ma60"]:
    history[column] = history[column].map(lambda value: "True" if value else "False")

current = pd.DataFrame([
    row("1005", 4, trading=6_000_000_000),
    row("1004", 8, top30=7),
    row("1001", 12, top30=5),
    row("1002", 18, trading=6_000_000_000),
    row("1003", 22, new=True),
])

changes = compare_priority_candidates(current, history, "2026-07-10", 100)
changes = attach_priority_candidate_lifecycle(changes, history, current, "2026-07-10", 100)
lifecycle = changes["lifecycle"].set_index("code")

assert lifecycle.loc["1003", "priority_lifecycle_status"] == "初登場"
assert lifecycle.loc["1003", "priority_first_date"] == "2026-07-10"
assert lifecycle.loc["1003", "priority_streak_days"] == 1
assert lifecycle.loc["1003", "priority_total_days"] == 1

assert lifecycle.loc["1002", "priority_lifecycle_status"] == "再浮上"
assert lifecycle.loc["1002", "priority_first_date"] == "2026-07-01"
assert lifecycle.loc["1002", "priority_streak_days"] == 1
assert lifecycle.loc["1002", "priority_total_days"] == 2
assert lifecycle.loc["1002", "priority_run_count"] == 2

assert lifecycle.loc["1001", "priority_lifecycle_status"] == "継続"
assert lifecycle.loc["1001", "priority_streak_days"] == 4
assert lifecycle.loc["1001", "priority_total_days"] == 4

assert lifecycle.loc["1004", "priority_lifecycle_status"] == "定着"
assert lifecycle.loc["1004", "priority_streak_days"] == 6
assert lifecycle.loc["1004", "priority_total_days"] == 6

assert lifecycle.loc["1005", "priority_lifecycle_status"] == "長期定着"
assert lifecycle.loc["1005", "priority_streak_days"] == 10
assert lifecycle.loc["1005", "priority_total_days"] == 10
assert "7777" not in lifecycle.index

assert len(changes["new"]) == 2
assert len(changes["continued"]) == 3
assert "priority_lifecycle_status" in changes["table"].columns

summary = {
    "実行日": "2026-07-10",
    "新規ランクイン": 1,
    "急上昇": 0,
    "TOP30継続10日以上": 0,
    "年初来高値更新": 5,
    "取得失敗": 0,
}
temperature = pd.DataFrame([{
    "date": "2026-07-10",
    "ytd_high_count": 50,
    "delta_ytd_high_count": 1,
    "top100_avg_score": 75,
    "delta_top100_avg_score": 1,
    "top100_avg_return_20d": 0.20,
    "delta_top100_avg_return_20d": 0.01,
    "top100_avg_volume_ratio": 2.0,
    "delta_top100_avg_volume_ratio": 0.1,
    "market_regime": "強気",
    "market_regime_score": 80,
    "previous_market_regime": "強気",
    "previous_market_regime_score": 78,
    "previous_market_regime_date": "2026-07-09",
    "regime_changed": False,
    "regime_transition": "強気 → 強気",
    "regime_transition_type": "維持",
    "regime_score_delta": 2,
    "regime_streak": 2,
}])
empty = current.iloc[0:0].copy()
cfg = {"ranking": {"email_top_n": 10}}
plain = build_plain_email(summary, current, empty, empty, current, current, temperature, changes, cfg)
html = build_html_email(summary, current, empty, empty, current, current, temperature, changes, cfg)

assert "継続力:" in plain
assert "初登場 1件" in plain
assert "再浮上 1件" in plain
assert "長期定着 1件" in plain
assert "初回 2026-07-01" in plain
assert "連続 10営業日" in plain
assert "継続力" in html
assert "長期定着" in html
assert "再浮上" in html

report_path = "/tmp/priority_lifecycle_test.xlsx"
excel_report(
    report_path,
    summary,
    current,
    empty,
    empty,
    current,
    current,
    changes["table"],
    changes["lifecycle"],
    temperature,
    [],
    pd.DataFrame([{"code": "1001"}]),
)
sheets = pd.ExcelFile(report_path).sheet_names
assert "Priority Lifecycle" in sheets
assert "Priority Changes" in sheets
print("priority candidate lifecycle smoke ok")
