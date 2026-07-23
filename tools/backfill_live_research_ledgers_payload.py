"""Expand the one-time exact-artifact live research ledger backfill.

The payload was generated from the exact Daily Momentum Report artifacts for
2026-07-13 through 2026-07-23. It writes research ledgers only and cannot mutate
production ranking, strategy, paper state, or live orders.
"""
from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

CHUNK_GLOB = "ledger_payload_chunk_*.txt"


def main() -> int:
    root = Path(__file__).resolve().parent
    chunks = sorted(root.glob(CHUNK_GLOB))
    if len(chunks) != 6:
        raise SystemExit(f"expected 6 payload chunks, found {len(chunks)}")
    encoded = "".join(path.read_text(encoding="utf-8").strip() for path in chunks)
    payload = json.loads(gzip.decompress(base64.b64decode(encoded)).decode("utf-8"))
    allowed = {
        "research/operations/daily_production_audit.csv",
        "research/operations/daily_production_audit_status.json",
        "research/evidence/live_session_eligibility.csv",
        "research/evidence/live_session_eligibility_status.json",
        "research/priority_outcomes/daily_research_decisions.csv",
        "research/priority_outcomes/daily_research_outcomes.csv",
        "research/priority_outcomes/latest_calibration.json",
        "research/priority_outcomes/latest_calibration.md",
    }
    if set(payload) != allowed:
        raise SystemExit("payload path allowlist mismatch")
    repository = root.parent
    for relative, content in payload.items():
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    print(json.dumps({
        "written": sorted(payload),
        "production_state_mutations": [],
        "automatic_strategy_change": False,
        "automatic_priority_rule_change": False,
        "automatic_paper_rule_change": False,
        "live_orders": False,
        "research_only": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
