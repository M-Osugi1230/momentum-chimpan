from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match, found {count}: {old[:160]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-10-dashboard-performance-scorecard-v9"',
    'APP_VERSION = "2026-07-10-dashboard-expectancy-score-v10"',
)

helpers = r'''

def expectancy_confidence(sample_count: int) -> str:
    if sample_count >= 20:
        return "高"
    if sample_count >= 8:
        return "中"
    if sample_count >= 3:
        return "低"
    return "蓄積中"


def build_tag_expectancy(signal_performance: pd.DataFrame, prior_strength: int = 5) -> pd.DataFrame:
    columns = [
        "tag", "expectancy_score", "expected_return", "expected_win_rate",
        "evidence_count", "confidence", "available_horizons",
    ]
    if signal_performance is None or signal_performance.empty:
        return pd.DataFrame(columns=columns)

    weights = {5: 0.20, 10: 0.30, 20: 0.50}
    overall = {
        int(row["horizon"]): row
        for _, row in signal_performance[signal_performance["group"] == "全重点候補"].iterrows()
    }
    records = []
    for tag, rows in signal_performance[signal_performance["group"] != "全重点候補"].groupby("group"):
        horizon_values = []
        counts = []
        for _, row in rows.iterrows():
            horizon = int(row["horizon"])
            count = int(row.get("count", 0) or 0)
            average = optional_number(row.get("average_return"))
            win_rate = optional_number(row.get("win_rate"))
            prior_row = overall.get(horizon)
            prior_average = optional_number(prior_row.get("average_return")) if prior_row is not None else 0.0
            prior_win = optional_number(prior_row.get("win_rate")) if prior_row is not None else 0.5
            if count <= 0 or average is None or win_rate is None:
                continue
            prior_average = 0.0 if prior_average is None else prior_average
            prior_win = 0.5 if prior_win is None else prior_win
            shrunk_average = (count * average + prior_strength * prior_average) / (count + prior_strength)
            shrunk_win = (count * win_rate + prior_strength * prior_win) / (count + prior_strength)
            horizon_score = max(0.0, min(100.0, 50.0 + shrunk_average * 200.0 + (shrunk_win - 0.5) * 30.0))
            horizon_values.append((weights.get(horizon, 0.0), horizon_score, shrunk_average, shrunk_win, horizon))
            counts.append(count)
        if not horizon_values:
            continue
        total_weight = sum(value[0] for value in horizon_values) or 1.0
        score = sum(value[0] * value[1] for value in horizon_values) / total_weight
        expected_return = sum(value[0] * value[2] for value in horizon_values) / total_weight
        expected_win = sum(value[0] * value[3] for value in horizon_values) / total_weight
        evidence_count = max(counts)
        records.append({
            "tag": str(tag),
            "expectancy_score": round(score, 1),
            "expected_return": expected_return,
            "expected_win_rate": expected_win,
            "evidence_count": evidence_count,
            "confidence": expectancy_confidence(evidence_count),
            "available_horizons": ",".join(str(value[4]) for value in horizon_values),
        })
    return pd.DataFrame(records, columns=columns)


def candidate_expectancy_values(labels: Any, tag_expectancy: pd.DataFrame) -> dict[str, Any]:
    label_list = list(labels) if isinstance(labels, (list, tuple, set)) else [item.strip() for item in str(labels or "").split("/") if item.strip()]
    if tag_expectancy is None or tag_expectancy.empty:
        return {
            "expectancy_score": 50.0,
            "expectancy_expected_return": None,
            "expectancy_win_rate": None,
            "expectancy_evidence_count": 0,
            "expectancy_confidence": "蓄積中",
            "expectancy_tags": "",
        }
    matched = tag_expectancy[tag_expectancy["tag"].isin(label_list)].copy()
    if matched.empty:
        return {
            "expectancy_score": 50.0,
            "expectancy_expected_return": None,
            "expectancy_win_rate": None,
            "expectancy_evidence_count": 0,
            "expectancy_confidence": "蓄積中",
            "expectancy_tags": "",
        }
    matched["blend_weight"] = matched["evidence_count"].clip(lower=1, upper=20)
    total_weight = float(matched["blend_weight"].sum()) or 1.0
    score = float((matched["expectancy_score"] * matched["blend_weight"]).sum() / total_weight)
    expected_return = float((matched["expected_return"] * matched["blend_weight"]).sum() / total_weight)
    win_rate = float((matched["expected_win_rate"] * matched["blend_weight"]).sum() / total_weight)
    evidence_count = int(matched["evidence_count"].max())
    return {
        "expectancy_score": round(score, 1),
        "expectancy_expected_return": expected_return,
        "expectancy_win_rate": win_rate,
        "expectancy_evidence_count": evidence_count,
        "expectancy_confidence": expectancy_confidence(evidence_count),
        "expectancy_tags": " / ".join(matched.sort_values("expectancy_score", ascending=False)["tag"].astype(str)),
    }


def attach_priority_expectancy(changes: dict[str, Any], signal_performance: pd.DataFrame) -> dict[str, Any]:
    enriched = dict(changes)
    tag_expectancy = build_tag_expectancy(signal_performance)
    current = changes.get("current", pd.DataFrame()).copy()
    expectancy_columns = [
        "expectancy_score", "expectancy_expected_return", "expectancy_win_rate",
        "expectancy_evidence_count", "expectancy_confidence", "expectancy_tags",
    ]
    if not current.empty:
        expectancy_rows = current.apply(lambda row: pd.Series(candidate_expectancy_values(row.get("priority_labels", []), tag_expectancy)), axis=1)
        for column in expectancy_columns:
            current[column] = expectancy_rows[column].values
        current["expectancy_has_evidence"] = current["expectancy_evidence_count"] >= 3
        current = current.sort_values(
            ["expectancy_has_evidence", "expectancy_score", "priority_signal_count", "score", "rank"],
            ascending=[False, False, False, False, True],
        )

    table = changes.get("table", pd.DataFrame()).copy()
    lifecycle = changes.get("lifecycle", pd.DataFrame()).copy()
    merge_columns = ["code", *expectancy_columns]
    expectancy_by_code = current[merge_columns].drop_duplicates("code") if not current.empty else pd.DataFrame(columns=merge_columns)
    for frame_name, frame in (("table", table), ("lifecycle", lifecycle)):
        if not frame.empty:
            frame = frame.drop(columns=[column for column in expectancy_columns if column in frame.columns], errors="ignore")
            frame = frame.merge(expectancy_by_code, on="code", how="left")
        enriched[frame_name] = frame
    enriched["current"] = current
    enriched["tag_expectancy"] = tag_expectancy
    enriched["expectancy"] = current.copy()
    return enriched


def expectancy_detail(row: pd.Series) -> str:
    count = int(optional_number(row.get("expectancy_evidence_count")) or 0)
    if count < 3:
        return f"実績蓄積中（{count}件）"
    return (
        f"期待値 {float(row.get('expectancy_score', 50)):.1f}点 / 信頼度 {optional_text(row.get('expectancy_confidence'))} / "
        f"実績 {count}件 / 推定勝率 {fmt_optional_pct(row.get('expectancy_win_rate'))} / "
        f"加重期待騰落率 {fmt_optional_pct(row.get('expectancy_expected_return'))}"
    )
'''

replace_once(
    "\n\ndef plain_priority_section(priority: pd.DataFrame) -> list[str]:",
    helpers + "\n\ndef plain_priority_section(priority: pd.DataFrame) -> list[str]:",
)

replace_once(
    '''        lifecycle_detail = priority_lifecycle_detail(r)
        if lifecycle_detail:
            lines.append(f"   継続力 {lifecycle_detail}")
        lines.append("")
''',
    '''        lifecycle_detail = priority_lifecycle_detail(r)
        if lifecycle_detail:
            lines.append(f"   継続力 {lifecycle_detail}")
        lines.append(f"   実績評価 {expectancy_detail(r)}")
        if optional_text(r.get("expectancy_tags")):
            lines.append(f"   根拠タグ {optional_text(r.get('expectancy_tags'))}")
        lines.append("")
''',
)

replace_once(
    '''        lifecycle_detail = priority_lifecycle_detail(r)
        lifecycle_detail_html = f'<div style="font-size:11px;line-height:1.7;color:#7c3aed;font-weight:800;margin-top:3px">継続力 {html_text(lifecycle_detail)}</div>' if lifecycle_detail else ""
        items.append(
''',
    '''        lifecycle_detail = priority_lifecycle_detail(r)
        lifecycle_detail_html = f'<div style="font-size:11px;line-height:1.7;color:#7c3aed;font-weight:800;margin-top:3px">継続力 {html_text(lifecycle_detail)}</div>' if lifecycle_detail else ""
        expectancy_count = int(optional_number(r.get("expectancy_evidence_count")) or 0)
        expectancy_color = "#15803d" if float(r.get("expectancy_score", 50) or 50) >= 65 and expectancy_count >= 3 else "#a16207" if expectancy_count >= 3 else "#64748b"
        expectancy_html = f'<div style="font-size:11px;line-height:1.7;color:{expectancy_color};font-weight:900;margin-top:3px">実績評価 {html_text(expectancy_detail(r))}</div>'
        expectancy_tags_html = f'<div style="font-size:10px;color:#64748b">根拠タグ {html_text(optional_text(r.get("expectancy_tags")))}</div>' if optional_text(r.get("expectancy_tags")) else ""
        items.append(
''',
)
replace_once(
    '''{lifecycle_detail_html}
</div>"""
''',
    '''{lifecycle_detail_html}
{expectancy_html}
{expectancy_tags_html}
</div>"""
''',
)

replace_once(
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
)
replace_once(
    '        priority_lifecycle.to_excel(w, sheet_name="Priority Lifecycle", index=False)\n        priority_performance.to_excel(w, sheet_name="Priority Performance", index=False)',
    '        priority_lifecycle.to_excel(w, sheet_name="Priority Lifecycle", index=False)\n        priority_expectancy.to_excel(w, sheet_name="Priority Expectancy", index=False)\n        priority_performance.to_excel(w, sheet_name="Priority Performance", index=False)',
)

replace_once(
    '    signal_performance = build_signal_performance_summary(priority_performance)\n',
    '    signal_performance = build_signal_performance_summary(priority_performance)\n    priority_changes = attach_priority_expectancy(priority_changes, signal_performance)\n',
)
replace_once(
    '        "レポート形式": "dashboard_performance_scorecard_v9",',
    '        "レポート形式": "dashboard_expectancy_score_v10",',
)
replace_once(
    '        "重点候補20日平均騰落率": overall_performance_stats(signal_performance, 20).get("average_return"),\n',
    '        "重点候補20日平均騰落率": overall_performance_stats(signal_performance, 20).get("average_return"),\n        "期待値評価済み候補": int((priority_changes.get("current", pd.DataFrame()).get("expectancy_evidence_count", pd.Series(dtype=float)).fillna(0) >= 3).sum()),\n        "期待値高信頼度候補": int((priority_changes.get("current", pd.DataFrame()).get("expectancy_confidence", pd.Series(dtype=str)) == "高").sum()),\n        "重点候補平均期待値スコア": float(priority_changes.get("current", pd.DataFrame()).get("expectancy_score", pd.Series(dtype=float)).mean()) if not priority_changes.get("current", pd.DataFrame()).empty else None,\n',
)
replace_once(
    '    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_performance, signal_performance, temperature, errors, universe_df)',
    '    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], priority_performance, signal_performance, temperature, errors, universe_df)',
)

path.write_text(text, encoding="utf-8")
print("Applied priority expectancy score update")
