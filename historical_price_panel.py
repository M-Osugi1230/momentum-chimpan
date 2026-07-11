"""Build an isolated daily adjusted price panel for historical execution research.

The panel uses the same current-listed universe as historical_backfill and
inherits its survivorship/delisting bias. It is an artifact-only research input
and never mutates production state.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import historical_backfill
import main
import replay

PANEL_VERSION = "2026-07-11-historical-price-panel-v1"
DEFAULT_HISTORY = "output/backfill/historical_ranking.csv"
DEFAULT_BACKFILL_MANIFEST = "output/backfill/backfill_manifest.json"
DEFAULT_OUTPUT = "output/backfill/historical_price_panel.csv"
DEFAULT_MANIFEST = "output/backfill/historical_price_panel_manifest.json"


def load_backfill_manifest(path: str) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("promotion_evidence_allowed") is not False:
        raise ValueError("price panel requires a non-promotable backfill manifest")
    if payload.get("universe_bias") != "CURRENT_LIST_ONLY_SURVIVORSHIP_AND_DELISTING_BIAS":
        raise ValueError("price panel requires the declared current-universe bias")
    return payload


def selected_members(history_path: str, cache_path: str, config_path: str) -> list[historical_backfill.UniverseMember]:
    history = pd.read_csv(history_path, dtype={"code": str})
    if "code" not in history.columns:
        raise ValueError("historical ranking is missing code")
    codes = set(history["code"].map(main.normalize_code))
    config = historical_backfill.load_config(config_path)
    universe = historical_backfill.load_current_universe(cache_path, config)
    members = [member for member in universe if member.code in codes]
    missing = sorted(codes - {member.code for member in members})
    if missing:
        raise ValueError(f"selected ranking codes missing from JPX cache: {missing[:10]}")
    return members


def flatten_price_panel(
    members: list[historical_backfill.UniverseMember],
    prices: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    metadata = {member.code: member for member in members}
    frames: list[pd.DataFrame] = []
    for code, frame in prices.items():
        if frame.empty or code not in metadata:
            continue
        member = metadata[code]
        work = frame.copy()
        work["date"] = pd.to_datetime(work["Date"], errors="coerce").dt.date.astype("string")
        work["code"] = code
        work["name"] = member.name
        work["sector33"] = member.sector33
        work["adjusted_open"] = pd.to_numeric(work["Open"], errors="coerce")
        work["adjusted_high"] = pd.to_numeric(work["High"], errors="coerce")
        work["adjusted_low"] = pd.to_numeric(work["Low"], errors="coerce")
        work["adjusted_close"] = pd.to_numeric(work["Close"], errors="coerce")
        work["raw_close"] = pd.to_numeric(work["RawClose"], errors="coerce")
        work["volume"] = pd.to_numeric(work["Volume"], errors="coerce")
        work["raw_trading_value"] = work["raw_close"] * work["volume"]
        frames.append(work[[
            "date", "code", "name", "sector33",
            "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close",
            "raw_close", "volume", "raw_trading_value",
        ]])
    if not frames:
        return pd.DataFrame(columns=[
            "date", "code", "name", "sector33",
            "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close",
            "raw_close", "volume", "raw_trading_value",
        ])
    panel = pd.concat(frames, ignore_index=True)
    panel = panel.dropna(subset=["date", "code", "adjusted_open", "adjusted_close"])
    panel = panel[(panel["adjusted_open"] > 0) & (panel["adjusted_close"] > 0)]
    return panel.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"]).reset_index(drop=True)


def build_panel(
    history_path: str,
    backfill_manifest_path: str,
    cache_path: str,
    config_path: str,
    output_path: str,
    manifest_path: str,
    batch_size: int,
) -> dict[str, Any]:
    before = replay.live_state_hashes()
    source = load_backfill_manifest(backfill_manifest_path)
    members = selected_members(history_path, cache_path, config_path)
    start = date.fromisoformat(str(source["requested_start"]))
    end = date.fromisoformat(str(source["requested_end"]))
    prices, errors = historical_backfill.download_price_history(members, start, end, batch_size=batch_size)
    panel = flatten_price_panel(members, prices)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(target, index=False)
    after = replay.live_state_hashes()
    mutations = [path for path in before if before[path] != after.get(path, "")]
    payload = {
        "panel_version": PANEL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "production_app_version": main.APP_VERSION,
        "execution_mode": main.EXECUTION_MODE,
        "source_backfill_manifest": backfill_manifest_path,
        "source_backfill_manifest_sha256": historical_backfill.sha256_file(backfill_manifest_path),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "selected_symbol_count": len(members),
        "downloaded_symbol_count": panel["code"].nunique() if not panel.empty else 0,
        "panel_row_count": len(panel),
        "first_panel_date": str(panel["date"].min()) if not panel.empty else "",
        "last_panel_date": str(panel["date"].max()) if not panel.empty else "",
        "download_error_count": len(errors),
        "universe_bias": source["universe_bias"],
        "promotion_evidence_allowed": False,
        "production_state_mutations": mutations,
        "research_only": True,
    }
    Path(manifest_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"manifest": payload, "panel": panel, "errors": errors}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build isolated daily prices for execution research")
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--backfill-manifest", default=DEFAULT_BACKFILL_MANIFEST)
    parser.add_argument("--cache", default="data/jpx_list_cache.csv")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    result = build_panel(
        args.history,
        args.backfill_manifest,
        args.cache,
        args.config,
        args.output,
        args.manifest,
        args.batch_size,
    )
    manifest = result["manifest"]
    if args.strict:
        if manifest["production_state_mutations"]:
            raise RuntimeError(f"production state mutated: {manifest['production_state_mutations']}")
        if manifest["panel_row_count"] == 0:
            raise RuntimeError("daily price panel is empty")
        if manifest["downloaded_symbol_count"] < max(int(manifest["selected_symbol_count"] * 0.50), 1):
            raise RuntimeError("less than 50% of selected symbols were downloaded")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
