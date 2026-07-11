from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import evidence_provenance as provenance


current_fingerprint = provenance.current_strategy_fingerprint()

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    ranking = root / "ranking.csv"
    report = root / "report.xlsx"
    fingerprint = root / "fingerprint.json"
    stamp_audit = root / "stamp_audit.json"
    filtered = root / "filtered.csv"
    live_manifest = root / "live_provenance.json"
    snapshot_root = root / "state_snapshots"

    pd.DataFrame([
        {"date": "2026-07-09", "rank": 1, "code": "1001", "close": 100, "score": 80},
        {"date": "2026-07-10", "rank": 1, "code": "1001", "close": 105, "score": 85},
        {"date": "2026-07-10", "rank": 2, "code": "1002", "close": 110, "score": 75},
    ]).to_csv(ranking, index=False)
    snapshot_path = snapshot_root / "2026-07-10" / "ranking_history.csv"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    pd.read_csv(ranking, dtype={"code": str}).to_csv(snapshot_path, index=False)
    with pd.ExcelWriter(report, engine="openpyxl") as writer:
        pd.DataFrame([{
            "実行日": "2026-07-10",
            "状態更新実行": "YES",
            "アプリ版": "test-app",
        }]).to_excel(writer, sheet_name="Summary", index=False)
    fingerprint.write_text(json.dumps({
        "strategy_fingerprint": current_fingerprint,
    }), encoding="utf-8")

    stamp = provenance.stamp_live_ranking_history(
        str(ranking), str(report), str(fingerprint), str(stamp_audit), str(snapshot_root)
    )
    assert stamp["stamped_rows"] == 2
    stamped = pd.read_csv(ranking, dtype={"code": str})
    current_rows = stamped[stamped["date"].astype(str) == "2026-07-10"]
    prior_rows = stamped[stamped["date"].astype(str) == "2026-07-09"]
    assert set(current_rows["strategy_fingerprint"]) == {current_fingerprint}
    assert prior_rows["strategy_fingerprint"].fillna("").eq("").all()
    assert stamp_audit.exists()

    original_allowed_source = provenance.ALLOWED_LIVE_SOURCE
    provenance.ALLOWED_LIVE_SOURCE = str(ranking)
    try:
        live = provenance.prepare_live_history(
            str(ranking), str(filtered), str(live_manifest), str(fingerprint)
        )
    finally:
        provenance.ALLOWED_LIVE_SOURCE = original_allowed_source
    eligible = pd.read_csv(filtered, dtype={"code": str})
    assert len(eligible) == 2
    assert set(eligible["date"].astype(str)) == {"2026-07-10"}
    assert live["promotion_evidence_allowed"] is True
    assert live["evidence_origin"] == provenance.LIVE_ORIGIN
    assert live["eligible_date_count"] == 1

    stale_report = root / "stale_report.xlsx"
    stale_ranking = root / "stale_ranking.csv"
    stamped.to_csv(stale_ranking, index=False)
    before = provenance.sha256_file(stale_ranking)
    with pd.ExcelWriter(stale_report, engine="openpyxl") as writer:
        pd.DataFrame([{
            "実行日": "2026-07-11",
            "状態更新実行": "NO",
            "アプリ版": "test-app",
        }]).to_excel(writer, sheet_name="Summary", index=False)
    stale_audit = provenance.stamp_live_ranking_history(
        str(stale_ranking), str(stale_report), str(fingerprint), str(root / "stale_audit.json")
    )
    assert stale_audit["stamped_rows"] == 0
    assert provenance.sha256_file(stale_ranking) == before

    backfill_source = root / "backfill_manifest.json"
    backfill_provenance = root / "backfill_provenance.json"
    backfill_source.write_text(json.dumps({
        "universe_bias": "CURRENT_LIST_ONLY_SURVIVORSHIP_AND_DELISTING_BIAS",
        "promotion_evidence_allowed": False,
        "ranking_date_count": 20,
        "jpx_cache_sha256": "cache-hash",
    }), encoding="utf-8")
    derived = provenance.seal_derived_backfill(str(backfill_source), str(backfill_provenance))
    assert derived["promotion_evidence_allowed"] is False
    assert derived["evidence_origin"] == provenance.BACKFILL_ORIGIN
    assert "SURVIVORSHIP_BIAS" in derived["bias_flags"]

    registry_path = root / "registry.yaml"
    robustness_path = root / "robustness.csv"
    output_live = root / "audit_live"
    output_backfill = root / "audit_backfill"
    registry = {
        "schema_version": 1,
        "policy": {
            "automatic_promotion": False,
            "minimum_outcome_count": 100,
            "required_robustness_status": "ROBUST",
            "maximum_fdr_q_value": 0.05,
            "require_positive_early_period": True,
            "require_positive_late_period": True,
            "require_positive_leave_one_sector": True,
            "require_manual_approval": True,
            "allowed_promotion_evidence_origins": [provenance.LIVE_ORIGIN],
        },
        "experiments": [{
            "experiment_id": "promoted-test",
            "experiment_type": "candidate",
            "status": "promoted",
            "hypothesis": "test",
            "strategy_fingerprint": current_fingerprint,
            "change_summary": "test",
            "evidence_scope": {
                "group_type": "overall",
                "group_value": "all",
                "horizon_days": 10,
            },
            "manual_approval": {
                "approved": True,
                "approved_by": "repository-owner",
                "approved_at": "2026-07-11",
            },
        }],
    }
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    pd.DataFrame([{
        "group_type": "overall",
        "group_value": "all",
        "horizon_days": 10,
        "count": 120,
        "robustness_status": "ROBUST",
        "fdr_q_value": 0.03,
        "early_net_average_excess": 0.01,
        "late_net_average_excess": 0.012,
        "worst_leave_one_sector_excess": 0.008,
    }]).to_csv(robustness_path, index=False)

    original_allowed_source = provenance.ALLOWED_LIVE_SOURCE
    live_payload = json.loads(live_manifest.read_text(encoding="utf-8"))
    live_payload["source_path"] = original_allowed_source
    live_payload["strategy_fingerprint"] = current_fingerprint
    live_manifest.write_text(json.dumps(live_payload), encoding="utf-8")
    live_result = provenance.governance_audit_with_provenance(
        str(output_live), str(registry_path), str(robustness_path), str(live_manifest)
    )
    assert live_result["issues"].empty
    assert live_result["manifest"]["provenance_valid"] is True
    assert bool(live_result["audit"].iloc[0]["promotion_valid_after_provenance"])

    backfill_result = provenance.governance_audit_with_provenance(
        str(output_backfill), str(registry_path), str(robustness_path), str(backfill_provenance)
    )
    assert not backfill_result["issues"].empty
    assert backfill_result["manifest"]["provenance_valid"] is False
    assert not bool(backfill_result["audit"].iloc[0]["promotion_valid_after_provenance"])
    assert "promotion blocked by evidence provenance" in backfill_result["issues"].iloc[-1]["issue"]

print("evidence provenance lock validation passed")
