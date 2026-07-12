# Daily Production Audit

This directory contains the isolated audit ledger for Issue #68.

## Purpose

Record every completed `Daily Momentum Report` workflow run and build an auditable ten-market-session reliability status without changing the screener, paper engine, production data, score weights, or execution rules.

## Files

### `daily_production_audit.csv`

Append-only run ledger. The natural key is `workflow_run_id`; reprocessing the same run replaces that row rather than duplicating it.

It records:

- upstream conclusion, event, commit, branch, timestamps, and duration;
- intended JST date and report date;
- workbook, heartbeat, fingerprint, evidence-stamp, recovery, and maintenance presence;
- market-data freshness and current-day ratio;
- ranking row count, date rows, duplicate count, and fingerprint consistency;
- market-temperature duplicate count;
- workbook universe, scan, success, failure, and retrieval-coverage values when available;
- failure-notification presence;
- complete artifact fingerprint;
- final audit status and explicit failure reasons.

Success and failure workflow runs are both retained. An absent or incomplete artifact is recorded as a failed audit row rather than silently skipped.

### `daily_production_audit_status.json`

Signed compact rolling status derived from the CSV.

It contains:

- number of audited workflow runs;
- scheduled success rate;
- latest ten distinct full state-update market sessions;
- remaining sessions until the ten-session gate;
- minimum and average retrieval coverage;
- audit and duplicate failures;
- session-level strategy fingerprints;
- `audit_fingerprint` and full `status_sha256`;
- explicit prohibition on production strategy and weight mutations.

States:

- `ACCUMULATING`: fewer than ten distinct full state-update market sessions;
- `PASS`: ten sessions exist and all current gates pass;
- `REVIEW_REQUIRED`: ten sessions exist but at least one reliability or coverage gate fails.

## Workflow

`.github/workflows/daily-operations-audit.yml` is triggered by completion of `Daily Momentum Report`.

Security and isolation:

- checks out current `main`, never the upstream run branch;
- downloads the exact upstream run artifact by run ID;
- top-level permissions are read-only;
- only the publish job has `contents: write`;
- the publish job stages only the two files in this directory;
- no email secret is used;
- no production state, strategy, configuration, ranking, paper, or execution file is written;
- audit commits do not retrigger the daily production workflow.

## Ten-session gate

A market session counts only when the upstream run completed successfully and the heartbeat confirms a full state update with a report date.

Current automated gates:

- upstream workflow success;
- daily workbook present;
- heartbeat present and successful;
- non-empty strategy fingerprint;
- evidence audit present and fingerprint-consistent;
- stamped ranking rows greater than zero;
- recovery snapshot sealed and complete;
- state-maintenance validation passed with zero failures;
- ranking history present for the report date;
- ranking strategy fingerprint matches;
- no duplicate `date + code` ranking rows;
- no duplicate market-temperature dates;
- minimum retrieval coverage of 98% when the metric is available.

The broader operational targets remain defined in `docs/KPI_DICTIONARY.md` and the recovery procedure remains in `docs/OPERATIONS_RUNBOOK.md`.

## Manual review

`PASS` means the automated ten-session gates passed. It does not authorize a strategy change or production release.

Any incident, missing metric, stale-data concern, email-delivery problem, or recovery anomaly should still be reviewed and recorded in Issue #68 and the future monthly operations review under Issue #74.
