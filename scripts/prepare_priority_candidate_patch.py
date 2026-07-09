from pathlib import Path

path = Path("scripts/apply_priority_candidate_changes.py")
text = path.read_text(encoding="utf-8")

repeated_block = '''replace_once(
    '    priority = select_priority_candidates(top100, 10)\\n',
    '    priority = priority_changes.get("current", select_priority_candidates(top100, 10)).head(10)\\n',
)
'''
if text.count(repeated_block) != 2:
    raise RuntimeError(f"Expected two repeated priority blocks, found {text.count(repeated_block)}")

combined_block = '''priority_marker = '    priority = select_priority_candidates(top100, 10)\\n'
priority_replacement = '    priority = priority_changes.get("current", select_priority_candidates(top100, 10)).head(10)\\n'
if text.count(priority_marker) != 2:
    raise RuntimeError(f"Expected two priority markers, found {text.count(priority_marker)}")
text = text.replace(priority_marker, priority_replacement, 2)
'''
text = text.replace(repeated_block, combined_block, 1).replace(repeated_block, "", 1)

if text.count("helpers = r'''") != 1:
    raise RuntimeError("Could not locate helpers opening delimiter")
text = text.replace("helpers = r'''", 'helpers = r"""', 1)

closing_marker = "</div>'''\n'''\n\nreplace_once("
if text.count(closing_marker) != 1:
    raise RuntimeError(f"Could not locate helpers closing delimiter: {text.count(closing_marker)}")
text = text.replace(closing_marker, "</div>'''\n\"\"\"\n\nreplace_once(", 1)

path.write_text(text, encoding="utf-8")
print("Prepared priority candidate patch script")
