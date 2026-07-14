# Momentum Chimpan — Email / Site Acceptance Contract

This document is the handoff contract between the daily research pipeline and the separately built public site. It defines what must be visible, consistent and testable. It does not authorize any change to scores, weights, ranking, thresholds, priority policy, paper execution or strategy.

## 1. Product job

A user arriving after the close must be able to decide which three-to-five companies to research next, while understanding market context, change, risk and data reliability.

- Email job: decide in about 90 seconds whether to open the site and which names matter.
- Site job: decide in about three minutes what to research, then explore the supporting evidence.

## 2. Shared source of truth

Email and site must use the exact same successful daily workbook/artifact.

Shared fields must never disagree:

- report date
- price-data date and freshness
- Market Regime and score
- Daily Action List membership, order and bucket
- reason, change and caution
- Data Quality grade
- Forward Evidence status
- operational P0/P1 state

The UI may summarize prose, but it must not reinterpret or recalculate governed values.

## 3. Email first-screen requirements

Before the first long scroll, show:

1. report date and price-data date
2. data freshness or operational warning
3. Market Regime and score
4. change from the previous regime/score
5. primary caution
6. Daily Action List count

Candidate cards must contain:

- bucket, code and name
- research-priority score, clearly labelled as research priority rather than a forecast
- one compact reason containing the strongest current evidence
- one compact change statement
- one actionable caution
- direct URL to the same stock detail on the site

The email must not include the full Top100, full sector tables, paper portfolio detail or long methodology text.

## 4. Site first-view requirements

Without requiring detailed table exploration, answer:

1. Is the data current and operationally valid?
2. Is the market strong, neutral, weak or overheated?
3. What changed since the prior report?
4. Which three-to-five names should be researched first?
5. What is the largest current caution?

A stale-data or P0 warning must visually override positive market presentation.

## 5. Exploration requirements

The site must support:

- code/name search
- sector, quality, lifecycle, new-entry and rising-fast filters
- sorting by rank, score, return, volume and relative strength
- direct stock links using `?code=XXXX#ranking` or an equivalent stable route
- stock detail with reason, change, caution and next research question
- mobile card mode that does not require horizontal table reading
- local watchlist
- comparison of up to three stocks
- sector-to-ranking navigation
- visible active filters and one-click reset
- exact workbook download or equivalent source-data access

## 6. Language rules

Use:

- 調査候補
- 調査優先度
- Momentum上位
- シグナル
- 検証中
- 注意

Do not use:

- 買い推奨
- 明日上がる
- 爆益
- 必勝
- 的中
- 今すぐ買う

A score must never be presented as expected future return or probability of profit.

## 7. Quality and evidence

Display clearly:

- Data Quality A/B/C/D and warnings
- price-data date
- update status
- Forward Evidence status and sample sufficiency
- past evidence count when an outcome statistic is shown
- research-only disclaimer

Past backtest evidence and forward evidence must be distinguishable.

## 8. Safety boundary

The presentation layer must remain read-only.

Forbidden:

- live-order controls
- automatic score or weight changes
- automatic strategy changes
- secret, credential or private-address exposure
- silently publishing an older report as current

Volume-ratio weight remains 15 unless a separately governed and approved strategy release changes it.

## 9. Acceptance tests

A release is accepted only when all are true:

- email and site share the same dates and Daily Action List order
- a stock email link opens the matching stock detail
- stale/P0/P1 test fixtures visibly override bullish presentation
- mobile width 390px is usable without horizontal ranking-table dependence
- keyboard focus is visible and search is operable
- no secrets or private email addresses appear in static assets
- workbook/payload hashes remain verifiable
- full strategy and operational CI remain green

## 10. Production observation

After release, verify on at least:

- iPhone Mail or Gmail on iOS
- desktop browser
- narrow mobile browser

Review actual reading time, link success, line wrapping and whether the user can state the market conclusion, primary caution and three research names without opening the workbook.
