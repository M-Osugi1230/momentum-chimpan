# モメンタムチンパン戦法 日本株モメンタムダッシュボード

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。特定銘柄の売買を推奨するものではありません。最終的な投資判断は利用者自身の責任で行ってください。

## 目的

毎営業日の引け後に日本株を自動スクリーニングし、モメンタムの強い銘柄、ランキング変化、市場温度感を Excel レポートとメールで確認できるようにします。売買注文は一切行いません。

## 今回の方針変更

旧版にあった `holdings.csv` ベースの売り候補機能は削除しました。

- `holdings.csv` は読み込みません。
- `Sell Candidates` シートは作成しません。
- メール本文にも売り候補は表示しません。
- 20日線割れなどの売りシグナル計算は行いません。

現在の目的は、保有銘柄の売却判断ではなく、日本株全体のモメンタム上位銘柄とその変化を追跡することです。

## できること

- JPX の上場銘柄一覧を取得し、Prime / Standard / Growth の通常個別株を中心にスクリーニングします。
- `yfinance` から Yahoo Finance 形式（例: `7203.T`）で株価を取得します。
- 年初来高値、騰落率、移動平均線、出来高倍率、売買代金から 100 点満点のモメンタムスコアを計算します。
- Excel にはモメンタム上位 100 銘柄を出力します。
- メールには要点と上位 10 銘柄を表示します。
- `data/momentum_history.csv` に年初来高値更新履歴を蓄積します。
- `data/momentum_daily_ranking.csv` に毎日の全銘柄ランキング履歴を蓄積します。
- `data/market_temperature.csv` に市場温度感を蓄積します。
- `output/daily_report.xlsx` を作成し、GitHub Actions artifact に保存します。
- Gmail アプリパスワードで HTML メールを送信します。

## できないこと

- 投資助言や特定銘柄の売買推奨は行いません。
- 自動売買や証券会社への注文送信は行いません。
- 売り候補、損益管理、保有銘柄管理は行いません。
- `yfinance` のデータ品質、遅延、欠損を保証しません。
- 日本の祝日カレンダーによる厳密な休場判定は行いません。当日データがない場合はログで確認できるようにしています。

## 初期設定方法

1. Python 3.11 以上を用意します。
2. 依存関係をインストールします。

```bash
python3 -m pip install -r requirements.txt
```

macOS で `zsh: command not found: python` と表示される場合は、`python` ではなく `python3` を使ってください。

3. 必要に応じて `config.yaml` を編集します。

主な設定項目:

- `market.include_markets`: 対象市場
- `market.min_trading_value`: 売買代金のスコア判定基準
- `market.min_price`: 最低株価フィルター
- `ranking.buy_candidate_limit`: Excel に出すモメンタム上位数。初期値は 100 です。
- `ranking.email_top_n`: メール本文に掲載する上位銘柄数。初期値は 10 です。
- `data.history_path`: 年初来高値履歴 CSV の保存先
- `data.ranking_history_path`: 日次ランキング履歴 CSV の保存先
- `data.market_temperature_path`: Market Temperature CSV の保存先
- `data.output_path`: Excel レポートの保存先
- `data.request_timeout_seconds`: 1銘柄あたりの yfinance 取得タイムアウト秒数
- `data.progress_log_interval`: 進捗ログを出す銘柄間隔
- `data.error_backup_dir`: エラー発生時のバックアップ保存先

## GitHub Secrets の設定方法

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

未設定の場合、メール送信はスキップされます。

## 手動実行方法

コマンドは必ずリポジトリ直下で実行してください。

```bash
pwd
ls requirements.txt main.py run_local.sh
python3 main.py
```

実行後、主に以下が作成・更新されます。

- `output/daily_report.xlsx`
- `data/momentum_history.csv`
- `data/momentum_daily_ranking.csv`
- `data/market_temperature.csv`
- `data/jpx_list_cache.csv`

### 少数銘柄での検証実行

全銘柄を取得する前に、環境変数 `MOMENTUM_MAX_SYMBOLS` で処理対象数を絞って動作確認できます。

```bash
MOMENTUM_MAX_SYMBOLS=100 python3 main.py
```

macOS では次の簡易スクリプトも使えます。

```bash
./run_local.sh 100
```

引数を付けずに `./run_local.sh` と実行した場合は、全ユニバースをスキャンします。

## GitHub Actions での自動実行方法

`.github/workflows/daily.yml` により、平日 07:45 UTC（日本時間 16:45）に実行されます。`workflow_dispatch` も有効なので、GitHub Actions 画面から手動実行できます。

ワークフローでは以下を行います。

1. Python をセットアップ
2. `requirements.txt` をインストール
3. `python main.py` を実行
4. `output/daily_report.xlsx` を artifact として保存
5. `data/momentum_history.csv`, `data/momentum_daily_ranking.csv`, `data/market_temperature.csv`, JPX 一覧キャッシュに変更があればコミット

自動コミットのため、workflow には `permissions: contents: write` を設定しています。

## モメンタムスコアの見方

100 点満点です。

| 項目 | 最大点 | 意味 |
| --- | ---: | --- |
| 年初来高値更新 | 30 | 当日の高値または終値が年初来高値を更新 |
| 年初来高値の連続更新日数 | 20 | 更新が連続しているほど加点 |
| 20 日騰落率 | 20 | 直近20営業日の上昇率 |
| 出来高倍率 | 15 | 当日出来高 ÷ 過去20日平均出来高 |
| 20 日線・60 日線より上 | 10 | トレンドが上向きか |
| 売買代金 | 5 | 売買代金が1億円以上か |

## 変化検知の見方

### 新規ランクイン

直近の過去実行日では TOP100 圏外、または履歴なしだった銘柄が、今日 TOP100 に入った状態です。休日明けでも単純な前日ではなく、`data/momentum_daily_ranking.csv` にある直近の過去実行日と比較します。

### 急上昇

直近の過去実行日と比べて、順位が 20 位以上上がった銘柄です。

```text
rank_change = previous_rank - current_rank
```

例: 前回 85 位、今回 40 位なら `rank_change = 45` で急上昇です。

### 過去最高順位更新

その銘柄の過去ランキング履歴の中で、今日の順位が最も良い場合です。

### TOP30継続

連続した実行日に TOP30 に入り続けている日数です。営業日ベースの履歴で見るため、土日祝日をまたいでも、実行日の連続としてカウントします。

## Market Temperature の見方

Market Temperature は、モメンタム市場全体の温度感を表します。

| 指標 | 意味 |
| --- | --- |
| `ytd_high_count` | 当日の年初来高値更新銘柄数 |
| `top100_avg_score` | TOP100 の平均スコア |
| `top100_avg_return_20d` | TOP100 の平均20日騰落率 |
| `top100_avg_volume_ratio` | TOP100 の平均出来高倍率 |
| `*_change` | 直近の過去実行日との差分 |

保存先は `data/market_temperature.csv` です。

## 出力 Excel の見方

### Summary

実行日、アプリ版、JPX上場銘柄数、通常株ユニバース数、除外銘柄数、実スキャン対象銘柄数、取得成功数、取得失敗数、年初来高値更新銘柄数、買い候補TOP100件数、新規ランクイン件数、急上昇件数、TOP30継続10日以上件数、比較対象日、検証モード、処理時間秒を表示します。

### Momentum Top100

モメンタムスコア順の TOP100 です。主な列は以下です。

- `rank`: 今日の順位
- `score`: 100 点満点のモメンタムスコア
- `rank_change`: 直近の過去実行日からの順位変化
- `new_entry`: 新規ランクインかどうか
- `rising_fast`: 急上昇かどうか
- `best_rank_update`: 過去最高順位更新かどうか
- `top30_streak`: TOP30 継続日数
- `reason`: スコア加点理由
- `score_ytd_high`, `score_ytd_streak`, `score_return_20d`, `score_volume_ratio`, `score_ma`, `score_trading_value`: スコア内訳

### New Entries

TOP100 に新規ランクインした銘柄です。前回TOP100圏外から入ってきた銘柄を確認します。

### Rising Fast

前回比で20位以上上昇した銘柄です。短期間で注目度が上がった可能性があります。

### Top30 Streak

TOP30 に連続して入っている銘柄です。継続的に強い銘柄を確認します。

### YTD High Ranking

年初来高値の連続更新日数、累積更新回数、スコアなどで並べたランキングです。

### Market Temperature

市場温度感の履歴です。年初来高値更新数、TOP100平均スコア、TOP100平均20日騰落率、TOP100平均出来高倍率と、その前回比を確認します。

### Scanned Universe

実際にスキャンした銘柄コード、銘柄名、市場、スキャンモードを表示します。`MOMENTUM_MAX_SYMBOLS` を指定した場合は、このシートも制限後の銘柄のみになります。

### Errors

株価取得失敗、JPX 一覧取得失敗、その他警告を記録します。`timestamp`, `stage`, `code`, `name`, `error`, `recoverable` を出力し、どの処理段階で起きたエラーかを追跡できるようにします。

## メール通知の見方

メール本文は、iPhoneなどのスマートフォンでも読みやすい HTML メールとして送信します。メールアプリが HTML を表示できない場合に備えて、同じ内容のプレーンテキスト版も同梱します。

表示順は以下です。

1. **まず見るポイント**: 買い候補TOP100件、新規ランクイン件数、急上昇件数、TOP30継続10日以上の件数、年初来高値更新件数、取得失敗件数をカード形式で表示します。
2. **実行状況**: スキャン対象数、取得成功率、比較対象日、処理時間などをまとめて表示します。
3. **スコアの見方**: 100 点満点の配点を短く説明します。
4. **モメンタムTOP10**: メール本文には上位10件だけを表示します。
5. **新規ランクイン 上位5件**: 前回TOP100圏外から入った銘柄を表示します。
6. **急上昇 上位5件**: 前回比で20位以上上昇した銘柄を表示します。
7. **TOP30継続 上位5件**: TOP30に入り続けている銘柄を表示します。
8. **エラー・詳細**: 取得失敗の有無と、Excel artifact の確認先を表示します。

メール上の銘柄は売買指示ではありません。詳細な数値と全TOP100は Excel artifact の `daily_report.xlsx` を確認してください。

## エラー発生時のバックアップ

Errorsシートに1件以上のエラーがある場合、`data.error_backup_dir` 配下に日時別フォルダを作成し、`errors.csv`, `daily_report.xlsx`, `momentum_history.csv`, `momentum_daily_ranking.csv`, `market_temperature.csv`, `jpx_list_cache.csv` を可能な範囲でコピーします。

## よくあるエラーと対処法

### `yfinance returned empty data`

対象銘柄の Yahoo Finance データが空です。一時的な障害、上場廃止、銘柄コードの不一致などが考えられます。処理全体は停止せず、Errors シートに記録されます。

### JPX 一覧取得に失敗する

ネットワーク障害や JPX 側の変更が考えられます。過去に取得した `data/jpx_list_cache.csv` があればそれを利用します。キャッシュもない場合はレポートにエラーを残し、空のユニバースとして安全に終了します。

### 実行を途中で止めたい

全銘柄スキャン中に止めたい場合は `control + C` を押してください。停止要求を検知して、可能な範囲でそこまでの結果をレポート化します。

### 全銘柄スキャンに時間がかかる

全銘柄スキャンは `yfinance` へ数千回アクセスするため時間がかかります。ログの `Progress: 100/xxxx symbols processed` のような表示で進捗を確認できます。特定銘柄で長時間止まる場合は、`config.yaml` の `data.request_timeout_seconds` を短くしてください。

### メールが送信されない

GitHub Secrets または `.env` の `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_APP_PASSWORD` を確認してください。Gmail は通常のパスワードではなくアプリパスワードが必要です。メール送信に失敗しても Excel 出力は完了します。

### 祝日なのに workflow が実行される

日本の祝日カレンダーによる厳密な停止は行いません。ランキング比較は単純な前日ではなく、履歴ファイルに存在する直近の過去実行日と比較します。

## 注意事項

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。特定銘柄の売買を推奨するものではありません。最終的な投資判断は利用者自身の責任で行ってください。
