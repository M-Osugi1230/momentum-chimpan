# Momentum Chimpan

日本株の引け後データを自動整理し、**今日詳しく調査する5〜10社**を短時間で決めるための研究支援システムです。

特定銘柄の売買を推奨するものではありません。実注文、自動売買、売り推奨、保有銘柄管理の代行、自動的な戦略変更は行いません。

## 目的

毎営業日の引け後に、約3分で次を把握できる状態を作ります。

1. 日本株市場全体のモメンタムは強いか、弱いか
2. 新しく強くなった銘柄は何か
3. 強さが加速・継続・再浮上・失速している銘柄は何か
4. 今日詳しく調査する5〜10社はどれか
5. 各候補について、なぜ今日なのか、何が変わったか、どこに注意するか

North Starは、メール・Webダッシュボード・Workbookのどれから入っても、市場状態、重要な変化、5〜10社の調査順、注意点、データ品質を理解できることです。

## 安全境界

- 実注文・証券会社API・自動売買なし
- Productionのスコア、配点、順位、フィルターを研究結果だけで自動変更しない
- ペーパーポートフォリオは研究専用
- Healthy v1 / Healthy v3等は明示的な承認までシャドー・研究専用
- 戦略変更は事前登録、独立検証、ライブシャドー、手動承認、別PR、ロールバックを必須とする
- 現行の出来高倍率配点は15点のまま

## ドキュメント

- [プロジェクト憲章](docs/PROJECT_CHARTER.md)
- [中長期ロードマップ](docs/ROADMAP.md)
- [システムアーキテクチャ](docs/ARCHITECTURE.md)
- [運用・復旧Runbook](docs/OPERATIONS_RUNBOOK.md)
- [データ辞書](docs/DATA_DICTIONARY.md)
- [KPI辞書](docs/KPI_DICTIONARY.md)
- [Daily Research Focus](docs/DAILY_RESEARCH_FOCUS.md)
- [Priority Outcomes](docs/PRIORITY_OUTCOMES.md)
- [Live Session Eligibility](docs/LIVE_SESSION_ELIGIBILITY.md)
- [研究エビデンス正本](research/evidence_catalog.yaml)
- [出来高倍率Forward Evidence事前登録](research/volume_component_forward_evidence.yaml)

検証済みの研究示唆はGitHub Issue #145 `Research Insight Ledger`へ、事実・解釈・制約・元Artifactを分けて保存します。

## 現在できること

### 日次スクリーニング

- JPX上場銘柄一覧を取得
- Prime / Standard / Growthの通常個別株を中心に分析
- ETF・ETN・REITを除外
- 最低株価・最低売買代金条件
- `yfinance`による価格取得
- Momentum Top100
- 新規ランクイン
- 急上昇
- 過去最高順位更新
- Top30継続
- 年初来高値更新ランキング
- Market Temperatureと市場レジーム
- 業種リーダー
- 相対強度ライフサイクル
- Data Quality A/B/C/D
- ペーパーポートフォリオと実行監査
- 研究エビデンスと運用状態の透明性表示

### Daily Research Focus

日次候補を次の5区分へ整理します。

- **A** — 今日必ず調査する候補。最大5社
- **B** — 時間があれば調査する候補
- **C** — 強さを継続監視する候補
- **Watch** — 順位・継続性・品質の改善待ち
- **Skip** — 現時点の優先度が低い、または品質不足

Daily Action Listは**5〜10社**を目標とします。

A/Bを優先し、A/Bが5社未満の場合だけ、Data Quality Dを除外したC/Watchから補助候補を追加します。品質条件を満たす候補が5社未満なら、無理に埋めず不足件数を表示します。

この補完は表示と調査計画だけに作用し、Momentumスコア・順位・Production戦略・ペーパー執行を変更しません。

各候補には次を表示します。

- 今日の理由
- 前回からの変化
- ライフサイクル
- 市場・業種との相対強度
- Data Quality
- 過熱・流動性・データ上の注意
- 決算・適時開示・チャート等の次の確認事項

## 現行スコア

| 項目 | 最大点 |
|---|---:|
| 年初来高値更新 | 30 |
| 年初来高値の連続更新日数 | 20 |
| 20日騰落率 | 20 |
| 出来高倍率 | 15 |
| 20日線・60日線より上 | 10 |
| 売買代金 | 5 |

出来高倍率15点の歴史検証は期間・銘柄群によって競合しています。

正本`research/evidence_catalog.yaml`の現行判断は次の通りです。

- current decision: `HOLD_UNCHANGED_PENDING_FORWARD_EVIDENCE`
- historical consensus: `CONFLICTED_TIME_UNSTABLE`
- research status: `UNRESOLVED`
- governing study: `volume-component-forward-evidence-v1`
- automatic weight change: disabled
- manual review required

したがって、ライブ前向き検証が成熟するまで15点を据え置きます。

## 日次の表示

### メール

メールは約90秒で把握できる要約です。

- 市場レジームと今日の方針
- 重要な注意
- Daily Action Listの先頭候補
- 新規・継続・脱落
- Data Qualityと運用状態
- Webダッシュボードへのリンク

### Webダッシュボード

既に実装済みで、正確なDaily Workbookから静的サイトを生成します。

- 3分サマリー
- Daily Action List
- 検索・絞り込み可能なMomentum Top100
- 新規、急上昇、優先度変化、ライフサイクル
- 市場温度、業種モメンタム、相対強度
- Data Quality
- ペーパーポートフォリオ
- 運用・復旧・研究エビデンス
- ウォッチリスト、最大3銘柄比較、銘柄深掘りリンク
- 正確なWorkbookのダウンロード

### Workbook

主なシート:

- Summary
- Daily Action List
- Momentum Top100
- New Entries
- Rising Fast
- Top30 Streak
- YTD High Ranking
- Market Temperature
- Scanned Universe
- Errors
- Research Evidence
- ライフサイクル・業種・相対強度
- ペーパーポートフォリオ・実行・運用関連

## 研究の現在地

### Healthy v1

過熱、低流動性、出来高異常、トレンド崩れを落とす候補フィルターとして、Productionより損失を抑える傾向が複数期間で確認されています。

一方、Healthy通過後の細かな順位には安定した単調性が弱く、「Eligible集合の改善」と「1位から30位の並べ替え」は分けて評価します。

### Healthy v3

Healthy v1通過候補のTop10を並べ替える研究仮説として相対改善が見えています。ただし絶対収益、コスト耐性、ライブ前向き条件が未成熟のためProductionへは接続しません。

### Balanced v2

Healthy v1を安定して上回る独立証拠がなく、本番昇格は支持されていません。

### スイング研究

- 固定60営業日保有は支持されない
- 20日以降にも上値余地はあるが、機械的な保有延長の期待値は未確定
- MA20から大きく乖離したProduction上位は長期で不安定
- 出口は固定日数より価格経路・状態変化・利益吐き出しを研究する

研究成果は説明改善、シャドーランキング、研究専用ペーパーコホート、独立ホールドアウトの順に反映します。ライブ前向き証拠と手動承認なしにProductionへ昇格しません。

## ペーパーポートフォリオ

仮想元本1,000万円の研究専用ポートフォリオです。

主な制約:

- 最大10銘柄
- 1銘柄12%上限
- 1業種25%上限
- 1取引あたり元本1%の計画リスク
- 100株単位
- Market Regime別の目標投資比率
- Run Health WARN時は投資比率を抑制
- Run Health FAIL時は新規エントリー停止
- 損切り、利益目標、トレーリング、時間、シグナル退出

今後は強気、やや強気、中立、弱気、過熱警戒、運用WARN/FAILの全局面について、リターンだけでなく、エクスポージャー、集中、回転、出口理由、ドローダウン、MAE/MFE、コスト耐性を分けて検証します。

## 日次処理と自己修復

生産環境の入口は`daily_runner.py`です。

`.github/workflows/daily.yml`は平日16:45 JSTに実行し、概ね次の順序で処理します。

1. 戦略フィンガープリントを固定
2. 全銘柄スキャンとレポート生成
3. メール本文・プレビュー・受付証跡を生成
4. Heartbeatと証拠stamp
5. 復旧可能なstate snapshotをseal
6. 同じRunのsnapshotを隔離Sandboxへ復元して検証
7. state maintenance
8. 完全なProduction stateのみcommit
9. 正確な運用Artifactを保存

`.github/workflows/reconcile-research-ledgers.yml`は、2026-07-13以降の完成済みDaily Runを列挙し、欠損や停止があっても次を冪等に再構築します。

- `research/operations/daily_production_audit.csv`
- `research/evidence/live_session_eligibility.csv`
- `research/priority_outcomes/daily_research_decisions.csv`
- `research/priority_outcomes/daily_research_outcomes.csv`
- 各署名付きStatus・Calibration

Priority Outcomeの取り込みには、正確な同日Recovery `PASS`とProduction state非変更の証明が必須です。

## Forward Evidence

2026年7月13日以降の適格ライブランキングを対象に、現行baselineと出来高倍率除外counterfactualを比較します。

登録済み条件:

- Eligibility Ledgerを通過したライブ日だけ
- strategy fingerprintとランキング行Hashを固定
- 翌営業日調整後寄付
- 同日終値entry禁止
- 5 / 10 / 20営業日の価格を保存し、主要判断は10 / 20営業日
- 市場・業種benchmark
- score multiset維持
- no-lookahead replay
- transaction friction反映

主要gateは10日・20日の双方で:

- baseline 100 outcome以上
- 出来高倍率除外100 outcome以上
- paired signal date 20日以上
- 事前登録した統計条件
- 手動Review

## GitHub Secrets

Repository Settings → Secrets and variables → Actionsに登録します。

| Secret | 内容 |
|---|---|
| `EMAIL_FROM` | Gmail送信元 |
| `EMAIL_TO` | 送信先 |
| `EMAIL_APP_PASSWORD` | Gmailアプリパスワード |

研究WorkflowへメールSecretを渡してはいけません。

## ローカル実行

```bash
python3 -m pip install -r requirements.txt
python3 daily_runner.py
```

少数銘柄の表示確認:

```bash
MOMENTUM_MAX_SYMBOLS=3 python3 daily_runner.py
```

macOS:

```bash
./run_local.sh 3
```

限定銘柄の結果をProduction stateとして保存してはいけません。
