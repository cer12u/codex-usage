# codex-usage（Token Usage + Cost）

Codexのログから「トークン使用量」と「推定コスト（$）」を素早く確認するためのシンプルなCLIです。

## Setup（最小）
- Python 3.8+
  - Live: `python3 codex_token_usage.py`
  - Monthly: `python3 codex_token_usage.py --monthly`
- Node.js 16+（任意）
  - Live: `npx .`
  - Monthly: `npx . --monthly`
- 単価（任意）
  - 自動取得（既定ON）/ 明示指定（`--prices ./pricing.example.json`）の詳細は docs/pricing.md へ

## 使い方（引数なし = Live）
```text
Live Session — start 2025-08-26 10:00 | end 15:00 | now 12:34:56 JST
start — end    dur        input (cached)      output (reasoning)    total      $    graph
──────────────────────────────────────────────────────────────────────────────────────────────────────
10:00—12:34  02:34      1.23M (450k)        210k (0)             1.44M     8.75  ████████
```
- JSONスナップショット: `codex-usage --json`

## 月次（日別）レポート（--monthly）
```text
date         input (cached)      output (reasoning)    total      $
──────────────────────────────────────────────────────────────────────────────
2025-08-24   23.16M (21.39M)     170k (101.95k)        23.33M    12.34
2025-08-25   15.15M (14.59M)     152.31k (72.06k)      15.30M     7.89
──────────────────────────────────────────────────────────────────────────────
sum          38.31M (35.98M)     322.31k (174.01k)     38.63M    20.23
```
- JSON配列: `codex-usage --monthly --json`

## 詳細は docs/ へ
- docs/cli.md: コマンドとオプション（最小）
- docs/live-session.md: Live（5hセッション）検出の詳細
- docs/pricing.md: 単価・コスト計算の詳細

