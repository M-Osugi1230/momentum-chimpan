# モメンタムチンパン戦法 自動ストックピックシステム

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。特定銘柄の売買を推奨するものではありません。最終的な投資判断は利用者自身の責任で行ってください。

## 目的

毎営業日の引け後に日本株を自動スクリーニングし、確認すべき「買い候補」「売り候補」「警戒」対象を Excel レポートとメールで把握できるようにします。売買注文は一切行いません。

## できること

- JPX の上場銘柄一覧を取得し、Prime / Standard / Growth の通常個別株を中心にスクリーニングします。
- `yfinance` から Yahoo Finance 形式（例: `7203.T`）で株価を取得します。
- 年初来高値、騰落率、移動平均線、出来高倍率、売買代金から 100 点満点のモメンタムスコアを計算します。
- 買い候補上位 30 銘柄を出力します。
- `holdings.csv` に記載した保有銘柄について、売り候補シグナルを確認対象として出力します。
- `data/momentum_history.csv` に年初来高値更新履歴を蓄積します。
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
- `ranking.buy_candidate_limit`: 買い候補出力数
- `ranking.email_top_n`: メール本文に掲載する買い候補数
- `signals.*`: 売り候補シグナルのしきい値
- `data.history_path`: 履歴 CSV の保存先
- `data.output_path`: Excel レポートの保存先

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

## holdings.csv の書き方

保有銘柄だけを売り候補判定します。空でもエラーにはなりません。

```csv
code,name,buy_price,quantity,memo
7203,トヨタ自動車,3000,100,サンプル
```

| 列 | 内容 |
| --- | --- |
| `code` | 4 桁の銘柄コード |
| `name` | 銘柄名 |
| `buy_price` | 買値 |
| `quantity` | 株数 |
| `memo` | 任意メモ |


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
- `data/momentum_history.csv`
- `data/jpx_list_cache.csv`


### 少数銘柄での検証実行

全銘柄を取得する前に、環境変数 `MOMENTUM_MAX_SYMBOLS` で処理対象数を絞って動作確認できます。

```bash
MOMENTUM_MAX_SYMBOLS=3 python3 main.py
```

この場合も `output/daily_report.xlsx` と `data/momentum_history.csv` が作成・更新されます。


### macOS向け：コマンド間違いを避ける簡単実行

macOS で `python` が見つからない場合や、毎回コマンドを打つのが不安な場合は、リポジトリ直下で次を実行してください。

```bash
./run_local.sh 3
```

このスクリプトは `python3` を優先して使い、依存関係をインストールしてから `MOMENTUM_MAX_SYMBOLS=3` で `main.py` を実行します。

## GitHub Actions での自動実行方法

`.github/workflows/daily.yml` により、平日 07:45 UTC（日本時間 16:45）に実行されます。`workflow_dispatch` も有効なので、GitHub Actions 画面から手動実行できます。

ワークフローでは以下を行います。

1. Python をセットアップ
2. `requirements.txt` をインストール
3. `python main.py` を実行
4. `output/daily_report.xlsx` を artifact として保存
5. `data/momentum_history.csv` と JPX 一覧キャッシュに変更があればコミット

自動コミットのため、workflow には `permissions: contents: write` を設定しています。

## 出力 Excel の見方

### Summary

実行日、対象銘柄数、取得成功数、取得失敗数、年初来高値更新銘柄数、買い候補数、売り候補数を表示します。

### Buy Candidates

モメンタムスコア順の買い候補です。「買い候補」は手動確認対象であり、買い推奨ではありません。

主な列:

- `score`: 100 点満点のモメンタムスコア
- `reason`: スコア加点理由
- `ytd_high_flag`: 年初来高値更新の有無
- `return_20d`: 20 日騰落率
- `volume_ratio`: 当日出来高 ÷ 過去 20 日平均出来高
- `trading_value`: 終値 × 出来高

### Sell Candidates

`holdings.csv` の保有銘柄のうち、20 日線割れ、高値から 10% 以上下落、短期モメンタム低下、出来高を伴う急落、60 日線割れに該当した確認対象です。「即売り」ではありません。

### YTD High Ranking

年初来高値の連続更新日数、累積更新回数、スコアなどで並べたランキングです。

### Errors

株価取得失敗、JPX 一覧取得失敗、その他警告を記録します。

## よくあるエラーと対処法

### `yfinance returned empty data`

対象銘柄の Yahoo Finance データが空です。一時的な障害、上場廃止、銘柄コードの不一致などが考えられます。処理全体は停止せず、Errors シートに記録されます。

### JPX 一覧取得に失敗する

ネットワーク障害や JPX 側の変更が考えられます。過去に取得した `data/jpx_list_cache.csv` があればそれを利用します。キャッシュもない場合は `holdings.csv` の銘柄を対象にします。

### メールが送信されない

GitHub Secrets または `.env` の `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_APP_PASSWORD` を確認してください。Gmail は通常のパスワードではなくアプリパスワードが必要です。メール送信に失敗しても Excel 出力は完了します。

### 祝日なのに workflow が実行される

初期版では日本の祝日カレンダーによる厳密な停止は行いません。株価データの最新日が実行日と異なる場合はログに出力します。

## 注意事項

本ツールは日本株のモメンタム確認を補助するためのスクリーニングツールです。特定銘柄の売買を推奨するものではありません。最終的な投資判断は利用者自身の責任で行ってください。
