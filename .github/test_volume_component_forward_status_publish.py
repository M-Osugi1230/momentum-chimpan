from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import volume_component_forward_status as status
import volume_component_forward_status_publish as publish


initial = status.build_initial_status()
assert status.validate_status(initial) == []
semantic = publish.semantic_fingerprint(initial)
assert len(semantic) == 64

volatile_only = copy.deepcopy(initial)
volatile_only["generated_at_utc"] = "2026-07-19T03:30:00+00:00"
volatile_only["source_run_id"] = "999999"
volatile_only["source_hashes"] = {"manifest_sha256": "f" * 64}
substantive_for_envelope = dict(volatile_only)
substantive_for_envelope.pop("status_sha256", None)
substantive_for_envelope.pop("evidence_fingerprint", None)
# The signed envelope remains valid for this synthetic status.
volatile_only["evidence_fingerprint"] = initial["evidence_fingerprint"]
envelope = dict(volatile_only)
envelope.pop("status_sha256", None)
volatile_only["status_sha256"] = status.canonical_hash(envelope)
assert status.validate_status(volatile_only) == []
assert publish.semantic_fingerprint(volatile_only) == semantic

changed = copy.deepcopy(initial)
changed["horizons"]["10"]["baseline_outcome_count"] = 10
changed["horizons"]["10"]["tested_outcome_count"] = 9
changed["horizons"]["10"]["minimum_variant_outcome_count"] = 9
changed["horizons"]["10"]["outcome_progress_ratio"] = 0.09
substantive = dict(changed)
substantive.pop("generated_at_utc", None)
substantive.pop("evidence_fingerprint", None)
substantive.pop("status_sha256", None)
changed["evidence_fingerprint"] = status.canonical_hash(substantive)
envelope = dict(changed)
envelope.pop("status_sha256", None)
changed["status_sha256"] = status.canonical_hash(envelope)
assert status.validate_status(changed) == []
assert publish.semantic_fingerprint(changed) != semantic

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    current_path = root / "current.json"
    candidate_path = root / "candidate.json"
    output_path = root / "output.json"

    current_path.write_text(
        json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    candidate_path.write_text(
        json.dumps(volatile_only, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    unchanged = publish.compare_and_stage(
        candidate_path, current_path, output_path
    )
    assert unchanged["changed"] is False
    assert not output_path.exists()

    candidate_path.write_text(
        json.dumps(changed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    updated = publish.compare_and_stage(
        candidate_path, current_path, output_path
    )
    assert updated["changed"] is True
    assert output_path.is_file()
    assert status.load_json(output_path) == changed

    current_path.write_text("{}", encoding="utf-8")
    output_path.unlink()
    replaced = publish.compare_and_stage(
        candidate_path, current_path, output_path
    )
    assert replaced["changed"] is True
    assert replaced["current_valid"] is False
    assert output_path.is_file()

print("volume component forward status publisher validation passed")
