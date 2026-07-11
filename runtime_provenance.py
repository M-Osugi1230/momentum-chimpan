"""Capture reproducible runtime and data-source provenance.

The manifest records Python/platform/package versions, dependency lock hashes,
strategy fingerprint, source URLs, and key repository file hashes. It is
operational metadata only and never changes strategy parameters or orders.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import main
import strategy_governance

PROVENANCE_VERSION = "2026-07-11-runtime-provenance-v1"
DEFAULT_OUTPUT = "data/runtime_provenance.json"
DEFAULT_FREEZE_OUTPUT = "output/runtime_pip_freeze.txt"
TRACKED_PACKAGES = (
    "pandas",
    "numpy",
    "yfinance",
    "openpyxl",
    "PyYAML",
    "requests",
    "python-dotenv",
)
TRACKED_FILES = (
    "main.py",
    "config.yaml",
    "requirements.txt",
    "requirements.lock",
    "research/experiment_registry.yaml",
    "data/jpx_list_cache.csv",
)


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(payload: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def package_versions(packages: tuple[str, ...] = TRACKED_PACKAGES) -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "MISSING"
    return versions


def pip_freeze() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--all"],
        check=True,
        capture_output=True,
        text=True,
    )
    excluded = {"pip", "setuptools", "wheel"}
    lines: list[str] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        name = line.split("==", 1)[0].split(" @ ", 1)[0].strip().lower()
        if name in excluded:
            continue
        lines.append(line)
    return sorted(set(lines), key=str.lower)


def load_previous(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def drift_items(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    if not previous:
        return []
    checks = {
        "python_version": (previous.get("python_version"), current.get("python_version")),
        "platform": (previous.get("platform"), current.get("platform")),
        "strategy_fingerprint": (
            previous.get("strategy_fingerprint"),
            current.get("strategy_fingerprint"),
        ),
        "package_versions": (
            previous.get("package_versions", {}),
            current.get("package_versions", {}),
        ),
        "requirements_lock_sha256": (
            previous.get("file_sha256", {}).get("requirements.lock", ""),
            current.get("file_sha256", {}).get("requirements.lock", ""),
        ),
        "requirements_sha256": (
            previous.get("file_sha256", {}).get("requirements.txt", ""),
            current.get("file_sha256", {}).get("requirements.txt", ""),
        ),
        "jpx_list_url": (
            previous.get("data_sources", {}).get("jpx_list_url", ""),
            current.get("data_sources", {}).get("jpx_list_url", ""),
        ),
    }
    rows: list[dict[str, Any]] = []
    for name, (before, after) in checks.items():
        if before != after:
            rows.append({"field": name, "previous": before, "current": after})
    return rows


def build_manifest(previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    packages = package_versions()
    files = {path: sha256_file(path) for path in TRACKED_FILES}
    strategy_fingerprint = strategy_governance.current_strategy_fingerprint()
    current: dict[str, Any] = {
        "provenance_version": PROVENANCE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "package_versions": packages,
        "strategy_fingerprint": strategy_fingerprint,
        "file_sha256": files,
        "data_sources": {
            "jpx_list_url": main.JPX_LIST_URL,
            "price_provider": "yfinance",
            "ticker_suffix": ".T",
            "timezone": os.environ.get("TZ", ""),
        },
        "github": {
            "repository": os.environ.get("GITHUB_REPOSITORY", ""),
            "run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "sha": os.environ.get("GITHUB_SHA", ""),
            "ref": os.environ.get("GITHUB_REF", ""),
            "event_name": os.environ.get("GITHUB_EVENT_NAME", ""),
        },
        "dependency_lock_present": bool(files.get("requirements.lock")),
        "required_packages_present": all(value != "MISSING" for value in packages.values()),
        "research_only": True,
    }
    drift = drift_items(previous, current)
    current["previous_generated_at_utc"] = previous.get("generated_at_utc", "")
    current["drift_count"] = len(drift)
    current["drift"] = drift
    current["environment_status"] = "INITIAL" if not previous else "DRIFT" if drift else "STABLE"
    canonical = json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    current["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return current


def snapshot(
    output_path: str = DEFAULT_OUTPUT,
    previous_path: str | None = None,
    freeze_output: str = DEFAULT_FREEZE_OUTPUT,
    strict: bool = False,
) -> dict[str, Any]:
    previous = load_previous(previous_path or output_path)
    manifest = build_manifest(previous)
    freeze = pip_freeze()
    freeze_target = Path(freeze_output)
    freeze_target.parent.mkdir(parents=True, exist_ok=True)
    freeze_target.write_text("\n".join(freeze) + "\n", encoding="utf-8")
    manifest["pip_freeze_sha256"] = sha256_file(freeze_target)
    manifest["pip_freeze_line_count"] = len(freeze)
    atomic_write_json(manifest, output_path)
    if strict:
        if not manifest["dependency_lock_present"]:
            raise RuntimeError("requirements.lock is missing")
        if not manifest["required_packages_present"]:
            missing = [
                name for name, version in manifest["package_versions"].items()
                if version == "MISSING"
            ]
            raise RuntimeError(f"required packages are missing: {missing}")
        if not manifest["strategy_fingerprint"]:
            raise RuntimeError("strategy fingerprint is empty")
        if not manifest["file_sha256"].get("requirements.txt"):
            raise RuntimeError("requirements.txt hash is missing")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture signed runtime provenance")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--previous")
    parser.add_argument("--freeze-output", default=DEFAULT_FREEZE_OUTPUT)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    result = snapshot(args.output, args.previous, args.freeze_output, args.strict)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
