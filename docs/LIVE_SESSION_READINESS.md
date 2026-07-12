# Live Session Readiness

Last updated: 2026-07-13

## Purpose

`Live Session Readiness` determines whether one completed `Daily Momentum Report` run is suitable for:

- prospective Forward Evidence;
- prospective Daily Research Priority 5/10/20-session outcome ingestion;
- the ten-session production audit.

It validates the exact upstream Actions artifact by run ID. It does not inspect a later repository snapshot and does not repair or mutate production state.

## Trigger

Workflow: `.github/workflows/live-session-readiness.yml`

The workflow runs after every completed `Daily Momentum Report`.

- validation job: synthetic contract tests;
- inspection job: exact upstream artifact download and signed readiness report;
- permissions: `actions: read`, `contents: read`;
- output: Actions artifact only;
- repository writes: none.

## Outputs

- `live_session_readiness.json`;
- `live_session_readiness.md`;
- build log.

The JSON includes:

- source workflow run and commit;
- report date;
- strategy fingerprint;
- complete artifact fingerprint;
- every gate and its metrics;
- Forward Evidence eligibility;
- Priority Outcome ingestion eligibility;
- `readiness_fingerprint`;
- full `status_sha256`.

## Readiness states

| State | Meaning |
|---|---|
| `PASS` | Every required production, evidence, quality, focus, email, and coverage gate passed |
| `REVIEW_REQUIRED` | No critical failure exists, but an operational warning requires attention |
| `FAIL` | The run cannot be accepted as a complete live evidence source |

The script exits non-zero only for `FAIL`. `REVIEW_REQUIRED` remains visible without hiding otherwise valid evidence prerequisites.

## Critical gates

### Upstream workflow

The upstream conclusion must be `success`.

### Artifact completeness

Required files:

- `daily_report.xlsx`;
- `operations_heartbeat.json`;
- `strategy_fingerprint.json`;
- `momentum_daily_ranking.csv`;
- `evidence_stamp_audit.json`;
- `recovery_snapshot_audit.json`;
- `state_maintenance.json`;
- `email_delivery_receipt.json`.

### Production heartbeat

The heartbeat must show:

- workflow status `SUCCESS`;
- full state update executed;
- report date on or after 2026-07-13.

### Strategy fingerprint

The governed fingerprint must be a non-empty SHA-256 value.

### Workbook contract

Required sheets:

- Summary;
- Momentum Top100;
- Action Priority;
- Daily Action List;
- Data Quality;
- Research Evidence.

### Ranking history

For the report date:

- at least one ranking row;
- no duplicate `date + code` rows;
- every row matches the governed strategy fingerprint.

### Evidence stamp

- evidence audit present;
- strategy fingerprint matches;
- stamped ranking rows greater than zero.

### Recovery and maintenance

- recovery snapshot status `SEALED`;
- recovery snapshot complete;
- maintenance validation `PASS`;
- maintenance failures zero.

### Data Quality

Every Momentum Top100 row must have:

- required quality fields;
- grade A, B, C, or D.

### Daily Research Focus

- Action Priority rows exist;
- A count at most five;
- Daily Action List count at most ten;
- no Data Quality C/D row remains in A;
- `why_today`, `what_changed`, `risk_summary`, and `next_research_questions` are complete.

### Priority Outcome ingestion

The exact artifact must produce at least one eligible prospective decision through the registered Priority Outcome policy.

### Forward Evidence prerequisites

- report date at or after the registered cutoff;
- valid strategy fingerprint;
- report-date ranking rows;
- ranking fingerprint consistency.

## Review-required gates

### SMTP acceptance

- `SMTP_ACCEPTED`: PASS;
- `SKIPPED_SECRETS_MISSING`: REVIEW_REQUIRED;
- invalid or failed receipt: FAIL.

The claim is limited to SMTP acceptance. Final inbox delivery is never claimed.

### Market-data coverage

- current-day price ratio at least 98%: PASS;
- lower or unavailable: REVIEW_REQUIRED.

A coverage warning does not silently remove data. It remains visible for the ten-session operating audit.

## Relationship to other publishers

This workflow verifies that the daily run is eligible. It does not wait for or replace downstream publishers.

After a passing or review-required eligible run:

- Daily Production Audit appends its run row;
- Email Delivery Audit appends its SMTP receipt row;
- Daily Priority Outcomes ingests decisions;
- Forward Evidence can later use the strategy-stamped ranking date.

Each downstream workflow retains its own signature and narrow write boundary.

## Governance

The readiness report always records:

- SMTP acceptance only;
- inbox delivery claimed false;
- automatic score change false;
- automatic weight change false;
- automatic strategy change false;
- automatic priority-rule change false;
- production-state mutations empty;
- research only true.

A PASS authorizes evidence accumulation only. It does not authorize a production strategy change.
