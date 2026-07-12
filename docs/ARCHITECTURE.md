# Momentum Chimpan Architecture

Last updated: 2026-07-12

## System boundary

Momentum Chimpan is a Japanese-equity research-priority and evidence system. It has four logical layers:

1. **Production daily analysis** — downloads the current universe and prices, calculates rankings and lifecycle information, writes the daily workbook, and sends email.
2. **Persistent operational state** — records ranking history, market state, paper state, execution audit, heartbeat, strategy fingerprint, and sealed snapshots.
3. **Research and evidence** — performs no-lookahead historical or prospective comparisons, usually keeping raw evidence in Actions artifacts.
4. **Governance and presentation** — controls strategy fingerprints, evidence precedence, signed compact statuses, review packets, and human-facing transparency.

## Daily production flow

```text
GitHub Actions schedule / manual full run
        |
        v
checkout main + install dependencies
        |
        v
strategy_governance.py snapshot
        |
        | exports exact strategy fingerprint
        v
daily_runner.py
        |
        +--> main.py production scan and report logic
        |       |
        |       +--> JPX universe and cache
        |       +--> price retrieval
        |       +--> score/rank/lifecycle/sector calculations
        |       +--> ranking, market and paper state updates
        |       +--> output/daily_report.xlsx
        |       +--> plain + HTML email
        |
        +--> research_transparency display-only augmentation
                |
                +--> canonical evidence catalog
                +--> signed forward-status progress
        |
        v
operations.py heartbeat
        |
        v
evidence_provenance.py stamp-live
        |
        v
state_recovery.py seal
        |
        v
operations.py maintain
        |
        v
persist documented production-state allowlist
        |
        v
upload report and diagnostics artifact
```

The report step may continue far enough to create diagnostics, but persistence is gated on the complete chain: strategy snapshot, report, heartbeat, evidence stamp, recovery seal, and maintenance.

## Production entrypoints

| Entrypoint | Purpose |
|---|---|
| `daily_runner.py` | Production report entrypoint used by the daily workflow; adds governed transparency without changing strategy logic |
| `main.py` | Core scan, score, history, workbook, email, and paper logic |
| `.github/workflows/daily.yml` | Scheduled and manual production orchestration |
| `run_local.sh` | Local verification/full execution helper |

## Persistent production-state allowlist

The daily workflow is allowed to persist only the following paths:

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

Adding another persisted file is a production-state change and requires documentation, recovery classification, and tests.

## Canonical governance sources

| File | Role | Mutation rule |
|---|---|---|
| `config.yaml` | Current production configuration | Reviewed production PR only |
| `research/evidence_catalog.yaml` | Canonical precedence and current research decision | Governed evidence PR only |
| `research/volume_component_forward_evidence.yaml` | Pre-registered forward study | Gate may not be changed after results are known |
| `research/strategy_approvals.yaml` | Human approval audit entries | Human-authored, hash-bound records only |
| `data/volume_component_forward_status.json` | Signed compact prospective progress | Isolated publisher only; semantic evidence changes only |

## Artifact-only evidence

Raw research signals, price panels, outcomes, bootstrap samples, large workbooks, and detailed diagnostics should remain in GitHub Actions artifacts unless a result is deliberately promoted into a small reviewed research result file.

This separation prevents research workflows from bloating or mutating production state.

## Prospective volume-component chain

```text
Daily ranking history with strategy fingerprints
        |
        v
Volume Component Forward Evidence (read-only)
        |
        +--> raw signals, outcomes, statistics, manifest
        |     stored as Actions artifact
        v
Publish Volume Component Forward Status
        |
        +--> validates exact upstream run and governance
        +--> creates compact signed candidate
        +--> compares semantic evidence
        +--> writes only data/volume_component_forward_status.json
        v
Research Transparency Dashboard
        |
        +--> Summary / email / Research Evidence sheet
        v
Volume Component Forward Review Packet
        |
        +--> NOT_READY or READY_FOR_HUMAN_WEIGHT_REVIEW
        +--> artifact only; never changes weight
```

A status-only publisher commit must not retrigger the expensive Forward Evidence analysis.

## Recovery classes

| Class | Examples | Recovery approach |
|---|---|---|
| Sealed production state | ranking, market, paper, execution histories | Restore the latest compatible state snapshot after fingerprint validation |
| Regenerable current output | daily workbook, run log | Re-run the daily workflow; do not treat as canonical state |
| External cache | JPX list cache | Restore from state or refresh from source with canary validation |
| Artifact-only research | raw forward outcomes and research workbooks | Re-download retained artifact or re-run registered research |
| Human governance | catalog, registrations, approvals | Restore from Git history; never synthesize automatically |
| Signed compact status | forward progress JSON | Rebuild only from the exact successful upstream artifact |

## Change isolation

### Strategy change

Changes scores, weights, thresholds, filters, lifecycle rules, exits, execution assumptions, or production eligibility. Requires formal release governance.

### State change

Adds, removes, or changes persisted data and recovery contracts. Requires operations and recovery review.

### Display change

Changes email or workbook presentation without changing ranking, paper logic, or persisted strategy state. Must still prove invariants.

### Research change

Adds registered analysis or evidence outputs. Must not mutate production strategy or state.

## Failure boundaries

| Failed stage | Persistence allowed? | Required action |
|---|---:|---|
| Strategy fingerprint | No | Fix governance/config inconsistency |
| Daily report | No | Inspect external data, calculation, email, and workbook logs |
| Heartbeat | No | Fix report validation or heartbeat generation |
| Evidence stamp | No | Resolve fingerprint/history/report mismatch |
| Recovery seal | No | Resolve snapshot completeness or hash failure |
| Maintenance | No | Resolve state validation or retention failure |
| Git persistence | No successful commit | Retry bounded push; preserve artifact for recovery |
| Email only | Depends on report stage policy | Record delivery failure; do not hide successful data generation |

## Security and permissions

- The daily workflow has `contents: write` only because it persists the documented production-state allowlist.
- Research workflows should normally use `contents: read`.
- The forward-status publisher has isolated write permission and may stage only its compact status file.
- Email secrets must never appear in research workflows or artifacts.
- No workflow has authority to place live orders.

## Ownership matrix

| Area | Canonical owner/source |
|---|---|
| Product purpose and non-goals | `docs/PROJECT_CHARTER.md` |
| Execution order and phase gates | `docs/ROADMAP.md` |
| Runtime and state boundaries | this document |
| Incident and recovery procedure | `docs/OPERATIONS_RUNBOOK.md` |
| Dataset contracts | `docs/DATA_DICTIONARY.md` |
| KPI definitions | `docs/KPI_DICTIONARY.md` |
| Current evidence decision | `research/evidence_catalog.yaml` |
| Current production behavior | code, `config.yaml`, and `.github/workflows/daily.yml` |
