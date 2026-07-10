from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one anchor, found {count}: {old[:160]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-11-dashboard-sector-leaders-v13"',
    'APP_VERSION = "2026-07-11-dashboard-performance-governance-v14"',
)
replace_once(
    '    "code", "name", "momentum_rank", "momentum_score", "sector_leader_score", "sector_leader_grade",',
    '    "code", "name", "close", "price_date", "momentum_rank", "momentum_score", "sector_leader_score", "sector_leader_grade",',
)

governance_functions = r'''

SECTOR_SIGNAL_HISTORY_COLUMNS = [
    "signal_date", "entry_price_date", "code", "name", "sector33", "entry_close",
    "sector_research_priority", "sector_leader_score", "sector_leader_grade",
    "sector_rotation", "sector_momentum_score", "momentum_rank", "momentum_score",
    "action_priority", "action_score", "expectancy_score", "expectancy_confidence",
]

SECTOR_OUTCOME_COLUMNS = [
    "signal_date", "entry_price_date", "exit_price_date", "code", "name", "sector33",
    "sector_research_priority", "sector_leader_grade", "sector_rotation",
    "sector_leader_score", "horizon_days", "entry_close", "exit_close",
    "forward_return", "win", "calendar_days",
]


def load_sector_signal_history(path: str) -> pd.DataFrame:
    history_path = Path(path)
    if not history_path.exists():
        return pd.DataFrame(columns=SECTOR_SIGNAL_HISTORY_COLUMNS)
    try:
        history = pd.read_csv(history_path)
    except Exception as exc:
        logger.warning("Sector leader signal history could not be read: %s", exc)
        return pd.DataFrame(columns=SECTOR_SIGNAL_HISTORY_COLUMNS)
    if "code" in history.columns:
        history["code"] = history["code"].map(normalize_code)
    for column in SECTOR_SIGNAL_HISTORY_COLUMNS:
        if column not in history.columns:
            history[column] = None
    return history[SECTOR_SIGNAL_HISTORY_COLUMNS]


def current_sector_signal_snapshot(today: str, sector_leaders: pd.DataFrame) -> pd.DataFrame:
    if sector_leaders is None or sector_leaders.empty:
        return pd.DataFrame(columns=SECTOR_SIGNAL_HISTORY_COLUMNS)
    rows = []
    for _, row in sector_leaders.iterrows():
        rows.append({
            "signal_date": today,
            "entry_price_date": optional_text(row.get("price_date")) or today,
            "code": normalize_code(row.get("code")),
            "name": optional_text(row.get("name")),
            "sector33": optional_text(row.get("sector33")),
            "entry_close": row_number(row, "close"),
            "sector_research_priority": optional_text(row.get("sector_research_priority")),
            "sector_leader_score": row_number(row, "sector_leader_score"),
            "sector_leader_grade": optional_text(row.get("sector_leader_grade")),
            "sector_rotation": optional_text(row.get("sector_rotation")),
            "sector_momentum_score": row_number(row, "sector_momentum_score"),
            "momentum_rank": int(row_number(row, "momentum_rank", 999)),
            "momentum_score": row_number(row, "momentum_score"),
            "action_priority": optional_text(row.get("action_priority")),
            "action_score": row_number(row, "action_score"),
            "expectancy_score": row_number(row, "expectancy_score", 50),
            "expectancy_confidence": optional_text(row.get("expectancy_confidence")) or "蓄積中",
        })
    return pd.DataFrame(rows, columns=SECTOR_SIGNAL_HISTORY_COLUMNS)


def update_sector_signal_history(path: str, current: pd.DataFrame) -> pd.DataFrame:
    old = load_sector_signal_history(path)
    frames = [frame for frame in (old, current) if frame is not None and not frame.empty]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=SECTOR_SIGNAL_HISTORY_COLUMNS)
    if not combined.empty:
        combined["code"] = combined["code"].map(normalize_code)
        combined = combined.drop_duplicates(["signal_date", "code"], keep="last")
        combined = combined.sort_values(["signal_date", "sector_leader_score", "code"], ascending=[True, False, True])
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(history_path, index=False)
    return combined[SECTOR_SIGNAL_HISTORY_COLUMNS]


def calculate_sector_leader_outcomes(signal_history: pd.DataFrame, price_history: pd.DataFrame, horizons: tuple[int, ...] = (5, 10, 20)) -> pd.DataFrame:
    if signal_history is None or signal_history.empty or price_history is None or price_history.empty:
        return pd.DataFrame(columns=SECTOR_OUTCOME_COLUMNS)
    required = {"date", "code", "close"}
    if not required.issubset(price_history.columns):
        return pd.DataFrame(columns=SECTOR_OUTCOME_COLUMNS)
    prices = price_history[["date", "code", "close"]].copy()
    prices["code"] = prices["code"].map(normalize_code)
    prices["date_sort"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["date_sort", "close"]).drop_duplicates(["code", "date_sort"], keep="last")
    price_groups = {code: group.sort_values("date_sort") for code, group in prices.groupby("code")}
    outcomes: list[dict[str, Any]] = []
    for _, signal_row in signal_history.iterrows():
        code = normalize_code(signal_row.get("code"))
        if code not in price_groups:
            continue
        entry_date = pd.to_datetime(signal_row.get("entry_price_date") or signal_row.get("signal_date"), errors="coerce")
        entry_close = pd.to_numeric(pd.Series([signal_row.get("entry_close")]), errors="coerce").iloc[0]
        if pd.isna(entry_date) or pd.isna(entry_close) or float(entry_close) <= 0:
            continue
        future = price_groups[code][price_groups[code]["date_sort"] > entry_date]
        for horizon in horizons:
            if len(future) < horizon:
                continue
            exit_row = future.iloc[horizon - 1]
            exit_close = float(exit_row["close"])
            forward_return = exit_close / float(entry_close) - 1
            outcomes.append({
                "signal_date": signal_row.get("signal_date"),
                "entry_price_date": entry_date.date().isoformat(),
                "exit_price_date": exit_row["date_sort"].date().isoformat(),
                "code": code,
                "name": signal_row.get("name"),
                "sector33": signal_row.get("sector33"),
                "sector_research_priority": signal_row.get("sector_research_priority"),
                "sector_leader_grade": signal_row.get("sector_leader_grade"),
                "sector_rotation": signal_row.get("sector_rotation"),
                "sector_leader_score": signal_row.get("sector_leader_score"),
                "horizon_days": horizon,
                "entry_close": float(entry_close),
                "exit_close": exit_close,
                "forward_return": forward_return,
                "win": bool(forward_return > 0),
                "calendar_days": int((exit_row["date_sort"] - entry_date).days),
            })
    return pd.DataFrame(outcomes, columns=SECTOR_OUTCOME_COLUMNS)


def sector_performance_record(group_type: str, group_value: str, horizon: int, subset: pd.DataFrame) -> dict[str, Any]:
    returns = pd.to_numeric(subset.get("forward_return", pd.Series(dtype=float)), errors="coerce").dropna()
    wins = subset.get("win", pd.Series(dtype=bool)).fillna(False).astype(bool)
    return {
        "group_type": group_type,
        "group_value": group_value,
        "horizon_days": horizon,
        "count": int(len(returns)),
        "win_rate": float(wins.mean()) if len(wins) else None,
        "average_return": float(returns.mean()) if not returns.empty else None,
        "median_return": float(returns.median()) if not returns.empty else None,
        "best_return": float(returns.max()) if not returns.empty else None,
        "worst_return": float(returns.min()) if not returns.empty else None,
        "average_leader_score": float(pd.to_numeric(subset.get("sector_leader_score", pd.Series(dtype=float)), errors="coerce").mean()) if len(subset) else None,
    }


def build_sector_leader_performance_summary(outcomes: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "group_type", "group_value", "horizon_days", "count", "win_rate",
        "average_return", "median_return", "best_return", "worst_return", "average_leader_score",
    ]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)
    records: list[dict[str, Any]] = []
    for horizon, horizon_rows in outcomes.groupby("horizon_days"):
        records.append(sector_performance_record("overall", "ALL", int(horizon), horizon_rows))
        for group_type, column in [
            ("priority", "sector_research_priority"),
            ("rotation", "sector_rotation"),
            ("grade", "sector_leader_grade"),
            ("sector", "sector33"),
        ]:
            for value, subset in horizon_rows.groupby(column, dropna=False):
                value_text = optional_text(value) or "未分類"
                records.append(sector_performance_record(group_type, value_text, int(horizon), subset))
    result = pd.DataFrame(records, columns=columns)
    return result.sort_values(["horizon_days", "group_type", "count", "group_value"], ascending=[True, True, False, True])


def performance_overall_stats(summary: pd.DataFrame, horizon: int) -> dict[str, Any]:
    if summary is None or summary.empty:
        return {}
    rows = summary[(summary["group_type"] == "overall") & (summary["horizon_days"] == horizon)]
    return {} if rows.empty else rows.iloc[0].to_dict()


def build_signal_governance(outcomes: pd.DataFrame, recent_limit: int = 20) -> pd.DataFrame:
    columns = [
        "scope_type", "scope_value", "horizon_days", "evidence_count", "recent_count",
        "baseline_average_return", "recent_average_return", "return_delta",
        "baseline_win_rate", "recent_win_rate", "win_rate_delta",
        "status", "health_score", "recommendation",
    ]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)
    scopes: list[tuple[str, str, pd.DataFrame]] = [("overall", "ALL", outcomes)]
    for scope_type, column, allowed in [
        ("priority", "sector_research_priority", ["最優先", "優先", "監視"]),
        ("rotation", "sector_rotation", ["加速", "主導", "改善", "減速"]),
    ]:
        for value in allowed:
            subset = outcomes[outcomes.get(column, pd.Series(index=outcomes.index, dtype=str)) == value]
            if not subset.empty:
                scopes.append((scope_type, value, subset))
    records: list[dict[str, Any]] = []
    for scope_type, scope_value, scope_rows in scopes:
        for horizon in (5, 10, 20):
            subset = scope_rows[scope_rows["horizon_days"] == horizon].copy()
            subset["signal_sort"] = pd.to_datetime(subset["signal_date"], errors="coerce")
            subset = subset.sort_values("signal_sort")
            count = len(subset)
            if count == 0:
                continue
            recent = subset.tail(min(recent_limit, count))
            baseline = subset.iloc[:-len(recent)] if count > len(recent) else subset
            baseline_return = float(pd.to_numeric(baseline["forward_return"], errors="coerce").mean())
            recent_return = float(pd.to_numeric(recent["forward_return"], errors="coerce").mean())
            baseline_win = float(baseline["win"].fillna(False).astype(bool).mean())
            recent_win = float(recent["win"].fillna(False).astype(bool).mean())
            return_delta = recent_return - baseline_return
            win_delta = recent_win - baseline_win
            if count < 8:
                status = "実績蓄積中"
                recommendation = "判定変更を行わず、実績を蓄積"
                health_score = 50
            elif (recent_return < 0 <= baseline_return) or return_delta <= -0.03 or win_delta <= -0.15:
                status = "劣化警戒"
                recommendation = "閾値を厳格化し、対象範囲を縮小"
                health_score = max(0, int(50 + return_delta * 500 + win_delta * 100))
            elif return_delta >= 0.03 and win_delta >= 0.10:
                status = "改善"
                recommendation = "十分な実績があれば対象範囲の拡張を検討"
                health_score = min(100, int(65 + return_delta * 300 + win_delta * 80))
            else:
                status = "安定"
                recommendation = "現行閾値を維持"
                health_score = min(100, max(0, int(60 + recent_return * 250 + (recent_win - 0.5) * 60)))
            records.append({
                "scope_type": scope_type,
                "scope_value": scope_value,
                "horizon_days": horizon,
                "evidence_count": count,
                "recent_count": len(recent),
                "baseline_average_return": baseline_return,
                "recent_average_return": recent_return,
                "return_delta": return_delta,
                "baseline_win_rate": baseline_win,
                "recent_win_rate": recent_win,
                "win_rate_delta": win_delta,
                "status": status,
                "health_score": health_score,
                "recommendation": recommendation,
            })
    result = pd.DataFrame(records, columns=columns)
    status_order = {"劣化警戒": 0, "実績蓄積中": 1, "安定": 2, "改善": 3}
    result["status_order"] = result["status"].map(status_order).fillna(9)
    return result.sort_values(["status_order", "scope_type", "horizon_days", "scope_value"]).drop(columns=["status_order"])


def build_adaptive_threshold_recommendations(governance: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "mode", "threshold_name", "current_value", "recommended_value", "change",
        "evidence_count", "governance_status", "reason", "activation_condition",
    ]
    current = {"最優先": 84, "優先": 72, "監視": 58}
    overall = pd.DataFrame()
    if governance is not None and not governance.empty:
        overall = governance[(governance["scope_type"] == "overall") & (governance["horizon_days"] == 10)]
        if overall.empty:
            overall = governance[(governance["scope_type"] == "overall") & (governance["horizon_days"] == 5)]
    status = "実績蓄積中" if overall.empty else optional_text(overall.iloc[0].get("status"))
    evidence = 0 if overall.empty else int(row_number(overall.iloc[0], "evidence_count"))
    recent_return = None if overall.empty else overall.iloc[0].get("recent_average_return")
    recent_win = None if overall.empty else overall.iloc[0].get("recent_win_rate")
    if status == "劣化警戒":
        adjustments = {"最優先": 4, "優先": 4, "監視": 3}
        reason = "直近実績の劣化を検知したため、候補抽出を厳格化"
    elif status == "改善" and evidence >= 30 and recent_return is not None and recent_win is not None and float(recent_return) > 0.03 and float(recent_win) >= 0.60:
        adjustments = {"最優先": -2, "優先": -2, "監視": -1}
        reason = "十分な実績を伴う改善を確認したため、限定的な対象拡張を提案"
    else:
        adjustments = {"最優先": 0, "優先": 0, "監視": 0}
        reason = "現行閾値を維持し、追加実績を蓄積"
    records = []
    for name, value in current.items():
        recommended = value + adjustments[name]
        records.append({
            "mode": "shadow_only",
            "threshold_name": name,
            "current_value": value,
            "recommended_value": recommended,
            "change": recommended - value,
            "evidence_count": evidence,
            "governance_status": status,
            "reason": reason,
            "activation_condition": "30件以上の実績、再現テスト合格、手動レビュー後にのみ本番反映",
        })
    return pd.DataFrame(records, columns=columns)


def run_health_overall(run_health: pd.DataFrame) -> str:
    if run_health is None or run_health.empty:
        return "UNKNOWN"
    overall = run_health[run_health["check_name"] == "overall"]
    return "UNKNOWN" if overall.empty else optional_text(overall.iloc[0].get("status"))


def build_run_health(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, sector_momentum: pd.DataFrame, sector_leaders: pd.DataFrame, errors: list[dict[str, Any]], scan_target: int, success: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(name: str, status: str, actual: Any, expected: str, detail: str) -> None:
        rows.append({"check_name": name, "status": status, "actual": actual, "expected": expected, "detail": detail})

    coverage = success / scan_target if scan_target else 0.0
    add("scan_coverage", "PASS" if coverage >= 0.95 else "WARN" if coverage >= 0.85 else "FAIL", coverage, ">=95%", "取得成功率")
    duplicate_codes = int(all_ranked["code"].duplicated().sum()) if all_ranked is not None and not all_ranked.empty and "code" in all_ranked.columns else 0
    add("duplicate_codes", "PASS" if duplicate_codes == 0 else "FAIL", duplicate_codes, "0", "同一実行内の重複コード")
    missing_sector_ratio = float((all_ranked.get("sector33", pd.Series(index=all_ranked.index, dtype=str)).fillna("").astype(str).str.strip() == "").mean()) if all_ranked is not None and not all_ranked.empty else 1.0
    add("missing_sector_ratio", "PASS" if missing_sector_ratio <= 0.05 else "WARN" if missing_sector_ratio <= 0.15 else "FAIL", missing_sector_ratio, "<=5%", "33業種分類の欠損率")
    latest = pd.to_datetime(all_ranked.get("price_date", pd.Series(dtype=str)), errors="coerce").max() if all_ranked is not None and not all_ranked.empty else pd.NaT
    age_days = None if pd.isna(latest) else int((pd.to_datetime(today) - latest.normalize()).days)
    stale_status = "FAIL" if age_days is None or age_days > 5 else "WARN" if age_days > 3 else "PASS"
    add("price_freshness", stale_status, age_days, "<=3 calendar days", "最新株価データからの経過日数")
    expected_top100 = min(100, len(all_ranked)) if all_ranked is not None else 0
    top100_count = len(top100) if top100 is not None else 0
    add("top100_count", "PASS" if top100_count == expected_top100 else "WARN", top100_count, str(expected_top100), "Momentum Top100件数")
    sector_count = len(sector_momentum) if sector_momentum is not None else 0
    add("sector_coverage", "PASS" if sector_count >= 25 else "WARN" if sector_count >= 15 else "FAIL", sector_count, ">=25", "集計できた33業種数")
    leader_count = len(sector_leaders) if sector_leaders is not None else 0
    add("sector_leader_count", "PASS" if leader_count >= 5 else "WARN" if leader_count > 0 else "FAIL", leader_count, ">=5", "業種リーダー候補数")
    error_rate = len(errors) / scan_target if scan_target else 1.0
    add("error_rate", "PASS" if error_rate <= 0.05 else "WARN" if error_rate <= 0.15 else "FAIL", error_rate, "<=5%", "取得失敗率")
    invalid_scores = 0
    if all_ranked is not None and not all_ranked.empty and "score" in all_ranked.columns:
        scores = pd.to_numeric(all_ranked["score"], errors="coerce")
        invalid_scores = int(((scores < 0) | (scores > 100) | scores.isna()).sum())
    add("score_bounds", "PASS" if invalid_scores == 0 else "FAIL", invalid_scores, "0", "Momentumスコアの範囲外・欠損")
    statuses = [row["status"] for row in rows]
    overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    rows.insert(0, {"check_name": "overall", "status": overall, "actual": overall, "expected": "PASS", "detail": f"PASS {statuses.count('PASS')} / WARN {statuses.count('WARN')} / FAIL {statuses.count('FAIL')}"})
    return pd.DataFrame(rows, columns=["check_name", "status", "actual", "expected", "detail"])


def plain_governance_section(performance: pd.DataFrame, governance: pd.DataFrame, thresholds: pd.DataFrame, run_health: pd.DataFrame) -> list[str]:
    lines = ["【実績検証・運用品質】", f"Run Health: {run_health_overall(run_health)}"]
    for horizon in (5, 10, 20):
        stats = performance_overall_stats(performance, horizon)
        if stats:
            lines.append(f"業種リーダー {horizon}日実績: {int(stats.get('count', 0))}件 / 勝率 {fmt_pct(stats.get('win_rate'))} / 平均 {fmt_pct(stats.get('average_return'))}")
    alerts = governance[governance["status"] == "劣化警戒"] if governance is not None and not governance.empty else pd.DataFrame()
    lines.append(f"劣化警戒: {len(alerts)}件")
    for _, row in alerts.head(3).iterrows():
        lines.append(f"  {row['scope_type']} {row['scope_value']} {int(row['horizon_days'])}日 / 直近 {fmt_pct(row.get('recent_average_return'))} / {row['recommendation']}")
    if thresholds is not None and not thresholds.empty:
        first = thresholds.iloc[0]
        change_text = ", ".join(f"{row['threshold_name']} {int(row['current_value'])}→{int(row['recommended_value'])}" for _, row in thresholds.iterrows())
        lines.append(f"閾値提案（shadow only）: {change_text} / {first['reason']}")
    warnings = run_health[run_health["status"].isin(["WARN", "FAIL"])] if run_health is not None and not run_health.empty else pd.DataFrame()
    for _, row in warnings.head(4).iterrows():
        lines.append(f"  品質 {row['status']}: {row['check_name']} / 実績 {row['actual']} / 基準 {row['expected']}")
    lines.append("")
    return lines


def html_governance_section(performance: pd.DataFrame, governance: pd.DataFrame, thresholds: pd.DataFrame, run_health: pd.DataFrame) -> str:
    overall = run_health_overall(run_health)
    health_color = "#15803d" if overall == "PASS" else "#b45309" if overall == "WARN" else "#b91c1c"
    metrics = []
    for horizon in (5, 10, 20):
        stats = performance_overall_stats(performance, horizon)
        if stats:
            metrics.append(f'<div style="font-size:12px;color:#334155">{horizon}日: <b>{int(stats.get("count", 0))}件</b> ・ 勝率 <b>{fmt_pct(stats.get("win_rate"))}</b> ・ 平均 <b>{fmt_pct(stats.get("average_return"))}</b></div>')
    alerts = governance[governance["status"] == "劣化警戒"] if governance is not None and not governance.empty else pd.DataFrame()
    alert_html = "".join(f'<div style="font-size:11px;color:#b91c1c;margin-top:4px">{html_text(row["scope_type"])} {html_text(row["scope_value"])} {int(row["horizon_days"])}日 ・ 直近 {fmt_pct(row.get("recent_average_return"))} ・ {html_text(row["recommendation"])}</div>' for _, row in alerts.head(3).iterrows())
    threshold_html = ""
    if thresholds is not None and not thresholds.empty:
        threshold_text = " / ".join(f'{row["threshold_name"]} {int(row["current_value"])}→{int(row["recommended_value"])}' for _, row in thresholds.iterrows())
        threshold_html = f'<div style="font-size:11px;color:#475569;margin-top:8px"><b>閾値提案（shadow only）:</b> {html_text(threshold_text)}</div>'
    warnings = run_health[run_health["status"].isin(["WARN", "FAIL"])] if run_health is not None and not run_health.empty else pd.DataFrame()
    warning_html = "".join(f'<div style="font-size:11px;color:#b45309;margin-top:3px">品質 {html_text(row["status"])}: {html_text(row["check_name"])} ・ 実績 {html_text(row["actual"])} ・ 基準 {html_text(row["expected"])}</div>' for _, row in warnings.head(4).iterrows())
    return f'''<div style="background:#fff;border:2px solid {health_color};border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#0f172a">実績検証・運用品質 <span style="float:right;color:{health_color}">{html_text(overall)}</span></div>
<div style="clear:both;font-size:12px;color:#64748b;margin:5px 0">業種リーダーの実績、シグナル劣化、閾値提案、データ品質を監視します。</div>
{"".join(metrics)}<div style="font-size:12px;font-weight:800;color:#334155;margin-top:7px">劣化警戒 {len(alerts)}件</div>{alert_html}{threshold_html}{warning_html}</div>'''
'''

replace_once(
    '\n\ndef market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:',
    governance_functions + '\n\ndef market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:',
)

replace_once(
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_signal_history: pd.DataFrame, sector_leader_outcomes: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
)
replace_once(
    '        sector_leaders.to_excel(w, sheet_name="Sector Leaders", index=False)\n        new_entries.to_excel(w, sheet_name="New Entries", index=False)',
    '        sector_leaders.to_excel(w, sheet_name="Sector Leaders", index=False)\n        sector_signal_history.to_excel(w, sheet_name="Sector Leader History", index=False)\n        sector_leader_outcomes.to_excel(w, sheet_name="Sector Leader Outcomes", index=False)\n        sector_leader_performance.to_excel(w, sheet_name="Sector Leader Performance", index=False)\n        signal_governance.to_excel(w, sheet_name="Signal Governance", index=False)\n        adaptive_thresholds.to_excel(w, sheet_name="Adaptive Thresholds", index=False)\n        run_health.to_excel(w, sheet_name="Run Health", index=False)\n        new_entries.to_excel(w, sheet_name="New Entries", index=False)',
)

replace_once(
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
)
replace_once(
    '    lines += plain_sector_leaders_section(sector_leaders)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))',
    '    lines += plain_sector_leaders_section(sector_leaders)\n    lines += plain_governance_section(sector_leader_performance, signal_governance, adaptive_thresholds, run_health)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))',
)
replace_once(
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
)
replace_once(
    '        html_sector_leaders_section(sector_leaders),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),',
    '        html_sector_leaders_section(sector_leaders),\n        html_governance_section(sector_leader_performance, signal_governance, adaptive_thresholds, run_health),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),',
)
replace_once(
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, sector_leader_performance: pd.DataFrame, signal_governance: pd.DataFrame, adaptive_thresholds: pd.DataFrame, run_health: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
)
replace_once(
    '    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, priority_changes, cfg), "plain", "utf-8"))\n    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, priority_changes, cfg), "html", "utf-8"))',
    '    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, priority_changes, cfg), "plain", "utf-8"))\n    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, priority_changes, cfg), "html", "utf-8"))',
)

replace_once(
    '    sector_leaders = build_sector_leaders(all_ranked, sector_momentum, action_priority)\n    sector_rotation = build_sector_rotation_table(sector_momentum, sector_leaders)\n    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)',
    '    sector_leaders = build_sector_leaders(all_ranked, sector_momentum, action_priority)\n    sector_rotation = build_sector_rotation_table(sector_momentum, sector_leaders)\n    sector_history_path = "data/sector_leader_signal_history.csv"\n    current_sector_signals = current_sector_signal_snapshot(today, sector_leaders)\n    sector_signal_history = update_sector_signal_history(sector_history_path, current_sector_signals)\n    sector_leader_outcomes = calculate_sector_leader_outcomes(sector_signal_history, performance_history)\n    sector_leader_performance = build_sector_leader_performance_summary(sector_leader_outcomes)\n    signal_governance = build_signal_governance(sector_leader_outcomes)\n    adaptive_thresholds = build_adaptive_threshold_recommendations(signal_governance)\n    run_health = build_run_health(today, all_ranked, top100, sector_momentum, sector_leaders, errors, len(stocks), success)\n    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)',
)
replace_once(
    '        "レポート形式": "dashboard_sector_leaders_v13",',
    '        "レポート形式": "dashboard_performance_governance_v14",',
)
replace_once(
    '        "最上位業種リーダースコア": float(sector_leaders.iloc[0]["sector_leader_score"]) if not sector_leaders.empty else None,\n        "重点候補数": priority_change_count(priority_changes, "current"),',
    '        "最上位業種リーダースコア": float(sector_leaders.iloc[0]["sector_leader_score"]) if not sector_leaders.empty else None,\n        "業種リーダー履歴件数": len(sector_signal_history),\n        "業種リーダー5日実績件数": int(performance_overall_stats(sector_leader_performance, 5).get("count", 0) or 0),\n        "業種リーダー5日勝率": performance_overall_stats(sector_leader_performance, 5).get("win_rate"),\n        "業種リーダー5日平均騰落率": performance_overall_stats(sector_leader_performance, 5).get("average_return"),\n        "業種リーダー10日実績件数": int(performance_overall_stats(sector_leader_performance, 10).get("count", 0) or 0),\n        "業種リーダー10日勝率": performance_overall_stats(sector_leader_performance, 10).get("win_rate"),\n        "業種リーダー10日平均騰落率": performance_overall_stats(sector_leader_performance, 10).get("average_return"),\n        "業種リーダー20日実績件数": int(performance_overall_stats(sector_leader_performance, 20).get("count", 0) or 0),\n        "業種リーダー20日勝率": performance_overall_stats(sector_leader_performance, 20).get("win_rate"),\n        "業種リーダー20日平均騰落率": performance_overall_stats(sector_leader_performance, 20).get("average_return"),\n        "シグナル劣化警戒数": int((signal_governance.get("status", pd.Series(dtype=str)) == "劣化警戒").sum()) if not signal_governance.empty else 0,\n        "閾値調整モード": "shadow_only",\n        "Run Health": run_health_overall(run_health),\n        "Run Health WARN": int((run_health.get("status", pd.Series(dtype=str)) == "WARN").sum()) if not run_health.empty else 0,\n        "Run Health FAIL": int((run_health.get("status", pd.Series(dtype=str)) == "FAIL").sum()) if not run_health.empty else 0,\n        "重点候補数": priority_change_count(priority_changes, "current"),',
)
replace_once(
    '    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, sector_momentum, sector_rotation, sector_leaders, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], action_priority, priority_performance, signal_performance, temperature, errors, universe_df)',
    '    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, sector_momentum, sector_rotation, sector_leaders, sector_signal_history, sector_leader_outcomes, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], action_priority, priority_performance, signal_performance, temperature, errors, universe_df)',
)
replace_once(
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, priority_changes, cfg)',
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, sector_leader_performance, signal_governance, adaptive_thresholds, run_health, priority_changes, cfg)',
)

path.write_text(text, encoding="utf-8")
print("Applied performance governance development batch")
