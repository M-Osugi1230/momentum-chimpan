# Daily Data Quality Grades

Last updated: 2026-07-12

## Purpose

Data Quality grades describe how reliable each daily ranked row is for human research prioritization.

They do not measure whether a stock is attractive. They do not change the Momentum score or rank, and they do not place or alter paper/live orders.

The machine-readable source is `research/data_quality_policy.yaml`.

## Grades

| Grade | Meaning | Research-priority effect |
|---|---|---|
| A | Complete, current, and no detected warning | May be priority A |
| B | Current and usable with a minor warning | May remain priority A with warning |
| C | Material caution | Cannot remain priority A; A is limited to B |
| D | Critical identity or core market-data failure | Forced to `見送り` |

A/B eligibility does not guarantee priority A. It only means data quality does not independently block it.

## Checks

### Critical — grade D

- stock code is not a four-digit numeric code;
- stock name is missing;
- price date is missing;
- close is missing or invalid;
- volume is missing or invalid;
- trading value is missing or invalid.

### Material — grade C unless a critical issue also exists

- observed price date differs from the intended report date;
- trading value is below `config.yaml#market.min_trading_value`;
- possible corporate action or unadjusted discontinuity, currently an absolute daily move of at least 45%;
- required analytical values are missing: 20-session return, volume ratio, MA20, or MA60.

### Warning — grade B unless a material or critical issue also exists

- JPX33 sector is blank;
- absolute 5-session return is at least 100%;
- absolute 20-session return is at least 200%;
- volume ratio is at least 50x;
- absolute MA20 deviation is at least 150%.

These thresholds are anomaly-review triggers, not statements that the data is necessarily wrong.

## Stored metadata

Each ranked row receives:

- `data_quality_version`;
- `data_quality_grade`;
- `data_quality_score`;
- `data_quality_eligible_for_a`;
- `data_quality_reason_codes`;
- `data_quality_warnings`;
- current-date, identity, core, analytical, and liquidity flags;
- possible-corporate-action, abnormal-price, and abnormal-volume flags.

The metadata is appended to the existing ranking-history schema. The natural key remains `date + code`.

## Priority integration

The existing A/B/C/見送り classification is calculated first.

The quality gate then records:

- `action_priority_before_quality`;
- final `action_priority`;
- `quality_adjustment_reason`.

Rules:

- quality A/B: no forced downgrade;
- quality C and original priority A: final priority B;
- quality D: final priority `見送り`;
- no quality C/D row may remain priority A.

The quality adjustment occurs in the display/research-priority layer after the paper portfolio logic has already run. It does not change the current paper engine.

## Daily output

### Summary

- assessed Top100 count;
- A/B/C/D counts;
- current-date ratio;
- priority-A eligibility ratio;
- number of priority adjustments;
- count of C/D rows remaining in A, required to be zero;
- possible corporate-action warning count.

### Workbook

- quality columns in `Momentum Top100`;
- original and final priority plus quality columns in `Action Priority`;
- dedicated `Data Quality` sheet with summary and row-level warnings.

### Email

The plain and HTML email display:

- A/B/C/D counts;
- current-date and A-eligibility ratios;
- adjustment count;
- up to five C/D warnings;
- explicit statement that score and rank are unchanged.

## Safe behavior

- Missing quality metadata at the priority gate is treated as grade D, not silently trusted.
- A missing or invalid core price row cannot become a high-confidence research priority.
- The market-level freshness guard still controls whether production state may be updated.
- Quality grading does not override a blocked production-state update.
- Corporate-action detection creates a review warning; it does not automatically repair prices.

## Governance

The following remain disabled:

- automatic score changes;
- automatic weight changes;
- automatic strategy changes;
- production-state mutations outside the existing ranking metadata;
- live orders.

Changing grade definitions or thresholds requires a reviewed policy change and regression tests. Any future use in paper execution or production eligibility must be separately registered and reviewed; this version is limited to display and human research priority.

## Validation

The dedicated CI verifies:

- deterministic A/B/C/D examples;
- stale-price and corporate-action warnings;
- exact score and rank preservation;
- C/D priority boundary;
- ranking-history metadata persistence;
- workbook and email output;
- three-symbol external-data integration;
- no changes to strategy files;
- read-only workflow permissions.
