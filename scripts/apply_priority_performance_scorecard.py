from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match, found {count}: {old[:180]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-10-dashboard-priority-lifecycle-v8"',
    'APP_VERSION = "2026-07-10-dashboard-performance-scorecard-v9"',
)

helpers = r"""

def combined_ranking_history(history: pd.DataFrame, current: pd.DataFrame, today: str) -> pd.DataFrame:
    frames = []
    if history is not None and not history.empty:
        old = history.copy()
        if "date" in old.columns:
            old = old[old["date"].astype(str) != str(today)]
        frames.append(old)
    if current is not None and not current.empty:
        frames.append(current.copy())
    if not frames:
        return pd.DataFrame(columns=ranking_history_columns())
    out = pd.concat(frames, ignore_index=True)
    out["code"] = out["code"].map(normalize_code)
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date.astype(str)
    out["rank"] = pd.to_numeric(out["rank"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    return out.dropna(subset=["date", "rank", "close", "code"]).drop_duplicates(["date", "code"], keep="last")


def calculate_priority_performance(history: pd.DataFrame, top_limit: int, horizons: tuple[int, ...] = (5, 10, 20)) -> pd.DataFrame:
    columns = [
        "signal_date", "code", "name", "signal_rank", "signal_score", "signal_close", "signal_labels",
        *[f"target_date_{h}d" for h in horizons],
        *[f"return_{h}d_after" for h in horizons],
        "max_return_20d_after", "min_return_20d_after", "observed_report_days",
    ]
    if history is None or history.empty:
        return pd.DataFrame(columns=columns)

    work = history.copy()
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work["rank"] = pd.to_numeric(work["rank"], errors="coerce")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["date_sort", "rank", "close", "code"])
    if work.empty:
        return pd.DataFrame(columns=columns)
    work["date"] = work["date_sort"].dt.date.astype(str)
    work["code"] = work["code"].map(normalize_code)
    dates = sorted(work["date"].unique(), key=pd.Timestamp)
    date_index = {date: index for index, date in enumerate(dates)}
    price_lookup = work.set_index(["date", "code"])["close"].to_dict()
    rows: list[dict[str, Any]] = []

    for signal_date, day_rows in work.groupby("date", sort=True):
        top100 = day_rows[day_rows["rank"] <= top_limit].copy()
        selected = select_priority_candidates(top100, max(top_limit, len(top100)))
        if selected.empty:
            continue
        start_index = date_index[signal_date]
        for _, signal in selected.iterrows():
            code = normalize_code(signal.get("code"))
            entry_close = float(signal["close"])
            labels = priority_labels_text(signal.get("priority_labels", []))
            record: dict[str, Any] = {
                "signal_date": signal_date,
                "code": code,
                "name": signal.get("name", ""),
                "signal_rank": int(signal.get("rank", 0)),
                "signal_score": float(signal.get("score", 0)),
                "signal_close": entry_close,
                "signal_labels": labels,
            }
            observed_returns: list[float] = []
            max_horizon = max(horizons)
            for offset in range(1, min(max_horizon, len(dates) - start_index - 1) + 1):
                future_date = dates[start_index + offset]
                future_close = price_lookup.get((future_date, code))
                if future_close is not None and entry_close:
                    observed_returns.append(float(future_close) / entry_close - 1)
            for horizon in horizons:
                if start_index + horizon < len(dates):
                    target_date = dates[start_index + horizon]
                    target_close = price_lookup.get((target_date, code))
                else:
                    target_date = None
                    target_close = None
                record[f"target_date_{horizon}d"] = target_date
                record[f"return_{horizon}d_after"] = (float(target_close) / entry_close - 1) if target_close is not None and entry_close else None
            record["max_return_20d_after"] = max(observed_returns) if observed_returns else None
            record["min_return_20d_after"] = min(observed_returns) if observed_returns else None
            record["observed_report_days"] = len(observed_returns)
            rows.append(record)
    return pd.DataFrame(rows, columns=columns)


def performance_stats(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {"count": 0, "win_rate": None, "average": None, "median": None, "best": None, "worst": None}
    return {
        "count": int(len(clean)),
        "win_rate": float((clean > 0).mean()),
        "average": float(clean.mean()),
        "median": float(clean.median()),
        "best": float(clean.max()),
        "worst": float(clean.min()),
    }


def build_signal_performance_summary(performance: pd.DataFrame, horizons: tuple[int, ...] = (5, 10, 20)) -> pd.DataFrame:
    columns = ["group", "horizon", "count", "win_rate", "average_return", "median_return", "best_return", "worst_return"]
    if performance is None or performance.empty:
        return pd.DataFrame(columns=columns)
    groups: list[tuple[str, pd.DataFrame]] = [("全重点候補", performance)]
    label_values = sorted({label.strip() for value in performance["signal_labels"].fillna("") for label in str(value).split("/") if label.strip()})
    for label in label_values:
        mask = performance["signal_labels"].fillna("").map(lambda value: label in [item.strip() for item in str(value).split("/")])
        groups.append((label, performance[mask].copy()))
    records = []
    for group_name, group_df in groups:
        for horizon in horizons:
            stats = performance_stats(group_df[f"return_{horizon}d_after"])
            records.append({
                "group": group_name,
                "horizon": horizon,
                "count": stats["count"],
                "win_rate": stats["win_rate"],
                "average_return": stats["average"],
                "median_return": stats["median"],
                "best_return": stats["best"],
                "worst_return": stats["worst"],
            })
    return pd.DataFrame(records, columns=columns)


def overall_performance_stats(summary: pd.DataFrame, horizon: int) -> dict[str, Any]:
    if summary is None or summary.empty:
        return {"count": 0, "win_rate": None, "average_return": None, "median_return": None, "best_return": None, "worst_return": None}
    rows = summary[(summary["group"] == "全重点候補") & (summary["horizon"] == horizon)]
    return rows.iloc[0].to_dict() if not rows.empty else {"count": 0, "win_rate": None, "average_return": None, "median_return": None, "best_return": None, "worst_return": None}


def best_signal_groups(summary: pd.DataFrame, horizon: int = 20, minimum_count: int = 3, limit: int = 3) -> pd.DataFrame:
    if summary is None or summary.empty:
        return pd.DataFrame(columns=summary.columns if summary is not None else [])
    rows = summary[(summary["group"] != "全重点候補") & (summary["horizon"] == horizon) & (summary["count"] >= minimum_count)].copy()
    return rows.sort_values(["average_return", "win_rate", "count"], ascending=[False, False, False]).head(limit)


def fmt_optional_pct(value: Any) -> str:
    return "-" if value is None or pd.isna(value) else fmt_pct(value)


def plain_performance_scorecard(summary: pd.DataFrame) -> list[str]:
    if summary is None or summary.empty:
        return ["【シグナル実績】", "履歴不足のため、実績集計を開始します。", ""]
    lines = ["【シグナル実績】"]
    for horizon in (5, 10, 20):
        stats = overall_performance_stats(summary, horizon)
        lines.append(
            f"{horizon}日後｜件数 {int(stats.get('count', 0) or 0)}｜勝率 {fmt_optional_pct(stats.get('win_rate'))}｜平均 {fmt_optional_pct(stats.get('average_return'))}｜中央値 {fmt_optional_pct(stats.get('median_return'))}"
        )
    best = best_signal_groups(summary)
    if not best.empty:
        lines.append("期待値上位タグ（20日後・3件以上）")
        for _, row in best.iterrows():
            lines.append(f"{row['group']}｜{int(row['count'])}件｜勝率 {fmt_optional_pct(row['win_rate'])}｜平均 {fmt_optional_pct(row['average_return'])}")
    lines.append("")
    return lines


def html_performance_scorecard(summary: pd.DataFrame) -> str:
    if summary is None or summary.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>シグナル実績</b><div style="font-size:12px;color:#64748b;margin-top:5px">履歴不足のため、実績集計を開始します。</div></div>'
    horizon_rows = []
    for horizon in (5, 10, 20):
        stats = overall_performance_stats(summary, horizon)
        horizon_rows.append(f'<div style="border-top:1px solid #e5e7eb;padding:8px 0;font-size:12px;color:#334155"><b>{horizon}日後</b> ・ {int(stats.get("count", 0) or 0)}件 ・ 勝率 <b>{fmt_optional_pct(stats.get("win_rate"))}</b> ・ 平均 <b>{fmt_optional_pct(stats.get("average_return"))}</b> ・ 中央値 {fmt_optional_pct(stats.get("median_return"))}</div>')
    best = best_signal_groups(summary)
    best_html = ""
    if not best.empty:
        items = "".join(f'<div style="font-size:12px;color:#475569;padding:3px 0">{html_text(row["group"])} ・ {int(row["count"])}件 ・ 勝率 {fmt_optional_pct(row["win_rate"])} ・ 平均 {fmt_optional_pct(row["average_return"])}</div>' for _, row in best.iterrows())
        best_html = f'<div style="font-size:12px;font-weight:900;color:#7c3aed;margin-top:8px">期待値上位タグ（20日後・3件以上）</div>{items}'
    return f'<div style="background:#fff;border:2px solid #7c3aed;border-radius:18px;padding:16px;margin-top:14px"><div style="font-size:18px;font-weight:900;color:#4c1d95">シグナル実績</div>{"".join(horizon_rows)}{best_html}</div>'
"""

replace_once(
    "\n\ndef plain_priority_section(priority: pd.DataFrame) -> list[str]:",
    helpers + "\n\ndef plain_priority_section(priority: pd.DataFrame) -> list[str]:",
)

replace_once(
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
)
replace_once(
    '        priority_lifecycle.to_excel(w, sheet_name="Priority Lifecycle", index=False)\n        temperature.to_excel(w, sheet_name="Market Temperature", index=False)',
    '        priority_lifecycle.to_excel(w, sheet_name="Priority Lifecycle", index=False)\n        priority_performance.to_excel(w, sheet_name="Priority Performance", index=False)\n        signal_performance.to_excel(w, sheet_name="Signal Performance", index=False)\n        temperature.to_excel(w, sheet_name="Market Temperature", index=False)',
)

replace_once(
    '    lines += plain_market_regime(regime)\n    lines += plain_priority_section(priority)',
    '    lines += plain_market_regime(regime)\n    lines += plain_performance_scorecard(summary.get("_signal_performance", pd.DataFrame()))\n    lines += plain_priority_section(priority)',
)
replace_once(
    '        html_market_regime(regime),\n        html_priority_section(priority),',
    '        html_market_regime(regime),\n        html_performance_scorecard(summary.get("_signal_performance", pd.DataFrame())),\n        html_priority_section(priority),',
)

replace_once(
    '    priority_changes = attach_priority_candidate_lifecycle(priority_changes, history, top100, today, top_limit)\n\n    temp_path = cfg["data"]["market_temperature_path"]',
    '    priority_changes = attach_priority_candidate_lifecycle(priority_changes, history, top100, today, top_limit)\n    performance_history = combined_ranking_history(history, all_ranked, today)\n    priority_performance = calculate_priority_performance(performance_history, top_limit)\n    signal_performance = build_signal_performance_summary(priority_performance)\n\n    temp_path = cfg["data"]["market_temperature_path"]',
)
replace_once(
    '        "レポート形式": "dashboard_priority_lifecycle_v8",',
    '        "レポート形式": "dashboard_performance_scorecard_v9",',
)
replace_once(
    '        "重点候補連続5日以上": int((priority_changes.get("lifecycle", pd.DataFrame()).get("priority_streak_days", pd.Series(dtype=float)).fillna(0) >= 5).sum()),\n',
    '        "重点候補連続5日以上": int((priority_changes.get("lifecycle", pd.DataFrame()).get("priority_streak_days", pd.Series(dtype=float)).fillna(0) >= 5).sum()),\n        "重点候補5日実績件数": int(overall_performance_stats(signal_performance, 5).get("count", 0) or 0),\n        "重点候補5日勝率": overall_performance_stats(signal_performance, 5).get("win_rate"),\n        "重点候補5日平均騰落率": overall_performance_stats(signal_performance, 5).get("average_return"),\n        "重点候補10日実績件数": int(overall_performance_stats(signal_performance, 10).get("count", 0) or 0),\n        "重点候補10日勝率": overall_performance_stats(signal_performance, 10).get("win_rate"),\n        "重点候補10日平均騰落率": overall_performance_stats(signal_performance, 10).get("average_return"),\n        "重点候補20日実績件数": int(overall_performance_stats(signal_performance, 20).get("count", 0) or 0),\n        "重点候補20日勝率": overall_performance_stats(signal_performance, 20).get("win_rate"),\n        "重点候補20日平均騰落率": overall_performance_stats(signal_performance, 20).get("average_return"),\n',
)
replace_once(
    '    excel_report(cfg["data"]["output_path"], summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], temperature, errors, universe_df)',
    '    summary["_signal_performance"] = signal_performance\n    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_performance, signal_performance, temperature, errors, universe_df)',
)
replace_once(
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, priority_changes, cfg)',
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, priority_changes, cfg)',
)

path.write_text(text, encoding="utf-8")
print("Applied priority performance scorecard update")
