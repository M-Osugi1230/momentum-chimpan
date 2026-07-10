from pathlib import Path

path = Path(".github/test_performance_governance.py")
text = path.read_text(encoding="utf-8")
old = '''sector_momentum = pd.DataFrame({"sector33": [f"業種{i:02d}" for i in range(30)]})
run_health = module.build_run_health(
    "2026-07-11", all_ranked, top100, sector_momentum, leader_rows,
    [], 120, 120,
)'''
new = '''sector_momentum = pd.DataFrame({"sector33": [f"業種{i:02d}" for i in range(30)]})
health_leaders = pd.concat([leader_rows.assign(code=f"{index + 1:04d}") for index in range(5)], ignore_index=True)
run_health = module.build_run_health(
    "2026-07-11", all_ranked, top100, sector_momentum, health_leaders,
    [], 120, 120,
)'''
if text.count(old) != 1:
    raise RuntimeError("Could not find health test anchor")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Aligned health test data with production gate")
