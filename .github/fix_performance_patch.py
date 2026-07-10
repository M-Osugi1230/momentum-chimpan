from pathlib import Path

path = Path(".github/performance_governance_patch.py")
text = path.read_text(encoding="utf-8")
text = text.replace("governance_functions = r'''", 'governance_functions = r"""', 1)
anchor = "\n'''\n\nreplace_once(\n    '\\n\\ndef market_temperature"
if anchor not in text:
    anchor = "\n'''\n\nreplace_once(\n    '\n\ndef market_temperature"
if anchor not in text:
    raise RuntimeError("Could not find governance_functions closing delimiter")
text = text.replace(anchor, anchor.replace("\n'''\n", '\n"""\n'), 1)
path.write_text(text, encoding="utf-8")
print("Prepared performance governance patch delimiters")
