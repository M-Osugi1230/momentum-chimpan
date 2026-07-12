# Momentum Chimpan

日本株のモメンタムを引け後に自動分析し、**今日どの銘柄から詳しく調査すべきか**を短時間で判断するための研究支援システムです。

特定銘柄の売買を推奨するものではありません。実注文、自動売買、売り推奨、保有銘柄管理、自動的な戦略変更は行いません。

## 目的

毎営業日の引け後に、次の4点を把握できる状態を作ります。

1. 日本株市場全体のモメンタムは強いか、弱いか
2. 新しく強くなった銘柄は何か
3. 強さが加速・継続・再浮上・失速している銘柄は何か
4. 今日どの銘柄を優先して詳しく調査すべきか

North Starは、メールまたはSummaryを約3分見るだけで、市場状態・重要な変化・優先調査候補・注意点を理解できることです。

## ドキュメント

- [プロジェクト憲章](docs/PROJECT_CHARTER.md)
- [中長期ロードマップ](docs/ROADMAP.md)
- [システムアーキテクチャ](docs/ARCHITECTURE.md)
- [運用・復旧Runbook](docs/OPERATIONS_RUNBOOK.md)
- [データ辞書](docs/DATA_DICTIONARY.md)
- [KPI辞書](docs/KPI_DICTIONARY.md)
- [研究エビデンス正本](research/evidence_catalog.yaml)
- [出来高倍率Forward Evidence事前登録](research/volume_component_forward_evidence.yaml)

ロードマップの実行索引はGitHub Issue #78です。

## 現在できること

### 日次スクリーニング

- JPX上場銘柄一覧を取得
- Prime / Standard / Growthの通常個別株を中心に分析
- ETF・ETN・REITを除外
- 最低株価・最低売買代金条件
- `yfinance`による価格取得
- Momentum Top100
- メール上位30件
- 新規ランクイン
- 急上昇
- 過去最高順位更新
- Top30継続
- 年初来高値更新ランキング
- Market Temperature
- 市場レジーム
- 業種リーダー
- 相対強度ライフサイクル
- ペーパーポートフォリオと実行監査
- 研究エビデンスの透明性表示

### 現行スコア

| 項目 | 最大点 |
|---|---:|
| 年初来高値更新 | 30 |
| 年初来高値の連続更新日数 | 20 |
| 20日騰落率 | 20 |
| 出来高倍率 | 15 |
| 20日線・60日線より上 | 10 |
| 売買代金 | 5 |

現在、出来高倍率15点については歴史検証の結果が期間・銘柄群によって競合しています。

正本`research/evidence_catalog.yaml`では次の状態です。

- current decision: `HOLD_UNCHANGED_PENDING_FORWARD_EVIDENCE`
- historical consensus: `CONFLICTED_TIME_UNSTABLE`
- research status: `UNRESOLVED`
- governing study: `volume-component-forward-evidence-v1`
- automatic weight change: disabled
- automatic strategy change: disabled
- manual review required

したがって、現行15点は据え置きます。Forward Evidence完了前に配点を自動変更しません。

## ランキング履歴と変化検知

`data/momentum_daily_ranking.csv`に、毎日の全スキャン銘柄を保存します。

主要項目:

- `date`
- `rank`
- `code`
- `name`
- `score`
- `return_20d`
- `volume_ratio`
- `ytd_high_flag`
- `ytd_high_streak`
- `ytd_high_count`
- `is_top100`
- `is_new_entry`
- `rank_change`
- `is_rising_fast`
- `is_best_rank`
- `top30_streak`
- 現行のライフサイクル・業種・優先度・証拠stamp関連項目

自然キーは`date + code`です。同じ市場日の再実行時も、同じ銘柄を二重保存しないことが前提です。

変化検知はカレンダー前日ではなく、履歴内の直近過去実行日と比較します。土日・祝日明けでも最新2回の結果を比較します。

## Market Temperature

`data/market_temperature.csv`に、市場全体のモメンタム状態を保存します。

主な内容:

- 年初来高値更新銘柄数
- Top100平均スコア
- Top100平均20日騰落率
- Top100平均出来高倍率
- 前回比
- 市場レジーム
- 移動平均ベースの市場breadth
- 過熱銘柄数・比率
- レジーム変化・継続日数

## 日次レポート

生産環境のエントリーポイントは`daily_runner.py`です。

`daily_runner.py`は`main.py`のランキング・履歴・ペーパー処理を実行したうえで、研究エビデンスの現在地をExcelとメールへ表示します。表示層からスコアやproduction stateを変更しません。

生成物:

- `output/daily_report.xlsx`
- HTMLメール
- プレーンテキストメール
- `output/run.log`
- 運用・証拠・復旧診断

Workbookの中心は次の情報です。

- Summary
- Momentum Top100
- New Entries
- Rising Fast
- Top30 Streak
- YTD High Ranking
- Market Temperature
- Scanned Universe
- Errors
- Research Evidence
- 現行アプリが生成するライフサイクル・業種・ペーパー・実行関連シート

`Research Evidence`では、出来高倍率15点据え置き、歴史エビデンスの競合、Forward Evidenceの件数・paired日数・統計状態を確認できます。

## 日次メール

現在の設定では、メールにMomentum上位30件を表示します。

メールでは、ランキングだけでなく次の情報を確認できます。

- 市場温度
- 主要な変化
- 新規ランクイン
- 急上昇
- Top30継続
- 年初来高値更新
- 業種・相対強度・ライフサイクル
- 取得失敗や注意点
- 現行研究判断とForward Evidence進捗

今後はIssue #70に基づき、A / B / C / Watch / Skipの調査優先度へ再設計します。

## ペーパートレードと研究専用実行

システムはペーパー状態と実行監査を保存できますが、実注文は行いません。

主なstate:

- `data/paper_portfolio.csv`
- `data/paper_trade_history.csv`
- `data/paper_equity_history.csv`
- `data/execution_audit.csv`

ペーパーデータは候補選定やExitの研究・レビューに使用します。自動的に本番戦略へ昇格しません。

## Forward Evidence

2026年7月13日以降のライブランキングを対象に、現行baselineと出来高倍率除外counterfactualを比較します。

登録済み条件:

- ライブランキング履歴のみ
- strategy fingerprint付き
- 同日終値entry禁止
- 翌営業日調整後寄付
- 5 / 10 / 20営業日
- 市場・業種benchmark
- score multiset維持
- no-lookahead replay
- transaction friction反映

主要gateは10日・20日の双方で:

- baseline 100 outcome以上
- 出来高倍率除外 100 outcome以上
- paired signal date 20日以上

Raw signals・価格・outcomesはGitHub Actions artifactに残します。

リポジトリへ保存するのは署名済みcompact statusのみです。

- `data/volume_component_forward_status.json`

このstatusが欠損・改ざん・不正な場合、日次表示は安全側の警告と0件表示へ戻り、出来高倍率15点を維持します。

## GitHub Actions日次運用

`.github/workflows/daily.yml`は平日16:45 JSTに実行されます。

主な順序:

1. `main`をcheckout
2. Pythonと依存関係を準備
3. 戦略フィンガープリントをsnapshot
4. `python daily_runner.py`
5. operational heartbeatを生成
6. ランキングとレポートの証拠stampを確認
7. 復旧可能なstate snapshotをseal
8. stateを検証し保存期間を管理
9. 完全なproduction stateだけをcommit
10. レポートと診断Artifactを保存
11. 失敗時は通知し、最終gateをfailureにする

production stateのallowlistと復旧方法は[Architecture](docs/ARCHITECTURE.md)と[Operations Runbook](docs/OPERATIONS_RUNBOOK.md)を参照してください。

## セットアップ

Python 3.11以上を用意します。GitHub ActionsではPython 3.12を使用しています。

```bash
python3 -m pip install -r requirements.txt
```

依存関係の再現性確認では`requirements.lock`も使用します。

## GitHub Secrets

Repository Settings → Secrets and variables → Actionsに登録します。

| Secret | 内容 |
|---|---|
| `EMAIL_FROM` | Gmail送信元 |
| `EMAIL_TO` | 送信先 |
| `EMAIL_APP_PASSWORD` | Gmailアプリパスワード |

未設定の場合、メール送信だけをスキップできる経路があります。研究Workflowへメールsecretを渡してはいけません。

## ローカル実行

現行のレポート構成を確認する場合:

```bash
python3 daily_runner.py
```

少数銘柄の検証:

```bash
MOMENTUM_MAX_SYMBOLS=3 python3 daily_runner.py
```

macOS helper:

```bash
./run_local.sh 3
```

引数なしの`./run_local.sh`は全銘柄スキャンです。

限定銘柄の検証結果をproduction stateとして保存してはいけません。

## 主な設定

`config.yaml`:

- `market.include_markets`
- `market.exclude_etf`
- `market.exclude_reit`
- `market.min_trading_value`
- `market.min_price`
- `ranking.buy_candidate_limit`
- `ranking.email_top_n`
- `data.lookback_days`
- 各履歴・出力path
- request timeout
- progress log interval

設定変更がスコア・対象銘柄・実行結果へ影響する場合、strategy fingerprintが変わるproduction changeとして扱います。

## 開発方針

Forward Evidenceが`ACCUMULATING`の間は、新しいスコア最適化を凍結します。

優先する開発:

- 本番運用の安定化
- データ品質
- メールとExcelの調査優先度UX
- 5/10/20営業日の結果追跡
- 証拠・復旧・レビュー品質

凍結する開発:

- 配点変更
- 新しいproductionスコア要素
- 後付けの除外条件
- 結果確認後のgate変更
- 直近好調期間だけを使う最適化
- 自動戦略昇格

## 免責

本システムはモメンタム確認と調査優先順位の支援を目的とします。

投資判断は利用者自身が行ってください。過去の検証結果、ランキング、ペーパートレード、期待値、研究ステータスは将来の成果を保証しません。
