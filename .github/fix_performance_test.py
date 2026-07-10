from pathlib import Path

path = Path(".github/test_performance_governance.py")
text = path.read_text(encoding="utf-8")

import_old = '''from tempfile import TemporaryDirectory
import importlib.util

import pandas as pd'''
import_new = '''from tempfile import TemporaryDirectory
import importlib.util
import sys

import pandas as pd'''
if text.count(import_old) != 1:
    raise RuntimeError("Could not find import anchor")
text = text.replace(import_old, import_new, 1)

loader_old = '''module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)'''
loader_new = '''module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)'''
if text.count(loader_old) != 1:
    raise RuntimeError("Could not find dynamic import anchor")
text = text.replace(loader_old, loader_new, 1)

health_old = '''sector_momentum = pd.DataFrame({"sector33": [f"業種{i:02d}" for i in range(30)]})
run_health = module.build_run_health(
    "2026-07-11", all_ranked, top100, sector_momentum, leader_rows,
    [], 120, 120,
)'''
health_new = '''sector_momentum = pd.DataFrame({"sector33": [f"業種{i:02d}" for i in range(30)]})
health_leaders = pd.concat([leader_rows.assign(code=f"{index + 1:04d}") for index in range(5)], ignore_index=True)
run_health = module.build_run_health(
    "2026-07-11", all_ranked, top100, sector_momentum, health_leaders,
    [], 120, 120,
)'''
if text.count(health_old) != 1:
    raise RuntimeError("Could not find health test anchor")
text = text.replace(health_old, health_new, 1)

path.write_text(text, encoding="utf-8")
print("Prepared Python 3.12 and production-aligned validation data")
