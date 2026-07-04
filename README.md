# モメンタムチンパン戦法 自動ストックピックシステム

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。特定銘柄の売買を推奨するものではありません。最終的な投資判断は利用者自身の責任で行ってください。

## 目的

毎営業日の引け後に日本株を自動スクリーニングし、単なる買い候補TOP10メールではなく、以下を確認できる「日本株モメンタムダッシュボード」を作ります。

- Momentum Top100
- 新規ランクイン
- 急上昇
- 過去最高順位更新
- TOP30継続
- 年初来高値更新ランキング
- Market Temperature

売買注文、自動売買、売り推奨、保有銘柄管理は行いません。

## できること

- JPX の上場銘柄一覧を取得し、Prime / Standard / Growth の通常個別株を中心にスクリーニングします。
- `yfinance` から Yahoo Finance 形式（例: `7203.T`）で株価を取得します。
- 年初来高値、騰落率、移動平均線、出来高倍率、売買代金から 100 点満点のモメンタムスコアを計算します。
- Excel には Momentum Top100 を出力します。
- メール本文には Momentum Top10 に加え、新規ランクイン上位5件、急上昇上位5件、TOP30継続ランキング上位5件を表示します。
- `data/momentum_daily_ranking.csv` に毎日の全スキャン銘柄ランキングを保存します。
- `data/market_temperature.csv` に市場温度感を保存します。
- `output/daily_report.xlsx` を作成し、GitHub Actions artifact に保存します。
- Gmail アプリパスワードで通知メールを送信します。

## 売り候補機能について

この版では売り候補機能を削除しています。

- `holdings.csv` は読み込みません。
- Sell Candidates シートは作成しません。
- メール本文に売り候補は表示しません。
- 売りシグナル計算は行いません。

売却判断は取得単価、保有期間、税金、ポジションサイズ、個別材料に強く依存するため、このツールでは扱いません。

## スコア配点

| 項目 | 最大点 |
| --- | ---: |
| 年初来高値更新 | 30 |
| 年初来高値の連続更新日数 | 20 |
| 20日騰落率 | 20 |
| 出来高倍率 | 15 |
| 20日線・60日線より上 | 10 |
| 売買代金 | 5 |

## 履歴と変化検知

`data/momentum_daily_ranking.csv` には、毎日の全スキャン銘柄を保存します。主な保存項目は以下です。

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

保存時は `code + date` で重複排除します。

変化検知は、単純なカレンダー前日ではなく、履歴ファイル内の直近過去実行日と比較します。そのため、土日・祝日明けでも最新2回の実行結果を比較できます。

### 新規ランクイン

直近過去実行日に Top100 圏外で、今日 Top100 に入った銘柄です。

### 急上昇

`rank_change = previous_rank - current_rank` で計算し、20位以上順位を上げた Top100 銘柄です。

### 過去最高順位更新

過去の保存履歴における自己最高順位を更新した銘柄です。

### TOP30継続

連続して Top30 に入っている実行日数です。`top30_streak` として保存・表示します。

## Market Temperature

`data/market_temperature.csv` に、市場全体の温度感を日次保存します。

| 項目 | 意味 |
| --- | --- |
| `ytd_high_count` | 当日の全スキャン銘柄における年初来高値更新数 |
| `top100_avg_score` | Top100 の平均スコア |
| `top100_avg_return_20d` | Top100 の平均20日騰落率 |
| `top100_avg_volume_ratio` | Top100 の平均出来高倍率 |
| `delta_*` | 直近過去実行日との差分 |

過去の版では年初来高値数が Top100 ベースだった時期があります。この版では全スキャン銘柄ベースです。

## Excelシート構成

`output/daily_report.xlsx` には以下のシートを出力します。

1. `Summary`
2. `Momentum Top100`
3. `New Entries`
4. `Rising Fast`
5. `Top30 Streak`
6. `YTD High Ranking`
7. `Market Temperature`
8. `Scanned Universe`
9. `Errors`

### Summary

実行日、アプリ版、レポート形式、JPX上場銘柄数、通常株ユニバース数、実スキャン対象銘柄数、取得成功数、取得失敗数、年初来高値更新数、Momentum Top100件数、新規ランクイン数、急上昇数、過去最高順位更新数、TOP30継続10日以上件数、検証モード、処理時間秒を表示します。

### Momentum Top100

モメンタムスコア順の上位100銘柄です。確認対象であり、買い推奨ではありません。

### New Entries

直近過去実行日の Top100 圏外から、今日 Top100 に入った銘柄です。

### Rising Fast

直近過去実行日から20位以上順位を上げた銘柄です。

### Top30 Streak

Top30 に連続して入っている銘柄を、継続日数の長い順に表示します。

### YTD High Ranking

当日、年初来高値を更新した銘柄を、連続更新日数、更新回数、スコアの順で表示します。

### Market Temperature

年初来高値更新数、Top100平均スコア、Top100平均20日騰落率、Top100平均出来高倍率と前回比を表示します。

### Scanned Universe

実際にスキャンした銘柄コード、銘柄名、市場、スキャンモードを表示します。`MOMENTUM_MAX_SYMBOLS` を指定した場合は、制限後の銘柄のみになります。

### Errors

株価取得失敗、JPX一覧取得失敗、その他警告を記録します。エラーがあっても可能な限り処理を継続し、Excelに記録します。

## メール通知の見方

メールは HTML / プレーンテキストの両形式で送信します。

まず見るポイントには以下を表示します。

- 買い候補TOP100件数
- 新規ランクイン件数
- 急上昇件数
- TOP30継続10日以上の件数
- 年初来高値更新件数
- 取得失敗件数

続いて以下を表示します。

1. Market Temperature
2. Momentum Top10
3. 新規ランクイン上位5件
4. 急上昇上位5件
5. TOP30継続ランキング上位5件

メール上の表示は、当日確認すべき銘柄を絞るためのダッシュボードです。詳細な数値は GitHub Actions artifact の `daily_report.xlsx` を確認してください。

## 初期設定

Python 3.11 以上を用意し、依存関係をインストールします。

```bash
python3 -m pip install -r requirements.txt
```

主な設定は `config.yaml` で管理します。

- `ranking.buy_candidate_limit`: Excel に出す Momentum Top 件数。既定は 100。
- `ranking.email_top_n`: メールに出す Momentum Top 件数。既定は 10。
- `data.ranking_history_path`: ランキング履歴 CSV の保存先。
- `data.market_temperature_path`: Market Temperature CSV の保存先。
- `data.output_path`: Excel レポートの保存先。
- `data.request_timeout_seconds`: 1銘柄あたりの株価取得タイムアウト秒数。
- `data.progress_log_interval`: 進捗ログを出す銘柄間隔。

## GitHub Secrets

GitHub リポジトリの **Settings → Secrets and variables → Actions → New repository secret** から以下を登録してください。

| Secret | 内容 |
| --- | --- |
| `EMAIL_FROM` | Gmail 送信元アドレス |
| `EMAIL_TO` | 送信先メールアドレス |
| `EMAIL_APP_PASSWORD` | Gmail アプリパスワード |

ローカル実行時は `.env.example` を参考に `.env` を作成できます。

```env
EMAIL_FROM=your-gmail-address@gmail.com
EMAIL_TO=recipient@example.com
EMAIL_APP_PASSWORD=your-gmail-app-password
```

未設定の場合、メール送信だけをスキップします。

## 手動実行

```bash
python3 main.py
```

少数銘柄だけで検証する場合:

```bash
MOMENTUM_MAX_SYMBOLS=3 python3 main.py
```

macOSで簡単に実行する場合:

```bash
./run_local.sh 3
```

引数なしの `./run_local.sh` は全銘柄スキャンです。

## GitHub Actions

`.github/workflows/daily.yml` により、平日 07:45 UTC（日本時間 16:45）に実行されます。`workflow_dispatch` も有効なので、GitHub Actions 画面から手動実行できます。

ワークフローでは以下を行います。

1. Python をセットアップ
2. `requirements.txt` をインストール
3. `python main.py` を実行
4. `output/daily_report.xlsx` を artifact として保存
5. `data/momentum_daily_ranking.csv` と `data/market_temperature.csv` に変更があればコミット

自動コミットのため、workflow には `permissions: contents: write` を設定しています。

## よくある確認

### 最新形式のレポートか確認する

最新形式では Summary に `アプリ版`, `レポート形式`, `Momentum Top100`, `TOP30継続10日以上` が表示されます。

```bash
grep -n "dashboard_full_history" main.py
grep -n "TOP30継続10日以上" main.py
grep -n "Momentum Top100" README.md
```

### JPX一覧取得に失敗する

ネットワーク障害や JPX 側の変更が考えられます。過去に取得した `data/jpx_list_cache.csv` があればそれを利用します。キャッシュもない場合は対象ユニバースを空として終了します。

### メールが送信されない

GitHub Secrets または `.env` の `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_APP_PASSWORD` を確認してください。Gmail は通常のパスワードではなくアプリパスワードが必要です。メール送信に失敗しても Excel 出力は完了します。

### 祝日なのに workflow が実行される

日本の祝日カレンダーによる厳密な停止は行いません。株価データの最新日が実行日と異なる場合はログに出力します。

## 注意事項

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。特定銘柄の売買を推奨するものではありません。最終的な投資判断は利用者自身の責任で行ってください。
