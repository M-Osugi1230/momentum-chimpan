from pathlib import Path


runtime_path = Path("runtime_provenance.py")
runtime = runtime_path.read_text(encoding="utf-8")
old = '''    manifest["pip_freeze_sha256"] = sha256_file(freeze_target)
    manifest["pip_freeze_line_count"] = len(freeze)
    atomic_write_json(manifest, output_path)
'''
new = '''    manifest["pip_freeze_sha256"] = sha256_file(freeze_target)
    manifest["pip_freeze_line_count"] = len(freeze)
    manifest.pop("manifest_sha256", None)
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    atomic_write_json(manifest, output_path)
'''
if old not in runtime:
    raise RuntimeError("runtime snapshot signature anchor not found")
runtime_path.write_text(runtime.replace(old, new, 1), encoding="utf-8")

validator_path = Path(".github/validate_workflows.py")
validator = validator_path.read_text(encoding="utf-8")
if '"runtime_provenance.py"' not in validator:
    validator = validator.replace(
        '        "strategy_governance.py snapshot",\n',
        '        "strategy_governance.py snapshot",\n'
        '        "runtime_provenance.py",\n'
        '        "data/runtime_provenance.json",\n'
        '        "output/runtime_pip_freeze.txt",\n'
        '        "steps.runtime.outcome == \'success\'",\n',
        1,
    )
validator_path.write_text(validator, encoding="utf-8")

print("runtime reproducibility finalization applied")
