from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

text = text.replace(
    'APP_VERSION = "2026-07-10-dashboard-expectancy-score-v10"',
    'APP_VERSION = "2026-07-11-dashboard-action-priority-v11"',
)

old_excel_signature = 'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:'
new_excel_signature = 'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance: pd.DataFrame, signal_performance: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:'
if old_excel_signature not in text:
    raise RuntimeError("excel_report signature anchor not found")
text = text.replace(old_excel_signature, new_excel_signature, 1)

excel_anchor = '        priority_expectancy.to_excel(w, sheet_name="Priority Expectancy", index=False)\n'
if excel_anchor not in text:
    raise RuntimeError("Priority Expectancy sheet anchor not found")
text = text.replace(
    excel_anchor,
    excel_anchor + '        action_priority.to_excel(w, sheet_name="Action Priority", index=False)\n',
    1,
)

function_anchor = '\n\ndef expectancy_detail(row: pd.Series) -> str:\n'
if function_anchor not in text:
    raise RuntimeError("expectancy_detail anchor not found")

functions = r'''

ACTION_PRIORITY_ORDER = {"A": 0, "B": 1, "C": 2, "見送り": 3}


def action_priority_values(row: pd.Series, regime: dict[str, Any]) -> dict[str, Any]:
    """Assign a transparent research priority without changing Momentum ranking."""
    expectancy_score = row_number(row, "expectancy_score", 50.0)
    evidence_count = int(row_number(row, "expectancy_evidence_count", 0.0))
    confidence = optional_text(row.get("expectancy_confidence")) or "蓄積中"
    momentum_score = row_number(row, "score")
    momentum_rank = int(row_number(row, "rank", 999.0))
    trading_value = row_number(row, "trading_value")
    volume_ratio = row_number(row, "volume_ratio")
    ma20_deviation = row_number(row, "ma20_deviation")
    labels = list(row.get("priority_labels", [])) if isinstance(row.get("priority_labels", []), (list, tuple, set)) else [item.strip() for item in str(row.get("priority_labels", "")).split("/") if item.strip()]
    lifecycle = optional_text(row.get("priority_lifecycle_status"))
    lifecycle_streak = int(row_number(row, "priority_streak_days", 0.0))
    regime_label = optional_text(regime.get("label")) or "中立"

    positive: list[str] = []
    cautions: list[str] = []
    action_score = 0.0

    if evidence_count >= 3:
        if expectancy_score >= 80:
            action_score += 30
        elif expectancy_score >= 70:
            action_score += 25
        elif expectancy_score >= 60:
            action_score += 18
        elif expectancy_score >= 50:
            action_score += 10
        else:
            action_score += 3
        positive.append(f"期待値{expectancy_score:.1f}点")
    else:
        action_score += 5
        cautions.append(f"期待値の実績蓄積中（{evidence_count}件）")

    confidence_points = {"高": 15, "中": 10, "低": 5, "蓄積中": 0}
    action_score += confidence_points.get(confidence, 0)
    if confidence in {"高", "中", "低"}:
        positive.append(f"信頼度 {confidence}")

    if momentum_score >= 85:
        action_score += 15
    elif momentum_score >= 75:
        action_score += 12
    elif momentum_score >= 65:
        action_score += 8
    elif momentum_score >= 60:
        action_score += 5
    if momentum_score >= 75:
        positive.append(f"Momentum {int(momentum_score)}点")

    if momentum_rank <= 10:
        action_score += 10
        positive.append("Momentum上位10位")
    elif momentum_rank <= 30:
        action_score += 7
        positive.append("Momentum上位30位")
    elif momentum_rank <= 60:
        action_score += 4
    else:
        action_score += 1

    if trading_value >= 5_000_000_000:
        action_score += 12
        liquidity_check = "流動性十分（売買代金50億円以上）"
        positive.append("売買代金50億円以上")
    elif trading_value >= 1_000_000_000:
        action_score += 9
        liquidity_check = "流動性良好（売買代金10億円以上）"
        positive.append("売買代金10億円以上")
    elif trading_value >= 300_000_000:
        action_score += 6
        liquidity_check = "流動性確認済み（売買代金3億円以上）"
    elif trading_value >= 100_000_000:
        action_score += 3
        liquidity_check = "最低流動性基準を充足"
    elif trading_value >= 50_000_000:
        action_score -= 12
        liquidity_check = "流動性不足（売買代金1億円未満）"
        cautions.append(liquidity_check)
    else:
        action_score -= 25
        liquidity_check = "流動性不足（売買代金5,000万円未満）"
        cautions.append(liquidity_check)

    if volume_ratio >= 3.0:
        action_score += 7
        positive.append(f"出来高{volume_ratio:.1f}倍")
    elif volume_ratio >= 2.0:
        action_score += 5
        positive.append(f"出来高{volume_ratio:.1f}倍")
    elif volume_ratio >= 1.5:
        action_score += 3
    elif volume_ratio < 1.0:
        cautions.append("出来高倍率1倍未満")

    bullish = regime_label in {"強気", "やや強気"}
    defensive = regime_label in {"中立", "弱気"}
    if bullish:
        tag_points = {"初動": 6, "加速": 7, "継続": 4, "大型資金": 7}
    elif defensive:
        tag_points = {"初動": 2, "加速": 2, "継続": 7, "大型資金": 6}
    else:
        tag_points = {"初動": 1, "加速": 2, "継続": 5, "大型資金": 5}
    for label in labels:
        if label == "過熱注意":
            continue
        action_score += tag_points.get(label, 0)
        if label in {"初動", "加速", "継続", "大型資金"}:
            positive.append(label)
    if bullish and any(label in labels for label in {"初動", "加速"}):
        positive.append(f"{regime_label}相場の初動・加速候補")
    if defensive and any(label in labels for label in {"継続", "大型資金"}):
        positive.append(f"{regime_label}相場の継続候補")

    lifecycle_points = {"長期定着": 8, "定着": 5, "継続": 3, "再浮上": 2, "初登場": 3 if bullish else 0}
    action_score += lifecycle_points.get(lifecycle, 0)
    if lifecycle in {"長期定着", "定着"}:
        positive.append(lifecycle)
    elif lifecycle == "継続" and lifecycle_streak > 0:
        positive.append(f"継続{lifecycle_streak}日")
    elif lifecycle == "初登場" and not bullish:
        cautions.append("初登場のため継続確認が必要")

    if regime_label == "過熱警戒":
        action_score -= 5
        cautions.append("過熱警戒相場")
    elif regime_label == "弱気":
        cautions.append("弱気相場のため選別を厳格化")

    if ma20_deviation >= 0.25:
        action_score -= 12
        cautions.append(f"20日線乖離{ma20_deviation:.1%}")
    elif ma20_deviation >= 0.20:
        action_score -= 8
        cautions.append(f"20日線乖離{ma20_deviation:.1%}")
    elif ma20_deviation >= 0.15:
        cautions.append(f"20日線乖離{ma20_deviation:.1%}")

    action_score = round(max(0.0, min(100.0, action_score)), 1)
    a_threshold = 88.0 if regime_label == "過熱警戒" else 80.0
    if action_score >= a_threshold:
        priority = "A"
    elif action_score >= 65:
        priority = "B"
    elif action_score >= 50:
        priority = "C"
    else:
        priority = "見送り"

    if evidence_count < 3 and priority == "A":
        priority = "B"
    if trading_value < 50_000_000:
        priority = "見送り"
    elif trading_value < 100_000_000 and priority in {"A", "B"}:
        priority = "C"
    if momentum_score < 60 and priority == "A":
        priority = "B"
    if regime_label == "過熱警戒" and confidence not in {"中", "高"} and priority == "A":
        priority = "B"

    overheat = "過熱注意" in labels
    if overheat:
        overheat_check = "過熱注意あり・原則1段階引き下げ"
        cautions.append("過熱注意")
        priority = {"A": "B", "B": "C", "C": "見送り", "見送り": "見送り"}[priority]
    else:
        overheat_check = "過熱注意なし"

    return {
        "market_regime": regime_label,
        "action_priority": priority,
        "action_score": action_score,
        "positive_reasons": " / ".join(dict.fromkeys(positive)),
        "caution_reasons": " / ".join(dict.fromkeys(cautions)),
        "liquidity_check": liquidity_check,
        "overheat_check": overheat_check,
    }


def attach_action_priority(changes: dict[str, Any], regime: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(changes)
    current = changes.get("current", pd.DataFrame()).copy()
    columns = [
        "code", "name", "momentum_rank", "momentum_score", "priority_labels",
        "lifecycle_status", "expectancy_score", "expectancy_confidence",
        "expectancy_evidence_count", "market_regime", "action_priority", "action_score",
        "positive_reasons", "caution_reasons", "liquidity_check", "overheat_check",
        "return_20d", "ma20_deviation", "volume_ratio", "trading_value",
    ]
    if current.empty:
        enriched["action_priority"] = pd.DataFrame(columns=columns)
        return enriched

    scored = current.apply(lambda row: pd.Series(action_priority_values(row, regime)), axis=1)
    for column in scored.columns:
        current[column] = scored[column].values
    current["action_priority_order"] = current["action_priority"].map(ACTION_PRIORITY_ORDER).fillna(99)
    action = current.rename(columns={
        "rank": "momentum_rank",
        "score": "momentum_score",
        "priority_lifecycle_status": "lifecycle_status",
    }).copy()
    action["priority_labels"] = action["priority_labels"].map(priority_labels_text)
    action = action.sort_values(
        ["action_priority_order", "action_score", "expectancy_score", "momentum_rank"],
        ascending=[True, False, False, True],
    )
    action = action[[column for column in columns if column in action.columns]]
    enriched["current"] = current.drop(columns=["action_priority_order"], errors="ignore")
    enriched["action_priority"] = action
    return enriched


def action_priority_count(action: pd.DataFrame, priority: str) -> int:
    if action is None or action.empty or "action_priority" not in action.columns:
        return 0
    return int((action["action_priority"] == priority).sum())


def plain_action_priority_section(action: pd.DataFrame) -> list[str]:
    if action is None or action.empty:
        return ["【本日の調査優先度】", "本日の重点候補はありません。", ""]
    counts = {priority: action_priority_count(action, priority) for priority in ["A", "B", "C", "見送り"]}
    lines = [
        "【本日の調査優先度】",
        "売買推奨ではなく、本日詳しく調査する順番です。",
        f"A評価 {counts['A']}件 / B評価 {counts['B']}件 / C評価 {counts['C']}件 / 見送り {counts['見送り']}件",
    ]
    if counts["A"] == 0:
        lines.append("本日のA評価はありません。")
    for priority in ["A", "B"]:
        subset = action[action["action_priority"] == priority].head(5)
        if subset.empty:
            continue
        lines.append(f"■ {priority}評価")
        for _, row in subset.iterrows():
            count = int(row_number(row, "expectancy_evidence_count"))
            lines.extend([
                f"#{int(row_number(row, 'momentum_rank'))} {row['code']} {row['name']}",
                f"調査優先度 {priority} / {row_number(row, 'action_score'):.1f}点",
                f"期待値 {row_number(row, 'expectancy_score', 50):.1f}点 / 信頼度 {optional_text(row.get('expectancy_confidence')) or '蓄積中'} / 実績{count}件",
                f"理由：{optional_text(row.get('positive_reasons')) or '-'}",
                f"注意：{optional_text(row.get('caution_reasons')) or '特記事項なし'}",
                "",
            ])
    return lines


def html_action_priority_section(action: pd.DataFrame) -> str:
    if action is None or action.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>本日の調査優先度</b><div style="font-size:12px;color:#64748b;margin-top:5px">本日の重点候補はありません。</div></div>'
    colors = {"A": ("#14532d", "#f0fdf4"), "B": ("#1d4ed8", "#eff6ff")}
    counts = {priority: action_priority_count(action, priority) for priority in ["A", "B", "C", "見送り"]}
    groups = []
    if counts["A"] == 0:
        groups.append('<div style="font-size:12px;color:#64748b;margin-top:10px">本日のA評価はありません。</div>')
    for priority in ["A", "B"]:
        subset = action[action["action_priority"] == priority].head(5)
        if subset.empty:
            continue
        color, background = colors[priority]
        items = []
        for _, row in subset.iterrows():
            count = int(row_number(row, "expectancy_evidence_count"))
            caution = optional_text(row.get("caution_reasons")) or "特記事項なし"
            items.append(f'''<div style="border-top:1px solid #dbeafe;padding:10px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(row_number(row, "momentum_rank"))} {html_text(row["code"])} {html_text(row["name"])} <span style="float:right;color:{color}">{priority} / {row_number(row, "action_score"):.1f}点</span></div>
<div style="clear:both;font-size:11px;color:#475569;margin-top:4px">期待値 {row_number(row, "expectancy_score", 50):.1f}点 ・ 信頼度 {html_text(optional_text(row.get("expectancy_confidence")) or "蓄積中")} ・ 実績{count}件</div>
<div style="font-size:11px;color:{color};font-weight:800;margin-top:3px">理由：{html_text(optional_text(row.get("positive_reasons")) or "-")}</div>
<div style="font-size:11px;color:#b45309;margin-top:3px">注意：{html_text(caution)}</div>
</div>''')
        groups.append(f'<div style="background:{background};border:1px solid {color};border-radius:14px;padding:12px;margin-top:10px"><div style="font-size:15px;font-weight:900;color:{color}">{priority}評価</div>{"".join(items)}</div>')
    return f'''<div style="background:#fff;border:2px solid #334155;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#0f172a">本日の調査優先度</div>
<div style="font-size:12px;color:#64748b;margin-top:4px">売買推奨ではなく、本日詳しく調査する順番です。</div>
<div style="font-size:13px;font-weight:800;color:#334155;margin-top:8px">A評価 {counts["A"]}件 ・ B評価 {counts["B"]}件 ・ C評価 {counts["C"]}件 ・ 見送り {counts["見送り"]}件</div>
{"".join(groups)}
</div>'''
'''
text = text.replace(function_anchor, functions + function_anchor, 1)

plain_anchor = '    lines += plain_market_regime(regime)\n    lines += plain_performance_scorecard(summary.get("_signal_performance", pd.DataFrame()))\n'
if plain_anchor not in text:
    raise RuntimeError("plain email anchor not found")
text = text.replace(
    plain_anchor,
    '    lines += plain_market_regime(regime)\n    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))\n    lines += plain_performance_scorecard(summary.get("_signal_performance", pd.DataFrame()))\n',
    1,
)

html_anchor = '        html_market_regime(regime),\n        html_performance_scorecard(summary.get("_signal_performance", pd.DataFrame())),\n'
if html_anchor not in text:
    raise RuntimeError("html email anchor not found")
text = text.replace(
    html_anchor,
    '        html_market_regime(regime),\n        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),\n        html_performance_scorecard(summary.get("_signal_performance", pd.DataFrame())),\n',
    1,
)

main_anchor = '    regime = enrich_regime_from_temperature(regime, temperature)\n    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)\n'
if main_anchor not in text:
    raise RuntimeError("main regime anchor not found")
text = text.replace(
    main_anchor,
    '    regime = enrich_regime_from_temperature(regime, temperature)\n    priority_changes = attach_action_priority(priority_changes, regime)\n    action_priority = priority_changes.get("action_priority", pd.DataFrame())\n    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)\n',
    1,
)

text = text.replace('"レポート形式": "dashboard_expectancy_score_v10",', '"レポート形式": "dashboard_action_priority_v11",', 1)

summary_anchor = '        "重点候補平均期待値スコア": float(priority_changes.get("current", pd.DataFrame()).get("expectancy_score", pd.Series(dtype=float)).mean()) if not priority_changes.get("current", pd.DataFrame()).empty else None,\n'
if summary_anchor not in text:
    raise RuntimeError("summary expectancy anchor not found")
summary_addition = '''        "調査優先度A": action_priority_count(action_priority, "A"),
        "調査優先度B": action_priority_count(action_priority, "B"),
        "調査優先度C": action_priority_count(action_priority, "C"),
        "調査優先度見送り": action_priority_count(action_priority, "見送り"),
        "A評価平均期待値": float(action_priority[action_priority["action_priority"] == "A"]["expectancy_score"].mean()) if not action_priority.empty and action_priority_count(action_priority, "A") > 0 else None,
        "A評価高信頼度件数": int(((action_priority.get("action_priority", pd.Series(dtype=str)) == "A") & (action_priority.get("expectancy_confidence", pd.Series(dtype=str)) == "高")).sum()) if not action_priority.empty else 0,
'''
text = text.replace(summary_anchor, summary_anchor + summary_addition, 1)

old_excel_call = 'excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], priority_performance, signal_performance, temperature, errors, universe_df)'
new_excel_call = 'excel_report(cfg["data"]["output_path"], {k: v for k, v in summary.items() if not str(k).startswith("_")}, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], priority_changes["expectancy"], action_priority, priority_performance, signal_performance, temperature, errors, universe_df)'
if old_excel_call not in text:
    raise RuntimeError("excel_report call anchor not found")
text = text.replace(old_excel_call, new_excel_call, 1)

path.write_text(text, encoding="utf-8")
print("Patched main.py with action priority feature")
