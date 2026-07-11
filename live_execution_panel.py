"""Build an isolated price panel for fingerprinted live-forward signals.

The panel is downloaded only for codes present in the current-strategy replay
signals. It is written under output/replay, never under data/, and never mutates
production state. Promotion eligibility is inherited from the sealed live
ranking provenance and is rechecked later by evidence_provenance.py.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import evidence_provenance
import historical_backfill
import historical_price_panel
import main
import replay

PANEL_VERSION = "2026-07-11-live-execution-panel-v1"
DEFAULT_SIGNALS = "output/replay/replay_signals.csv"
DEFAULT_PROVENANCE = "output/replay/evidence_provenance.json"
DEFAULT_OUTPUT = "output/replay/live_execution_price_panel.csv"
DEFAULT_MANIFEST = "output/replay/live_execution_price_panel_manifest.json"


def load_json(path: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON manifest must be an object: {path}")
    return payload


def load_signals(path: str) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(target, dtype={"code": str})
    required = {"signal_date", "code", "name", "sector33"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"replay signals missing columns: {missing}")
    frame["code"] = frame["code"].map(main.normalize_code)
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce")
    return frame.dropna(subset=["signal_date", "code"]).copy()


def signal_members(signals: pd.DataFrame) -> list[historical_backfill.UniverseMember]:
    members: list[historical_backfill.UniverseMember] = []
    seen: set[str] = set()
    for _, row in signals.sort_values(["code", "signal_date"]).iterrows():
        code = main.normalize_code(row.get("code"))
        if not code or code in seen:
            continue
        seen.add(code)
        members.append(
            historical_backfill.UniverseMember(
                code=code,
                name=str(row.get("name", "") or ""),
                market="",
                sector33=main.normalize_sector33(row.get("sector33", "")),
            )
        )
    return members


def validate_source_provenance(payload: dict[str, Any]) -> tuple[str, bool]:
    origin = str(payload.get("evidence_origin", ""))
    fingerprint = str(payload.get("strategy_fingerprint", ""))
    promotion_allowed = payload.get("promotion_evidence_allowed") is True
    if origin != evidence_provenance.LIVE_ORIGIN:
        raise ValueError(f"live execution panel requires {evidence_provenance.LIVE_ORIGIN}, got {origin}")
    if not promotion_allowed:
        raise ValueError("source live evidence is not promotion-eligible")
    current = evidence_provenance.current_strategy_fingerprint()
    if fingerprint != current:
        raise ValueError("source live evidence strategy fingerprint does not match current code")
    return fingerprint, promotion_allowed


def build_live_panel(
    signals_path: str,
    provenance_path: str,
    output_path: str,
    manifest_path: str,
    batch_size: int = 50,
    future_buffer_days: int = 7,
) -> dict[str, Any]:
    before = replay.live_state_hashes()
    signals = load_signals(signals_path)
    provenance = load_json(provenance_path)
    fingerprint, promotion_allowed = validate_source_provenance(provenance)
    members = signal_members(signals)

    if signals.empty or not members:
        panel = historical_price_panel.flatten_price_panel([], {})
        errors: list[dict[str, Any]] = []
        requested_start = ""
        requested_end = ""
    else:
        earliest_signal = pd.Timestamp(signals["signal_date"].min()).date()
        latest_signal = pd.Timestamp(signals["signal_date"].max()).date()
        requested_start_date = earliest_signal - timedelta(days=7)
        today = datetime.now(timezone.utc).date()
        requested_end_date = max(today, latest_signal + timedelta(days=max(future_buffer_days, 1)))
        prices, errors = historical_backfill.download_price_history(
            members,
            requested_start_date,
            requested_end_date,
            batch_size=max(int(batch_size), 1),
        )
        panel = historical_price_panel.flatten_price_panel(members, prices)
        requested_start = requested_start_date.isoformat()
        requested_end = requested_end_date.isoformat()

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(target, index=False)
    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    downloaded_codes = set(panel.get("code", pd.Series(dtype=str)).astype(str)) if not panel.empty else set()
    payload = {
        "panel_version": PANEL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "source_evidence_origin": provenance.get("evidence_origin", ""),
        "source_provenance_sha256": historical_backfill.sha256_file(provenance_path),
        "strategy_fingerprint": fingerprint,
        "source_promotion_evidence_allowed": promotion_allowed,
        "promotion_evidence_allowed": promotion_allowed,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "signal_row_count": len(signals),
        "requested_symbol_count": len(members),
        "downloaded_symbol_count": len(downloaded_codes),
        "panel_row_count": len(panel),
        "first_panel_date": str(panel["date"].min()) if not panel.empty else "",
        "last_panel_date": str(panel["date"].max()) if not panel.empty else "",
        "download_error_count": len(errors),
        "production_state_mutations": mutations,
        "research_only": True,
    }
    manifest = Path(manifest_path)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"manifest": payload, "panel": panel, "errors": errors}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build isolated live-forward execution prices")
    parser.add_argument("--signals", default=DEFAULT_SIGNALS)
    parser.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    result = build_live_panel(
        args.signals,
        args.provenance,
        args.output,
        args.manifest,
        args.batch_size,
    )
    manifest = result["manifest"]
    if args.strict:
        if manifest["production_state_mutations"]:
            raise RuntimeError(f"production state mutated: {manifest['production_state_mutations']}")
        if manifest["requested_symbol_count"] and manifest["panel_row_count"] == 0:
            raise RuntimeError("live execution price panel is empty")
        requested = int(manifest["requested_symbol_count"])
        downloaded = int(manifest["downloaded_symbol_count"])
        if requested and downloaded / requested < 0.80:
            raise RuntimeError(f"less than 80% of signal symbols downloaded: {downloaded}/{requested}")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
