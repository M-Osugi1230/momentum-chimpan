from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match, found {count}: {old[:160]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-10-dashboard-regime-history-v6"',
    'APP_VERSION = "2026-07-10-dashboard-priority-changes-v7"',
)

replace_once(
    '''def row_flag(r: pd.Series, key: str) -> bool:
    value = r.get(key)
    if value is None or pd.isna(value):
        return False
    return bool(value)
''',
    '''def row_flag(r: pd.Series, key: str) -> bool:
    value = r.get(key)
    if value is None or pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)
''',
)

helpers = r'''

def priority_labels_text(labels: Any) -> str:
    if isinstance(labels, (list, tuple, set)):
        return " / ".join(str(label) for label in labels if str(label).strip())
    return optional_text(labels)


def latest_previous_top100(history: pd.DataFrame, today: str, top_limit: int) -> tuple[pd.DataFrame, str]:
    if history is None or history.empty or "date" not in history.columns or "rank" not in history.columns:
        return pd.DataFrame(), ""
    work = history.copy()
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work["rank"] = pd.to_numeric(work["rank"], errors="coerce")
    work = work.dropna(subset=["date_sort", "rank"])
    work = work[work["date"].astype(str) != str(today)]
    if work.empty:
        return pd.DataFrame(), ""
    previous_date_value = work["date_sort"].max()
    previous = work[(work["date_sort"] == previous_date_value) & (work["rank"] <= top_limit)].copy()
    if previous.empty:
        return previous, previous_date_value.date().isoformat()
    previous["code"] = previous["code"].map(normalize_code)
    return previous.sort_values("rank"), previous_date_value.date().isoformat()


def optional_number(value: Any) -> float | None:
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(converted) else float(converted)


def compare_priority_candidates(top100: pd.DataFrame, history: pd.DataFrame, today: str, top_limit: int) -> dict[str, Any]:
    current_top100 = top100.copy()
    if not current_top100.empty:
        current_top100["code"] = current_top100["code"].map(normalize_code)
    current = select_priority_candidates(current_top100, max(top_limit, len(current_top100))).copy()
    previous_top100, previous_date = latest_previous_top100(history, today, top_limit)
    previous = select_priority_candidates(previous_top100, max(top_limit, len(previous_top100))).copy() if not previous_top100.empty else pd.DataFrame()

    current_index = current.set_index("code", drop=False) if not current.empty else pd.DataFrame()
    previous_index = previous.set_index("code", drop=False) if not previous.empty else pd.DataFrame()
    top100_index = current_top100.set_index("code", drop=False) if not current_top100.empty else pd.DataFrame()
    current_codes = set(current["code"]) if not current.empty else set()
    previous_codes = set(previous["code"]) if not previous.empty else set()
    records: list[dict[str, Any]] = []

    for _, row in current.iterrows():
        code = normalize_code(row.get("code"))
        previous_row = previous_index.loc[code] if code in previous_codes else None
        current_labels = list(row.get("priority_labels", []))
        previous_labels = list(previous_row.get("priority_labels", [])) if previous_row is not None else []
        status = "継続" if previous_row is not None else "新規"
        records.append({
            "date": today,
            "previous_date": previous_date,
            "status": status,
            "code": code,
            "name": row.get("name", ""),
            "current_rank": optional_number(row.get("rank")),
            "previous_rank": optional_number(previous_row.get("rank")) if previous_row is not None else None,
            "current_score": optional_number(row.get("score")),
            "previous_score": optional_number(previous_row.get("score")) if previous_row is not None else None,
            "current_labels": priority_labels_text(current_labels),
            "previous_labels": priority_labels_text(previous_labels),
            "label_changed": bool(previous_row is not None and set(current_labels) != set(previous_labels)),
            "exit_reason": "",
            "return_20d": optional_number(row.get("return_20d")),
            "volume_ratio": optional_number(row.get("volume_ratio")),
            "trading_value": optional_number(row.get("trading_value")),
        })

    for _, row in previous.iterrows():
        code = normalize_code(row.get("code"))
        if code in current_codes:
            continue
        current_row = top100_index.loc[code] if not top100_index.empty and code in top100_index.index else None
        records.append({
            "date": today,
            "previous_date": previous_date,
            "status": "脱落",
            "code": code,
            "name": row.get("name", ""),
            "current_rank": optional_number(current_row.get("rank")) if current_row is not None else None,
            "previous_rank": optional_number(row.get("rank")),
            "current_score": optional_number(current_row.get("score")) if current_row is not None else None,
            "previous_score": optional_number(row.get("score")),
            "current_labels": priority_labels_text(priority_candidate_labels(current_row)) if current_row is not None else "",
            "previous_labels": priority_labels_text(row.get("priority_labels", [])),
            "label_changed": False,
            "exit_reason": "重点条件外" if current_row is not None else "Top100圏外",
            "return_20d": optional_number(current_row.get("return_20d")) if current_row is not None else optional_number(row.get("return_20d")),
            "volume_ratio": optional_number(current_row.get("volume_ratio")) if current_row is not None else optional_number(row.get("volume_ratio")),
            "trading_value": optional_number(current_row.get("trading_value")) if current_row is not None else optional_number(row.get("trading_value")),
        })

    columns = [
        "date", "previous_date", "status", "code", "name", "current_rank", "previous_rank",
        "current_score", "previous_score", "current_labels", "previous_labels", "label_changed",
        "exit_reason", "return_20d", "volume_ratio", "trading_value",
    ]
    table = pd.DataFrame(records, columns=columns)
    if not table.empty:
        status_order = pd.Categorical(table["status"], categories=["新規", "継続", "脱落"], ordered=True)
        table = table.assign(status_order=status_order).sort_values(
            ["status_order", "current_rank", "previous_rank"], na_position="last"
        ).drop(columns=["status_order"])

    new_rows = table[table["status"] == "新規"].copy() if not table.empty else table.copy()
    continued_rows = table[table["status"] == "継続"].copy() if not table.empty else table.copy()
    dropped_rows = table[table["status"] == "脱落"].copy() if not table.empty else table.copy()
    changed_rows = continued_rows[continued_rows["label_changed"] == True].copy() if not continued_rows.empty else continued_rows.copy()
    return {
        "previous_date": previous_date,
        "current": current,
        "table": table,
        "new": new_rows,
        "continued": continued_rows,
        "dropped": dropped_rows,
        "label_changed": changed_rows,
    }


def priority_change_count(changes: dict[str, Any], key: str) -> int:
    value = changes.get(key)
    return len(value) if isinstance(value, pd.DataFrame) else 0


def priority_rank_label(value: Any, prefix: str = "#") -> str:
    number = optional_number(value)
    return "-" if number is None else f"{prefix}{int(number)}"


def plain_priority_changes_section(changes: dict[str, Any]) -> list[str]:
    previous_date = optional_text(changes.get("previous_date"))
    if not previous_date:
        return ["【重点候補の変化】", "前回のランキング履歴がないため、本日から比較を開始します。", ""]
    new_rows = changes.get("new", pd.DataFrame())
    continued_rows = changes.get("continued", pd.DataFrame())
    dropped_rows = changes.get("dropped", pd.DataFrame())
    changed_rows = changes.get("label_changed", pd.DataFrame())
    lines = [
        "【重点候補の変化】",
        f"比較日 {previous_date} / 新規 {len(new_rows)}件 / 継続 {len(continued_rows)}件 / 脱落 {len(dropped_rows)}件 / タグ変化 {len(changed_rows)}件",
    ]
    if not new_rows.empty:
        lines.append("■ 今日から重点候補")
        for _, row in new_rows.head(5).iterrows():
            lines.append(f"{priority_rank_label(row.get('current_rank'))} {row['code']} {row['name']}｜{row['current_labels']}｜{fmt_int(row.get('current_score'))}点")
    if not changed_rows.empty:
        lines.append("■ タグ変化")
        for _, row in changed_rows.head(5).iterrows():
            lines.append(f"{priority_rank_label(row.get('current_rank'))} {row['code']} {row['name']}｜{row['previous_labels']} → {row['current_labels']}")
    if not dropped_rows.empty:
        lines.append("■ 重点候補から脱落")
        for _, row in dropped_rows.head(5).iterrows():
            current_rank = priority_rank_label(row.get("current_rank"))
            current_text = f"現在{current_rank}" if current_rank != "-" else "現在Top100圏外"
            lines.append(f"前回{priority_rank_label(row.get('previous_rank'))} {row['code']} {row['name']}｜{row['previous_labels']}｜{row['exit_reason']}（{current_text}）")
    lines.append("")
    return lines


def html_priority_changes_section(changes: dict[str, Any]) -> str:
    previous_date = optional_text(changes.get("previous_date"))
    if not previous_date:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:14px;margin-top:14px"><b>重点候補の変化</b><div style="font-size:12px;color:#64748b;margin-top:5px">前回履歴がないため、本日から比較を開始します。</div></div>'
    new_rows = changes.get("new", pd.DataFrame())
    continued_rows = changes.get("continued", pd.DataFrame())
    dropped_rows = changes.get("dropped", pd.DataFrame())
    changed_rows = changes.get("label_changed", pd.DataFrame())

    def rows_html(df: pd.DataFrame, kind: str) -> str:
        parts = []
        for _, row in df.head(5).iterrows():
            if kind == "new":
                title = f"{priority_rank_label(row.get('current_rank'))} {row['code']} {row['name']}"
                detail = f"{row['current_labels']} ・ {fmt_int(row.get('current_score'))}点"
                color = "#15803d"
            elif kind == "changed":
                title = f"{priority_rank_label(row.get('current_rank'))} {row['code']} {row['name']}"
                detail = f"{row['previous_labels']} → {row['current_labels']}"
                color = "#2563eb"
            else:
                title = f"前回{priority_rank_label(row.get('previous_rank'))} {row['code']} {row['name']}"
                current_rank = priority_rank_label(row.get("current_rank"))
                current_text = f"現在{current_rank}" if current_rank != "-" else "現在Top100圏外"
                detail = f"{row['previous_labels']} ・ {row['exit_reason']}（{current_text}）"
                color = "#b91c1c"
            parts.append(f'<div style="border-top:1px solid #e5e7eb;padding:8px 0"><div style="font-size:13px;font-weight:800;color:{color}">{html_text(title)}</div><div style="font-size:11px;line-height:1.6;color:#475569">{html_text(detail)}</div></div>')
        return "".join(parts)

    groups = []
    if not new_rows.empty:
        groups.append(f'<div style="font-size:12px;font-weight:900;color:#15803d;margin-top:10px">今日から重点候補</div>{rows_html(new_rows, "new")}')
    if not changed_rows.empty:
        groups.append(f'<div style="font-size:12px;font-weight:900;color:#2563eb;margin-top:10px">タグ変化</div>{rows_html(changed_rows, "changed")}')
    if not dropped_rows.empty:
        groups.append(f'<div style="font-size:12px;font-weight:900;color:#b91c1c;margin-top:10px">重点候補から脱落</div>{rows_html(dropped_rows, "dropped")}')
    return f'''<div style="background:#fff;border:1px solid #cbd5e1;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:17px;font-weight:900;color:#0f172a">重点候補の変化</div>
<div style="font-size:12px;color:#64748b;margin-top:4px">比較日 {html_text(previous_date)} ・ 新規 {len(new_rows)}件 ・ 継続 {len(continued_rows)}件 ・ 脱落 {len(dropped_rows)}件 ・ タグ変化 {len(changed_rows)}件</div>
{"".join(groups)}
</div>'''
'''

replace_once(
    "\n\ndef plain_priority_section(priority: pd.DataFrame) -> list[str]:",
    helpers + "\n\ndef plain_priority_section(priority: pd.DataFrame) -> list[str]:",
)

replace_once(
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
)
replace_once(
    '        ytd_high_ranking.to_excel(w, sheet_name="YTD High Ranking", index=False)\n        temperature.to_excel(w, sheet_name="Market Temperature", index=False)',
    '        ytd_high_ranking.to_excel(w, sheet_name="YTD High Ranking", index=False)\n        priority_changes.to_excel(w, sheet_name="Priority Changes", index=False)\n        temperature.to_excel(w, sheet_name="Market Temperature", index=False)',
)

replace_once(
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> str:',
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
)
replace_once(
    '    priority = select_priority_candidates(top100, 10)\n',
    '    priority = priority_changes.get("current", select_priority_candidates(top100, 10)).head(10)\n',
)
replace_once(
    '        f"今日の重点候補: {len(priority)}件",\n',
    '        f"今日の重点候補: {priority_change_count(priority_changes, \'current\')}件",\n        f"重点候補変化: 新規 {priority_change_count(priority_changes, \'new\')} / 継続 {priority_change_count(priority_changes, \'continued\')} / 脱落 {priority_change_count(priority_changes, \'dropped\')}",\n',
)
replace_once(
    '    lines += plain_priority_section(priority)\n    lines += plain_metric_highlights(top100)',
    '    lines += plain_priority_section(priority)\n    lines += plain_priority_changes_section(priority_changes)\n    lines += plain_metric_highlights(top100)',
)

replace_once(
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> str:',
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> str:',
)
replace_once(
    '    priority = select_priority_candidates(top100, 10)\n',
    '    priority = priority_changes.get("current", select_priority_candidates(top100, 10)).head(10)\n',
)
replace_once(
    '        html_priority_section(priority),\n        html_metric_highlights(top100),',
    '        html_priority_section(priority),\n        html_priority_changes_section(priority_changes),\n        html_metric_highlights(top100),',
)

replace_once(
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> None:',
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, priority_changes: dict[str, Any], cfg: dict[str, Any]) -> None:',
)
replace_once(
    '    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, cfg), "plain", "utf-8"))\n    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, cfg), "html", "utf-8"))',
    '    msg.attach(MIMEText(build_plain_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, priority_changes, cfg), "plain", "utf-8"))\n    msg.attach(MIMEText(build_html_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, priority_changes, cfg), "html", "utf-8"))',
)

replace_once(
    '    ytd_high_ranking = all_ranked[all_ranked["ytd_high_flag"] == True].sort_values(["ytd_high_streak", "ytd_high_count", "score"], ascending=[False, False, False]).copy() if not all_ranked.empty else all_ranked.copy()\n\n    temp_path = cfg["data"]["market_temperature_path"]',
    '    ytd_high_ranking = all_ranked[all_ranked["ytd_high_flag"] == True].sort_values(["ytd_high_streak", "ytd_high_count", "score"], ascending=[False, False, False]).copy() if not all_ranked.empty else all_ranked.copy()\n    priority_changes = compare_priority_candidates(top100, history, today, top_limit)\n\n    temp_path = cfg["data"]["market_temperature_path"]',
)
replace_once(
    '        "レポート形式": "dashboard_regime_history_v6",',
    '        "レポート形式": "dashboard_priority_changes_v7",',
)
replace_once(
    '        "Momentum Top100": len(top100),\n',
    '        "Momentum Top100": len(top100),\n        "重点候補数": priority_change_count(priority_changes, "current"),\n        "重点候補新規": priority_change_count(priority_changes, "new"),\n        "重点候補継続": priority_change_count(priority_changes, "continued"),\n        "重点候補脱落": priority_change_count(priority_changes, "dropped"),\n        "重点候補タグ変化": priority_change_count(priority_changes, "label_changed"),\n        "重点候補比較日": priority_changes.get("previous_date", ""),\n',
)
replace_once(
    '    excel_report(cfg["data"]["output_path"], summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, errors, universe_df)',
    '    excel_report(cfg["data"]["output_path"], summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], temperature, errors, universe_df)',
)
replace_once(
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, cfg)',
    '        send_email(summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, temperature, priority_changes, cfg)',
)

path.write_text(text, encoding="utf-8")
print("Applied priority candidate change tracking update")
