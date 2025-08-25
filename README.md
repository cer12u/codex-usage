# codex-usage (Token Usage Extractor)

Codex TUI のログ（既定: `~/.codex/log/codex-tui.log`）から、`TokenCount(TokenUsage {...})` を抽出し、
イベントごとのトークン使用量や日別の合計を出力する軽量CLIです。料金換算は行いません。

## Features
- Per-event 出力（デフォルト）
- 日別集計（`--daily`）
- 出力形式: TSV（デフォルト）/ CSV / NDJSON
- モデル名列の付与（`--include-model`）
- 直近 N イベントへの絞り込み（`--last`）

## Requirements
- Python 3.7+

## Quick Start
```bash
# 全イベントをTSVで出力（デフォルト）
python3 codex_token_usage.py | head

# モデル名列を含める
python3 codex_token_usage.py --include-model | head

# 日別集計（UTC日）をTSVで出力
python3 codex_token_usage.py --daily

# CSVでヘッダなし
python3 codex_token_usage.py --daily --format csv --no-header

# NDJSONで出力
python3 codex_token_usage.py --format ndjson | head

# 直近1000イベントのみで日別集計
python3 codex_token_usage.py --last 1000 --daily

# ログパスを明示指定
python3 codex_token_usage.py --log /path/to/codex-tui.log --daily

# 合計サマリを標準エラーへ（合計イベント数と各トークン合計）
python3 codex_token_usage.py --daily --summary 1> daily.tsv 2> summary.txt
```

## Output
- Per-event（TSV/CSV の列）
  - `ts`: タイムスタンプ（UTC, ISO8601風）
  - `input_tokens`
  - `cached_input_tokens`
  - `output_tokens`
  - `reasoning_output_tokens`
  - `total_tokens`
  - `model`（`--include-model` 指定時のみ）

- Daily（TSV/CSV の列）
  - `date`（UTC日, YYYY-MM-DD）
  - `events`（該当日の TokenCount イベント件数）
  - `input_tokens`
  - `cached_input_tokens`
  - `output_tokens`
  - `reasoning_output_tokens`
  - `total_tokens`

- NDJSON は各レコードを1行JSONとして出力します。

## Notes
- 料金換算は行いません（ccusage/claude-monitor 同様、外部の単価テーブルを別で当て込む運用を想定）。
- `cached_input_tokens` はキャッシュ読み出し相当のトークン数です。課金有無/単価はモデル依存のため、本ツールでは集計のみ提供します。
- ログ内の diff/引用文字列に含まれるパターンは除外し、実際の `TokenCount` イベントのみを対象にしています。
- ログが極端に大きい場合は `--last` を併用するとメモリを節約できます。

## Examples
- 直近200イベントを日別集計（TSV）
  ```
  date	events	input_tokens	cached_input_tokens	output_tokens	reasoning_output_tokens	total_tokens
  2025-08-23	126	7526347	6739200	38038	19840	7564385
  2025-08-25	74	2287852	2027264	33664	21312	2321516
  ```

## Development
- フォーマット/型チェックは特に依存なし。必要に応じて `ruff`/`black` 等をお好みで導入してください。
- 機能要望: 期間指定（`--from/--to`）、日別×モデル別集計（`--by-model`）など拡張可能です。
