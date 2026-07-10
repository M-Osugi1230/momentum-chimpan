from pathlib import Path

path = Path(".github/sector_leader_rotation_patch.py")
text = path.read_text(encoding="utf-8")
text = text.replace("sector_functions = r'''", 'sector_functions = r"""', 1)
anchor = "\n'''\n\nreplace_once(\n    '\\\\n\\\\ndef market_temperature"
# The generated patch script contains a literal backslash-n anchor after the inserted block.
if anchor not in text:
    anchor = "\n'''\n\nreplace_once(\n    '\\n\\ndef market_temperature"
if anchor not in text:
    raise RuntimeError("Could not find sector_functions closing delimiter")
text = text.replace(anchor, anchor.replace("\n'''\n", '\n"""\n'), 1)
path.write_text(text, encoding="utf-8")
print("Prepared sector leader patch delimiters")
