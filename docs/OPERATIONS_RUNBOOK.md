# Momentum Chimpan Operations Runbook

Last updated: 2026-07-12

## Purpose

This runbook defines how to verify, diagnose, recover, and audit the daily Momentum Chimpan production workflow without relying on chat history.

## Normal schedule

- Workflow: `Daily Momentum Report`
- File: `.github/workflows/daily.yml`
- Schedule: weekdays at 07:45 UTC / 16:45 JST
- Manual execution: `workflow_dispatch`
- Production entrypoint: `python daily_runner.py`
- Full run: `max_symbols=0`
- Limited-symbol runs are verification only and must not persist production state.

## Healthy-run checklist

A production run is healthy only when all required gates succeed:

1. checkout and dependency installation;
2. governed strategy fingerprint snapshot;
3. full report execution;
4. workbook generation;
5. operational heartbeat generation;
6. ranking/report strategy-stamp verification;
7. sealed recoverable state snapshot;
8. state validation and retention maintenance;
9. production-state persistence;
10. artifact upload;
11. no final operational gate failure.

Email delivery should also be checked, but a delivery problem must be distinguished from a data-generation failure.

## Expected outputs

### Production state

- `data/momentum_daily_ranking.csv`
- `data/market_temperature.csv`
- `data/sector_leader_signal_history.csv`
- `data/paper_portfolio.csv`
- `data/paper_trade_history.csv`
- `data/paper_equity_history.csv`
- `data/execution_audit.csv`
- `data/operations_heartbeat.json`
- `data/strategy_fingerprint.json`
- `data/state_snapshots/**`
- `data/jpx_list_cache.csv`

### Run artifact

- `output/daily_report.xlsx`
- `output/run.log`
- `output/state_maintenance.json`
- `output/evidence_stamp_audit.json`
- `output/recovery_snapshot_audit.json`
- `output/ops_notification.txt`, when applicable
- copies of the current persisted state for diagnosis

## First-response procedure

### 1. Identify the failed stage

Use the workflow step result and `output/ops_notification.txt`.

The standard stage names are:

- `strategy-fingerprint`
- `report`
- `heartbeat`
- `evidence-stamp`
- `recovery-snapshot-seal`
- `state-maintenance`
- `state-persistence`

### 2. Do not force persistence

If any required upstream gate failed, do not manually commit partially updated production CSV/JSON files.

The correct response is to repair or rerun from the last coherent state.

### 3. Preserve evidence

Before rerunning or modifying code:

- retain the failed workflow artifact;
- record run ID, run URL, event type, commit SHA, and strategy fingerprint when available;
- preserve the exact error log;
- note whether email was sent;
- note whether any repository state was committed.

### 4. Classify the incident

| Category | Examples |
|---|---|
| External data | JPX unavailable, yfinance errors, widespread stale prices |
| Code or dependency | import error, incompatible package, calculation exception |
| Governance | fingerprint mismatch, unregistered strategy change |
| State integrity | duplicate keys, corrupted CSV, snapshot hash mismatch |
| Git persistence | rebase conflict, push failure, concurrent update |
| Delivery | Gmail authentication or recipient failure |
| Capacity | timeout, rate limit, Actions quota, excessive runtime |

## Stage-specific response

### Strategy fingerprint failure

1. Compare `config.yaml`, governed strategy files, and the expected fingerprint.
2. Confirm whether a recent PR intentionally changed strategy behavior.
3. If no registered change exists, treat the mismatch as a blocker.
4. Do not run production with an invented or manually overridden fingerprint.

### Report failure

1. Inspect `output/run.log`.
2. Determine whether the failure is universe retrieval, price retrieval, calculation, workbook, paper state, or email.
3. Check whether the issue affects one symbol or a material share of the universe.
4. For broad external failure, prefer a later full rerun over accepting incomplete output.
5. For an isolated symbol failure, confirm the report records the error and coverage remains above the documented gate.

### Heartbeat failure

1. Confirm `output/daily_report.xlsx` exists and opens.
2. Confirm required Summary fields are present.
3. Rebuild heartbeat only from the exact generated report.
4. Do not mark a failed or incomplete report as healthy.

### Evidence-stamp failure

1. Compare the ranking strategy stamp, report fingerprint, and `data/strategy_fingerprint.json`.
2. Confirm the ranking history was produced by the current governed strategy.
3. Do not merge histories from incompatible fingerprints.
4. Restore or rerun from a coherent snapshot if necessary.

### Recovery-snapshot failure

1. Inspect `output/recovery_snapshot_audit.json`.
2. Confirm all required state files exist or are validly empty.
3. Confirm hashes and fingerprint compatibility.
4. Do not persist a state that cannot be sealed and restored.

### Maintenance failure

1. Inspect `output/state_maintenance.json`.
2. Check duplicate keys, invalid dates, snapshot consistency, and retention boundaries.
3. Repair the state through a reviewed fix or restore from the latest sealed snapshot.

### State-persistence failure

1. Confirm all upstream gates succeeded.
2. Check whether another workflow or publisher updated `main`.
3. Fetch and rebase using the existing bounded retry process.
4. Never force-push `main`.
5. If retries fail, keep the artifact and restore the exact coherent state in a reviewed recovery PR.

### Email-delivery failure

1. Confirm report generation and state gates independently.
2. Verify `EMAIL_FROM`, `EMAIL_TO`, and `EMAIL_APP_PASSWORD` availability without exposing values.
3. Retry delivery only from the exact generated report and body when possible.
4. Record the failure in the monthly operations review.

## Full-rerun policy

A full manual rerun is appropriate when:

- the scheduled run failed before persistence;
- external data availability recovered;
- the same governed code and strategy fingerprint are used;
- no later successful production run exists for that market date.

A rerun must use `max_symbols=0`.

Do not replace a complete later run with a partial or limited-symbol run.

## Duplicate-run policy

The natural ranking key is `date + code`.

When a market date is rerun:

- replace or deduplicate according to the existing application contract;
- retain one canonical row per date and code;
- verify market-temperature and paper-state consistency;
- confirm repeated execution did not double-count trades or equity changes.

## Recovery procedure

1. Identify the latest sealed snapshot compatible with the target strategy fingerprint.
2. Validate the snapshot audit and hashes.
3. Restore only the documented production-state files.
4. Re-run state maintenance and validation.
5. Run a limited verification without persistence if useful.
6. Run a full production workflow.
7. Confirm heartbeat, evidence stamp, recovery seal, and persistence.
8. Document the incident and the restored snapshot identifier.

## Ten-session production audit

Issue #68 tracks the first formal audit.

For each full market session record:

| Field | Required |
|---|---|
| Market date | Yes |
| Workflow run ID and URL | Yes |
| Commit SHA | Yes |
| Strategy fingerprint | Yes |
| Start and completion time | Yes |
| Full universe count | Yes |
| Successful price count | Yes |
| Failed price count | Yes |
| Retrieval coverage | Yes |
| Workbook generated | Yes |
| Email sent | Yes |
| Heartbeat passed | Yes |
| Evidence stamp passed | Yes |
| Snapshot sealed | Yes |
| State persisted | Yes |
| Incident notes | Yes |

Target thresholds are defined in `docs/KPI_DICTIONARY.md`.

## Prospective evidence operations

The weekly Forward Evidence process is separate from daily production.

Verification sequence:

1. successful read-only analysis on `main`;
2. raw artifact contains manifest, status, variant metrics, statistics, and provenance;
3. isolated publisher receives the exact successful run;
4. compact candidate passes signature and governance validation;
5. semantic evidence comparison determines whether a commit is needed;
6. publisher stages only `data/volume_component_forward_status.json`;
7. status-only commit does not retrigger the analysis;
8. daily report and review packet consume the signed status.

Until a non-initial source run and non-empty strategy fingerprint are published, Issue #69 remains open.

## Escalation and change policy

- Operational fixes may restore intended behavior but must not silently alter score logic.
- A strategy change requires the formal governed release process in Issue #77.
- A new persisted file requires architecture, data-dictionary, retention, and recovery updates.
- A new external dependency requires lock/reproducibility review.
- A major incident should be summarized in the monthly review under Issue #74.
