from pathlib import Path


path = Path("historical_backfill.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if old not in text:
        raise RuntimeError(f"anchor not found: {label}")
    text = text.replace(old, new, 1)


replace_once(
    "import main\nimport replay\n",
    "import main\nimport relative_strength_lifecycle as rs_lifecycle\nimport replay\n",
    "lifecycle import",
)
replace_once(
    'BACKFILL_VERSION = "2026-07-11-historical-backfill-v1"',
    'BACKFILL_VERSION = "2026-07-11-historical-relative-strength-v2"',
    "backfill version",
)
replace_once(
    "        ranked = main.enrich_ranking_features(base, historical, day, top_limit)\n        columns = [column for column in main.ranking_history_columns() if column in ranked.columns]\n",
    "        ranked = main.enrich_ranking_features(base, historical, day, top_limit)\n"
    "        ranked = main.attach_relative_strength(ranked)\n"
    "        ranked = rs_lifecycle.attach(ranked, historical, day)\n"
    "        columns = [column for column in main.ranking_history_columns() if column in ranked.columns]\n",
    "historical ranking enrichment",
)
replace_once(
    '        "ranking_row_count": len(history),\n        "jpx_cache_sha256": cache_hash,\n',
    '        "ranking_row_count": len(history),\n'
    '        "relative_strength_enabled": True,\n'
    '        "relative_strength_non_null_ratio": (\n'
    '            float(pd.to_numeric(history.get("relative_strength_score"), errors="coerce").notna().mean())\n'
    '            if not history.empty else 0.0\n'
    '        ),\n'
    '        "relative_strength_grade_count": (\n'
    '            int(history.get("relative_strength_grade", pd.Series(dtype=str)).replace("", pd.NA).dropna().nunique())\n'
    '            if not history.empty else 0\n'
    '        ),\n'
    '        "relative_strength_lifecycle_enabled": True,\n'
    '        "relative_strength_lifecycle_non_null_ratio": (\n'
    '            float(history.get("relative_strength_lifecycle", pd.Series(dtype=str)).replace("", pd.NA).notna().mean())\n'
    '            if not history.empty else 0.0\n'
    '        ),\n'
    '        "jpx_cache_sha256": cache_hash,\n',
    "manifest relative strength metadata",
)
replace_once(
    '        if history.duplicated(["date", "code"]).any():\n            raise RuntimeError("duplicate date/code rows in historical ranking")\n',
    '        if history.duplicated(["date", "code"]).any():\n'
    '            raise RuntimeError("duplicate date/code rows in historical ranking")\n'
    '        relative_score = pd.to_numeric(history.get("relative_strength_score"), errors="coerce")\n'
    '        if relative_score.isna().any():\n'
    '            raise RuntimeError("historical ranking contains missing relative strength scores")\n'
    '        lifecycle = history.get("relative_strength_lifecycle", pd.Series(dtype=str)).fillna("").astype(str).str.strip()\n'
    '        if lifecycle.eq("").any():\n'
    '            raise RuntimeError("historical ranking contains missing relative strength lifecycle states")\n',
    "strict historical relative strength validation",
)

path.write_text(text, encoding="utf-8")
print("historical relative strength integration applied")
