import re
from pathlib import Path

main_path = Path("main.py")
text = main_path.read_text(encoding="utf-8")
source = Path(".github/action_priority_patch.py").read_text(encoding="utf-8")
match = re.search(r"functions = r'''(.*?)'''\ntext = text\.replace\(function_anchor", source, flags=re.S)
if not match:
    raise RuntimeError("Could not extract action priority functions")
functions = match.group(1)


def sub(pattern: str, replacement: str, label: str, count: int = 1) -> None:
    global text
    text, changed = re.subn(pattern, replacement, text, count=count, flags=re.S)
    if changed != count:
        raise RuntimeError(f"{label}: expected {count} replacement, got {changed}")
    print(f"patched: {label}")


sub(r'APP_VERSION = "[^"]+"', 'APP_VERSION = "2026-07-11-dashboard-action-priority-v11"', "app version")

sub(
    r'def excel_report\((.*?)priority_expectancy: pd\.DataFrame,\s*priority_performance:',
    r'def excel_report(\1priority_expectancy: pd.DataFrame, action_priority: pd.DataFrame, priority_performance:',
    "excel signature",
)
sub(
    r'(priority_expectancy\.to_excel\(w, sheet_name="Priority Expectancy", index=False\)\n)',
    r'\1        action_priority.to_excel(w, sheet_name="Action Priority", index=False)\n',
    "action priority sheet",
)
sub(r'\n\ndef expectancy_detail\(row: pd\.Series\) -> str:', functions + '\n\ndef expectancy_detail(row: pd.Series) -> str:', "action priority functions")

sub(
    r'(lines \+= plain_market_regime\(regime\)\n)',
    r'\1    lines += plain_action_priority_section(priority_changes.get("action_priority", pd.DataFrame()))\n',
    "plain email section",
)
sub(
    r'(html_market_regime\(regime\),\n)',
    r'\1        html_action_priority_section(priority_changes.get("action_priority", pd.DataFrame())),\n',
    "html email section",
)
sub(
    r'(regime = enrich_regime_from_temperature\(regime, temperature\)\n)',
    r'\1    priority_changes = attach_action_priority(priority_changes, regime)\n    action_priority = priority_changes.get("action_priority", pd.DataFrame())\n',
    "main action priority attachment",
)
sub(r'"レポート形式": "[^"]+"', '"レポート形式": "dashboard_action_priority_v11"', "report format")

summary_addition = '''        "調査優先度A": action_priority_count(action_priority, "A"),
        "調査優先度B": action_priority_count(action_priority, "B"),
        "調査優先度C": action_priority_count(action_priority, "C"),
        "調査優先度見送り": action_priority_count(action_priority, "見送り"),
        "A評価平均期待値": float(action_priority[action_priority["action_priority"] == "A"]["expectancy_score"].mean()) if not action_priority.empty and action_priority_count(action_priority, "A") > 0 else None,
        "A評価高信頼度件数": int(((action_priority.get("action_priority", pd.Series(dtype=str)) == "A") & (action_priority.get("expectancy_confidence", pd.Series(dtype=str)) == "高")).sum()) if not action_priority.empty else 0,
'''
sub(
    r'(        "重点候補平均期待値スコア":.*?\n)',
    r'\1' + summary_addition,
    "summary action priority metrics",
)

sub(
    r'(priority_changes\["expectancy"\],\s*)priority_performance,',
    r'\1action_priority, priority_performance,',
    "excel action priority argument",
)

main_path.write_text(text, encoding="utf-8")
print("Patched main.py with resilient action priority implementation")
