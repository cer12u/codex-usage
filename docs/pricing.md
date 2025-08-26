# Pricing and Cost Calculation

## Sources
- Helicone public API (`/api/llm-costs?provider=openai`), cached to `~/.cache/codex-usage/prices.helicone.openai.json`
- Local overrides: `--prices pricing.json`, `--usd-per-1k-*`

## Normalization
- Accepts both per-1k and per-1M fields; per-1M are divided by 1000.
- Resolved fields (USD/1k): `input`, `cached_input`, `output`, `reasoning`.

## Billing modes
- Input-only (default for Codex): all input at `input` rate; cached is ignored.
- Cached-pricing (`--cached-pricing`):
  - `(input - cached) * input_rate + cached * cached_rate`
  - output at `output` rate; reasoning at `reasoning` rate

## JSON output
- Monthly JSON: array of daily records with `cost_usd` (if prices are available).
- Live JSON: single snapshot with totals and `cost_usd`.
