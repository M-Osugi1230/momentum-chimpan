# Daily Research Priority Outcomes

Last updated: 2026-07-12

## Purpose

This subsystem measures whether the daily A/B/C/Watch/Skip research-priority decisions were useful after the fact.

It is prospective research evidence. It does not change Momentum scores, ranking, paper execution, production state, priority rules, or live orders.

The machine-readable registration is `research/priority_outcomes/policy.yaml`.

## Source and eligibility

Decisions are accepted only from the exact successful `Daily Momentum Report` artifact when:

- the heartbeat confirms a full production state update;
- the workflow status is successful;
- a non-empty governed strategy fingerprint exists;
- the report date is 2026-07-13 or later;
- the workbook contains the `Action Priority` sheet produced by the registered Daily Research Focus layer.

Limited-symbol verification runs and incomplete reports are not eligible.

## Decision history

`research/priority_outcomes/daily_research_decisions.csv`

Natural key:

- decision date;
- stock code;
- strategy fingerprint;
- focus-policy version.

The stable `decision_id` is a SHA-256 hash of that natural key.

Each row preserves:

- exact source workflow run and artifact fingerprint;
- decision date;
- strategy and focus-policy versions;
- A/B/C/Watch/Skip bucket;
- Daily Action List membership and rank;
- Momentum rank and score;
- action and expectancy information;
- lifecycle, market regime, sector, relative-strength grade, and Data Quality grade;
- why today, what changed, risk summary, and next research questions;
- entry model and transaction-friction assumption.

Reprocessing the same decision replaces the matching `decision_id` rather than duplicating it.

## Outcome history

`research/priority_outcomes/daily_research_outcomes.csv`

Natural key:

- `decision_id`;
- horizon sessions.

One decision produces three rows: 5, 10, and 20 sessions.

### Entry

- first available trading session strictly after the decision date;
- adjusted opening price;
- same-day close entry is prohibited;
- the entry session counts as session one.

### Exit

- adjusted closing price on session 5, 10, or 20;
- the outcome remains `PENDING` until that session exists;
- completed outcomes retain a price fingerprint and are not silently rewritten on later runs.

### Transaction-friction proxy

A fixed 20-basis-point round-trip cost is subtracted from the stock return.

This is a research-comparison proxy, not an order simulator or personal transaction-cost estimate.

### Market benchmark

TOPIX ticker `^TOPX` is evaluated over the same entry open and exit close dates.

`market_excess_return = net_stock_return - TOPIX_return`

A stock outcome may be marked `COMPLETE_WITHOUT_MARKET` when the stock matured but aligned market data is missing. Such a row is not included in market-excess calibration until the benchmark becomes available.

### Sector proxy

The sector proxy is the median net return of other decisions from:

- the same decision date;
- the same horizon;
- the same JPX33 sector.

The current row is excluded. At least three peer decisions are required.

This is explicitly a same-day decision-cohort proxy, not a licensed JPX sector-index return.

## Outcome statuses

| Status | Meaning |
|---|---|
| `PENDING` | Required future sessions do not yet exist |
| `COMPLETE` | Stock and aligned TOPIX outcome matured |
| `COMPLETE_WITHOUT_MARKET` | Stock matured but aligned TOPIX data is missing |
| `PRICE_ERROR` | Stock price history could not be obtained |
| `INVALID_DECISION` | Decision date or required decision metadata is invalid |

Missing, pending, and error rows remain visible. They are not silently removed from the denominator.

## Calibration report

Files:

- `research/priority_outcomes/latest_calibration.json`
- `research/priority_outcomes/latest_calibration.md`

The signed JSON and human-readable Markdown include:

- decision and outcome counts;
- pending and error counts;
- lookahead violations;
- distinct decision dates;
- A/B review-gate progress;
- sample size;
- mean net return;
- mean and median TOPIX excess return;
- positive-excess rate;
- deterministic bootstrap 95% confidence interval;
- small-sample warning.

Dimensions:

- research bucket;
- lifecycle;
- market regime;
- JPX33 sector;
- Data Quality grade;
- Momentum rank bucket;
- Momentum score bucket.

## Review gate

A priority-rule review cannot be marked ready until all of the following exist for both A and B at all 5/10/20-session horizons:

- at least 30 complete outcomes;
- at least 20 distinct decision dates;
- zero lookahead violations.

Even after these gates pass:

- `production_rule_change_allowed` remains false;
- manual review is mandatory;
- any proposed rule change requires a separate registered study and production PR.

## Workflow and permissions

`.github/workflows/daily-priority-outcomes.yml` runs:

- after `Daily Momentum Report` completes;
- manually when requested;
- monthly to refresh the calibration report.

The workflow:

1. checks out current `main`;
2. downloads the exact upstream report artifact by run ID when available;
3. ingests eligible decisions;
4. fetches only the price history needed to mature pending outcomes;
5. validates no-lookahead and duplicate rules;
6. rebuilds the signed calibration report;
7. stages only the four research files in `research/priority_outcomes/`.

Top-level permissions are read-only. Only the isolated publish job receives `contents: write`.

No email secret, production-state file, configuration file, ranking history, or paper file is written.

## Limitations

- yfinance is the current research price source and may be revised by its upstream providers;
- the 20-basis-point friction proxy is not personalized;
- the sector benchmark is a decision-cohort proxy rather than a sector index;
- small samples must not be interpreted as validated ranking quality;
- observational differences between buckets do not establish causal value without further governed analysis.
