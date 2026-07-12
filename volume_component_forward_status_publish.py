"""Compare signed forward statuses while ignoring volatile run metadata.

A new repository commit is warranted only when evidence progress or governance
content changes. Generated timestamps, workflow run IDs, artifact input hashes,
and cryptographic envelope hashes do not independently trigger a commit.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

import volume_component_forward_status as status

VOLATILE_FIELDS = {
    "generated_at_utc",
    "source_run_id",
    "source_hashes",
    "evidence_fingerprint",
    "status_sha256",
}


def semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in VOLATILE_FIELDS
    }


def semantic_fingerprint(payload: dict[str, Any]) -> str:
    return status.canonical_hash(semantic_payload(payload))


def compare_and_stage(
    candidate_path: str | Path,
    current_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    candidate = status.load_json(candidate_path)
    candidate_errors = status.validate_status(candidate)
    if candidate_errors:
        raise ValueError(
            "candidate status is invalid: " + " / ".join(candidate_errors)
        )

    current_target = Path(current_path)
    current: dict[str, Any] = {}
    current_errors: list[str] = []
    if current_target.exists():
        try:
            current = status.load_json(current_target)
            current_errors = status.validate_status(current)
        except Exception as exc:
            current_errors = [str(exc)]

    candidate_semantic = semantic_fingerprint(candidate)
    current_semantic = (
        semantic_fingerprint(current)
        if current and not current_errors
        else ""
    )
    changed = candidate_semantic != current_semantic
    output_target = Path(output_path)
    if changed:
        output_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate_path, output_target)

    return {
        "changed": changed,
        "candidate_semantic_fingerprint": candidate_semantic,
        "current_semantic_fingerprint": current_semantic,
        "candidate_evidence_fingerprint": candidate.get(
            "evidence_fingerprint", ""
        ),
        "current_evidence_fingerprint": current.get(
            "evidence_fingerprint", ""
        ),
        "current_valid": bool(current and not current_errors),
        "current_errors": current_errors,
        "evidence_status": candidate.get("evidence_status", ""),
        "output_path": str(output_target),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare and stage signed volume forward status"
    )
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output", default="")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    result = compare_and_stage(
        args.candidate,
        args.current,
        args.output,
    )
    github_output = args.github_output or os.getenv("GITHUB_OUTPUT", "")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write(
                f"changed={'true' if result['changed'] else 'false'}\n"
            )
            handle.write(
                "candidate_semantic_fingerprint="
                f"{result['candidate_semantic_fingerprint']}\n"
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
