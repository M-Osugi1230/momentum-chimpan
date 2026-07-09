from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match, found {count}: {old[:140]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-10-dashboard-market-regime-v5"',
    'APP_VERSION = "2026-07-10-dashboard-regime-history-v6"',
)

history_helpers = r'''

def optional_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text_value = str(value).strip()
    return "" if text_value.lower() in {"", "nan", "none"} else text_value


def latest_previous_regime(history: pd.DataFrame, today: str) -> dict[str, Any]:
    if history is None or history.empty or "date" not in history.columns or "market_regime" not in history.columns:
        return {}
    work = history.copy()
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date_sort"])
    work = work[work["date"].astype(str) != str(today)]
    work["regime_text"] = work["market_regime"].map(optional_text)
    work = work[work["regime_text"] != ""].sort_values("date_sort")
    if work.empty:
        return {}
    row = work.iloc[-1]
    score_value = pd.to_numeric(pd.Series([row.get("market_regime_score")]), errors="coerce").iloc[0]
    return {
        "date": str(row.get("date", "")),
        "label": optional_text(row.get("market_regime")),
        "score": None if pd.isna(score_value) else int(float(score_value)),
    }


def market_regime_transition_type(previous_label: str, current_label: str) -> str:
    if not previous_label:
        return "履歴開始"
    if previous_label == current_label:
        return "維持"
    if current_label == "過熱警戒":
        return "警戒強化"
    if previous_label == "過熱警戒" and current_label != "過熱警戒":
        return "過熱緩和"
    order = {"弱気": 0, "中立": 1, "やや強気": 2, "強気": 3}
    previous_rank = order.get(previous_label)
    current_rank = order.get(current_label)
    if previous_rank is None or current_rank is None:
        return "転換"
    if current_rank > previous_rank:
        return "改善"
    if current_rank < previous_rank:
        return "悪化"
    return "転換"


def market_regime_streak(history: pd.DataFrame, today: str, current_label: str) -> int:
    if history is None or history.empty or "date" not in history.columns or "market_regime" not in history.columns:
        return 1
    work = history.copy()
    work["date_sort"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date_sort"])
    work = work[work["date"].astype(str) != str(today)].sort_values("date_sort", ascending=False)
    streak = 1
    for _, row in work.iterrows():
        label = optional_text(row.get("market_regime"))
        if not label:
            continue
        if label != current_label:
            break
        streak += 1
    return streak


def attach_market_regime_history(today: str, temperature: pd.DataFrame, regime: dict[str, Any], history: pd.DataFrame) -> pd.DataFrame:
    current = temperature.copy()
    previous = latest_previous_regime(history, today)
    previous_label = previous.get("label", "")
    previous_score = previous.get("score")
    current_label = regime["label"]
    current_score = int(regime["score"])
    changed = bool(previous_label and previous_label != current_label)
    transition_type = market_regime_transition_type(previous_label, current_label)
    transition = f"{previous_label} → {current_label}" if previous_label else f"履歴開始 → {current_label}"
    score_delta = None if previous_score is None else current_score - int(previous_score)
    streak = market_regime_streak(history, today, current_label)

    current["market_regime"] = current_label
    current["market_regime_base"] = regime.get("base_label", current_label)
    current["market_regime_score"] = current_score
    current["market_regime_ma20_ratio"] = regime.get("ma20_ratio", 0.0)
    current["market_regime_ma60_ratio"] = regime.get("ma60_ratio", 0.0)
    current["market_regime_overheat_count"] = regime.get("overheat_count", 0)
    current["market_regime_overheat_ratio"] = regime.get("overheat_ratio", 0.0)
    current["previous_market_regime"] = previous_label
    current["previous_market_regime_score"] = previous_score
    current["previous_market_regime_date"] = previous.get("date", "")
    current["regime_changed"] = changed
    current["regime_transition"] = transition
    current["regime_transition_type"] = transition_type
    current["regime_score_delta"] = score_delta
    current["regime_streak"] = streak
    return current


def enrich_regime_from_temperature(regime: dict[str, Any], temperature: pd.DataFrame) -> dict[str, Any]:
    enriched = dict(regime)
    if temperature is None or temperature.empty:
        return enriched
    row = temperature.iloc[0]
    previous_score_value = pd.to_numeric(pd.Series([row.get("previous_market_regime_score")]), errors="coerce").iloc[0]
    score_delta_value = pd.to_numeric(pd.Series([row.get("regime_score_delta")]), errors="coerce").iloc[0]
    streak_value = pd.to_numeric(pd.Series([row.get("regime_streak")]), errors="coerce").iloc[0]
    enriched.update({
        "previous_label": optional_text(row.get("previous_market_regime")),
        "previous_score": None if pd.isna(previous_score_value) else int(float(previous_score_value)),
        "previous_date": optional_text(row.get("previous_market_regime_date")),
        "changed": str(row.get("regime_changed", "")).lower() in {"true", "1"} if not isinstance(row.get("regime_changed"), bool) else bool(row.get("regime_changed")),
        "transition": optional_text(row.get("regime_transition")),
        "transition_type": optional_text(row.get("regime_transition_type")),
        "score_delta": None if pd.isna(score_delta_value) else int(float(score_delta_value)),
        "streak": 1 if pd.isna(streak_value) else int(float(streak_value)),
    })
    return enriched


def regime_history_text(regime: dict[str, Any]) -> str:
    previous_label = regime.get("previous_label", "")
    score_delta = regime.get("score_delta")
    delta_text = "" if score_delta is None else f" / スコア前回比 {score_delta:+d}点"
    if not previous_label:
        return f"履歴: 本日から判定履歴を開始（{regime['label']}）"
    if regime.get("changed"):
        return f"転換: {regime.get('transition', '')}（{regime.get('transition_type', '転換')}）{delta_text}"
    return f"継続: {regime['label']}を{regime.get('streak', 1)}営業日維持{delta_text}"
'''

replace_once(
    "\n\ndef plain_market_regime(regime: dict[str, Any]) -> list[str]:",
    history_helpers + "\n\ndef plain_market_regime(regime: dict[str, Any]) -> list[str]:",
)

old_plain = '''def plain_market_regime(regime: dict[str, Any]) -> list[str]:
    return [
        "【Market Regime】",
        f"判定: {regime['label']} / 市場環境スコア {regime['score']}点",
        f"20日線上 {regime['ma20_ratio']:.1%} / 60日線上 {regime['ma60_ratio']:.1%} / 過熱銘柄 {regime['overheat_count']}件 ({regime['overheat_ratio']:.1%})",
        f"方針: {regime['guidance']}",
        "",
    ]
'''
new_plain = '''def plain_market_regime(regime: dict[str, Any]) -> list[str]:
    return [
        "【Market Regime】",
        f"判定: {regime['label']} / 市場環境スコア {regime['score']}点",
        regime_history_text(regime),
        f"20日線上 {regime['ma20_ratio']:.1%} / 60日線上 {regime['ma60_ratio']:.1%} / 過熱銘柄 {regime['overheat_count']}件 ({regime['overheat_ratio']:.1%})",
        f"方針: {regime['guidance']}",
        "",
    ]
'''
replace_once(old_plain, new_plain)

old_html = '''def html_market_regime(regime: dict[str, Any]) -> str:
    return f"""<div style="background:{regime['background']};border:2px solid {regime['color']};border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:12px;font-weight:800;color:{regime['color']}">MARKET REGIME</div>
<div style="font-size:24px;font-weight:900;color:{regime['color']};margin-top:2px">{html_text(regime['label'])} <span style="font-size:16px">{regime['score']}点</span></div>
<div style="font-size:12px;line-height:1.8;color:#334155;margin-top:8px">20日線上 <b>{regime['ma20_ratio']:.1%}</b> ・ 60日線上 <b>{regime['ma60_ratio']:.1%}</b> ・ 過熱銘柄 <b>{regime['overheat_count']}件 ({regime['overheat_ratio']:.1%})</b></div>
<div style="font-size:13px;line-height:1.8;color:#334155;margin-top:6px"><b>本日の方針:</b> {html_text(regime['guidance'])}</div>
</div>"""
'''
new_html = '''def html_market_regime(regime: dict[str, Any]) -> str:
    transition_color = "#b91c1c" if regime.get("transition_type") in {"悪化", "警戒強化"} else "#15803d" if regime.get("transition_type") in {"改善", "過熱緩和"} else "#475569"
    return f"""<div style="background:{regime['background']};border:2px solid {regime['color']};border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:12px;font-weight:800;color:{regime['color']}">MARKET REGIME</div>
<div style="font-size:24px;font-weight:900;color:{regime['color']};margin-top:2px">{html_text(regime['label'])} <span style="font-size:16px">{regime['score']}点</span></div>
<div style="font-size:12px;font-weight:800;color:{transition_color};margin-top:6px">{html_text(regime_history_text(regime))}</div>
<div style="font-size:12px;line-height:1.8;color:#334155;margin-top:8px">20日線上 <b>{regime['ma20_ratio']:.1%}</b> ・ 60日線上 <b>{regime['ma60_ratio']:.1%}</b> ・ 過熱銘柄 <b>{regime['overheat_count']}件 ({regime['overheat_ratio']:.1%})</b></div>
<div style="font-size:13px;line-height:1.8;color:#334155;margin-top:6px"><b>本日の方針:</b> {html_text(regime['guidance'])}</div>
</div>"""
'''
replace_once(old_html, new_html)

replace_once(
    '    regime = calculate_market_regime(top100, temperature)\n    lines = [',
    '    regime = enrich_regime_from_temperature(calculate_market_regime(top100, temperature), temperature)\n    lines = [',
)
replace_once(
    '    regime = calculate_market_regime(top100, temperature)\n    cards = [',
    '    regime = enrich_regime_from_temperature(calculate_market_regime(top100, temperature), temperature)\n    cards = [',
)

old_main = '''    temperature = market_temperature(today, all_ranked, top100, old_temp)
    regime = calculate_market_regime(top100, temperature)
    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)
'''
new_main = '''    temperature = market_temperature(today, all_ranked, top100, old_temp)
    regime = calculate_market_regime(top100, temperature)
    temperature = attach_market_regime_history(today, temperature, regime, old_temp)
    regime = enrich_regime_from_temperature(regime, temperature)
    pd.concat([old_temp, temperature], ignore_index=True).drop_duplicates(["date"], keep="last").to_csv(temp_path, index=False)
'''
replace_once(old_main, new_main)

replace_once(
    '        "レポート形式": "dashboard_market_regime_v5",',
    '        "レポート形式": "dashboard_regime_history_v6",',
)
replace_once(
    '        "Market Regime Score": regime["score"],\n',
    '        "Market Regime Score": regime["score"],\n        "前回Market Regime": regime.get("previous_label", ""),\n        "Market Regime転換": regime.get("transition", ""),\n        "Market Regime転換種別": regime.get("transition_type", ""),\n        "Market Regime転換有無": regime.get("changed", False),\n        "Market Regime継続日数": regime.get("streak", 1),\n        "Market Regime Score前回比": regime.get("score_delta"),\n',
)

path.write_text(text, encoding="utf-8")
print("Applied Market Regime history update")
