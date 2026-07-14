"""Privacy-safe, signed preview of the exact daily decision email.

The preview stores only the generated subject and research content. It never
stores sender/recipient identities, credentials, SMTP responses, or exception
messages. It is an observability artifact and cannot change ranking, strategy,
paper execution, or production state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PREVIEW_VERSION = "2026-07-14-email-preview-v1"
DEFAULT_OUTPUT_DIR = "output/email_preview"
REQUIRED_FILES = ("subject.txt", "plain.txt", "email.html", "manifest.json")
FORBIDDEN_MARKERS = (
    "EMAIL_APP_PASSWORD",
    "EMAIL_FROM",
    "EMAIL_TO",
    "smtp.gmail.com",
    "@icloud.com",
    "@gmail.com",
    "private-sender@example.com",
    "first@example.com",
    "second@example.com",
)


def canonical_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def summary_value(summary: Any, *keys: str) -> str:
    if not isinstance(summary, dict):
        return ""
    for key in keys:
        value = summary.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def write_preview(
    *,
    subject: str,
    plain: str,
    html: str,
    summary: dict[str, Any] | None = None,
    candidate_count: int = 0,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """Atomically write the exact presentation strings and a signed manifest."""
    subject_text = str(subject or "").strip()
    plain_text = str(plain or "")
    html_text = str(html or "")
    if not subject_text:
        raise ValueError("email preview subject is required")
    if "\n" in subject_text or "\r" in subject_text:
        raise ValueError("email preview subject must be one line")
    if not plain_text.strip():
        raise ValueError("email preview plain body is required")
    if not html_text.strip():
        raise ValueError("email preview HTML body is required")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    atomic_write(output / "subject.txt", subject_text + "\n")
    atomic_write(output / "plain.txt", plain_text)
    atomic_write(output / "email.html", html_text)

    files = []
    for name in ("subject.txt", "plain.txt", "email.html"):
        path = output / name
        files.append({
            "path": name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    core = {
        "preview_version": PREVIEW_VERSION,
        "report_date": summary_value(summary, "実行日", "report_date"),
        "price_date": summary_value(summary, "株価データ日", "最新株価日", "price_date"),
        "market_regime": summary_value(summary, "Market Regime"),
        "market_regime_score": summary_value(summary, "Market Regime Score"),
        "candidate_count": max(int(candidate_count), 0),
        "subject_sha256": sha256_bytes(subject_text.encode("utf-8")),
        "plain_sha256": sha256_bytes(plain_text.encode("utf-8")),
        "html_sha256": sha256_bytes(html_text.encode("utf-8")),
        "files": files,
        "contains_recipient_identity": False,
        "contains_sender_identity": False,
        "contains_credentials": False,
        "research_only": True,
        "automatic_strategy_change": False,
        "production_state_mutations": [],
    }
    manifest = {
        **core,
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "manifest_fingerprint": canonical_hash(core),
    }
    manifest["status_sha256"] = canonical_hash(manifest)
    atomic_write(output / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    validation = validate_preview(output)
    if not validation["passed"]:
        raise ValueError("invalid email preview: " + "; ".join(validation["issues"]))
    return {"manifest": manifest, "validation": validation}


def validate_preview(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    output = Path(output_dir)
    issues: list[str] = []
    for name in REQUIRED_FILES:
        if not (output / name).is_file():
            issues.append(f"missing required file: {name}")

    manifest: dict[str, Any] = {}
    manifest_path = output / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as error:
            issues.append(f"invalid manifest JSON: {type(error).__name__}")

    if manifest:
        if manifest.get("preview_version") != PREVIEW_VERSION:
            issues.append("invalid preview_version")
        if manifest.get("research_only") is not True:
            issues.append("preview must remain research_only")
        if manifest.get("automatic_strategy_change") is not False:
            issues.append("automatic_strategy_change must be false")
        if manifest.get("production_state_mutations") != []:
            issues.append("production_state_mutations must be empty")
        for field in (
            "contains_recipient_identity",
            "contains_sender_identity",
            "contains_credentials",
        ):
            if manifest.get(field) is not False:
                issues.append(f"{field} must be false")

        unsigned = dict(manifest)
        supplied_status = unsigned.pop("status_sha256", "")
        if supplied_status != canonical_hash(unsigned):
            issues.append("status_sha256 mismatch")
        core = dict(unsigned)
        core.pop("generated_at_utc", None)
        supplied_fingerprint = core.pop("manifest_fingerprint", "")
        if supplied_fingerprint != canonical_hash(core):
            issues.append("manifest_fingerprint mismatch")

        listed = {
            str(entry.get("path")): entry
            for entry in manifest.get("files", [])
            if isinstance(entry, dict)
        }
        for name in ("subject.txt", "plain.txt", "email.html"):
            path = output / name
            entry = listed.get(name)
            if not entry:
                issues.append(f"manifest missing file: {name}")
                continue
            if path.is_file():
                if entry.get("sha256") != sha256_file(path):
                    issues.append(f"file hash mismatch: {name}")
                if int(entry.get("size_bytes", -1)) != path.stat().st_size:
                    issues.append(f"file size mismatch: {name}")

        subject_path = output / "subject.txt"
        plain_path = output / "plain.txt"
        html_path = output / "email.html"
        if subject_path.is_file():
            subject = subject_path.read_text(encoding="utf-8").rstrip("\n")
            if "\n" in subject or "\r" in subject:
                issues.append("subject must be one line")
            if manifest.get("subject_sha256") != sha256_bytes(subject.encode("utf-8")):
                issues.append("subject content hash mismatch")
        if plain_path.is_file():
            plain = plain_path.read_text(encoding="utf-8")
            if manifest.get("plain_sha256") != sha256_bytes(plain.encode("utf-8")):
                issues.append("plain content hash mismatch")
        if html_path.is_file():
            html = html_path.read_text(encoding="utf-8")
            if manifest.get("html_sha256") != sha256_bytes(html.encode("utf-8")):
                issues.append("HTML content hash mismatch")

    combined = ""
    for name in ("subject.txt", "plain.txt", "email.html", "manifest.json"):
        path = output / name
        if path.is_file():
            combined += "\n" + path.read_text(encoding="utf-8", errors="replace")
    lowered = combined.lower()
    for marker in FORBIDDEN_MARKERS:
        if marker.lower() in lowered:
            issues.append(f"private marker found: {marker}")

    return {"passed": not issues, "issues": sorted(set(issues)), "manifest": manifest}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a generated daily email preview")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = validate_preview(args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
