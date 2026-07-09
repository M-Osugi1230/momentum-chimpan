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
    'APP_VERSION = "2026-07-10-dashboard-priority-changes-v7"',
    'APP_VERSION = "2026-07-10-dashboard-priority-lifecycle-v8"',
)

helpers = r"""

def priority_lifecycle_status(streak_days: int, total_days: int, run_count: int) -> str:
    if total_days <= 1:
        return "初登場"
    if run_count >= 2 and streak_days == 1:
        return "再浮上"
    if streak_days >= 10:
        return "長期定着"
    if streak_days >= 5:
        return "定着"
    return "継続"


def priority_candidate_history_events(history: pd.DataFrame, top100: pd.DataFrame, today: str, top_limit: int) -> tuple[pd.DataFrame, list[str]]:
    frames: list[pd.DataFrame] = []
    report_dates: set[str] = {str(today)}

    if history is not None and not history.empty and {"date", "rank"}.issubset(history.columns):
        work = history.copy()
        work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
        work["rank"] = pd.to_numeric(work["rank"], errors="coerce")
        work = work.dropna(subset=["date_sort", "rank"])
        work = work[(work["date"].astype(str) != str(today)) & (work["rank"] <= top_limit)].copy()
        if not work.empty:
            work["date"] = work["date_sort"].dt.date.astype(str)
            work["code"] = work["code"].map(normalize_code)
            report_dates.update(work["date"].unique().tolist())
            for report_date, day_rows in work.groupby("date", sort=True):
                selected = select_priority_candidates(day_rows, max(top_limit, len(day_rows))).copy()
                if selected.empty:
                    continue
                selected["priority_date"] = str(report_date)
                frames.append(selected[["priority_date", "code"]].drop_duplicates())

    current = top100.copy()
    if not current.empty:
        current["code"] = current["code"].map(normalize_code)
        selected_current = select_priority_candidates(current, max(top_limit, len(current))).copy()
        if not selected_current.empty:
            selected_current["priority_date"] = str(today)
            frames.append(selected_current[["priority_date", "code"]].drop_duplicates())

    events = pd.concat(frames, ignore_index=True).drop_duplicates(["priority_date", "code"]) if frames else pd.DataFrame(columns=["priority_date", "code"])
    ordered_dates = sorted(report_dates, key=lambda value: pd.Timestamp(value))
    return events, ordered_dates


def calculate_priority_candidate_lifecycle(history: pd.DataFrame, top100: pd.DataFrame, today: str, top_limit: int) -> pd.DataFrame:
    current = select_priority_candidates(top100, max(top_limit, len(top100))).copy() if not top100.empty else pd.DataFrame()
    columns = [
        "code", "priority_first_date", "priority_last_date", "priority_streak_days",
        "priority_total_days", "priority_run_count", "priority_lifecycle_status",
    ]
    if current.empty:
        return pd.DataFrame(columns=columns)

    current["code"] = current["code"].map(normalize_code)
    events, report_dates = priority_candidate_history_events(history, top100, today, top_limit)
    event_dates_by_code = {
        code: set(group["priority_date"].astype(str))
        for code, group in events.groupby("code")
    }
    records: list[dict[str, Any]] = []
    for code in current["code"].drop_duplicates():
        qualified_dates = event_dates_by_code.get(code, {str(today)})
        ordered_qualified = sorted(qualified_dates, key=lambda value: pd.Timestamp(value))
        streak_days = 0
        for report_date in reversed(report_dates):
            if report_date in qualified_dates:
                streak_days += 1
            else:
                break

        run_count = 0
        active = False
        for report_date in report_dates:
            qualified = report_date in qualified_dates
            if qualified and not active:
                run_count += 1
            active = qualified

        total_days = len(ordered_qualified)
        records.append({
            "code": code,
            "priority_first_date": ordered_qualified[0],
            "priority_last_date": ordered_qualified[-1],
            "priority_streak_days": streak_days,
            "priority_total_days": total_days,
            "priority_run_count": run_count,
            "priority_lifecycle_status": priority_lifecycle_status(streak_days, total_days, run_count),
        })
    return pd.DataFrame(records, columns=columns)


def attach_priority_candidate_lifecycle(changes: dict[str, Any], history: pd.DataFrame, top100: pd.DataFrame, today: str, top_limit: int) -> dict[str, Any]:
    enriched = dict(changes)
    lifecycle = calculate_priority_candidate_lifecycle(history, top100, today, top_limit)
    current = changes.get("current", pd.DataFrame()).copy()
    if not current.empty:
        current["code"] = current["code"].map(normalize_code)
        current = current.merge(lifecycle, on="code", how="left")
        current = current.sort_values(
            ["priority_signal_count", "score", "trading_value", "rank"],
            ascending=[False, False, False, True],
        )

    table = changes.get("table", pd.DataFrame()).copy()
    if not table.empty:
        table["code"] = table["code"].map(normalize_code)
        table = table.merge(lifecycle, on="code", how="left")

    enriched["current"] = current
    enriched["lifecycle"] = current.sort_values(
        ["priority_streak_days", "priority_total_days", "rank"],
        ascending=[False, False, True],
        na_position="last",
    ) if not current.empty else current.copy()
    enriched["table"] = table
    enriched["new"] = table[table["status"] == "新規"].copy() if not table.empty else table.copy()
    enriched["continued"] = table[table["status"] == "継続"].copy() if not table.empty else table.copy()
    enriched["dropped"] = table[table["status"] == "脱落"].copy() if not table.empty else table.copy()
    enriched["label_changed"] = enriched["continued"][enriched["continued"]["label_changed"] == True].copy() if not enriched["continued"].empty else enriched["continued"].copy()
    return enriched


def priority_lifecycle_count(changes: dict[str, Any], status: str) -> int:
    lifecycle = changes.get("lifecycle", pd.DataFrame())
    if lifecycle is None or lifecycle.empty or "priority_lifecycle_status" not in lifecycle.columns:
        return 0
    return int((lifecycle["priority_lifecycle_status"] == status).sum())


def priority_lifecycle_summary(priority: pd.DataFrame) -> str:
    if priority.empty or "priority_lifecycle_status" not in priority.columns:
        return ""
    order = ["初登場", "再浮上", "継続", "定着", "長期定着"]
    counts = priority["priority_lifecycle_status"].value_counts()
    parts = [f"{status} {int(counts.get(status, 0))}件" for status in order if int(counts.get(status, 0)) > 0]
    return " / ".join(parts)


def priority_lifecycle_detail(row: pd.Series) -> str:
    status = optional_text(row.get("priority_lifecycle_status"))
    first_date = optional_text(row.get("priority_first_date"))
    streak = optional_number(row.get("priority_streak_days"))
    total = optional_number(row.get("priority_total_days"))
    if not status:
        return ""
    return f"{status} / 初回 {first_date or '-'} / 連続 {int(streak or 0)}営業日 / 累計 {int(total or 0)}日"
"""

replace_once(
    "\n\ndef priority_change_count(changes: dict[str, Any], key: str) -> int:",
    helpers + "\n\ndef priority_change_count(changes: dict[str, Any], key: str) -> int:",
)

old_plain = '''def plain_priority_section(priority: pd.DataFrame) -> list[str]:
    if priority.empty:
        return []
    lines = [
        "【今日の重点候補】",
        "複数のモメンタム条件が重なった銘柄です。過熱注意は買い推奨ではなく、値動き確認の注意タグです。",
    ]
    for _, r in priority.iterrows():
        tags = " / ".join(r.get("priority_labels", []))
        rank_change = fmt_rank_change(r.get("rank_change"))
        movement = f" / {rank_change}" if rank_change else ""
        lines += [
            f"#{int(r['rank'])} {r['code']} {r['name']}｜{int(r['score'])}点｜{tags}",
            f"   20日 {fmt_pct(r.get('return_20d'))} / 出来高 {fmt_num(r.get('volume_ratio'))}倍 / 売買代金 {fmt_trading_value(r.get('trading_value'))}{movement}",
            "",
        ]
    return lines
'''
new_plain = '''def plain_priority_section(priority: pd.DataFrame) -> list[str]:
    if priority.empty:
        return []
    lines = [
        "【今日の重点候補】",
        "複数のモメンタム条件が重なった銘柄です。過熱注意は買い推奨ではなく、値動き確認の注意タグです。",
    ]
    lifecycle_summary = priority_lifecycle_summary(priority)
    if lifecycle_summary:
        lines.append(f"継続力: {lifecycle_summary}")
    for _, r in priority.iterrows():
        tags = " / ".join(r.get("priority_labels", []))
        rank_change = fmt_rank_change(r.get("rank_change"))
        movement = f" / {rank_change}" if rank_change else ""
        lines += [
            f"#{int(r['rank'])} {r['code']} {r['name']}｜{int(r['score'])}点｜{tags}",
            f"   20日 {fmt_pct(r.get('return_20d'))} / 出来高 {fmt_num(r.get('volume_ratio'))}倍 / 売買代金 {fmt_trading_value(r.get('trading_value'))}{movement}",
        ]
        lifecycle_detail = priority_lifecycle_detail(r)
        if lifecycle_detail:
            lines.append(f"   継続力 {lifecycle_detail}")
        lines.append("")
    return lines
'''
replace_once(old_plain, new_plain)

old_html = '''def html_priority_section(priority: pd.DataFrame) -> str:
    if priority.empty:
        return ""
    items = []
    for _, r in priority.iterrows():
        tag_html = []
        for label in r.get("priority_labels", []):
            if label == "過熱注意":
                background, color = "#fee2e2", "#991b1b"
            elif label == "大型資金":
                background, color = "#ede9fe", "#5b21b6"
            elif label == "継続":
                background, color = "#dcfce7", "#166534"
            elif label == "加速":
                background, color = "#ffedd5", "#9a3412"
            else:
                background, color = "#dbeafe", "#1d4ed8"
            tag_html.append(
                f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:3px 8px;border-radius:999px;background:{background};color:{color};font-size:12px;font-weight:800">{html_text(label)}</span>'
            )
        rank_change = fmt_rank_change(r.get("rank_change"))
        movement = f" ・ {html_text(rank_change)}" if rank_change else ""
        items.append(
            f"""<div style="border-top:1px solid #e5e7eb;padding:11px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(r["rank"])} {html_text(r["code"])} {html_text(r["name"])} <span style="color:{score_color(r["score"])}">{int(r["score"])}点</span></div>
<div style="margin:5px 0">{"".join(tag_html)}</div>
<div style="font-size:12px;line-height:1.7;color:#475569">20日 {fmt_pct(r.get("return_20d"))} ・ 出来高 {fmt_num(r.get("volume_ratio"))}倍 ・ 売買代金 {fmt_trading_value(r.get("trading_value"))}{movement}</div>
</div>"""
        )
    return f"""<div style="background:#fff;border:2px solid #0f172a;border-radius:18px;padding:16px;margin-top:18px">
<div style="font-size:18px;font-weight:900;color:#0f172a">今日の重点候補</div>
<div style="font-size:12px;line-height:1.7;color:#64748b;margin-top:4px">複数のモメンタム条件が重なった銘柄です。過熱注意は売買指示ではなく、値動き確認の注意タグです。</div>
{"".join(items)}
</div>"""
'''
new_html = '''def html_priority_section(priority: pd.DataFrame) -> str:
    if priority.empty:
        return ""
    items = []
    lifecycle_colors = {
        "初登場": ("#dcfce7", "#166534"),
        "再浮上": ("#ffedd5", "#9a3412"),
        "継続": ("#dbeafe", "#1d4ed8"),
        "定着": ("#ede9fe", "#6d28d9"),
        "長期定着": ("#f3e8ff", "#581c87"),
    }
    for _, r in priority.iterrows():
        tag_html = []
        for label in r.get("priority_labels", []):
            if label == "過熱注意":
                background, color = "#fee2e2", "#991b1b"
            elif label == "大型資金":
                background, color = "#ede9fe", "#5b21b6"
            elif label == "継続":
                background, color = "#dcfce7", "#166534"
            elif label == "加速":
                background, color = "#ffedd5", "#9a3412"
            else:
                background, color = "#dbeafe", "#1d4ed8"
            tag_html.append(
                f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:3px 8px;border-radius:999px;background:{background};color:{color};font-size:12px;font-weight:800">{html_text(label)}</span>'
            )
        lifecycle_status = optional_text(r.get("priority_lifecycle_status"))
        lifecycle_html = ""
        if lifecycle_status:
            lifecycle_background, lifecycle_color = lifecycle_colors.get(lifecycle_status, ("#f1f5f9", "#475569"))
            lifecycle_html = f'<span style="display:inline-block;margin:2px 0 2px 4px;padding:3px 8px;border-radius:999px;background:{lifecycle_background};color:{lifecycle_color};font-size:12px;font-weight:900">{html_text(lifecycle_status)}</span>'
        rank_change = fmt_rank_change(r.get("rank_change"))
        movement = f" ・ {html_text(rank_change)}" if rank_change else ""
        lifecycle_detail = priority_lifecycle_detail(r)
        lifecycle_detail_html = f'<div style="font-size:11px;line-height:1.7;color:#7c3aed;font-weight:800;margin-top:3px">継続力 {html_text(lifecycle_detail)}</div>' if lifecycle_detail else ""
        items.append(
            f"""<div style="border-top:1px solid #e5e7eb;padding:11px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(r["rank"])} {html_text(r["code"])} {html_text(r["name"])} <span style="color:{score_color(r["score"])}">{int(r["score"])}点</span></div>
<div style="margin:5px 0">{"".join(tag_html)}{lifecycle_html}</div>
<div style="font-size:12px;line-height:1.7;color:#475569">20日 {fmt_pct(r.get("return_20d"))} ・ 出来高 {fmt_num(r.get("volume_ratio"))}倍 ・ 売買代金 {fmt_trading_value(r.get("trading_value"))}{movement}</div>
{lifecycle_detail_html}
</div>"""
        )
    lifecycle_summary = priority_lifecycle_summary(priority)
    lifecycle_summary_html = f'<div style="font-size:12px;font-weight:800;color:#7c3aed;margin-top:6px">継続力: {html_text(lifecycle_summary)}</div>' if lifecycle_summary else ""
    return f"""<div style="background:#fff;border:2px solid #0f172a;border-radius:18px;padding:16px;margin-top:18px">
<div style="font-size:18px;font-weight:900;color:#0f172a">今日の重点候補</div>
<div style="font-size:12px;line-height:1.7;color:#64748b;margin-top:4px">複数のモメンタム条件が重なった銘柄です。過熱注意は売買指示ではなく、値動き確認の注意タグです。</div>
{lifecycle_summary_html}
{"".join(items)}
</div>"""
'''
replace_once(old_html, new_html)

replace_once(
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, priority_changes: pd.DataFrame, priority_lifecycle: pd.DataFrame, temperature: pd.DataFrame, errors: list[dict[str, Any]], universe: pd.DataFrame) -> None:',
)
replace_once(
    '        priority_changes.to_excel(w, sheet_name="Priority Changes", index=False)\n        temperature.to_excel(w, sheet_name="Market Temperature", index=False)',
    '        priority_changes.to_excel(w, sheet_name="Priority Changes", index=False)\n        priority_lifecycle.to_excel(w, sheet_name="Priority Lifecycle", index=False)\n        temperature.to_excel(w, sheet_name="Market Temperature", index=False)',
)

replace_once(
    '    priority_changes = compare_priority_candidates(top100, history, today, top_limit)\n',
    '    priority_changes = compare_priority_candidates(top100, history, today, top_limit)\n    priority_changes = attach_priority_candidate_lifecycle(priority_changes, history, top100, today, top_limit)\n',
)
replace_once(
    '        "レポート形式": "dashboard_priority_changes_v7",',
    '        "レポート形式": "dashboard_priority_lifecycle_v8",',
)
replace_once(
    '        "重点候補比較日": priority_changes.get("previous_date", ""),\n',
    '        "重点候補比較日": priority_changes.get("previous_date", ""),\n        "重点候補初登場": priority_lifecycle_count(priority_changes, "初登場"),\n        "重点候補再浮上": priority_lifecycle_count(priority_changes, "再浮上"),\n        "重点候補定着": priority_lifecycle_count(priority_changes, "定着"),\n        "重点候補長期定着": priority_lifecycle_count(priority_changes, "長期定着"),\n        "重点候補連続5日以上": int((priority_changes.get("lifecycle", pd.DataFrame()).get("priority_streak_days", pd.Series(dtype=float)).fillna(0) >= 5).sum()),\n',
)
replace_once(
    '    excel_report(cfg["data"]["output_path"], summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], temperature, errors, universe_df)',
    '    excel_report(cfg["data"]["output_path"], summary, top100, new_entries, rising_fast, top30_streak, ytd_high_ranking, priority_changes["table"], priority_changes["lifecycle"], temperature, errors, universe_df)',
)

path.write_text(text, encoding="utf-8")
print("Applied priority candidate lifecycle update")
