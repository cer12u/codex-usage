# Live Session Detection (5h window)

Goal: Help you manage token spend within OpenAI's usage-limited window by showing a fixed 5-hour session view.

## Triggers (in order of priority)
- Usage limit hit → first activity after it (latched as session start)
- 5h inactivity gap → first activity after the gap (latched)
- On startup: pick the oldest activity in the last 5 hours as a provisional start (if none, session is unset and shows N/A)

Activity includes:
- `ExecCommandBegin(...)`
- `TaskStarted`
- `TokenCount(TokenUsage {...})`

## Fixed window
- Once the start is chosen, `end = start + 5h`.
- The origin does not slide.
- After 5h, aggregation stops; we show an N/A row until the next trigger starts a new session.

## JSON snapshot
- `--json` with live prints a single snapshot as JSON and exits:
  - `{ mode, start, end, now, duration_sec, input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens, total_tokens, cost_usd? }`

## Timezone
- Display uses your local timezone; JSON uses UTC ISO8601 (Z).
