# Research Evidence Catalog

- Catalog version: `2026-07-12-research-evidence-catalog-v1`
- Updated: `2026-07-12`
- Subject: **出来高倍率スコア構成要素**

## Current governed decision

| Item | Current state |
|---|---|
| Production weight | 15 points |
| Decision | `HOLD_UNCHANGED_PENDING_FORWARD_EVIDENCE` |
| Historical consensus | `CONFLICTED_TIME_UNSTABLE` |
| Research status | `UNRESOLVED` |
| Governing study | `volume-component-forward-evidence-v1` |
| Next trigger | `FORWARD_EVIDENCE_GATE_COMPLETION` |
| Automatic weight change | **Forbidden** |
| Automatic strategy change | **Forbidden** |

> Historical evidence conflicts across universe size and time windows; prospective evidence is accumulating.

## Evidence precedence

1. `PROSPECTIVE_LIVE`
2. `EXPANDED_DISJOINT_HISTORICAL`
3. `DISJOINT_CROSS_FOLD_HISTORICAL`
4. `SINGLE_HOLDOUT_HISTORICAL`

## Study chronology

| Study | Class | PR | Status | Universe / folds | Delta excess | p-value | CI |
|---|---|---:|---|---|---:|---:|---|
| `score-component-ablation-v1` | `SINGLE_HOLDOUT_HISTORICAL` | 59 | `REMOVAL_HURTS_VALIDATED` | 72 symbols / 1 fold | -14.74pt | 1.80% | -0.10% to -0.02% |
| `volume-component-cross-fold-v1` | `DISJOINT_CROSS_FOLD_HISTORICAL` | 60 | `DIRECTIONALLY_SUPPORTED` | 144 symbols / 3 folds | -2.32pt | 24.29% | -0.02% to +0.00% |
| `volume-component-expanded-5fold-v1` | `EXPANDED_DISJOINT_HISTORICAL` | 62 | `NOT_SUPPORTED` | 300 symbols / 5 folds | +0.85pt | 74.71% | -0.01% to +0.01% |
| `volume-component-forward-evidence-v1` | `PROSPECTIVE_LIVE` | 61 | `ACCUMULATING` | — | — | — | — |

## Interpretation

- **単一holdoutスコア構成要素ablation**: A single disjoint 72-symbol holdout showed strong harm from removing volume ratio.
- **3fold出来高倍率cross-fold検証**: All three folds showed harm directionally, but the aggregate confidence interval crossed zero.
- **5fold・300銘柄拡張検証**: The full-window effect did not reproduce at 300 symbols; late-period harm was 5/5 while early-period harm was 1/5.
- **事前登録ライブforward evidence**: Prospective next-session executable evidence is accumulating and takes precedence for the next decision.

## Decision guardrails

- Historical evidence is conflicting and time-unstable.
- The current 15-point weight remains unchanged while prospective evidence accumulates.
- A favorable recent subperiod cannot be selected after observing the results.
- Historical results cannot independently authorize a promotion or weight change.
- Any future change requires the prospective evidence gate and manual review.

## Machine-readable source

The canonical source is [`research/evidence_catalog.yaml`](evidence_catalog.yaml).
