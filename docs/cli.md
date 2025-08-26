# CLI Reference (minimal)

## Modes
- Live (default)
  - `codex-usage` or `python3 codex_token_usage.py`
  - Options:
    - `--json`: emit a single JSON snapshot and exit
    - `--session-bar tokens|cost`: graph scale (tokens default)
- Monthly
  - `codex-usage --monthly` or `python3 codex_token_usage.py --monthly`
  - Options:
    - `--json`: emit JSON array of daily records

## Pricing flags (optional)
- `--prices pricing.json` (flat or {default/models/aliases})
- `--usd-per-1k-input|output|reasoning|cached-input`
- `--cached-pricing` (use cached read pricing)

## Notes
- Legacy/advanced flags remain for compatibility but are not required in typical flows.
