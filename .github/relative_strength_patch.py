from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if old not in text:
        raise RuntimeError(f"anchor not found for {label}")
    text = text.replace(old, new, 1)


replace_once(
    'APP_VERSION = "2026-07-11-dashboard-market-freshness-v17"',
    'APP_VERSION = "2026-07-11-dashboard-relative-strength-v18"',
    "app version",
)

relative_block = r'''

RELATIVE_STRENGTH_COLUMNS = [
    "market_median_return_20d", "market_median_return_60d",
    "sector_median_return_20d", "sector_median_return_60d",
    "market_relative_20d", "market_relative_60d",
    "sector_relative_20d", "sector_relative_60d",
    "relative_strength_score", "relative_strength_rank", "relative_strength_grade",
    "dual_outperformer", "relative_strength_reason",
]


def relative_percentile(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() == 0:
        return pd.Series(0.5, index=values.index, dtype=float)
    return numeric.rank(method="average", pct=True).fillna(0.5)


def attach_relative_strength(frame: pd.DataFrame) -> pd.DataFrame:
    """Measure stock strength versus the scanned market and its JPX 33-sector peers."""
    if frame is None or frame.empty:
        result = frame.copy() if frame is not None else pd.DataFrame()
        for column in RELATIVE_STRENGTH_COLUMNS:
            if column not in result.columns:
                result[column] = pd.Series(dtype="object")
        return result

    result = frame.copy()
    result["sector33"] = result.get("sector33", pd.Series(index=result.index, dtype=str)).map(normalize_sector33)
    for horizon in (20, 60):
        source = f"return_{horizon}d"
        result[source] = pd.to_numeric(result.get(source, pd.Series(index=result.index, dtype=float)), errors="coerce")
        market_median = result[source].median()
        result[f"market_median_return_{horizon}d"] = market_median
        sector_median = result.groupby("sector33", dropna=False)[source].transform("median")
        blank_sector = result["sector33"].eq("")
        sector_median = sector_median.where(~blank_sector, market_median).fillna(market_median)
        result[f"sector_median_return_{horizon}d"] = sector_median
        result[f"market_relative_{horizon}d"] = result[source] - market_median
        result[f"sector_relative_{horizon}d"] = result[source] - sector_median

    components = {
        "market_relative_20d": 0.30,
        "market_relative_60d": 0.25,
        "sector_relative_20d": 0.25,
        "sector_relative_60d": 0.20,
    }
    score = pd.Series(0.0, index=result.index, dtype=float)
    for column, weight in components.items():
        score += relative_percentile(result[column]) * weight * 100.0
    result["relative_strength_score"] = score.round(1).clip(lower=0.0, upper=100.0)
    result["relative_strength_rank"] = result["relative_strength_score"].rank(method="min", ascending=False).astype("Int64")
    result["relative_strength_grade"] = result["relative_strength_score"].map(
        lambda value: "S" if value >= 85 else "A" if value >= 70 else "B" if value >= 55 else "C"
    )
    result["dual_outperformer"] = (
        result["market_relative_20d"].gt(0)
        & result["market_relative_60d"].gt(0)
        & result["sector_relative_20d"].gt(0)
        & result["sector_relative_60d"].gt(0)
    )

    def reason(row: pd.Series) -> str:
        parts: list[str] = []
        if row_number(row, "market_relative_20d") > 0:
            parts.append("20日で市場超過")
        if row_number(row, "sector_relative_20d") > 0:
            parts.append("20日で同業超過")
        if row_number(row, "market_relative_60d") > 0:
            parts.append("60日で市場超過")
        if row_number(row, "sector_relative_60d") > 0:
            parts.append("60日で同業超過")
        return " / ".join(parts) if parts else "市場・同業比で劣後"

    result["relative_strength_reason"] = result.apply(reason, axis=1)
    return result


def build_relative_strength_table(top100: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "relative_strength_rank", "rank", "code", "name", "sector33", "score",
        "relative_strength_score", "relative_strength_grade", "dual_outperformer",
        "return_20d", "market_median_return_20d", "market_relative_20d",
        "sector_median_return_20d", "sector_relative_20d",
        "return_60d", "market_median_return_60d", "market_relative_60d",
        "sector_median_return_60d", "sector_relative_60d", "relative_strength_reason",
        "trading_value", "volume_ratio",
    ]
    if top100 is None or top100.empty:
        return pd.DataFrame(columns=columns)
    result = top100.copy().sort_values(
        ["relative_strength_score", "score", "rank"], ascending=[False, False, True]
    )
    return result[[column for column in columns if column in result.columns]].reset_index(drop=True)


def plain_relative_strength_section(relative_strength: pd.DataFrame, limit: int = 10) -> list[str]:
    if relative_strength is None or relative_strength.empty:
        return ["【市場・業種相対強度】", "相対強度を算出できませんでした。", ""]
    dual_count = int(relative_strength.get("dual_outperformer", pd.Series(dtype=bool)).fillna(False).sum())
    lines = [
        "【市場・業種相対強度】",
        "全スキャン銘柄の中央値を市場基準、JPX33業種内中央値を同業基準として比較します。",
        f"市場・同業の20日/60日をすべて上回る銘柄: {dual_count}件",
    ]
    for _, row in relative_strength.head(limit).iterrows():
        lines.append(
            f"#{int(row_number(row, 'relative_strength_rank'))} {row['code']} {row['name']}｜"
            f"相対{row_number(row, 'relative_strength_score'):.1f}点 {optional_text(row.get('relative_strength_grade'))}｜"
            f"市場20日 {fmt_pct(row.get('market_relative_20d'))}｜同業20日 {fmt_pct(row.get('sector_relative_20d'))}｜"
            f"市場60日 {fmt_pct(row.get('market_relative_60d'))}｜同業60日 {fmt_pct(row.get('sector_relative_60d'))}"
        )
    lines.append("")
    return lines


def html_relative_strength_section(relative_strength: pd.DataFrame, limit: int = 10) -> str:
    if relative_strength is None or relative_strength.empty:
        return '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-top:14px"><b>市場・業種相対強度</b><div style="font-size:12px;color:#64748b;margin-top:5px">相対強度を算出できませんでした。</div></div>'
    dual_count = int(relative_strength.get("dual_outperformer", pd.Series(dtype=bool)).fillna(False).sum())
    items = []
    for _, row in relative_strength.head(limit).iterrows():
        grade = optional_text(row.get("relative_strength_grade"))
        color = "#15803d" if grade in {"S", "A"} else "#1d4ed8" if grade == "B" else "#64748b"
        items.append(f'''<div style="border-top:1px solid #e5e7eb;padding:9px 0">
<div style="font-size:14px;font-weight:900;color:#0f172a">#{int(row_number(row, "relative_strength_rank"))} {html_text(row["code"])} {html_text(row["name"])} <span style="float:right;color:{color}">{row_number(row, "relative_strength_score"):.1f}点 {html_text(grade)}</span></div>
<div style="clear:both;font-size:11px;color:#475569">市場20日 <b>{fmt_pct(row.get("market_relative_20d"))}</b> ・ 同業20日 <b>{fmt_pct(row.get("sector_relative_20d"))}</b> ・ 市場60日 {fmt_pct(row.get("market_relative_60d"))} ・ 同業60日 {fmt_pct(row.get("sector_relative_60d"))}</div>
</div>''')
    return f'''<div style="background:#fff;border:2px solid #0369a1;border-radius:18px;padding:16px;margin-top:14px">
<div style="font-size:18px;font-weight:900;color:#075985">市場・業種相対強度</div>
<div style="font-size:12px;color:#64748b;margin-top:4px">市場中央値とJPX33業種内中央値の双方を比較します。20日/60日の全条件超過は {dual_count}件です。</div>{"".join(items)}</div>'''
'''
replace_once("\n\ndef score(m: dict[str, Any]", relative_block + "\n\ndef score(m: dict[str, Any]", "relative strength functions")

replace_once(
    '"ytd_high_streak", "ytd_high_count", "return_5d", "return_20d", "return_60d", "volume_ratio",',
    '"ytd_high_streak", "ytd_high_count", "return_5d", "return_20d", "return_60d",\n        "market_median_return_20d", "market_median_return_60d", "sector_median_return_20d", "sector_median_return_60d",\n        "market_relative_20d", "market_relative_60d", "sector_relative_20d", "sector_relative_60d",\n        "relative_strength_score", "relative_strength_rank", "relative_strength_grade", "dual_outperformer", "relative_strength_reason",\n        "volume_ratio",',
    "ranking history relative columns",
)

replace_once(
    '"expectancy_confidence", "return_20d", "return_60d", "volume_ratio", "trading_value",',
    '"expectancy_confidence", "return_20d", "return_60d",\n    "market_relative_20d", "market_relative_60d", "sector_relative_20d", "sector_relative_60d",\n    "relative_strength_score", "relative_strength_rank", "relative_strength_grade", "dual_outperformer",\n    "volume_ratio", "trading_value",',
    "sector leader relative columns",
)

replace_once(
    '    confidence = optional_text(row.get("expectancy_confidence")) or "蓄積中"\n\n    reasons: list[str] = []',
    '    confidence = optional_text(row.get("expectancy_confidence")) or "蓄積中"\n    relative_strength_score = row_number(row, "relative_strength_score", 50.0)\n    relative_strength_grade = optional_text(row.get("relative_strength_grade")) or "C"\n\n    reasons: list[str] = []',
    "leader relative variables",
)
replace_once(
    '    score = momentum_score * 0.38 + sector_score * 0.27\n\n    if momentum_rank <= 10:',
    '    score = momentum_score * 0.38 + sector_score * 0.27\n\n    if relative_strength_score >= 80:\n        score += 8\n        reasons.append(f"相対強度{relative_strength_score:.1f}点・{relative_strength_grade}")\n    elif relative_strength_score >= 65:\n        score += 5\n        reasons.append(f"相対強度{relative_strength_score:.1f}点")\n    elif relative_strength_score >= 50:\n        score += 2\n    elif relative_strength_score < 35:\n        score -= 5\n        cautions.append("市場・同業比で相対劣後")\n\n    if momentum_rank <= 10:',
    "leader relative scoring",
)

replace_once(
    '    "action_priority", "action_score", "expectancy_score", "expectancy_confidence",\n]',
    '    "action_priority", "action_score", "expectancy_score", "expectancy_confidence",\n    "relative_strength_score", "relative_strength_grade", "market_relative_20d", "sector_relative_20d",\n]',
    "sector signal relative columns",
)
replace_once(
    '            "expectancy_confidence": optional_text(row.get("expectancy_confidence")) or "蓄積中",\n        })',
    '            "expectancy_confidence": optional_text(row.get("expectancy_confidence")) or "蓄積中",\n            "relative_strength_score": row_number(row, "relative_strength_score", 50),\n            "relative_strength_grade": optional_text(row.get("relative_strength_grade")) or "C",\n            "market_relative_20d": optional_number(row.get("market_relative_20d")),\n            "sector_relative_20d": optional_number(row.get("sector_relative_20d")),\n        })',
    "sector signal snapshot relative fields",
)

replace_once(
    '    "sector_leader_score", "horizon_days", "entry_close", "exit_close",\n    "forward_return", "win", "calendar_days",\n]',
    '    "sector_leader_score", "horizon_days", "entry_close", "exit_close",\n    "forward_return", "win", "market_benchmark_return", "sector_benchmark_return",\n    "market_excess_return", "sector_excess_return", "market_outperformance", "sector_outperformance",\n    "market_peer_count", "sector_peer_count", "calendar_days",\n]',
    "sector outcome alpha columns",
)

benchmark_block = r'''


def peer_forward_benchmarks(
    prices: pd.DataFrame,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    sector33: str,
    cache: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    entry_key = entry_date.date().isoformat()
    exit_key = exit_date.date().isoformat()
    sector_key = normalize_sector33(sector33)
    cache_key = (entry_key, exit_key, sector_key)
    if cache_key in cache:
        return cache[cache_key]

    entry_rows = prices[prices["date_sort"] == entry_date.normalize()][["code", "close", "sector33"]].rename(columns={"close": "entry_peer_close"})
    exit_rows = prices[prices["date_sort"] == exit_date.normalize()][["code", "close"]].rename(columns={"close": "exit_peer_close"})
    paired = entry_rows.merge(exit_rows, on="code", how="inner")
    paired = paired[(paired["entry_peer_close"] > 0) & paired["exit_peer_close"].notna()].copy()
    paired["peer_forward_return"] = paired["exit_peer_close"] / paired["entry_peer_close"] - 1

    market_return = float(paired["peer_forward_return"].median()) if len(paired) >= 10 else None
    sector_rows = paired[paired["sector33"].map(normalize_sector33) == sector_key] if sector_key else pd.DataFrame()
    sector_return = float(sector_rows["peer_forward_return"].median()) if len(sector_rows) >= 2 else None
    result = {
        "market_benchmark_return": market_return,
        "sector_benchmark_return": sector_return,
        "market_peer_count": int(len(paired)),
        "sector_peer_count": int(len(sector_rows)),
    }
    cache[cache_key] = result
    return result
'''
replace_once("\n\ndef calculate_sector_leader_outcomes(", benchmark_block + "\n\ndef calculate_sector_leader_outcomes(", "peer benchmark helper")
replace_once(
    '    prices = price_history[["date", "code", "close"]].copy()\n    prices["code"] = prices["code"].map(normalize_code)',
    '    price_columns = ["date", "code", "close"] + (["sector33"] if "sector33" in price_history.columns else [])\n    prices = price_history[price_columns].copy()\n    if "sector33" not in prices.columns:\n        prices["sector33"] = ""\n    prices["sector33"] = prices["sector33"].map(normalize_sector33)\n    prices["code"] = prices["code"].map(normalize_code)',
    "outcome price columns",
)
replace_once(
    '    price_groups = {code: group.sort_values("date_sort") for code, group in prices.groupby("code")}\n    outcomes: list[dict[str, Any]] = []',
    '    price_groups = {code: group.sort_values("date_sort") for code, group in prices.groupby("code")}\n    benchmark_cache: dict[tuple[str, str, str], dict[str, Any]] = {}\n    outcomes: list[dict[str, Any]] = []',
    "benchmark cache",
)
replace_once(
    '            forward_return = exit_close / float(entry_close) - 1\n            outcomes.append({',
    '            forward_return = exit_close / float(entry_close) - 1\n            benchmark = peer_forward_benchmarks(\n                prices, entry_date.normalize(), exit_row["date_sort"].normalize(),\n                optional_text(signal_row.get("sector33")), benchmark_cache,\n            )\n            market_benchmark_return = benchmark["market_benchmark_return"]\n            sector_benchmark_return = benchmark["sector_benchmark_return"]\n            market_excess_return = None if market_benchmark_return is None else forward_return - market_benchmark_return\n            sector_excess_return = None if sector_benchmark_return is None else forward_return - sector_benchmark_return\n            outcomes.append({',
    "outcome benchmark calculation",
)
replace_once(
    '                "forward_return": forward_return,\n                "win": bool(forward_return > 0),\n                "calendar_days": int((exit_row["date_sort"] - entry_date).days),',
    '                "forward_return": forward_return,\n                "win": bool(forward_return > 0),\n                "market_benchmark_return": market_benchmark_return,\n                "sector_benchmark_return": sector_benchmark_return,\n                "market_excess_return": market_excess_return,\n                "sector_excess_return": sector_excess_return,\n                "market_outperformance": None if market_excess_return is None else bool(market_excess_return > 0),\n                "sector_outperformance": None if sector_excess_return is None else bool(sector_excess_return > 0),\n                "market_peer_count": benchmark["market_peer_count"],\n                "sector_peer_count": benchmark["sector_peer_count"],\n                "calendar_days": int((exit_row["date_sort"] - entry_date).days),',
    "outcome alpha fields",
)

replace_once(
    '    returns = pd.to_numeric(subset.get("forward_return", pd.Series(dtype=float)), errors="coerce").dropna()\n    wins = subset.get("win", pd.Series(dtype=bool)).fillna(False).astype(bool)\n    return {',
    '    returns = pd.to_numeric(subset.get("forward_return", pd.Series(dtype=float)), errors="coerce").dropna()\n    wins = subset.get("win", pd.Series(dtype=bool)).fillna(False).astype(bool)\n    market_excess = pd.to_numeric(subset.get("market_excess_return", pd.Series(dtype=float)), errors="coerce").dropna()\n    sector_excess = pd.to_numeric(subset.get("sector_excess_return", pd.Series(dtype=float)), errors="coerce").dropna()\n    market_flags = subset.get("market_outperformance", pd.Series(index=subset.index, dtype=object)).dropna().astype(bool)\n    sector_flags = subset.get("sector_outperformance", pd.Series(index=subset.index, dtype=object)).dropna().astype(bool)\n    return {',
    "performance alpha variables",
)
replace_once(
    '        "worst_return": float(returns.min()) if not returns.empty else None,\n        "average_leader_score":',
    '        "worst_return": float(returns.min()) if not returns.empty else None,\n        "average_market_excess_return": float(market_excess.mean()) if not market_excess.empty else None,\n        "market_outperformance_rate": float(market_flags.mean()) if len(market_flags) else None,\n        "average_sector_excess_return": float(sector_excess.mean()) if not sector_excess.empty else None,\n        "sector_outperformance_rate": float(sector_flags.mean()) if len(sector_flags) else None,\n        "average_leader_score":',
    "performance alpha metrics",
)
replace_once(
    '        "average_return", "median_return", "best_return", "worst_return", "average_leader_score",\n    ]',
    '        "average_return", "median_return", "best_return", "worst_return",\n        "average_market_excess_return", "market_outperformance_rate",\n        "average_sector_excess_return", "sector_outperformance_rate", "average_leader_score",\n    ]',
    "performance summary alpha columns",
)

replace_once(
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, sector_momentum:',
    'def excel_report(path: str, summary: dict[str, Any], top100: pd.DataFrame, relative_strength: pd.DataFrame, sector_momentum:',
    "excel signature",
)
replace_once(
    '        top100.to_excel(w, sheet_name="Momentum Top100", index=False)\n        sector_momentum.to_excel',
    '        top100.to_excel(w, sheet_name="Momentum Top100", index=False)\n        relative_strength.to_excel(w, sheet_name="Relative Strength", index=False)\n        sector_momentum.to_excel',
    "excel relative sheet",
)

replace_once(
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries:',
    'def build_plain_email(summary: dict[str, Any], top100: pd.DataFrame, relative_strength: pd.DataFrame, new_entries:',
    "plain email signature",
)
replace_once(
    '    lines += plain_market_regime(regime)\n    lines += plain_sector_momentum_section',
    '    lines += plain_market_regime(regime)\n    lines += plain_relative_strength_section(relative_strength)\n    lines += plain_sector_momentum_section',
    "plain email relative section",
)
replace_once(
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries:',
    'def build_html_email(summary: dict[str, Any], top100: pd.DataFrame, relative_strength: pd.DataFrame, new_entries:',
    "html email signature",
)
replace_once(
    '        html_market_regime(regime),\n        html_sector_momentum_section',
    '        html_market_regime(regime),\n        html_relative_strength_section(relative_strength),\n        html_sector_momentum_section',
    "html relative section",
)
replace_once(
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, new_entries:',
    'def send_email(summary: dict[str, Any], top100: pd.DataFrame, relative_strength: pd.DataFrame, new_entries:',
    "send email signature",
)
replace_once(
    'build_plain_email(summary, top100, new_entries,',
    'build_plain_email(summary, top100, relative_strength, new_entries,',
    "plain email call",
)
replace_once(
    'build_html_email(summary, top100, new_entries,',
    'build_html_email(summary, top100, relative_strength, new_entries,',
    "html email call",
)

replace_once(
    '    all_ranked = enrich_ranking_features(base_all, history, today, top_limit) if not base_all.empty else pd.DataFrame(columns=ranking_history_columns())\n    if not all_ranked.empty:',
    '    all_ranked = enrich_ranking_features(base_all, history, today, top_limit) if not base_all.empty else pd.DataFrame(columns=ranking_history_columns())\n    all_ranked = attach_relative_strength(all_ranked)\n    if not all_ranked.empty:',
    "main attach relative strength",
)
replace_once(
    '    top100 = all_ranked[all_ranked["rank"] <= top_limit].copy() if not all_ranked.empty else pd.DataFrame(columns=ranking_history_columns())\n    new_entries =',
    '    top100 = all_ranked[all_ranked["rank"] <= top_limit].copy() if not all_ranked.empty else pd.DataFrame(columns=ranking_history_columns())\n    relative_strength = build_relative_strength_table(top100)\n    new_entries =',
    "main relative table",
)
replace_once(
    '        "Momentum Top100": len(top100),\n        "業種集計数":',
    '        "Momentum Top100": len(top100),\n        "相対強度S/A": int(relative_strength.get("relative_strength_grade", pd.Series(dtype=str)).isin(["S", "A"]).sum()) if not relative_strength.empty else 0,\n        "市場・同業双方超過": int(relative_strength.get("dual_outperformer", pd.Series(dtype=bool)).fillna(False).sum()) if not relative_strength.empty else 0,\n        "相対強度トップ": (str(relative_strength.iloc[0]["code"]) + " " + str(relative_strength.iloc[0]["name"])) if not relative_strength.empty else "",\n        "相対強度トップスコア": float(relative_strength.iloc[0]["relative_strength_score"]) if not relative_strength.empty else None,\n        "市場中央値20日騰落率": float(all_ranked["return_20d"].median()) if not all_ranked.empty else None,\n        "市場中央値60日騰落率": float(all_ranked["return_60d"].median()) if not all_ranked.empty else None,\n        "業種集計数":',
    "summary relative metrics",
)
replace_once(
    '"レポート形式": "dashboard_market_freshness_v17",',
    '"レポート形式": "dashboard_relative_strength_v18",',
    "report format",
)
replace_once(
    '}, top100, sector_momentum, sector_rotation,',
    '}, top100, relative_strength, sector_momentum, sector_rotation,',
    "excel relative argument",
)
replace_once(
    'send_email(summary, top100, new_entries, rising_fast,',
    'send_email(summary, top100, relative_strength, new_entries, rising_fast,',
    "main email relative argument",
)

path.write_text(text, encoding="utf-8")
print("relative strength patch applied")
