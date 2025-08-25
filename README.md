# codex-usage (Token Usage + Cost)

Codex TUI のログ（既定: `~/.codex/log/codex-tui.log`）から、`TokenCount(TokenUsage {...})` を抽出し、
イベントごとのトークン使用量や日別の合計を出力する軽量CLIです。任意の単価表を指定すれば、従量課金の推定コストも算出できます。既定は枠付きテーブル表示です。

## Features
- Per-event 出力（デフォルト）
- 日別集計（`--daily`）
- 出力形式: Table（枠付き, 既定）/ TSV / CSV / NDJSON / JSON
- モデル名列の付与（`--include-model`）
- 直近 N イベントへの絞り込み（`--last`）
- 期間フィルタ（`--last-month`, `--since-days`, `--since-date`）
- 単価からのコスト計算（`--prices` または `--usd-per-1k-*`）
  - 自動単価取得: Helicone の公開API（OpenAI）から初回取得・キャッシュ（既定ON）
  - 明示指定: `--prices pricing.json` または `--usd-per-1k-*` で上書き
- Node ラッパー（`npx` 実行用）と Python パッケージ（`uv` 実行用）を同梱

## Requirements
- Python 3.8+
- Node.js 16+（`npx` での起動時）

## Quick Start
既定の挙動（引数なし / 引数あり）:
- 引数なし: ライブ表示（直近5時間, 追従）をテーブルで表示（Ctrl-Cで終了）
- 引数あり（時間指定が無い場合）: 直近30日を日別に月次レポート化（テーブル表示）
```bash
# 引数なし: 直近5時間（ライブ追従表示）
python3 codex_token_usage.py

# ライブの表示幅（時間）を変える
python3 codex_token_usage.py --since-hours 2   # 直近2時間を追従

# モデル名列を含める
python3 codex_token_usage.py --include-model | head

# 引数あり（時間指定なし）: 月次（日別）レポート
python3 codex_token_usage.py

# TSV/CSV/NDJSON/JSON 出力
python3 codex_token_usage.py --daily --format tsv
python3 codex_token_usage.py --daily --format csv --no-header
python3 codex_token_usage.py --daily --format ndjson | head
python3 codex_token_usage.py --daily --format json | jq '.'

# 直近1000イベントのみで日別集計
python3 codex_token_usage.py --last 1000 --daily

# ログパスを明示指定
python3 codex_token_usage.py --log /path/to/codex-tui.log --daily

# 合計サマリを標準エラーへ（合計イベント数と各トークン合計）
python3 codex_token_usage.py --daily --summary 1> daily.tsv 2> summary.txt

# 直近1ヶ月（30日）のみを日別集計
python3 codex_token_usage.py --daily --last-month

# 直近30日の合計とコスト（単価を明示指定, USD/1k tokens）
python3 codex_token_usage.py --last-month --summary \
  --usd-per-1k-input 0.005 --usd-per-1k-output 0.015

# 単価ファイルを使う（例: pricing.example.json）
python3 codex_token_usage.py --daily --last-month --summary --prices ./pricing.example.json

# Helicone から単価を自動取得（既定ON）/ キャッシュ制御
python3 codex_token_usage.py --daily --last-month --summary                 # 初回のみ取得し~/.cacheに保存
python3 codex_token_usage.py --refresh-prices --cache-ttl-hours 1          # 強制更新 / TTL変更
python3 codex_token_usage.py --no-auto-prices --prices ./pricing.json      # 自動取得を無効化

# 枠付き（テーブル）で見やすく出力（Unicode/ASCII）
python3 codex_token_usage.py --daily --last-month --border unicode
python3 codex_token_usage.py --daily --last-month --border ascii
python3 codex_token_usage.py --no-table --daily --last-month --format tsv  # テーブル無効化
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
  - `cost_usd`（単価指定時のみ）

- Table 形式（`--format table`）
  - 上記列を枠付きで整形して表示します。
  - 境界線は `--border unicode`（デフォルト）または `--border ascii` が選べます。
  - テーブル表示は列を簡素化: `input (cached)` / `output (reasoning)` / `total` / `cost_usd`

- NDJSON は各レコードを1行JSONとして出力します。

## Notes
- 単価はモデル・時期により変動します。`pricing.example.json` を参考に環境に合わせて調整してください。
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

## Run via npx / uv
- npx（Node ラッパーが Python CLI を起動します）
  ```bash
  # このリポジトリ直下で
  npx . --daily --last-month --summary --prices ./pricing.example.json
  # 将来的にnpm公開後は
  # npx codex-usage --daily --last-month --summary --prices ./pricing.json
  ```

- uv（Pythonパッケージとして実行）
  ```bash
  # ローカルから直接
  uv run codex_token_usage.py --daily --last-month --summary --prices ./pricing.example.json
  # 将来的に公開後は（エントリポイント）
  # uvx codex-usage --daily --last-month --summary --prices ./pricing.json
  ```
