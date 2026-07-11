from pathlib import Path


path = Path("main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if old not in text:
        raise RuntimeError(f"anchor not found: {label}")
    text = text.replace(old, new, 1)


replace_once(
    "import pandas as pd\nimport yfinance as yf",
    "import pandas as pd\nimport relative_strength_lifecycle as rs_lifecycle\nimport yfinance as yf",
    "lifecycle import",
)
replace_once(
    'APP_VERSION = "2026-07-11-dashboard-relative-strength-v18"',
    'APP_VERSION = "2026-07-11-dashboard-relative-strength-lifecycle-v19"',
    "app version",
)
replace_once(
    '        "relative_strength_score", "relative_strength_rank", "relative_strength_grade", "dual_outperformer", "relative_strength_reason",\n        "volume_ratio",',
    '        "relative_strength_score", "relative_strength_rank", "relative_strength_grade", "dual_outperformer", "relative_strength_reason",\n        *rs_lifecycle.LIFECYCLE_COLUMNS,\n        "volume_ratio",',
    "ranking history columns",
)

signature_anchor = "relative_strength: pd.DataFrame, sector_momentum: pd.DataFrame"
if text.count(signature_anchor) != 1:
    raise RuntimeError(f"unexpected excel signature count: {text.count(signature_anchor)}")
text = text.replace(
    signature_anchor,
    "relative_strength: pd.DataFrame, relative_strength_lifecycle: pd.DataFrame, sector_momentum: pd.DataFrame",
    1,
)
replace_once(
    '        relative_strength.to_excel(w, sheet_name="Relative Strength", index=False)\n        sector_momentum.to_excel',
    '        relative_strength.to_excel(w, sheet_name="Relative Strength", index=False)\n        relative_strength_lifecycle.to_excel(w, sheet_name="RS Lifecycle", index=False)\n        sector_momentum.to_excel',
    "excel lifecycle sheet",
)

email_signature_anchor = "relative_strength: pd.DataFrame, new_entries: pd.DataFrame"
if text.count(email_signature_anchor) != 3:
    raise RuntimeError(f"unexpected email signature count: {text.count(email_signature_anchor)}")
text = text.replace(
    email_signature_anchor,
    "relative_strength: pd.DataFrame, relative_strength_lifecycle: pd.DataFrame, new_entries: pd.DataFrame",
)
replace_once(
    "    lines += plain_relative_strength_section(relative_strength)\n    lines += plain_sector_momentum_section",
    "    lines += plain_relative_strength_section(relative_strength)\n    lines += rs_lifecycle.plain_section(relative_strength_lifecycle)\n    lines += plain_sector_momentum_section",
    "plain email lifecycle",
)
replace_once(
    "        html_relative_strength_section(relative_strength),\n        html_sector_momentum_section",
    "        html_relative_strength_section(relative_strength),\n        rs_lifecycle.html_section(relative_strength_lifecycle),\n        html_sector_momentum_section",
    "html email lifecycle",
)

email_call_anchor = "summary, top100, relative_strength, new_entries"
if text.count(email_call_anchor) != 3:
    raise RuntimeError(f"unexpected email call count: {text.count(email_call_anchor)}")
text = text.replace(
    email_call_anchor,
    "summary, top100, relative_strength, relative_strength_lifecycle, new_entries",
)

replace_once(
    "    all_ranked = attach_relative_strength(all_ranked)\n    if not all_ranked.empty:",
    "    all_ranked = attach_relative_strength(all_ranked)\n    all_ranked = rs_lifecycle.attach(all_ranked, history, today)\n    if not all_ranked.empty:",
    "attach lifecycle before persistence",
)
replace_once(
    "    relative_strength = build_relative_strength_table(top100)\n    new_entries =",
    "    relative_strength = build_relative_strength_table(top100)\n    relative_strength_lifecycle = rs_lifecycle.build_table(top100)\n    new_entries =",
    "build lifecycle table",
)
replace_once(
    '        "レポート形式": "dashboard_relative_strength_v18",',
    '        "レポート形式": "dashboard_relative_strength_lifecycle_v19",',
    "report format",
)
replace_once(
    '        "市場・同業双方超過": int(relative_strength.get("dual_outperformer", pd.Series(dtype=bool)).fillna(False).sum()) if not relative_strength.empty else 0,\n        "相対強度トップ":',
    '        "市場・同業双方超過": int(relative_strength.get("dual_outperformer", pd.Series(dtype=bool)).fillna(False).sum()) if not relative_strength.empty else 0,\n'
    '        "相対強度急加速": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "急加速"),\n'
    '        "相対強度再浮上": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "再浮上"),\n'
    '        "相対強度加速": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "加速"),\n'
    '        "相対強度主導継続": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "主導継続"),\n'
    '        "相対強度失速警戒": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "失速警戒"),\n'
    '        "相対強度崩れ": rs_lifecycle.lifecycle_count(relative_strength_lifecycle, "崩れ"),\n'
    '        "相対強度A以上5日継続": int((relative_strength_lifecycle.get("relative_strength_strong_streak", pd.Series(dtype=float)).fillna(0) >= 5).sum()) if not relative_strength_lifecycle.empty else 0,\n'
    '        "相対強度双方超過5日継続": int((relative_strength_lifecycle.get("dual_outperformer_streak", pd.Series(dtype=float)).fillna(0) >= 5).sum()) if not relative_strength_lifecycle.empty else 0,\n'
    '        "相対強度トップ":',
    "summary lifecycle metrics",
)
replace_once(
    "top100, relative_strength, sector_momentum, sector_rotation",
    "top100, relative_strength, relative_strength_lifecycle, sector_momentum, sector_rotation",
    "excel lifecycle argument",
)

path.write_text(text, encoding="utf-8")
print("relative strength lifecycle integrated")
