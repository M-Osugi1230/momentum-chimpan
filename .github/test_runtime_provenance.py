from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import runtime_provenance as runtime


previous = {
    "python_version": "3.12.0",
    "platform": "test-platform",
    "strategy_fingerprint": "old-strategy",
    "package_versions": {"pandas": "1.0"},
    "file_sha256": {
        "requirements.lock": "old-lock",
        "requirements.txt": "same-req",
    },
    "data_sources": {"jpx_list_url": "old-url"},
}
current = {
    "python_version": "3.12.1",
    "platform": "test-platform",
    "strategy_fingerprint": "new-strategy",
    "package_versions": {"pandas": "2.0"},
    "file_sha256": {
        "requirements.lock": "new-lock",
        "requirements.txt": "same-req",
    },
    "data_sources": {"jpx_list_url": "new-url"},
}
drift = runtime.drift_items(previous, current)
fields = {row["field"] for row in drift}
assert fields == {
    "python_version",
    "strategy_fingerprint",
    "package_versions",
    "requirements_lock_sha256",
    "jpx_list_url",
}
assert runtime.drift_items({}, current) == []

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    old_cwd = Path.cwd()
    original_files = runtime.TRACKED_FILES
    original_packages = runtime.package_versions
    original_freeze = runtime.pip_freeze
    original_fingerprint = runtime.evidence_provenance.current_strategy_fingerprint
    try:
        os.chdir(root)
        for path, content in {
            "main.py": "print('main')\n",
            "config.yaml": "market: {}\n",
            "requirements.txt": "pandas\n",
            "requirements.lock": "pandas==2.2.3\n",
            "research/experiment_registry.yaml": "policy: {}\n",
            "data/jpx_list_cache.csv": "code,name\n1001,Alpha\n",
        }.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        runtime.TRACKED_FILES = original_files
        runtime.package_versions = lambda packages=runtime.TRACKED_PACKAGES: {
            package: f"1.0.{index}" for index, package in enumerate(packages)
        }
        runtime.pip_freeze = lambda: [
            "numpy==2.1.0",
            "pandas==2.2.3",
            "yfinance==0.2.65",
        ]
        runtime.evidence_provenance.current_strategy_fingerprint = lambda: "strategy-abc"

        output = root / "data/runtime_provenance.json"
        freeze_output = root / "output/runtime_pip_freeze.txt"
        initial = runtime.snapshot(
            str(output),
            str(output),
            str(freeze_output),
            strict=True,
        )
        assert initial["environment_status"] == "INITIAL"
        assert initial["drift_count"] == 0
        assert initial["dependency_lock_present"] is True
        assert initial["required_packages_present"] is True
        assert initial["strategy_fingerprint"] == "strategy-abc"
        assert initial["file_sha256"]["requirements.lock"]
        assert initial["pip_freeze_line_count"] == 3
        assert len(initial["pip_freeze_sha256"]) == 64
        assert len(initial["manifest_sha256"]) == 64
        assert output.exists()
        assert freeze_output.exists()

        stable = runtime.snapshot(
            str(output),
            str(output),
            str(freeze_output),
            strict=True,
        )
        assert stable["environment_status"] == "STABLE"
        assert stable["drift_count"] == 0
        assert stable["previous_generated_at_utc"] == initial["generated_at_utc"]

        (root / "requirements.lock").write_text("pandas==2.3.0\n", encoding="utf-8")
        changed = runtime.snapshot(
            str(output),
            str(output),
            str(freeze_output),
            strict=True,
        )
        assert changed["environment_status"] == "DRIFT"
        assert "requirements_lock_sha256" in {row["field"] for row in changed["drift"]}

        (root / "requirements.lock").unlink()
        try:
            runtime.snapshot(
                str(output),
                str(output),
                str(freeze_output),
                strict=True,
            )
            raise AssertionError("strict provenance should reject a missing lock")
        except RuntimeError as exc:
            assert "requirements.lock" in str(exc)

        (root / "requirements.lock").write_text("pandas==2.2.3\n", encoding="utf-8")
        runtime.package_versions = lambda packages=runtime.TRACKED_PACKAGES: {
            package: ("MISSING" if package == "yfinance" else "1.0")
            for package in packages
        }
        try:
            runtime.snapshot(
                str(output),
                str(output),
                str(freeze_output),
                strict=True,
            )
            raise AssertionError("strict provenance should reject missing packages")
        except RuntimeError as exc:
            assert "yfinance" in str(exc)
    finally:
        runtime.TRACKED_FILES = original_files
        runtime.package_versions = original_packages
        runtime.pip_freeze = original_freeze
        runtime.evidence_provenance.current_strategy_fingerprint = original_fingerprint
        os.chdir(old_cwd)

print("runtime reproducibility validation passed")
