# Monthly Operations and Evidence Review

Last updated: 2026-07-13

## Purpose

The monthly review combines operational reliability, SMTP acceptance, data quality, daily research focus, prospective 5/10/20-session calibration, Forward Evidence, and strategy governance in one compact artifact.

It is a read-only management report. It does not change production state, Momentum scores, score weights, priority rules, paper execution, or live orders.

## Schedule

Workflow: `.github/workflows/monthly-operations-review.yml`

- runs on the first day of each month;
- reviews the previous calendar month by default;
- supports a manual `YYYY-MM` review month;
- uses `contents: read` only;
- requests the maximum 365-day artifact retention, while the current repository-level cap makes the effective retention 90 days;
- does not commit generated reports to the repository.

GitHub applies the lower of the workflow request and the repository retention limit. The first validated artifact expired after 90 days, so 90 days is the current operational retention contract unless the repository setting is increased.

## Outputs

The artifact contains:

- `monthly_review.json` — signed detailed source;
- `monthly_review_summary.csv` — one-row management summary;
- `monthly_review.md` — human-readable review.

The JSON includes a `review_fingerprint` covering substantive contents and a full `status_sha256` envelope.

## Canonical sources

The report reads only established sources:

- `research/operations/daily_production_audit.csv`;
- `research/operations/daily_production_audit_status.json`;
- `research/operations/email_delivery_audit.csv`;
- `research/operations/email_delivery_audit_status.json`;
- `data/momentum_daily_ranking.csv`;
- `data/operations_heartbeat.json`;
- `research/priority_outcomes/daily_research_decisions.csv`;
- `research/priority_outcomes/daily_research_outcomes.csv`;
- `research/priority_outcomes/latest_calibration.json`;
- `data/volume_component_forward_status.json`;
- `research/evidence_catalog.yaml`;
- `research/strategy_approvals.yaml`.

The report links each source to the exact commit reviewed.

## Review states

| State | Meaning |
|---|---|
| `ACCUMULATING` | The month does not yet contain audited production runs |
| `PASS` | No automated review reason requires attention |
| `REVIEW_REQUIRED` | At least one operational, quality, lookahead, duplicate, freshness, or governance issue requires human review |

A `PASS` does not authorize a production-rule change.

## Operations section

Metrics include:

- audited and scheduled run counts;
- scheduled workflow success rate;
- audit pass rate;
- report-generation rate;
- completion within the 30-minute SLO;
- average and maximum duration;
- average universe and scan counts;
- minimum and average price-retrieval coverage;
- stale or partial run count;
- ranking and Market Temperature duplicate rows;
- recovery snapshot sealing;
- state-maintenance validation;
- failure-notification coverage;
- incident and corrective-action status.

A failed run followed by a later passing run for the same report date is marked `RECOVERED_BY_LATER_PASS`. Otherwise, corrective action remains `NOT_RECORDED_IN_AUDIT_LEDGER`.

## SMTP acceptance section

The monthly review no longer infers email success from workflow or workbook success.

It reads the signed SMTP receipt audit and reports:

- receipt row count;
- valid-receipt rate;
- scheduled receipt count;
- scheduled email attempt count;
- scheduled SMTP accepted count;
- scheduled SMTP acceptance rate;
- scheduled secret-configuration skips;
- scheduled send failures.

The operational claim is limited to:

`SMTP_ACCEPTANCE_ONLY`

A successful receipt means the application observed no SMTP rejection. It does not prove final inbox delivery.

The report always states:

`NOT_OBSERVED_BY_SMTP_ACCEPTANCE_RECEIPT`

for final inbox delivery, spam placement, opening, and reading.

## Data Quality section

Metrics include:

- A/B/C/D row counts;
- A-or-B rate;
- current-date rate;
- possible corporate-action warnings;
- number of quality C/D rows remaining in priority A.

The final value must remain zero.

## Daily Research Focus section

Metrics include:

- A/B/C/Watch/Skip counts;
- decision dates;
- Daily Action List count;
- average and maximum action-list size;
- A-cap violation days;
- action-list-cap violation days;
- explanation completeness for `why_today`, `what_changed`, `risk_summary`, and `next_research_questions`.

Current limits remain:

- priority A: maximum five names per day;
- Daily Action List: maximum ten A/B names per day.

## 5/10/20-session calibration section

The monthly view reports outcomes whose decision date belongs to the review month.

It shows:

- COMPLETE/PENDING/error status counts;
- counts by 5/10/20-session horizon;
- A/B/C/Watch/Skip sample size;
- mean net return;
- mean TOPIX excess return;
- positive TOPIX-excess rate;
- deterministic bootstrap 95% confidence interval;
- small-sample warnings.

The report also displays the global prospective review gates from `latest_calibration.json`.

No priority-rule review is ready until A and B each have at least 30 completed outcomes and 20 distinct decision dates at all three horizons, with zero lookahead violations.

## Forward Evidence section

The review displays:

- registered study ID;
- current evidence status;
- sample adequacy;
- exact source run;
- 10- and 20-session baseline/tested outcome progress;
- paired-date progress;
- current statistical values when available.

Forward Evidence cannot automatically change the current 15-point volume-ratio weight.

## Strategy governance section

The review reads the canonical evidence catalog and manual approval ledger.

It reports:

- current production weight;
- current research decision;
- historical consensus;
- governing study;
- automatic-change flags;
- approved strategy changes recorded during the month.

The expected monthly strategy-change count is zero unless an explicit, hash-bound human approval was recorded.

## Known measurement gaps

The review explicitly retains these limitations:

- SMTP acceptance is observable, but final inbox delivery, spam placement, opening, and reading are not;
- sector outcome comparison is a same-date decision-cohort proxy rather than a licensed sector index;
- small samples are not validated evidence of priority quality;
- monthly artifacts currently expire after 90 days because of the repository retention cap.

Unknown or unavailable measurements remain blank or labeled rather than being converted into success.

## Governance

The monthly workflow has no repository write permission and does not use email credentials.

The report always records:

- `production_state_mutations: []`;
- automatic score change: false;
- automatic weight change: false;
- automatic strategy change: false;
- automatic priority-rule change: false;
- manual review required: true;
- research only: true.
