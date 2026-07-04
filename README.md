# モメンタムチンパン戦法 自動ストックピックシステム

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。特定銘柄の売買を推奨するものではありません。最終的な投資判断は利用者自身の責任で行ってください。

## 目的

毎営業日の引け後に日本株を自動スクリーニングし、確認すべき「Momentum Top100」「新規ランクイン」「急上昇」「TOP30継続」などを Excel レポートとメールダッシュボードで把握できるようにします。売買注文は一切行いません。

## できること

- JPX の上場銘柄一覧を取得し、Prime / Standard / Growth の通常個別株を中心にスクリーニングします。
- `yfinance` から Yahoo Finance 形式（例: `7203.T`）で株価を取得します。
- 年初来高値、騰落率、移動平均線、出来高倍率、売買代金から 100 点満点のモメンタムスコアを計算します。
- Momentum Top100 銘柄を出力します。
- `data/momentum_daily_ranking.csv` にランキング履歴を、`data/market_temperature.csv` に年初来高値数、Top100平均スコア、Top100平均20日騰落率、Top100平均出来高倍率と前回比を蓄積します。
- `output/daily_report.xlsx` を作成し、GitHub Actions artifact に保存します。
- Gmail アプリパスワードで通知メールを送信します。

## できないこと

- 投資助言や特定銘柄の売買推奨は行いません。
- 自動売買や証券会社への注文送信は行いません。
- `yfinance` のデータ品質、遅延、欠損を保証しません。
- 日本の祝日カレンダーによる厳密な休場判定は初期版では行いません。当日データがない場合はログで確認できるようにしています。

## 初期設定方法

1. Python 3.11 以上を用意します。
2. 依存関係をインストールします。

```bash
python3 -m pip install -r requirements.txt
```


> macOS で `zsh: command not found: python` と表示される場合は、`python` ではなく `python3` を使ってください。以降のコマンド例も `python3` 前提です。

3. 必要に応じて `config.yaml` を編集します。

主な設定項目:

- `market.include_markets`: 対象市場
- `market.min_trading_value`: 売買代金のスコア判定基準
- `market.min_price`: 最低株価フィルター
- `ranking.buy_candidate_limit`: Momentum Top 出力数（既定 100）
- `ranking.email_top_n`: メール本文に掲載する Momentum Top 銘柄数
- `data.ranking_history_path`: ランキング履歴 CSV の保存先
- `data.market_temperature_path`: Market Temperature CSV の保存先
- `data.output_path`: Excel レポートの保存先
- `data.request_timeout_seconds`: 1銘柄あたりのyfinance取得タイムアウト秒数
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

## 保有銘柄・売り候補について

今回の版では売り候補機能を削除しました。`holdings.csv` は使用せず、保有銘柄の判定も行いません。

### 実行前にいる場所を確認する

コマンドは必ずこのリポジトリの直下で実行してください。`pwd` で現在地を確認し、`requirements.txt`, `main.py`, `run_local.sh` が見えることを確認します。

```bash
pwd
ls requirements.txt main.py run_local.sh
```

`irbank-scraper` など別のフォルダにいる場合は、このリポジトリを clone した場所へ移動してください。場所が分からない場合は次で探せます。

```bash
find ~ -name run_local.sh -print 2>/dev/null
```


`find` で何も表示されない場合は、このリポジトリがまだMac上にありません。GitHubのリポジトリページで緑色の **Code** ボタンからURLをコピーし、任意の作業フォルダで次を実行してください。

```bash
cd ~
git clone <GitHubでコピーしたURL> momentum-chimpan
cd momentum-chimpan
./run_local.sh 3
```

`<GitHubでコピーしたURL>` は実際のリポジトリURLに置き換えてください。

表示されたパスが `/Users/あなた/xxx/momentum-chimpan/run_local.sh` のような形なら、次のように移動します。

```bash
cd /Users/あなた/xxx/momentum-chimpan
```

## 手動実行方法

```bash
python3 main.py
```

実行後、以下が作成・更新されます。

- `output/daily_report.xlsx`
- `data/momentum_daily_ranking.csv`
- `data/market_temperature.csv`
- `data/jpx_list_cache.csv`


### 少数銘柄での検証実行

全銘柄を取得する前に、環境変数 `MOMENTUM_MAX_SYMBOLS` で処理対象数を絞って動作確認できます。

```bash
MOMENTUM_MAX_SYMBOLS=3 python3 main.py
```

この場合も `output/daily_report.xlsx`, `data/momentum_daily_ranking.csv`, `data/market_temperature.csv` が作成・更新されます。ただし、これは検証モードであり、投資判断用の全銘柄スキャンではありません。


### macOS向け：コマンド間違いを避ける簡単実行

macOS で `python` が見つからない場合や、毎回コマンドを打つのが不安な場合は、リポジトリ直下で次を実行してください。

```bash
./run_local.sh 3
```

このスクリプトは `python3` を優先して使い、依存関係をインストールしてから `MOMENTUM_MAX_SYMBOLS=3` で `main.py` を実行します。引数を付けずに `./run_local.sh` と実行した場合は、全ユニバースをスキャンします。

## GitHub Actions での自動実行方法

`.github/workflows/daily.yml` により、平日 07:45 UTC（日本時間 16:45）に実行されます。`workflow_dispatch` も有効なので、GitHub Actions 画面から手動実行できます。

ワークフローでは以下を行います。

1. Python をセットアップ
2. `requirements.txt` をインストール
3. `python main.py` を実行
4. `output/daily_report.xlsx` を artifact として保存
5. `data/momentum_daily_ranking.csv` と `data/market_temperature.csv` に変更があればコミット

自動コミットのため、workflow には `permissions: contents: write` を設定しています。

## 出力 Excel の見方

### Summary


### 古いレポートを開いていないか確認する

最新形式の `Summary` には `アプリ版`, `レポート形式`, `JPX上場銘柄数`, `通常株ユニバース数`, `実スキャン対象銘柄数`, `検証モード`, `銘柄数制限` が表示されます。`対象銘柄数` だけが表示される場合は、古いレポートを開いているか、古いコードで再生成しています。

```bash
grep -n "JPX上場銘柄数" main.py
grep -n "MAX_SYMBOLS" run_local.sh
rm -f output/daily_report.xlsx
./run_local.sh
open output/daily_report.xlsx
```

実行日、アプリ版、レポート形式、JPX上場銘柄数、通常株ユニバース数、除外銘柄数、実スキャン対象銘柄数、取得成功数、取得失敗数、年初来高値更新銘柄数、Momentum Top100件数、新規ランクイン数、急上昇数、過去最高順位更新数、TOP30継続銘柄数、検証モード、処理時間秒を表示します。

### Momentum Top100

モメンタムスコア順の上位 100 銘柄です。手動確認対象であり、買い推奨ではありません。

主な列:

- `score`: 100 点満点のモメンタムスコア
- `reason`: スコア加点理由
- `score_ytd_high`: 年初来高値更新の加点（最大 30 点）
- `score_ytd_streak`: 年初来高値の連続更新日数の加点（最大 20 点）
- `score_return_20d`: 20 日騰落率の加点（最大 20 点）
- `score_volume_ratio`: 出来高倍率の加点（最大 15 点）
- `score_ma`: 20 日線・60 日線より上にあることの加点（最大 10 点）
- `score_trading_value`: 売買代金 1 億円以上の加点（最大 5 点）
- `ytd_high_flag`: 年初来高値更新の有無
- `return_20d`: 20 日騰落率
- `volume_ratio`: 当日出来高 ÷ 過去 20 日平均出来高
- `trading_value`: 終値 × 出来高

### New Entries

ランキング履歴と比較して新しく Top100 に入った銘柄です。

### Rising Fast

前回順位から大きく上昇した銘柄です。

### Top30 Streak

TOP30 に継続して入っている銘柄と継続日数です。

### YTD High Ranking

過去最高順位を更新した銘柄を中心に確認できます。

### Scanned Universe

実際にスキャンした銘柄コード、銘柄名、市場、スキャンモードを表示します。`MOMENTUM_MAX_SYMBOLS` を指定した場合は、このシートも制限後の銘柄のみになります。


### エラー発生時のバックアップ

Errorsシートに1件以上のエラーがある場合、`data.error_backup_dir` 配下に日時別フォルダを作成し、`errors.csv`, `daily_report.xlsx`, `momentum_daily_ranking.csv`, `jpx_list_cache.csv` を可能な範囲でコピーします。これにより、後から失敗銘柄や当日のレポート状態を確認できます。

### Errors

株価取得失敗、JPX 一覧取得失敗、その他警告を記録します。`timestamp`, `stage`, `code`, `name`, `error`, `recoverable` を出力し、どの処理段階で起きたエラーかを追跡できるようにします。


## メール通知の見方

メール本文は、iPhoneなどのスマートフォンでも読みやすいように HTML メールとして送信します。メールアプリが HTML を表示できない場合に備えて、同じ内容のプレーンテキスト版も同梱します。

表示順は以下です。

1. **Dashboard**: Market Temperature、Momentum Top100、新規ランクイン、急上昇、過去最高順位更新、取得失敗をカード形式で表示します。
2. **Market Temperature**: 年初来高値数、Top100平均スコア、Top100平均20日騰落率、Top100平均出来高倍率と、それぞれの前回比を表示します。
3. **Momentum TopN**: 各銘柄をカード形式で表示し、新規ランクイン、急上昇、最高順位、TOP30継続日数のバッジを表示します。
4. **詳細**: Excel artifact の確認先を表示します。

Momentum Top のスコア内訳は以下の配点です。

| 項目 | 最大点 |
| --- | ---: |
| 年初来高値更新 | 30 |
| 年初来高値の連続更新日数 | 20 |
| 20 日騰落率 | 20 |
| 出来高倍率 | 15 |
| 20 日線・60 日線より上 | 10 |
| 売買代金 | 5 |

メール上の「Momentum Top100」や各バッジは売買指示ではありません。詳細な数値は Excel artifact の `daily_report.xlsx` を確認してください。


### メール改善版の反映確認

最新版では、`main.py` に `MIMEMultipart`, `build_html_email`, `Market Temperature`, `Momentum Top100`, `top30_streak_days` という文言が含まれます。ローカルで更新できているか確認する場合は、以下を実行してください。

```bash
grep -n "MIMEMultipart" main.py
grep -n "build_html_email" main.py
grep -n "Market Temperature" main.py
grep -n "Momentum Top100" main.py
grep -n "top30_streak_days" main.py
```

いずれも行番号が表示されれば、ダッシュボード版が反映されています。

## よくあるエラーと対処法

### `yfinance returned empty data`

対象銘柄の Yahoo Finance データが空です。一時的な障害、上場廃止、銘柄コードの不一致などが考えられます。処理全体は停止せず、Errors シートに記録されます。

### JPX 一覧取得に失敗する

ネットワーク障害や JPX 側の変更が考えられます。過去に取得した `data/jpx_list_cache.csv` があればそれを利用します。キャッシュもない場合は対象ユニバースを空として終了します。




### 実行を途中で止めたい

全銘柄スキャン中に止めたい場合は `control + C` を押してください。ダウンロード中の1銘柄が戻ってきた時点でスキャンを停止し、そこまでの結果でレポート作成へ進みます。古い版では `curl_cffi` のコールバック由来の例外が表示されることがありますが、最新版では停止要求を検知して次の処理へ進みます。

### 全銘柄スキャンに時間がかかる

全銘柄スキャンは `yfinance` へ数千回アクセスするため時間がかかります。ログの `Progress: 100/xxxx symbols processed` のような表示で進捗を確認できます。特定銘柄で長時間止まる場合は、`config.yaml` の `data.request_timeout_seconds` を短くしてください。

### ログメッセージはコマンドとして入力しない

実行中に表示される `Email secrets are not set; skip email` はログメッセージです。ターミナルに入力するコマンドではありません。この表示は、メール設定が未設定のためメール送信だけをスキップしたという意味で、Excelレポート作成が完了していれば問題ありません。

### メールが送信されない

GitHub Secrets または `.env` の `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_APP_PASSWORD` を確認してください。Gmail は通常のパスワードではなくアプリパスワードが必要です。メール送信に失敗しても Excel 出力は完了します。

### 祝日なのに workflow が実行される

初期版では日本の祝日カレンダーによる厳密な停止は行いません。株価データの最新日が実行日と異なる場合はログに出力します。

## 注意事項

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。特定銘柄の売買を推奨するものではありません。最終的な投資判断は利用者自身の責任で行ってください。
