# Live Session Eligibility Ledger

Last updated: 2026-07-13

## Purpose

The Live Session Eligibility Ledger binds each completed `Daily Momentum Report` run to the signed exact-artifact readiness decision introduced in PR #112.

It exists because weekly Forward Evidence reads committed ranking history rather than one daily Actions artifact. The ledger provides a deterministic bridge without depending on the ordering of parallel `workflow_run` jobs.

The ledger is research-only. It does not change scores, weights, filters, priority rules, paper execution, production state, or live orders.

## Canonical files

| File | Purpose |
|---|---|
| `research/evidence/live_session_eligibility.csv` | One idempotent record per source workflow run |
| `research/evidence/live_session_eligibility_status.json` | Signed compact ledger status |
| `live_session_eligibility.py` | Exact-artifact record builder, validator, and publisher contract |
| `forward_eligible_history.py` | Weekly Forward Evidence date and row-hash filter |
| `.github/workflows/live-session-eligibility-ledger.yml` | Isolated eligibility publisher |

## Daily record contract

After every completed `Daily Momentum Report`, the publisher downloads that exact run's `momentum-operations-*` artifact and rebuilds Live Session Readiness from:

- source run ID and URL;
- upstream conclusion and event;
- source head commit;
- source creation and completion timestamps;
- exact artifact files.

The row preserves:

- report date;
- governed strategy fingerprint;
- `PASS`, `REVIEW_REQUIRED`, or `FAIL` readiness state;
- Forward Evidence and Priority Outcome eligibility flags;
- artifact fingerprint;
- readiness fingerprint and full status SHA-256;
- report-date ranking row count;
- canonical report-date ranking row SHA-256;
- critical-failure and review-warning counts.

Failed or incomplete daily runs are recorded with eligibility false. They remain visible rather than disappearing from the operational history.

The natural key is `source_run_id`. Reprocessing the same run replaces that row instead of creating a duplicate.

## Canonical ranking-date hash

The report-date ranking hash covers only rows for one report date from the exact daily artifact.

Before hashing:

- columns are sorted by name;
- stock codes are normalized to four digits;
- rows are stably sorted by code and rank;
- missing values, booleans, integers, and floating-point values receive deterministic text representations;
- the report date and column list are included in the canonical payload.

This allows the weekly publisher to prove that committed ranking rows are the same rows that passed the exact-artifact readiness decision. A later mutation, deletion, or replacement of those rows produces a different hash and excludes the date.

## Weekly Forward Evidence filter

`forward_eligible_history.py` replaces direct strategy-fingerprint-only preparation for the volume-component Forward Evidence workflow.

A ranking date is accepted only when:

1. committed ranking rows carry the current governed strategy fingerprint;
2. the ledger contains an eligible Forward Evidence row for the same date and fingerprint;
3. the canonical SHA-256 of the committed report-date rows exactly matches the ledger record.

Dates are excluded with an explicit reason:

- `NO_ELIGIBLE_LEDGER_ROW`;
- `RANKING_DATE_SHA256_MISMATCH`.

The generated provenance manifest records:

- ledger path and file SHA-256;
- source and filtered-history SHA-256 values;
- verified dates and source run IDs;
- excluded dates and reasons;
- verified and excluded date counts;
- the current strategy fingerprint;
- disabled automatic strategy and weight changes;
- an empty production-state mutation list.

## Publisher permissions and persistence

The eligibility workflow has read-only repository permissions by default. Only the isolated publish job receives `contents: write`.

Its persistence allowlist contains exactly:

- `research/evidence/live_session_eligibility.csv`;
- `research/evidence/live_session_eligibility_status.json`.

It does not stage ranking history, configuration, strategy code, paper files, outcome files, email credentials, or other production state.

The weekly Forward Evidence workflow remains fully read-only and artifact-only.

## Status integrity

The signed JSON status reports:

- total source runs;
- eligible Forward Evidence runs and dates;
- latest eligible report date;
- failed-readiness count;
- review-required count;
- duplicate source-run count;
- all automatic-change flags fixed to false;
- an empty production-state mutation list.

`ledger_fingerprint` signs the substantive status, and `status_sha256` signs the complete payload including generation time and ledger fingerprint.

## Initial state

Before the first eligible live session, the committed state is intentionally:

- ledger state `EMPTY`;
- zero source runs;
- zero eligible dates;
- zero automatic changes;
- no production mutations.

This is an expected preregistration state, not a failure.

## Limitations

- the ledger proves exact artifact eligibility and ranking-row identity; it does not claim investment performance;
- `REVIEW_REQUIRED` can remain evidence-eligible only where the registered Live Session Readiness contract explicitly allows it;
- Final Forward Evidence conclusions still require the registered sample, paired-date, stability, significance, and manual-review gates;
- completing those gates never activates a production weight automatically.
