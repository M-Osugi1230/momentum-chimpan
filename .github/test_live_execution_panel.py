from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import evidence_provenance
import live_execution_panel as live_panel


current_fingerprint = live_panel.evidence_provenance.current_strategy_fingerprint()

signals = pd.DataFrame([
    {
        "signal_date": "2026-07-01",
        "code": "1001",
        "name": "Alpha",
        "sector33": "電気機器",
    },
    {
        "signal_date": "2026-07-02",
        "code": "1001",
        "name": "Alpha",
        "sector33": "電気機器",
    },
    {
        "signal_date": "2026-07-01",
        "code": "2001",
        "name": "Beta",
        "sector33": "銀行業",
    },
])
members = live_panel.signal_members(signals.assign(signal_date=pd.to_datetime(signals["signal_date"])))
assert [member.code for member in members] == ["1001", "2001"]
assert members[0].sector33 == "電気機器"

valid_provenance = {
    "evidence_origin": evidence_provenance.LIVE_ORIGIN,
    "promotion_evidence_allowed": True,
    "strategy_fingerprint": current_fingerprint,
}
fingerprint, allowed = live_panel.validate_source_provenance(valid_provenance)
assert fingerprint == current_fingerprint
assert allowed is True

for invalid in [
    dict(valid_provenance, evidence_origin=evidence_provenance.BACKFILL_ORIGIN),
    dict(valid_provenance, promotion_evidence_allowed=False),
    dict(valid_provenance, strategy_fingerprint="wrong"),
]:
    try:
        live_panel.validate_source_provenance(invalid)
        raise AssertionError("invalid live provenance should have failed")
    except ValueError:
        pass

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    signals_path = root / "signals.csv"
    provenance_path = root / "provenance.json"
    output_path = root / "panel.csv"
    manifest_path = root / "panel_manifest.json"
    signals.to_csv(signals_path, index=False)
    provenance_path.write_text(json.dumps(valid_provenance), encoding="utf-8")

    dates = pd.bdate_range("2026-06-25", periods=12)
    synthetic_prices = {
        "1001": pd.DataFrame({
            "Date": dates,
            "Open": [100 + i for i in range(len(dates))],
            "High": [101 + i for i in range(len(dates))],
            "Low": [99 + i for i in range(len(dates))],
            "Close": [100.5 + i for i in range(len(dates))],
            "Volume": [1_000_000] * len(dates),
            "RawClose": [100.5 + i for i in range(len(dates))],
        }),
        "2001": pd.DataFrame({
            "Date": dates,
            "Open": [200 + i for i in range(len(dates))],
            "High": [201 + i for i in range(len(dates))],
            "Low": [199 + i for i in range(len(dates))],
            "Close": [200.5 + i for i in range(len(dates))],
            "Volume": [2_000_000] * len(dates),
            "RawClose": [200.5 + i for i in range(len(dates))],
        }),
    }

    original_download = live_panel.historical_backfill.download_price_history
    original_hashes = live_panel.replay.live_state_hashes
    live_panel.historical_backfill.download_price_history = lambda members, start, end, batch_size=50: (synthetic_prices, [])
    live_panel.replay.live_state_hashes = lambda: {"data/state.csv": "unchanged"}
    try:
        result = live_panel.build_live_panel(
            str(signals_path),
            str(provenance_path),
            str(output_path),
            str(manifest_path),
            batch_size=10,
        )
    finally:
        live_panel.historical_backfill.download_price_history = original_download
        live_panel.replay.live_state_hashes = original_hashes

    assert output_path.exists()
    assert manifest_path.exists()
    panel = pd.read_csv(output_path, dtype={"code": str})
    assert set(panel["code"]) == {"1001", "2001"}
    assert len(panel) == 24
    manifest = result["manifest"]
    assert manifest["source_evidence_origin"] == evidence_provenance.LIVE_ORIGIN
    assert manifest["promotion_evidence_allowed"] is True
    assert manifest["strategy_fingerprint"] == current_fingerprint
    assert manifest["requested_symbol_count"] == 2
    assert manifest["downloaded_symbol_count"] == 2
    assert manifest["production_state_mutations"] == []
    assert manifest["research_only"] is True

print("live execution panel validation passed")
