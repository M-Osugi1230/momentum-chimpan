from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

old = '''    result["sector_rotation_order"] = result["sector_rotation"].map(SECTOR_ROTATION_ORDER).fillna(99)
    result = result.sort_values(
        ["sector_rotation_order", "sector_rotation_score", "sector_rank"],
        ascending=[True, False, True],
    ).drop(columns=["sector_rotation_order"])
    return result
'''
new = '''    return result.sort_values("sector_rank").reset_index(drop=True)
'''
if text.count(old) != 1:
    raise RuntimeError(f"attach_sector_rotation anchor count={text.count(old)}")
text = text.replace(old, new, 1)

old_columns = '''    "code", "name", "momentum_rank", "momentum_score", "sector_leader_score",
    "sector_research_priority", "action_priority", "action_score", "expectancy_score",
'''
new_columns = '''    "code", "name", "momentum_rank", "momentum_score", "sector_leader_score", "sector_leader_grade",
    "sector_research_priority", "action_priority", "action_score", "expectancy_score",
'''
if text.count(old_columns) != 1:
    raise RuntimeError(f"leader columns anchor count={text.count(old_columns)}")
text = text.replace(old_columns, new_columns, 1)

path.write_text(text, encoding="utf-8")
print("Applied sector batch display-order fixes")
