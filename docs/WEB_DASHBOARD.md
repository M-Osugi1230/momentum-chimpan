# Web Dashboard and Concise Email

## Purpose

Momentum Chimpan uses two presentation layers from the same successful daily report.

- **Email** is the three-minute decision digest.
- **Web dashboard** is the full research workspace.

Neither layer changes Momentum scores, ranking, research priorities, paper execution, strategy fingerprints, or production state.

## Email contract

The email contains only:

1. market regime and one-line guidance;
2. up to five A/B Daily Action List candidates;
3. why today, what changed, and the main caution;
4. important change counts and Data Quality status;
5. operational health and Forward Evidence status;
6. the web dashboard link.

The email does not repeat the Top30, full sector tables, paper portfolio detail, or long evidence sections.

## Dashboard contract

The dashboard is a dependency-free static site generated from the exact `daily_report.xlsx` artifact. It includes:

- Daily Action List explanations;
- searchable and filterable Momentum Top100;
- ranking history and stock detail;
- new, continued, and dropped priority candidates;
- sector momentum and sector leaders;
- market and sector relative strength;
- relative-strength lifecycle;
- Data Quality grades and warnings;
- paper portfolio and signal performance;
- research evidence, release readiness, and operational health;
- exact workbook download.

## Data flow

1. `Daily Momentum Report` completes successfully.
2. The daily workflow uploads the exact workbook and state research files as an artifact.
3. `Publish Momentum Dashboard` verifies the exact source run is a completed successful run from `main` and is not a pull-request run.
4. It downloads that exact artifact by source run ID.
5. `site_builder.py` creates and validates `output/site/`.
6. GitHub Pages receives the dedicated static Pages artifact.

The Pages workflow has read-only access to repository contents and does not commit or push production files.

## Static output

- `index.html`
- `404.html`
- `assets/styles.css`
- `assets/app.js`
- `assets/data.js`
- `downloads/daily_report.xlsx`
- `site_manifest.json`
- `.nojekyll`

`site_manifest.json` binds the published site to the exact workbook SHA-256 and hashes every published file.

## Backfill

Use the `Publish Momentum Dashboard` workflow dispatch input `source_run_id` to publish an earlier successful Daily Momentum Report artifact. The source run is revalidated through the GitHub Actions API before its artifact is used.

For the first production report, use source run ID `29243611472`.

## GitHub Pages prerequisite

Repository Settings → Pages must use **GitHub Actions** as the publishing source. This is a one-time repository setting. After that, each successful daily source run updates the site automatically.

## Safety boundary

- research-only presentation;
- no live orders;
- no credentials or recipient addresses;
- no score, weight, filter, priority, or strategy changes;
- current volume-ratio weight remains 15 points;
- no automatic promotion from research evidence.
