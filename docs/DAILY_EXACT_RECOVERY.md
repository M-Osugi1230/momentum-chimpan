# Daily Exact Recovery Verification

Last updated: 2026-07-13

## Purpose

Each full `Daily Momentum Report` run must prove that the exact state snapshot it just sealed can be restored into an isolated sandbox before the workflow persists production state.

The weekly Recovery Drill remains useful for recurring operational checks. The daily exact drill answers a stricter question: can this source run's own snapshot, identified by date and manifest SHA-256, be restored without touching production paths?

## Source binding

`daily_recovery_drill.py` reads `output/recovery_snapshot_audit.json` generated earlier in the same daily workflow.

For a state-updating run, the audit must provide:

- `status: SEALED`;
- `complete: true`;
- the exact snapshot date;
- the exact `snapshot_manifest_sha256`.

The drill opens only `data/state_snapshots/<snapshot_date>` and requires its manifest SHA-256 to match the source audit exactly. Selecting a different valid snapshot is not allowed.

## Verification

The exact snapshot is validated for:

- all governed state files present;
- file SHA-256 values matching the sealed manifest;
- readable CSV structure;
- row and column shape matching the manifest;
- report-date ranking rows carrying the expected strategy fingerprint;
- byte-identical copies in an isolated sandbox;
- unchanged hashes for every production state path before and after the drill.

The governed state set remains:

- ranking history;
- market-temperature history;
- sector-leader signal history;
- paper portfolio;
- paper trade history;
- paper equity history.

## Outcomes

| Status | Meaning |
|---|---|
| `PASS` | Exact date and manifest matched; all state files restored and verified in the sandbox |
| `FAIL` | Audit, exact snapshot, manifest hash, file integrity, sandbox copy, or production non-mutation check failed |
| `SKIPPED_NO_STATE_UPDATE` | The source run intentionally did not update production state, such as a non-trading or stale-data run |

`SKIPPED_NO_STATE_UPDATE` passes the operational workflow gate but is not evidence eligible.

## Daily workflow order

1. produce the daily report;
2. write the operational heartbeat;
3. stamp exact ranking provenance;
4. seal the recoverable state snapshot;
5. run exact recovery into `output/recovery/sandbox`;
6. validate the signed drill manifest;
7. perform bounded state maintenance;
8. persist complete production state.

A failed drill prevents maintenance and persistence and enters the existing operational-failure notification path.

## Outputs

The exact daily operations artifact retains:

- `output/recovery/recovery_drill_manifest.json`;
- `output/recovery/recovery_snapshot_catalog.csv`;
- `output/recovery/recovery_plan.csv`;
- `output/recovery/recovery_restore_verification.csv`;
- `output/recovery/recovery_drill.xlsx`;
- `output/recovery/sandbox/`;
- `output/recovery_drill.log`.

The JSON manifest contains both:

- `drill_fingerprint`, signing the substantive result;
- `status_sha256`, signing the complete payload.

## Safety boundary

The drill is fixed to:

- `production_state_mutated: false`;
- `automatic_production_restore: false`;
- `manual_restore_only: true`;
- `sandbox_only: true`;
- `research_only: true`.

It never copies sandbox files back into production and never changes strategy, scores, weights, filters, priority rules, paper execution, or live orders.
