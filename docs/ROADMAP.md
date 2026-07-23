# Momentum Chimpan Roadmap

Last updated: 2026-07-23

## Roadmap rule

Momentum Chimpan is a research-priority system, not an automatic trading system.
The daily goal is to narrow the Japanese equity universe to **five to ten stocks
that deserve detailed research today**, while preserving the evidence, cautions,
and data quality needed to understand why each stock was selected.

Work is prioritized in this order:

1. correct and recoverable daily operation;
2. complete research ledgers and measurable outcomes;
3. five-to-ten-name daily research usefulness;
4. research-only shadow and paper validation across market regimes;
5. governed production review only after prospective evidence matures;
6. external-user and monetization readiness last.

No favorable historical result may automatically change a production score,
weight, filter, priority rule, paper rule, or exit rule.

## Current execution status

| Workstream | State | Current gate |
|---|---|---|
| Documentation and contracts | Implemented; synchronized | README, roadmap, architecture, runbook, dictionaries, and tests must match `main` |
| Daily ranking and report | Operating | Full-universe coverage, freshness, duplicate, and recovery checks continue |
| Five-to-ten daily research list | Implemented; observing | A remains capped at five; quality-screened C/Watch may supplement A/B only when fewer than five detailed candidates exist |
| Email digest | Implemented | Email remains concise; workbook and dashboard contain the full Daily Action List and evidence |
| Web dashboard | Implemented | Exact successful daily workbook is the source; mobile, search, watchlist, comparison, and stock deep links are presentation-only |
| Data Quality A/B/C/D | Implemented; observing | Quality C cannot remain A; quality D cannot enter the detailed research supplement |
| Exact same-day recovery | Implemented | Every evidence-eligible state-update run requires exact recovery `PASS` and unchanged production state |
| Ten-session production audit | `ACCUMULATING` | Reconcile every exact Daily Momentum Report run and complete ten eligible market sessions |
| Live Session Eligibility | `ACCUMULATING` after reconciliation | Exact artifact, recovery, strategy fingerprint, and ranking-row hash must agree |
| Priority outcomes | `ACCUMULATING` | Mature no-lookahead 5/10/20-session outcomes and retain pending/error rows visibly |
| Volume Forward Evidence | `ACCUMULATING` | 100 outcomes per variant and 20 paired dates at both 10 and 20 sessions |
| Healthy v1/v3 research | Research-only shadow | Use confirmed tendencies to improve candidate filtering and comparison; no automatic production promotion |
| Paper portfolio | Research-only | Validate policy and outcomes across strong, mildly strong, neutral, weak, overheated, and operational WARN/FAIL conditions |
| Strategy release governance | Implemented | Separate pre-registration, shadow period, manual approval, production PR, rollback, and post-release audit |
| External use and monetization | Gated future | Recurring value, evidence maturity, operations SLOs, and data-license review required |

`Implemented` means the code and validation contract are present. It does not mean
that time-dependent evidence gates have accumulated enough real market sessions.

## Phase 0 — Foundation and production safety

**State: substantially complete**

Delivered:

- project charter, README, roadmap, architecture, runbook, data dictionary, and KPI dictionary;
- governed strategy fingerprint and release integrity;
- exact daily artifact, signed email preview, and SMTP acceptance receipt;
- state snapshot, isolated same-day recovery drill, retention, and failure notification;
- full workbook, concise email, and rich static dashboard;
- Data Quality and Daily Research Focus;
- research-only paper portfolio and audit records.

The foundation is reopened only when implementation and documentation diverge.

## Phase 1 — Self-healing operations and research ledgers

**State: active, highest priority**

### Objective

A successful Daily Momentum Report must not be lost between production and the
three downstream evidence stores:

- production audit;
- live-session eligibility and Forward Evidence;
- daily priority decisions and 5/10/20-session outcomes.

### Deliverables

- a scheduled and manually dispatchable reconciliation workflow;
- exact source-run enumeration from 2026-07-13 onward;
- exact artifact download by run ID;
- idempotent audit, eligibility, decision, and outcome rebuild;
- recovery-aware readiness before outcome ingestion;
- outcome maturity even on days without a new daily artifact;
- signed cross-ledger coverage diagnostics;
- a narrow research-only persistence allowlist.

### Exit gate

| Measure | Target |
|---|---:|
| Eligible daily workflow success | >= 99% |
| Report generation success | >= 99% |
| Normal-stock universe coverage | >= 99% |
| Price retrieval coverage | >= 98% |
| Duplicate `code + date` rows | 0 |
| Stale prices accepted as current | 0 |
| Exact daily recovery | 100% PASS for eligible state-update sessions |
| Successful daily runs missing from audit | 0 |
| Successful daily runs missing from eligibility ledger | 0 |
| Eligible daily decisions missing from outcome history | 0 |
| Failure notification coverage | 100% |

## Phase 2 — Five-to-ten-stock research experience

**State: implemented; prospective observation active**

### Daily contract

- **A** — must research today; maximum five;
- **B** — research if time permits;
- **C** — established candidate to continue monitoring;
- **Watch** — wait for score, continuity, or data conditions to improve;
- **Skip** — low priority or unreliable;
- **Daily Action List** — target five to ten names.

A/B names are selected first. When fewer than five exist, the system may add
quality-screened C/Watch names as clearly marked supplemental research candidates.
It must not add Data Quality D, change Momentum score/rank, or affect paper execution.
If fewer than five quality candidates exist, the shortfall is shown rather than
filling the list with unreliable names.

Every detailed candidate must show:

- why today;
- what changed;
- lifecycle and relative strength context;
- data-quality grade;
- overheating, liquidity, and other cautions;
- next questions requiring external research.

### Exit gate

- five-to-ten target and any shortfall are visible;
- A never exceeds five;
- explanations and warnings have 100% coverage;
- quality C/D safety boundaries are preserved;
- email, workbook, and dashboard counts agree;
- prospective outcomes exist before any priority-rule change.

## Phase 3 — Prospective outcome calibration

**State: accumulating**

Execution model:

- exact eligible decisions from 2026-07-13 onward;
- next available session adjusted open;
- no same-day close entry;
- adjusted closes after 5, 10, and 20 sessions;
- 20bp round-trip friction;
- TOPIX and same-sector comparison where available;
- pending, error, and invalid observations remain visible.

Human review requires sufficient samples and dates for A and B, zero lookahead
violations, and explicit small-sample warnings. Favorable calibration does not
automatically change production rules.

## Phase 4 — Research insight application

**State: active, research-only**

Confirmed findings are stored in the Research Insight Ledger with facts,
interpretation, limitations, source runs, and artifacts separated.

Current application direction:

- use Healthy v1 primarily as an overheating, liquidity, and structural-break filter;
- evaluate Healthy v3 primarily as a Top10 ordering hypothesis;
- keep Balanced v2 out of production unless independent evidence changes;
- reject fixed 60-session holding as a default rule;
- study price paths and state-dependent exits rather than one fixed holding period;
- treat large MA20 extension as a long-horizon instability context;
- use positive-volume stock sessions for long-horizon research data quality.

Improvements from these findings first enter one of:

1. a read-only report explanation;
2. a shadow ranking;
3. a research-only paper cohort;
4. a pre-registered independent holdout.

Only mature prospective evidence may later enter governed strategy review.

## Phase 5 — Paper validation across all market regimes

**State: active research workstream**

The paper portfolio must be evaluated separately for:

- strong;
- mildly strong;
- neutral;
- weak;
- overheated-warning;
- operational WARN;
- operational FAIL/no-new-entry.

Required dimensions:

- target and actual exposure;
- number of entries and exits;
- position and sector concentration;
- stop, target, trailing, time, and signal-exit frequencies;
- gross/net return, win rate, drawdown, MAE, and MFE;
- turnover and friction sensitivity;
- Production, Healthy v1, and Healthy v3 research cohorts where supported;
- early/late period and leave-one-sector-out robustness;
- explicit sample-size and regime-coverage warnings.

Paper results remain research-only and cannot place live orders.

## Phase 6 — Prospective Forward Evidence and governed strategy review

**State: accumulating / future review**

For both 10- and 20-session horizons, the registered volume-component study
requires at least:

- 100 baseline outcomes;
- 100 tested outcomes;
- 20 paired signal dates;
- registered direction, early/late consistency, p-value, and bootstrap-CI gates;
- exact strategy fingerprint and distribution-preservation checks;
- a manual review packet and explicit human decision.

Completion of the evidence gate does not automatically change the current
15-point volume-ratio weight.

Any production change additionally requires:

1. a pre-registered hypothesis and acceptance criteria;
2. discovery/holdout separation;
3. costs and multiple horizons;
4. at least 20 live market sessions of shadow comparison;
5. explicit approval or rejection;
6. a separate production-change PR;
7. rollback and post-release audit.

## Phase 7 — Dashboard operation and external readiness

The Web dashboard is already implemented; it is no longer a future engineering
phase. Current work is to keep it synchronized with the exact daily workbook and
make operational/evidence gaps visible.

External users or monetization remain gated until:

- at least three months of stable daily value;
- operations SLOs are met;
- five-to-ten research calibration is measurable;
- at least one prospective evidence cycle completes;
- legal and market-data redistribution boundaries are reviewed;
- positioning remains research support rather than investment advice.

## Immediate execution order

1. Merge and run research-ledger reconciliation.
2. Backfill every completed Daily Momentum Report run from 2026-07-13 onward.
3. Confirm audit, eligibility, and priority ledgers contain the same eligible source runs.
4. Mature all available 5-session outcomes, then 10- and 20-session outcomes as time passes.
5. Run Forward Evidence from the reconciled eligibility ledger.
6. Complete the ten-session operational audit and signed SLO decision.
7. Observe the five-to-ten Daily Action List and shortfall behavior in production.
8. Continue Healthy v1/v3 shadow research and stock only reproducible insights.
9. Validate the paper portfolio across every registered market and operational regime.
10. Keep production score/weight optimization frozen until prospective evidence and explicit review mature.

## Backlog discipline

A feature enters active work only when it clearly improves at least one of:

- daily research time;
- candidate quality;
- data reliability;
- operational recoverability;
- outcome measurability;
- regime coverage;
- evidence transparency.

Otherwise it remains in the backlog.
