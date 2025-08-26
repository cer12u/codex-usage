# codex-usage（Token Usage + Cost）

Codex TUI のログ（`~/.codex/log/codex-tui.log`）からトークン使用量を抽出し、ライブ（5hセッション）と月次（日別）で見やすく集計します。単価を与えると推定コスト（$）も表示・JSON出力できます。

## Features
- Live（5hセッション）表示（デフォルト）
- Monthly（直近30日：日別合計）
- JSON出力（Liveスナップショット／Monthly配列）
- 単価からのコスト算出（Helicone自動 or 明示指定）

## Requirements
- Python 3.8+
- Node.js 16+（`npx` での起動時）

## Quick Start（最小の使い方）
- ライブ（セッション）表示（デフォルト）
  - `codex-usage`
  - JSONスナップショット: `codex-usage --json`
- 月次（日別）レポート（直近30日）
  - `codex-usage --monthly`
  - JSON: `codex-usage --monthly --json`
```bash
# 旧ライブ（イベント一覧）を見たい場合のみ（互換）
python3 codex_token_usage.py --live --live-events

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

## Output（概要）
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
  - cost_usd は小数点以下2桁、tokens は k/M 接頭辞（例: 1.5k, 2.3M）

- NDJSON は各レコードを1行JSONとして出力します。

## Notes
- 単価はモデル・時期により変動します。`pricing.example.json` を参考に環境に合わせて調整してください。
- `cached_input_tokens` はキャッシュ読み出し相当のトークン数です。課金有無/単価はモデル依存のため、本ツールでは集計のみ提供します。
- ログ内の diff/引用文字列に含まれるパターンは除外し、実際の `TokenCount` イベントのみを対象にしています。
- ログが極端に大きい場合は `--last` を併用するとメモリを節約できます。

## Live（5hセッション）
- 仕様の詳細は docs/live-session.md を参照してください。

## Examples
より多くの例は docs/cli.md を参照してください。

## Development
- フォーマット/型チェックは特に依存なし。必要に応じて `ruff`/`black` 等をお好みで導入してください。
- 機能要望: 期間指定（`--from/--to`）、日別×モデル別集計（`--by-model`）など拡張可能です。

### Tests（サンプルベース）
- 依存: `pytest` がある環境で実行可能です。
- 実行例:
  ```bash
  pytest -q
  ```
- テスト内容:
  - `tests/test_parsing.py`: ログ行→イベントのパース（`iter_events`）
  - `tests/test_costing.py`: 単価正規化とコスト算出（`compute_cost_usd`, `summarize_with_cost`）
  - `tests/test_sessions.py`: usage limit/5hギャップ→最初のアクティビティで起点をラッチするロジック（`update_session_state_with_line`, `tail_first_activity_after`）

## Run via npx / uv（任意）
- npx（Node ラッパーが Python CLI を起動します）
  ```bash
  # このリポジトリ直下で
  npx . --monthly --json --prices ./pricing.example.json
  # 将来的にnpm公開後は
  # npx codex-usage --daily --last-month --summary --prices ./pricing.json
  ```

- uv（Pythonパッケージとして実行）
  ```bash
  # ローカルから直接
  uv run codex_token_usage.py --monthly --json --prices ./pricing.example.json
  # 将来的に公開後は（エントリポイント）
  # uvx codex-usage --daily --last-month --summary --prices ./pricing.json
  ```


## 詳細ドキュメント
- docs/cli.md: コマンド体系（最小）
- docs/live-session.md: ライブ（5hセッション）表示の詳細
- docs/pricing.md: 単価・コスト計算の詳細
