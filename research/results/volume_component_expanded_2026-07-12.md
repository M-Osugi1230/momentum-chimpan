# 出来高倍率スコア構成要素：5fold拡張検証結果

- 実行日: 2026-07-12
- GitHub Actions run: `29188906688`
- Artifact: `volume-component-expanded-validation-29188906688`
- Artifact ID: `8258998686`
- Artifact SHA-256: `4a75280ad477b918ee03830ae928e6b133afd37c788902214b91b8f571451952`
- Study version: `2026-07-11-volume-component-cross-fold-v1`
- Aggregate guard: `2026-07-12-volume-component-complete-case-v1`
- Final status: **`NOT_SUPPORTED`**

## 結論

PR #60で事前登録されていた本番規模設計（5fold × 60銘柄、500 calendar days）を前倒し実行した。

全期間では、出来高倍率を除外した場合に成績が悪化したfoldは2/5に留まり、3/5では除外側が改善した。中央値・平均値ともに`tested - baseline`がプラスであり、出来高倍率の正の寄与は完全分離foldをまたいで再現しなかった。

したがって、出来高倍率の現行15点配点を歴史データだけで正当化する根拠にも、削除・減点する根拠にもならない。**配点は変更せず、2026-07-13以降の事前登録済みforward evidenceを主要な次期判断材料とする。**

一方で、後半期間では5/5foldすべてで出来高倍率除外が悪化した。前半期間では1/5foldのみ悪化しており、出来高倍率の効果には強い時間依存性がある。この所見は探索的解釈として残すが、基準変更や直近期間への再適合には使用しない。

## 事前登録設計

- 5つの完全分離fold
- 各fold 60銘柄、合計300銘柄
- JPX33業種sector-stratified selection
- fold間の銘柄重複0件
- 500 calendar days
- 5営業日ごとのranking snapshot
- baseline vs distribution-preserving `drop_volume_ratio`
- 翌営業日調整後寄付
- 同一の資金、コスト、リスク、退出条件
- full / early / late
- sample adequate foldのみを集約
- 全評価foldの完全共通日のみを集約
- 5営業日block sign-flip
- moving-block bootstrap 2,000回

結果確認後にvalidation gateは変更していない。

## Aggregate result

| Metric | Result |
|---|---:|
| Fold count | 5 |
| Evaluable folds | 5 |
| 出来高倍率除外で悪化したfold | 2 / 5 |
| Harm-direction fraction | 40.0% |
| Early-period harm fraction | 20.0% |
| Late-period harm fraction | 100.0% |
| Median delta excess | +0.8510pt |
| Mean delta excess | +0.4678pt |
| Median delta max drawdown | -0.1511pt |
| Complete-case dates | 275 |
| Excluded incomplete dates | 0 |
| Mean daily difference | +0.0014% |
| Bootstrap 95% CI | -0.0077% ～ +0.0108% |
| Two-sided p-value | 74.71% |
| Improvement one-sided p-value | 37.83% |
| Harm one-sided p-value | 62.22% |
| Final status | `NOT_SUPPORTED` |

`delta`およびdaily differenceは`drop_volume_ratio - baseline`。負値が出来高倍率ありの優位を示し、正値が除外側の優位を示す。

## Fold results

| Fold | Baseline trades | Tested trades | Baseline return | Tested return | Delta excess | Delta DD | Early delta | Late delta | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| fold_01 | 111 | 112 | +28.83% | +26.27% | -2.57pt | -0.68pt | +0.82pt | -0.81pt | REMOVAL_HURTS |
| fold_02 | 87 | 85 | +11.61% | +8.01% | -3.60pt | +0.35pt | -2.59pt | -0.50pt | REMOVAL_HURTS |
| fold_03 | 90 | 93 | +11.52% | +14.38% | +2.87pt | +0.98pt | +0.84pt | -0.10pt | REMOVAL_IMPROVES_OR_NEUTRAL |
| fold_04 | 98 | 96 | +15.15% | +19.94% | +4.79pt | -0.15pt | +5.25pt | -1.92pt | REMOVAL_IMPROVES_OR_NEUTRAL |
| fold_05 | 105 | 104 | +20.60% | +21.45% | +0.85pt | -0.16pt | +1.75pt | -0.28pt | REMOVAL_IMPROVES_OR_NEUTRAL |

## Evidence chronology

### PR #59：72銘柄holdout

- baseline return: +16.66%
- volume-ratio removal return: +1.92%
- delta excess: -14.74pt
- FDR q-value: 1.80%
- status: `REMOVAL_HURTS_VALIDATED`

単一holdoutでは強い正の寄与が確認された。

### PR #60：3fold × 48銘柄、420日

- 3/3foldで除外が悪化
- median delta excess: -2.32pt
- two-sided p-value: 24.29%
- bootstrap CIは0を跨ぐ
- status: `DIRECTIONALLY_SUPPORTED`

方向は再現したが統計的確証には至らなかった。

### 今回：5fold × 60銘柄、500日

- 2/5foldで除外が悪化
- 3/5foldで除外側が改善
- full-window status: `NOT_SUPPORTED`
- late-periodでは5/5foldで除外が悪化

銘柄集合と期間を拡張すると全期間の寄与は再現せず、時間依存性が明確になった。

### PR #61：prospective forward evidence

- registration date: 2026-07-12
- eligible signal date: 2026-07-13以降
- next-session adjusted-open execution
- 5 / 10 / 20営業日
- current strategy fingerprint付きlive historyのみ
- 必要サンプル未達中は`ACCUMULATING`

今後の判断では、同じ過去期間への追加適合ではなく、事前登録後のforward evidenceを優先する。

## Governance decision

- `promotion_evidence_allowed=false`
- `automatic_weight_change=false`
- `automatic_strategy_change=false`
- manual review required
- production state mutations: 0
- same-day close entry: prohibited
- live orders: none
- execution mode: `RESEARCH_AND_PAPER_ONLY`

### 現時点の判断

1. 出来高倍率15点は変更しない。
2. 歴史的な正の寄与を確定事項として扱わない。
3. 後半期間だけを選んだ再最適化をしない。
4. forward trackerで必要サンプルを蓄積する。
5. forward evidenceが基準を満たした後にのみ、配点比較の新しい事前登録研究を検討する。
