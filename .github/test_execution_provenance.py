from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import evidence_provenance
import execution_realism


current_fingerprint = evidence_provenance.current_strategy_fingerprint()


def live_source() -> dict:
    return {
        "evidence_origin": evidence_provenance.LIVE_ORIGIN,
        "promotion_evidence_allowed": True,
        "strategy_fingerprint": current_fingerprint,
        "source_path": evidence_provenance.ALLOWED_LIVE_SOURCE,
        "research_only": True,
    }


def execution_manifest(**overrides) -> dict:
    payload = {
        "entry_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
        "same_day_close_entry_allowed": False,
        "default_entry_slippage_bps": 5.0,
        "default_exit_slippage_bps": 5.0,
        "default_fees_bps": 20.0,
        "outcome_count": 120,
        "promotion_evidence_allowed": True,
        "strategy_fingerprint": current_fingerprint,
        "research_only": True,
    }
    payload.update(overrides)
    return payload


policy = {
    "allowed_promotion_evidence_origins": [evidence_provenance.LIVE_ORIGIN],
    "required_promotion_execution_model": "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN",
}
registry = {"policy": policy, "experiments": []}

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    source_path = root / "source.json"
    execution_path = root / "execution.json"
    sealed_path = root / "sealed.json"
    source_path.write_text(json.dumps(live_source()), encoding="utf-8")
    execution_path.write_text(json.dumps(execution_manifest()), encoding="utf-8")

    sealed = evidence_provenance.seal_execution_evidence(
        str(source_path), str(execution_path), str(sealed_path)
    )
    assert sealed_path.exists()
    assert sealed["evidence_origin"] == evidence_provenance.LIVE_ORIGIN
    assert sealed["execution_evidence"] is True
    assert sealed["promotion_evidence_allowed"] is True
    assert sealed["execution_model"] == "NEXT_AVAILABLE_SESSION_ADJUSTED_OPEN"
    assert sealed["same_day_close_entry_allowed"] is False
    valid, detail = evidence_provenance.provenance_valid(sealed, registry)
    assert valid is True, detail

    invalid_models = [
        execution_manifest(entry_model="SIGNAL_DATE_CLOSE", same_day_close_entry_allowed=True),
        execution_manifest(default_entry_slippage_bps=None),
        execution_manifest(default_exit_slippage_bps=None),
        execution_manifest(default_fees_bps=None),
        execution_manifest(promotion_evidence_allowed=False),
        execution_manifest(strategy_fingerprint="wrong"),
    ]
    for index, invalid_manifest in enumerate(invalid_models):
        invalid_path = root / f"invalid_execution_{index}.json"
        invalid_sealed_path = root / f"invalid_sealed_{index}.json"
        invalid_path.write_text(json.dumps(invalid_manifest), encoding="utf-8")
        invalid_sealed = evidence_provenance.seal_execution_evidence(
            str(source_path), str(invalid_path), str(invalid_sealed_path)
        )
        assert invalid_sealed["promotion_evidence_allowed"] is False
        valid, _ = evidence_provenance.provenance_valid(invalid_sealed, registry)
        assert valid is False

    backfill_source = live_source()
    backfill_source.update({
        "evidence_origin": evidence_provenance.BACKFILL_ORIGIN,
        "promotion_evidence_allowed": False,
        "source_path": "output/backfill/historical_ranking.csv",
    })
    backfill_source_path = root / "backfill_source.json"
    backfill_source_path.write_text(json.dumps(backfill_source), encoding="utf-8")
    backfill_sealed = evidence_provenance.seal_execution_evidence(
        str(backfill_source_path), str(execution_path), str(root / "backfill_sealed.json")
    )
    assert backfill_sealed["promotion_evidence_allowed"] is False
    valid, _ = evidence_provenance.provenance_valid(backfill_sealed, registry)
    assert valid is False

    outcomes = pd.DataFrame([{
        "signal_date": "2026-07-01",
        "entry_price_date": "2026-07-02",
        "exit_price_date": "2026-07-08",
        "code": "1001",
        "sector33": "電気機器",
        "horizon_days": 5,
        "forward_return": 0.03,
        "excess_vs_universe": 0.01,
        "entry_slippage_bps": 5.0,
        "exit_slippage_bps": 5.0,
        "fees_bps": 20.0,
    }])
    coverage = pd.DataFrame([{
        "signal_date": "2026-07-01",
        "code": "1001",
        "status": "EXECUTABLE",
    }])
    result = execution_realism.write_outputs(
        outcomes,
        coverage,
        str(root / "execution_outputs"),
        str(source_path),
    )
    assert result["manifest"]["source_promotion_evidence_allowed"] is True
    assert result["manifest"]["promotion_evidence_allowed"] is True
    assert result["manifest"]["strategy_fingerprint"] == current_fingerprint

print("live execution provenance validation passed")
