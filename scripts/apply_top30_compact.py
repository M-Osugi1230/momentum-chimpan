from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match, found {count}: {old[:120]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-09-dashboard-priority-candidates-v3"',
    'APP_VERSION = "2026-07-10-dashboard-top30-compact-v4"',
)

helper_code = r'''

def compact_rank_slice(top100: pd.DataFrame, start_rank: int, end_rank: int) -> pd.DataFrame:
    """Return a rank range for compact email display."""
    if top100.empty or "rank" not in top100.columns or start_rank > end_rank:
        return pd.DataFrame(columns=top100.columns)
    work = top100[(top100["rank"] >= start_rank) & (top100["rank"] <= end_rank)].copy()
    return work.sort_values("rank")


def compact_signal_text(r: pd.Series) -> str:
    signals: list[str] = []
    if row_flag(r, "is_new_entry"):
        signals.append("NEW")
    if row_flag(r, "is_rising_fast"):
        signals.append(f"急上昇 +{fmt_int(r.get('rank_change'))}")
    elif fmt_rank_change(r.get("rank_change")):
        signals.append(fmt_rank_change(r.get("rank_change")))
    if row_flag(r, "is_best_rank"):
        signals.append("最高順位")
    streak = int(row_number(r, "top30_streak", row_number(r, "top30_streak_days")))
    if streak >= 3:
        signals.append(f"TOP30 {streak}日")
    return " / ".join(signals)


def plain_compact_ranking_section(title: str, df: pd.DataFrame) -> list[str]:
    if df.empty:
        return []
    lines = [f"【{title}】"]
    for _, r in df.iterrows():
        signal = compact_signal_text(r)
        suffix = f"｜{signal}" if signal else ""
        lines.append(
            f"#{int(r['rank'])} {r['code']} {r['name']}｜{int(r['score'])}点｜"
            f"5日 {fmt_pct(r.get('return_5d'))}｜20日 {fmt_pct(r.get('return_20d'))}｜"
            f"出来高 {fmt_num(r.get('volume_ratio'))}倍｜売買代金 {fmt_trading_value(r.get('trading_value'))}{suffix}"
        )
    lines.append("")
    return lines


def html_compact_ranking_section(title: str, df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    rows = []
    for _, r in df.iterrows():
        signal = compact_signal_text(r)
        signal_html = (
            f'<div style="font-size:11px;color:#0369a1;margin-top:3px;font-weight:700">{html_text(signal)}</div>'
            if signal else ""
        )
        rows.append(
            f"""<div style="border-top:1px solid #e5e7eb;padding:9px 0">
<div style="font-size:13px;font-weight:800;color:#0f172a">#{int(r["rank"])} {html_text(r["code"])} {html_text(r["name"])} <span style="float:right;color:{score_color(r["score"])}">{int(r["score"])}点</span></div>
<div style="clear:both;font-size:11px;line-height:1.7;color:#475569">5日 {fmt_pct(r.get("return_5d"))} ・ 20日 {fmt_pct(r.get("return_20d"))} ・ 出来高 {fmt_num(r.get("volume_ratio"))}倍 ・ 売買代金 {fmt_trading_value(r.get("trading_value"))}</div>
{signal_html}
</div>"""
        )
    return f"""<h2 style="margin-top:22px">{html_text(title)}</h2>
<div style="background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:6px 14px">{"".join(rows)}</div>"""
'''

replace_once(
    "\n\ndef build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> str:",
    helper_code + "\n\ndef build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries: pd.DataFrame, rising_fast: pd.DataFrame, top30_streak: pd.DataFrame, ytd_high_ranking: pd.DataFrame, temperature: pd.DataFrame, cfg: dict[str, Any]) -> str:",
)

replace_once(
    '    priority = select_priority_candidates(top100, 10)\n    lines = [',
    '    priority = select_priority_candidates(top100, 10)\n    compact_ranked = compact_rank_slice(top100, top_n + 1, 30)\n    lines = [',
)
replace_once(
    '    lines += plain_ranking_section(f"Momentum Top{top_n}", top100, top_n, show_empty=True)\n',
    '    lines += plain_ranking_section(f"Momentum Top{top_n}（詳細）", top100, top_n, show_empty=True)\n    lines += plain_compact_ranking_section(f"Momentum {top_n + 1}-30（コンパクト）", compact_ranked)\n',
)

old_html_marker = '    priority = select_priority_candidates(top100, 10)\n    cards = ['
if text.count(old_html_marker) != 1:
    raise RuntimeError(f"Expected one HTML priority marker, found {text.count(old_html_marker)}")
text = text.replace(
    old_html_marker,
    '    priority = select_priority_candidates(top100, 10)\n    compact_ranked = compact_rank_slice(top100, top_n + 1, 30)\n    cards = [',
    1,
)
replace_once(
    '        html_section(f"Momentum Top{top_n}", top100, top_n, show_empty=True),\n',
    '        html_section(f"Momentum Top{top_n}（詳細）", top100, top_n, show_empty=True),\n        html_compact_ranking_section(f"Momentum {top_n + 1}-30（コンパクト）", compact_ranked),\n',
)
replace_once(
    '        "レポート形式": "dashboard_priority_candidates_v3",',
    '        "レポート形式": "dashboard_top30_compact_v4",',
)

path.write_text(text, encoding="utf-8")
print("Applied compact Top30 email update")
