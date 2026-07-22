# Swing Tendency Study v1 出力定義

主要出力は次の通りです。

- `swing_horizon_summary.csv`: 年別・手法別・Top別・保有期間別の平均、中央値、5%トリム平均、勝率、市場超過、20/50/100bpコスト耐性
- `marginal_holding_return.csv`: 0→20日、20→40日、40→60日の追加保有リターン
- `signal_state_swing_summary.csv`: FIRST_PICK、STABLE_REPEAT、IMPROVING、DETERIORATING、REENTRY別
- `regime_swing_summary.csv`: breadth、トレンド、ボラティリティ局面別
- `liquidity_swing_summary.csv`: 流動性・MA20乖離帯別
- `swing_path_detail.csv`: MFE、MAE、最大終値ドローダウン、MFE/MAE発生日、20/40/60日終値リターン
- `threshold_first_touch.csv`: +5/+10/+15%と-5/-8/-10%の先着
- `pre_profit_adverse_excursion.csv`: 利益到達前の最大逆行幅
- `swing_stability_scorecard.csv`: 複数年で方向が揃うかの記述的スコアカード
- `swing_tendency_report_ja.md`: 日本語要約

すべて研究専用であり、本番変更の根拠として自動利用しません。
