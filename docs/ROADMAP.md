# Momentum Chimpan Roadmap

Last updated: 2026-07-12

## Roadmap rule

The roadmap prioritizes daily research value, data quality, and operational reliability before new indicators or strategy optimization.

Until the prospective evidence gate completes, engineering capacity should be allocated approximately as follows:

| Workstream | Target allocation |
|---|---:|
| Daily user experience | 40% |
| Data quality and operational reliability | 40% |
| Evidence and calibration | 20% |
| New score components and weight optimization | 0% |

## Phase 0 — Re-baseline and production audit

Target: July 2026

### Objectives

- make the current system understandable without chat history;
- align README and implementation;
- document persistent state, artifacts, and governance sources;
- verify the live Forward Evidence publisher chain;
- begin a ten-session full-production audit;
- freeze new score optimization.

### Deliverables

- project charter;
- roadmap;
- architecture;
- operations runbook;
- data dictionary;
- KPI dictionary;
- current README;
- Issue-based execution backlog.

### Exit gate

- documentation matches the current workflow;
- ten-session production audit has started;
- production-state allowlist is documented;
- Forward Evidence chain has a tracked verification issue;
- no unregistered production strategy change is in progress.

## Phase 1 — Operational reliability and data-quality grades

Target: July–September 2026

### Objectives

- establish 99% daily workflow reliability;
- measure universe and price retrieval coverage;
- prevent stale or incomplete data from becoming top priorities;
- prove state recovery and failure notification.

### Deliverables

- ten-session audit table;
- incident log and corrective actions;
- recovery drill;
- per-stock data-quality grades A/B/C/D;
- stale-price, missing-session, abnormal-price, and corporate-action warnings;
- coverage and freshness history.

### Exit gate

| Measure | Target |
|---|---:|
| Daily workflow success | >= 99% |
| Report generation success | >= 99% |
| Normal-stock universe coverage | >= 99% |
| Price retrieval coverage | >= 98% |
| Duplicate `code + date` rows | 0 |
| Stale prices accepted as current | 0 |
| Failure notification coverage | 100% |
| Recovery drill | Passed |

## Phase 2 — Three-minute research-priority experience

Target: August–October 2026

### Objective

Move from a ranking viewer to a research-priority decision tool.

### Classification

- **A** — must research today; maximum five names;
- **B** — research if time permits;
- **C** — continue monitoring;
- **Watch** — waiting for promotion conditions;
- **Skip** — low priority or unreliable data.

### Required explanation for A/B

- why today;
- what changed from the previous run;
- lifecycle state;
- market and sector relative strength;
- score and rank context;
- data-quality grade;
- overheating, liquidity, and other cautions;
- next research questions outside the system.

### Email order

1. today's conclusion;
2. market temperature;
3. A candidates;
4. important changes;
5. risks and warnings;
6. B/C candidates;
7. research-evidence status;
8. workbook and artifact guidance.

### Exit gate

- email can be understood in three minutes;
- A list contains no more than five names;
- A/B explanations and warnings have 100% coverage;
- promotions, demotions, new entries, and lost-priority names are visible;
- grades C/D cannot become A priorities;
- outcome-storage contract exists before classification activation.

## Phase 3 — Outcome calibration

Target: September 2026–January 2027

### Objectives

Track whether the system's research priorities were useful rather than merely plausible.

### Dimensions

- priority class;
- lifecycle state;
- score and rank bucket;
- market temperature;
- sector;
- data-quality grade;
- 5/10/20-session raw return;
- excess versus market and sector;
- sample size and confidence interval.

### Principles

- no same-day close entry;
- exact decision date and strategy fingerprint preserved;
- prospective data outranks historical backfill;
- small samples are labeled;
- no automatic activation from favorable results.

### Exit gate

- no-lookahead outcome builder operational;
- daily priority history retained;
- monthly calibration report generated;
- sample-size warnings shown;
- major classifications have sufficient observations for review.

## Phase 4 — Prospective evidence and paper validation

Target: July–November 2026 and ongoing

### Current registered gate

For both 10- and 20-session horizons:

- at least 100 outcomes for baseline;
- at least 100 outcomes for `drop_volume_ratio`;
- at least 20 paired signal dates;
- mean difference below zero for support;
- early and late differences below zero for robust support;
- two-sided p-value <= 5%;
- bootstrap confidence-interval upper bound below zero.

### Status states

- `ACCUMULATING`;
- `DIRECTIONALLY_SUPPORTED`;
- `ROBUSTLY_SUPPORTED`;
- `NOT_SUPPORTED`.

### Exit gate

- signed compact status finalized;
- current strategy fingerprint matches evidence;
- no lookahead or distribution-preservation violation;
- manual review packet produced;
- explicit human decision recorded.

Completion of the gate does not automatically change the 15-point production weight.

## Phase 5 — Governed strategy review

Target: December 2026–March 2027

### Scope

Only mature questions with prospective evidence may enter this phase, including score weights, filters, priority rules, and paper exits.

### Mandatory process

1. pre-register hypothesis and acceptance criteria;
2. preserve discovery/holdout separation;
3. evaluate costs and multiple time windows;
4. create evidence review packet;
5. run at least 20 market sessions of shadow comparison;
6. record manual approval or rejection;
7. use a separate production-change PR;
8. retain rollback path and post-release audit.

## Phase 6 — Web dashboard

Target: April 2027 or later

### Entry gate

- at least three months of stable daily operation;
- daily success >= 99%;
- data-quality grades operational;
- A/B/C experience stable;
- Forward Evidence chain operating;
- report definitions no longer changing frequently.

### Scope

Mobile-first presentation of the established daily workflow: summary, ranking history, lifecycle, market temperature, sector heatmap, priority history, outcome calibration, evidence, and quality.

Web development is a presentation expansion, not a reason to redesign unproven strategy logic.

## Phase 7 — External-user and monetization readiness

Target: only after recurring value is proven

### Entry gate

- reliable recurring daily value;
- measurable priority calibration;
- completed prospective evidence cycle;
- stable data and operations SLOs;
- legal and data-license review.

### Required decisions

- target user and paid job-to-be-done;
- defensible historical data and workflow;
- free versus paid boundaries;
- redistribution permissions;
- disclaimers and user controls;
- continued positioning as research-priority support, not investment advice.

## Immediate execution order

1. Complete documentation PR for #67/#79/#80/#81/#82/#83.
2. Start ten-session production audit under #68.
3. Verify Forward Evidence publisher under #69.
4. Implement data-quality foundations under #71.
5. Design and implement priority experience under #70.
6. Activate outcome tracking under #72 with the priority launch.
7. Produce monthly governance reporting under #74.

## Backlog discipline

A new feature enters active development only when it clearly improves one of:

- daily research time;
- research-candidate quality;
- data reliability;
- operational recoverability;
- outcome measurability.

Otherwise it remains in the backlog.
