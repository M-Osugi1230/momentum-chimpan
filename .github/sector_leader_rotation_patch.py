from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one anchor, found {count}: {old[:120]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-11-dashboard-sector-momentum-v12"',
    'APP_VERSION = "2026-07-11-dashboard-sector-leaders-v13"',
)

sector_functions = r'''

SECTOR_ROTATION_ORDER = {
    "加速": 0,
    "主導": 1,
    "改善": 2,
    "履歴開始": 3,
    "減速": 4,
    "底上げ": 5,
    "低迷": 6,
}

SECTOR_LEADER_COLUMNS = [
    "overall_leader_rank", "sector_leader_rank", "sector33", "sector_rank",
    "sector_momentum_score", "sector_strength", "sector_rotation", "sector_score_delta",
    "code", "name", "momentum_rank", "momentum_score", "sector_leader_score",
    "sector_research_priority", "action_priority", "action_score", "expectancy_score",
    "expectancy_confidence", "return_20d", "return_60d", "volume_ratio", "trading_value",
    "ma20_deviation", "leader_reasons", "leader_cautions",
]


def sector_rotation_values(row: pd.Series) -> dict[str, Any]:
    score = row_number(row, "sector_momentum_score")
    delta_value = row.get("sector_score_delta")
    rank_change_value = row.get("sector_rank_change")
    has_history = delta_value is not None and not pd.isna(delta_value)
    delta = 0.0 if not has_history else float(delta_value)
    rank_change = 0 if rank_change_value is None or pd.isna(rank_change_value) else int(float(rank_change_value))

    if not has_history:
        state = "履歴開始"
    elif score >= 60 and (delta >= 3 or rank_change >= 3):
        state = "加速"
    elif score >= 60 and delta > -3 and rank_change > -3:
        state = "主導"
    elif score >= 45 and (delta >= 3 or rank_change >= 3):
        state = "改善"
    elif score >= 45 and (delta <= -3 or rank_change <= -3):
        state = "減速"
    elif score < 45 and (delta >= 3 or rank_change >= 3):
        state = "底上げ"
    else:
        state = "低迷"

    base = min(max(score, 0.0), 100.0)
    rotation_score = base
    rotation_score += min(max(delta, -15.0), 15.0) * 1.3
    rotation_score += min(max(rank_change, -10), 10) * 1.2
    rotation_score = round(min(max(rotation_score, 0.0), 100.0), 1)

    if state == "加速":
        reason = "業種スコアまたは順位が上向き、かつ業種の絶対強度も高い"
    elif state == "主導":
        reason = "高い業種強度を維持"
    elif state == "改善":
        reason = "中立圏から順位またはスコアが改善"
    elif state == "減速":
        reason = "業種強度は残るが順位またはスコアが悪化"
    elif state == "底上げ":
        reason = "弱い水準から改善の兆し"
    elif state == "履歴開始":
        reason = "比較履歴を開始"
    else:
        reason = "業種強度と改善度がともに低い"

    return {
        "sector_rotation": state,
        "sector_rotation_score": rotation_score,
        "sector_rotation_reason": reason,
    }


def attach_sector_rotation(sector_momentum: pd.DataFrame) -> pd.DataFrame:
    if sector_momentum is None or sector_momentum.empty:
        columns = list(SECTOR_MOMENTUM_COLUMNS) + ["sector_rotation", "sector_rotation_score", "sector_rotation_reason"]
        return pd.DataFrame(columns=columns)
    result = sector_momentum.copy()
    rotation = result.apply(lambda row: pd.Series(sector_rotation_values(row)), axis=1)
    for column in rotation.columns:
        result[column] = rotation[column].values
    result["sector_rotation_order"] = result["sector_rotation"].map(SECTOR_ROTATION_ORDER).fillna(99)
    result = result.sort_values(
        ["sector_rotation_order", "sector_rotation_score", "sector_rank"],
        ascending=[True, False, True],
    ).drop(columns=["sector_rotation_order"])
    return result


def build_sector_rotation_table(sector_momentum: pd.DataFrame, sector_leaders: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "sector_rank", "sector33", "sector_momentum_score", "sector_strength", "sector_rotation",
        "sector_rotation_score", "sector_rotation_reason", "previous_sector_rank", "sector_rank_change",
        "previous_sector_score", "sector_score_delta", "top100_count", "top30_count", "above_ma20_ratio",
        "above_ma60_ratio", "top_sector_leader", "top_sector_leader_score",
    ]
    if sector_momentum is None or sector_momentum.empty:
        return pd.DataFrame(columns=columns)
    result = sector_momentum.copy()
    if sector_leaders is not None and not sector_leaders.empty:
        first = sector_leaders.sort_values(["sector33", "sector_leader_rank"]).drop_duplicates("sector33")
        first = first.assign(
            top_sector_leader=first["code"].astype(str) + " " + first["name"].astype(str),
            top_sector_leader_score=first["sector_leader_score"],
        )[["sector33", "top_sector_leader", "top_sector_leader_score"]]
        result = result.merge(first, on="sector33", how="left")
    else:
        result["top_sector_leader"] = ""
        result["top_sector_leader_score"] = None
    result["sector_rotation_order"] = result["sector_rotation"].map(SECTOR_ROTATION_ORDER).fillna(99)
    result = result.sort_values(
        ["sector_rotation_order", "sector_rotation_score", "sector_rank"],
        ascending=[True, False, True],
    ).drop(columns=["sector_rotation_order"])
    return result[[column for column in columns if column in result.columns]]


def leader_action_priority_points(value: Any) -> int:
    return {"A": 10, "B": 6, "C": 2, "見送り": -8}.get(optional_text(value), 0)


def sector_leader_values(row: pd.Series) -> dict[str, Any]:
    momentum_score = row_number(row, "score")
    momentum_rank = int(row_number(row, "rank", 999.0))
    sector_score = row_number(row, "sector_momentum_score")
    rotation = optional_text(row.get("sector_rotation"))
    trading_value = row_number(row, "trading_value")
    volume_ratio = row_number(row, "volume_ratio")
    return_20d = row_number(row, "return_20d")
    ma20_deviation = row_number(row, "ma20_deviation")
    action_priority = optional_text(row.get("action_priority"))
    expectancy_score = row_number(row, "expectancy_score", 50.0)
    confidence = optional_text(row.get("expectancy_confidence")) or "蓄積中"

    reasons: list[str] = []
    cautions: list[str] = []
    score = momentum_score * 0.38 + sector_score * 0.27

    if momentum_rank <= 10:
        score += 12
        reasons.append("Momentum上位10位")
    elif momentum_rank <= 30:
        score += 9
        reasons.append("Momentum上位30位")
    elif momentum_rank <= 60:
        score += 6
    elif momentum_rank <= 100:
        score += 3

    rotation_points = {"加速": 10, "主導": 7, "改善": 6, "履歴開始": 2, "減速": -3, "底上げ": 1, "低迷": -6}
    score += rotation_points.get(rotation, 0)
    if rotation in {"加速", "主導", "改善"}:
        reasons.append(f"業種{rotation}")
    elif rotation in {"減速", "低迷"}:
        cautions.append(f"業種{rotation}")

    score += leader_action_priority_points(action_priority)
    if action_priority in {"A", "B"}:
        reasons.append(f"既存調査優先度{action_priority}")
    elif action_priority == "見送り":
        cautions.append("既存調査優先度は見送り")

    if expectancy_score >= 70 and confidence in {"高", "中"}:
        score += 6
        reasons.append(f"期待値{expectancy_score:.1f}点・信頼度{confidence}")
    elif expectancy_score < 50:
        score -= 3
        cautions.append("期待値50点未満")

    if trading_value >= 5_000_000_000:
        score += 7
        reasons.append("売買代金50億円以上")
    elif trading_value >= 1_000_000_000:
        score += 5
        reasons.append("売買代金10億円以上")
    elif trading_value >= 300_000_000:
        score += 3
    elif trading_value < 100_000_000:
        score -= 15
        cautions.append("売買代金1億円未満")

    if volume_ratio >= 3:
        score += 6
        reasons.append(f"出来高{volume_ratio:.1f}倍")
    elif volume_ratio >= 2:
        score += 4
    elif volume_ratio < 1:
        cautions.append("出来高倍率1倍未満")

    overheat = ma20_deviation >= 0.25 or return_20d >= 0.50
    if overheat:
        score -= 12
        cautions.append("過熱水準")
    elif ma20_deviation >= 0.18:
        score -= 5
        cautions.append(f"20日線乖離{ma20_deviation:.1%}")

    score = round(min(max(score, 0.0), 100.0), 1)
    if score >= 84 and trading_value >= 300_000_000 and not overheat and rotation in {"加速", "主導"}:
        priority = "最優先"
    elif score >= 72 and trading_value >= 100_000_000 and not overheat:
        priority = "優先"
    elif score >= 58 and trading_value >= 100_000_000:
        priority = "監視"
    else:
        priority = "見送り"

    grade = "S" if score >= 88 else "A" if score >= 78 else "B" if score >= 68 else "C"
    return {
        "sector_leader_score": score,
        "sector_leader_grade": grade,
        "sector_research_priority": priority,
        "leader_reasons": " / ".join(dict.fromkeys(reasons)),
        "leader_cautions": " / ".join(dict.fromkeys(cautions)),
    }


def build_sector_leaders(all_ranked: pd.DataFrame, sector_momentum: pd.DataFrame, action_priority: pd.DataFrame, limit_per_sector: int = 3) -> pd.DataFrame:
    columns = list(SECTOR_LEADER_COLUMNS)
    if all_ranked is None or all_ranked.empty or sector_momentum is None or sector_momentum.empty:
        return pd.DataFrame(columns=columns)
    sector_cols = [
        "sector33", "sector_rank", "sector_momentum_score", "sector_strength", "sector_rotation", "sector_score_delta",
    ]
    candidates = all_ranked.copy()
    candidates["sector33"] = candidates["sector33"].map(normalize_sector33)
    candidates = candidates[(candidates["sector33"] != "") & (numeric_series(candidates, "rank") <= 100)].copy()
    candidates = candidates.merge(sector_momentum[sector_cols].drop_duplicates("sector33"), on="sector33", how="left")

    if action_priority is not None and not action_priority.empty:
        action_cols = [
            "code", "action_priority", "action_score", "expectancy_score", "expectancy_confidence",
            "expectancy_evidence_count", "positive_reasons", "caution_reasons",
        ]
        available = [column for column in action_cols if column in action_priority.columns]
        candidates = candidates.merge(action_priority[available].drop_duplicates("code"), on="code", how="left")

    scored = candidates.apply(lambda row: pd.Series(sector_leader_values(row)), axis=1)
    for column in scored.columns:
        candidates[column] = scored[column].values
    candidates = candidates[numeric_series(candidates, "trading_value") >= 50_000_000].copy()
    candidates = candidates.sort_values(
        ["sector33", "sector_leader_score", "rank"],
        ascending=[True, False, True],
    )
    candidates["sector_leader_rank"] = candidates.groupby("sector33").cumcount() + 1
    candidates = candidates[candidates["sector_leader_rank"] <= limit_per_sector].copy()
    candidates = candidates.sort_values(
        ["sector_leader_score", "sector_momentum_score", "rank"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    candidates.insert(0, "overall_leader_rank", range(1, len(candidates) + 1))
    candidates = candidates.rename(columns={"rank": "momentum_rank", "score": "momentum_score"})
    return candidates[[column for column in columns if column in candidates.columns]]


def sector_research_priority_count(leaders: pd.DataFrame, priority: str) -> int:
    if leaders is None or leaders.empty or "sector_research_priority" not in leaders.columns:
        return 0
    return int((leaders["sector_research_priority"] == priority).sum())


def plain_sector_rotation_section(sector_rotation: pd.DataFrame, limit: int = 8) -> list[str]:
    if sector_rotation is None or sector_rotation.empty:
        return ["【業種ローテーション】", "比較可能な業種履歴がありません。", ""]
    lines = [
        "【業種ローテーション】",
        "業種の絶対強度と前回からのスコア・順位変化を組み合わせています。",
    ]
    for _, row in sector_rotation.head(limit).iterrows():
        delta = row.get("sector_score_delta")
        delta_text = "履歴開始" if delta is None or pd.isna(delta) else f"スコア差 {float(delta):+.1f}"
        rank_text = sector_rank_change_text(row.get("sector_rank_change"))
        lines.append(
            f"#{int(row['sector_rank'])} {row['sector33']}｜{row['sector_rotation']}｜"
            f"業種{float(row['sector_momentum_score']):.1f}点｜{delta_text}"
            + (f"｜{rank_text}" if rank_text else "")
        )
        leader = optional_text(row.get("top_sector_leader"))
        if leader:
            lines.append(f"   リーダー: {leader} / {row_number(row, 'top_sector_leader_score'):.1f}点")
    lines.append("")
    return lines


def plain_sector_leaders_section(leaders: pd.DataFrame, limit: int = 10) -> list[str]:
    if leaders is None or leaders.empty:
        return ["【業種リーダー候補】", "該当候補はありません。", ""]
    counts = {value: sector_research_priority_count(leaders, value) for value in ["最優先", "優先", "監視", "見送り"]}
    lines = [
        "【業種リーダー候補】",
        "売買推奨ではなく、強い・改善中の業種内で優先的に調査する銘柄です。",
        f"最優先 {counts['最優先']}件 / 優先 {counts['優先']}件 / 監視 {counts['監視']}件 / 見送り {counts['見送り']}件",
    ]
    subset = leaders[leaders["sector_research_priority"].isin(["最優先", "優先", "監視"])].head(limit)
    for _, row in subset.iterrows():
        lines.extend([
            f"#{int(row['overall_leader_rank'])} {row['code']} {row['name']}｜{row['sector33']} #{int(row['sector_rank'])} {row['sector_rotation']}",
            f"   業種リーダー {row_number(row, 'sector_leader_score'):.1f}点 / 調査 {row['sector_research_priority']} / Momentum #{int(row_number(row, 'momentum_rank'))}",
            f"   理由：{optional_text(row.get('leader_reasons')) or '-'}",
            f"   注意：{optional_text(row.get('leader_cautions')) or '特記事項なし'}",
            "",
        ])
    return lines


def html_sector_rotation_section(sector_rotation: pd.DataFrame, limit: int = 8) -> str:
    if sector_rotation is None or sector_rotation.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>業種ローテーション</b><div style="font-size:12px;color:#64748b;margin-top:5px">比較可能な業種履歴がありません。</div></div>'
    colors = {"加速": "#15803d", "主導": "#1d4ed8", "改善": "#0f766e", "減速": "#b45309", "底上げ": "#7c3aed", "低迷": "#64748b", "履歴開始": "#475569"}
    items = []
    for _, row in sector_rotation.head(limit).iterrows():
        state = optional_text(row.get("sector_rotation"))
        color = colors.get(state, "#475569")
        delta = row.get("sector_score_delta")
        delta_text = "履歴開始" if delta is None or pd.isna(delta) else f"スコア差 {float(delta):+.1f}"
        leader = optional_text(row.get("top_sector_leader"))
        leader_html = f'<div style="font-size:10px;color:#64748b;margin-top:3px">リーダー: {html_text(leader)} / {row_number(row, "top_sector_leader_score"):.1f}点</div>' if leader else ""
        items.append(f'''<div style="border-top:1px solid #e5e7eb;padding:9px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(row["sector_rank"])} {html_text(row["sector33"])} <span style="float:right;color:{color}">{html_text(state)}</span></div>
<div style="clear:both;font-size:11px;color:#475569">業種 {row_number(row, "sector_momentum_score"):.1f}点 ・ {html_text(delta_text)} ・ {html_text(sector_rank_change_text(row.get("sector_rank_change")))}</div>
{leader_html}</div>''')
    return f'''<div style="background:#fff;border:2px solid #0f766e;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#115e59">業種ローテーション</div>
<div style="font-size:12px;color:#64748b;margin-top:4px">絶対強度と前回からの変化を組み合わせています。</div>
{"".join(items)}</div>'''


def html_sector_leaders_section(leaders: pd.DataFrame, limit: int = 10) -> str:
    if leaders is None or leaders.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>業種リーダー候補</b><div style="font-size:12px;color:#64748b;margin-top:5px">該当候補はありません。</div></div>'
    priority_colors = {"最優先": "#14532d", "優先": "#1d4ed8", "監視": "#a16207", "見送り": "#64748b"}
    subset = leaders[leaders["sector_research_priority"].isin(["最優先", "優先", "監視"])].head(limit)
    items = []
    for _, row in subset.iterrows():
        priority = optional_text(row.get("sector_research_priority"))
        color = priority_colors.get(priority, "#475569")
        caution = optional_text(row.get("leader_cautions")) or "特記事項なし"
        items.append(f'''<div style="border-top:1px solid #e5e7eb;padding:10px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(row["overall_leader_rank"])} {html_text(row["code"])} {html_text(row["name"])} <span style="float:right;color:{color}">{html_text(priority)} / {row_number(row, "sector_leader_score"):.1f}点</span></div>
<div style="clear:both;font-size:11px;color:#475569">{html_text(row["sector33"])} #{int(row["sector_rank"])} {html_text(row["sector_rotation"])} ・ Momentum #{int(row_number(row, "momentum_rank"))}</div>
<div style="font-size:11px;color:#15803d;font-weight:800;margin-top:3px">理由：{html_text(optional_text(row.get("leader_reasons")) or "-")}</div>
<div style="font-size:11px;color:#b45309;margin-top:3px">注意：{html_text(caution)}</div>
</div>''')
    counts = {value: sector_research_priority_count(leaders, value) for value in ["最優先", "優先", "監視", "見送り"]}
    return f'''<div style="background:#fff;border:2px solid #334155;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#0f172a">業種リーダー候補</div>
<div style="font-size:12px;color:#64748b;margin-top:4px">強い・改善中の業種内で優先的に調査する銘柄です。売買推奨ではありません。</div>
<div style="font-size:13px;font-weight:800;color:#334155;margin-top:8px">最優先 {counts['最優先']}件 ・ 優先 {counts['優先']}件 ・ 監視 {counts['監視']}件 ・ 見送り {counts['見送り']}件</div>
{"".join(items)}</div>'''
'''

replace_once(
    '\n\ndef market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:',
    sector_functions + '\n\ndef market_temperature(today: str, all_ranked: pd.DataFrame, top100: pd.DataFrame, previous_temperature: pd.DataFrame) -> pd.DataFrame:',
)

replace_once(
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, sector_momentum: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
)
replace_once(
    '        sector_momentum.to_excel(w, sheet_name="Sector Momentum", index=False)\n        new_entries.to_excel(w, sheet_name="New Entries", index=False)',
    '        sector_momentum.to_excel(w, sheet_name="Sector Momentum", index=False)\n        sector_rotation.to_excel(w, sheet_name="Sector Rotation", index=False)\n        sector_leaders.to_excel(w, sheet_name="Sector Leaders", index=False)\n        new_entries.to_excel(w, sheet_name="New Entries", index=False)',
)

replace_once(
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
)
replace_once(
    '    lines += plain_sector_momentum_section(sector_momentum)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))',
    '    lines += plain_sector_momentum_section(sector_momentum)\n    lines += plain_sector_rotation_section(sector_rotation)\n    lines += plain_sector_leaders_section(sector_leaders)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))',
)

replace_once(
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
)
replace_once(
    '        html_sector_momentum_section(sector_momentum),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),',
    '        html_sector_momentum_section(sector_momentum),\n        html_sector_rotation_section(sector_rotation),\n        html_sector_leaders_section(sector_leaders),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),',
)

replace_once(
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, sector_momentum: pd.DataFrame, sector_rotation: pd.DataFrame, sector_leaders: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
)
replace_once(
    '    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, priority_changes, cfg), "plain", "utf-8"))\n    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, priority_changes, cfg), "html", "utf-8"))',
    '    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, priority_changes, cfg), "plain", "utf-8"))\n    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, priority_changes, cfg), "html", "utf-8"))',
)

replace_once(
    '    sector_momentum = calculate_sector_momentum(all_ranked, history, today, top_limit)',
    '    sector_momentum = attach_sector_rotation(calculate_sector_momentum(all_ranked, history, today, top_limit))',
)
replace_once(
    '    action_priority = priority_changes.get("action_priority", pd.DataFrame())\n    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)',
    '    action_priority = priority_changes.get("action_priority", pd.DataFrame())\n    sector_leaders = build_sector_leaders(all_ranked, sector_momentum, action_priority)\n    sector_rotation = build_sector_rotation_table(sector_momentum, sector_leaders)\n    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)',
)
replace_once(
    '        "レポート形式": "dashboard_sector_momentum_v12",',
    '        "レポート形式": "dashboard_sector_leaders_v13",',
)
replace_once(
    '        "最上位業種スコア": float(sector_momentum.iloc[0]["sector_momentum_score"]) if not sector_momentum.empty else None,\n        "重点候補数": priority_change_count(priority_changes, "current"),',
    '        "最上位業種スコア": float(sector_momentum.iloc[0]["sector_momentum_score"]) if not sector_momentum.empty else None,\n        "加速業種数": int((sector_momentum.get("sector_rotation", pd.Series(dtype=str)) == "加速").sum()) if not sector_momentum.empty else 0,\n        "主導業種数": int((sector_momentum.get("sector_rotation", pd.Series(dtype=str)) == "主導").sum()) if not sector_momentum.empty else 0,\n        "改善業種数": int((sector_momentum.get("sector_rotation", pd.Series(dtype=str)) == "改善").sum()) if not sector_momentum.empty else 0,\n        "業種リーダー候補数": len(sector_leaders),\n        "業種リーダー最優先": sector_research_priority_count(sector_leaders, "最優先"),\n        "業種リーダー優先": sector_research_priority_count(sector_leaders, "優先"),\n        "最上位業種リーダー": (str(sector_leaders.iloc[0]["code"]) + " " + str(sector_leaders.iloc[0]["name"])) if not sector_leaders.empty else "",\n        "最上位業種リーダースコア": float(sector_leaders.iloc[0]["sector_leader_score"]) if not sector_leaders.empty else None,\n        "重点候補数": priority_change_count(priority_changes, "current"),',
)
replace_once(
    '    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, sector_momentum, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], action_priority, priority_performance, signal_performance, temperature, errors, universe_df)',
    '    excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, sector_momentum, sector_rotation, sector_leaders, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], action_priority, priority_performance, signal_performance, temperature, errors, universe_df)',
)
replace_once(
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, priority_changes, cfg)',
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, sector_momentum, sector_rotation, sector_leaders, priority_changes, cfg)',
)

path.write_text(text, encoding="utf-8")
print("Applied sector leader and rotation development batch")
