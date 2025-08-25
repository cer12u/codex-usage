import io
from codex_token_usage import iter_events


def test_iter_events_parses_token_counts_and_model():
    # Sample log lines: SessionConfigured sets model; TokenCount events follow
    lines = [
        '2025-08-25T10:00:00.000000Z  INFO handle_codex_event: SessionConfigured(SessionConfiguredEvent { session_id: abc, model: "gpt-5", history_log_id: 1, history_entry_count: 1 })\n',
        '2025-08-25T10:00:05.000000Z  INFO handle_codex_event: TokenCount(TokenUsage { input_tokens: 1000, cached_input_tokens: Some(200), output_tokens: 300, reasoning_output_tokens: Some(0), total_tokens: 1300 })\n',
        '2025-08-25T10:00:06.000000Z  INFO handle_codex_event: TokenCount(TokenUsage { input_tokens: 500, cached_input_tokens: Some(0), output_tokens: 50, reasoning_output_tokens: Some(10), total_tokens: 560 })\n',
    ]

    evs = list(iter_events(lines, include_model=True))
    assert len(evs) == 2
    assert evs[0]["input_tokens"] == 1000
    assert evs[0]["cached_input_tokens"] == 200
    assert evs[0]["output_tokens"] == 300
    assert evs[0]["reasoning_output_tokens"] == 0
    assert evs[0]["total_tokens"] == 1300
    assert evs[0]["model"] == "gpt-5"

