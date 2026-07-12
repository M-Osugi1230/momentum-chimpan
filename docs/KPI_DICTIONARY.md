# Momentum Chimpan KPI Dictionary

Last updated: 2026-07-12

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

### Email delivery success rate

`confirmed_email_delivery_attempts_without_error / expected_daily_email_attempts`

Target: >= 99% monthly.

Email failure is tracked separately from report and state success.

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

## Forward Evidence KPIs

The canonical source is `data/volume_component_forward_status.json` after signature validation.

### 10-session baseline outcome progress

`baseline_10d_outcomes / 100`, capped at 100% for display.

### 10-session tested outcome progress

`drop_volume_ratio_10d_outcomes / 100`, capped at 100%.

### 20-session baseline outcome progress

`baseline_20d_outcomes / 100`, capped at 100%.

### 20-session tested outcome progress

`drop_volume_ratio_20d_outcomes / 100`, capped at 100%.

### Paired-date progress

For each required horizon:

`paired_signal_dates / 20`, capped at 100%.

### Forward sample adequacy

PASS only when both 10- and 20-session horizons satisfy:

- baseline outcomes >= 100;
- tested outcomes >= 100;
- paired dates >= 20.

### Robust support gate

For both required horizons, the registered study requires:

- mean tested-minus-baseline difference < 0;
- early difference < 0;
- late difference < 0;
- two-sided p-value <= 0.05;
- bootstrap confidence-interval upper bound < 0.

This KPI produces evidence status, not an automatic production decision.

## Paper-validation KPIs

### Closed paper trades

Count of unique completed paper trades.

Minimums depend on the registered review. Existing release-review logic includes a 20-trade minimum for its paper criterion.

### Paper win rate

`profitable_closed_trades / closed_trades`

Must always be displayed with trade count.

### Paper profit factor

`gross_profit / absolute_gross_loss`

Undefined when there is no loss; do not silently replace with an arbitrary finite number.

### Paper maximum drawdown

Worst peak-to-trough decline in the paper equity curve.

### Paper excess return

Paper portfolio return minus the registered benchmark return over aligned tradable dates.

### Paper exposure

Average deployed paper capital divided by paper equity/capital according to the engine's registered definition.

## Future priority-calibration KPIs

These become active with Issues #70 and #72.

### Priority-class forward return

Average 5/10/20-session return by A/B/C/Watch/Skip, with observation count and confidence interval.

### Priority-class excess return

Average forward return minus market and sector benchmark over the same horizon.

### Positive excess rate

`observations_with_positive_excess / eligible_observations`

### Promotion value

Difference in subsequent outcome for promoted names versus names that remained in the lower class, using pre-registered matching or comparison rules.

### Demotion risk reduction

Difference in downside or negative-excess frequency after a demotion signal, measured without treating demotion as an automatic sell instruction.

### Calibration monotonicity

Evidence that A has stronger subsequent research outcomes than B, and B than C, with uncertainty shown.

No monotonicity claim is made before sufficient prospective samples exist.

## Monthly review minimum

Issue #74 should report at least:

- workflow success and completion SLOs;
- universe and price coverage;
- stale, duplicate, identity, and error counts;
- delivery health;
- recovery readiness;
- Forward Evidence counts and status;
- paper metrics;
- future priority calibration when available;
- incidents and corrective actions;
- explicit count of production strategy changes, expected to be zero unless separately approved.
