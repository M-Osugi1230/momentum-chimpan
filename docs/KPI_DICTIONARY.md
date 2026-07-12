# Momentum Chimpan KPI Dictionary

Last updated: 2026-07-13

## KPI design rules

- Every KPI must have one definition, one source, one time window, and one owner.
- A displayed percentage must preserve its numerator and denominator.
- Missing data must not be silently treated as success.
- Production reliability, data quality, user value, and research evidence are reported separately.
- Research KPIs may inform human review but cannot automatically change strategy.

## Product-value KPIs

### Daily review time

| Field | Definition |
|---|---|
| Goal | Time required to understand the market state and identify today's research priorities |
| Target | <= 3 minutes for the email/summary view |
| Measurement | Periodic human timing test using the actual daily report |
| Owner | Product UX |

### A-priority count

| Field | Definition |
|---|---|
| Goal | Keep mandatory daily research focused |
| Target | 0–5 stocks per full production day |
| Source | Future priority-decision history |
| Failure | More than five A names, or hidden truncation without explanation |

### A/B explanation coverage

`explained_A_or_B / total_A_or_B`

Target: 100%.

An explanation must include why today, key change, lifecycle, relative-strength context, data quality, and caution.

### A/B warning coverage

`A_or_B_with_explicit_risk_section / total_A_or_B`

Target: 100%.

### Important-change coverage

`displayed_promotions_demotions_new_and_lost_priority / detected_important_changes`

Target: 100%.

### Daily detailed-research load

Number of stocks the user is expected to investigate beyond the email.

Target: <= 10, including A and selected B names.

## Operational KPIs

### Daily workflow success rate

`successful_required_gate_runs / scheduled_full_production_runs`

Required gates:

- strategy fingerprint;
- report;
- heartbeat;
- evidence stamp;
- recovery seal;
- maintenance;
- production-state persistence.

Target: >= 99% on a rolling 30-market-session basis.

A run with a failed required gate is not successful even if a workbook exists.

### Report generation success rate

`valid_daily_workbooks / scheduled_full_production_runs`

Target: >= 99%.

A valid workbook must exist, open, and contain required Summary fields and sheets.

### Completion-time SLO

- Scheduled start: 16:45 JST.
- Target: complete by 17:15 JST for at least 95% of full production runs.
- Measure both total runtime and completion timestamp.

### SMTP receipt validity rate

`valid_signed_smtp_receipts / daily_runs_expected_to_emit_a_receipt`

Target: 100% monthly.

A receipt is valid only when its status and integrity hashes pass validation. Missing or unreadable receipts remain failures rather than being inferred from workbook or workflow success.

### Scheduled SMTP acceptance rate

`smtp_accepted_scheduled_receipts / valid_scheduled_smtp_receipts`

Target: >= 99% monthly after the email configuration is active.

Statuses are reported separately:

- `SMTP_ACCEPTED`;
- `SKIPPED_SECRETS_MISSING`;
- `FAILED`.

This KPI proves only that the configured SMTP server accepted the message without an observed exception. It does not prove final inbox delivery, spam placement, opening, or reading.

### Inbox-delivery success rate

Not currently observable.

The system must display `NOT_OBSERVED_BY_SMTP_ACCEPTANCE_RECEIPT` rather than converting SMTP acceptance into an inbox-delivery claim.

### Failure-notification coverage

`failed_required_gate_runs_with_notification / failed_required_gate_runs`

Target: 100%.

### Production-state persistence success

`full_runs_with_successful_state_commit / full_runs_eligible_for_persistence`

Target: >= 99%.

### Recovery readiness

A binary monthly KPI requiring:

- at least one valid recent sealed snapshot;
- compatible strategy fingerprint;
- passing snapshot audit;
- documented restore procedure.

Target: PASS every month.

### Recovery drill success

A controlled restoration from a sealed snapshot followed by state validation and a non-persisting verification run.

Target: 100% for scheduled drills.

## Data-quality KPIs

### Normal-stock universe coverage

`actual_scanned_normal_stocks / expected_eligible_normal_stocks`

Target: >= 99%.

The expected denominator must come from the validated JPX universe after the documented market, ETF/REIT, price, and liquidity rules are applied as appropriate.

### Price retrieval coverage

`stocks_with_sufficient_current_price_history / actual_scanned_stocks`

Target: >= 98%.

Report the failed-stock count and reason distribution.

### Current-market-date coverage

`stocks_whose_latest_valid_price_date_matches_expected_market_date / successfully_retrieved_stocks`

Target: 100% for A-priority eligibility; system-wide deviations must be visible.

### Stale-price false acceptance

Count of stocks treated as current when the latest valid market date is older than expected without an explicit accepted market-calendar reason.

Target: 0.

### Ranking duplicate count

Count of duplicate natural keys in `data/momentum_daily_ranking.csv`, where the natural key is `date + code`.

Target: 0.

### Market-temperature duplicate count

Count of duplicate `date` rows in `data/market_temperature.csv`.

Target: 0.

### Identity inconsistency count

Count of code/name/market combinations inconsistent with the validated universe source.

Target: 0 unresolved material inconsistencies.

### Data-quality grading coverage

`Top100_rows_with_quality_grade_and_reason_codes / Top100_rows`

Target: 100% after Issue #71 is activated.

### Invalid A-priority quality count

Number of A-priority stocks with quality grade C or D.

Target: 0.

## Evidence-integrity KPIs

### Lookahead violations

Count of records using unavailable same-day or future information under the registered execution model.

Target: 0.

### Strategy fingerprint mismatch

Count of evidence, report, ranking, runtime, or snapshot comparisons with incompatible fingerprints.

Target: 0 accepted mismatches. Any mismatch is blocking.

### Distribution-preservation violations

Count of registered counterfactual dates where the daily score multiset is not preserved when preservation is required.

Target: 0.

### Unregistered production strategy changes

Count of production score, filter, exit, or priority-rule changes without the governed release path.

Target: 0.

### Automatic strategy or weight changes

Count of production changes activated automatically from research evidence.

Target: 0.
