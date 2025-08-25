from datetime import datetime, timedelta, timezone
from codex_token_usage import (
    update_session_state_with_line,
    parse_ts,
    tail_first_activity_after,
    first_event_dt_after,
    compute_session_origin,
    reduce_session,
)


def test_usage_limit_then_activity_latches_start(tmp_path):
    lines = [
        '2025-08-25T00:00:00.000000Z  INFO handle_codex_event: TokenCount(TokenUsage { input_tokens: 100, cached_input_tokens: Some(0), output_tokens: 10, reasoning_output_tokens: Some(0), total_tokens: 110 })\n',
        '2025-08-25T05:10:00.000000Z  INFO handle_codex_event: Error(ErrorEvent { message: "You\'ve hit your usage limit. ..." })\n',
        '2025-08-25T05:20:00.000000Z  INFO handle_codex_event: ExecCommandBegin(ExecCommandBeginEvent { call_id: "call_X", command: ["bash","-lc","pytest -q"], cwd: "/tmp", parsed_cmd: [Test { cmd: ["pytest","-q"] }] })\n',
    ]
    state = {}
    for ln in lines:
        update_session_state_with_line(state, ln)
    assert state.get("session_start") == parse_ts('2025-08-25T05:20:00.000000Z')
    assert state.get("session_end") == parse_ts('2025-08-25T05:20:00.000000Z') + timedelta(hours=5)


def test_gap_over_5h_activity_latches_start():
    lines = [
        '2025-08-25T00:00:00.000000Z  INFO handle_codex_event: TokenCount(TokenUsage { input_tokens: 100, cached_input_tokens: Some(0), output_tokens: 10, reasoning_output_tokens: Some(0), total_tokens: 110 })\n',
        '2025-08-25T05:05:00.000000Z  INFO handle_codex_event: ExecCommandBegin(ExecCommandBeginEvent { call_id: "call_Y", command: ["bash","-lc","echo hi"], cwd: "/tmp", parsed_cmd: [Unknown { cmd: ["echo","hi"] }] })\n',
    ]
    state = {}
    for ln in lines:
        update_session_state_with_line(state, ln)
    assert state.get("session_start") == parse_ts('2025-08-25T05:05:00.000000Z')


def test_tail_first_activity_after(tmp_path):
    p = tmp_path / "log.txt"
    p.write_text(
        '\n'.join([
            '2025-08-25T00:00:00.000000Z  INFO handle_codex_event: TokenCount(TokenUsage { input_tokens: 1, cached_input_tokens: Some(0), output_tokens: 0, reasoning_output_tokens: Some(0), total_tokens: 1 })',
            '2025-08-25T05:00:00.000000Z  INFO handle_codex_event: ExecCommandBegin(ExecCommandBeginEvent { call_id: "call1", command: ["bash","-lc","ls"], cwd: "/tmp", parsed_cmd: [Unknown { cmd: ["ls"] }] })',
            '2025-08-25T06:00:00.000000Z  INFO handle_codex_event: TaskStarted',
        ])
    )
    base = parse_ts('2025-08-25T04:59:59.000000Z')
    dt = tail_first_activity_after(str(p), base_dt=base)
    assert dt == parse_ts('2025-08-25T05:00:00.000000Z')


def test_compute_session_origin_prefers_usage_limit_then_activity():
    now = parse_ts('2025-08-25T06:00:00.000000Z')
    activities = [
        parse_ts('2025-08-25T05:00:00.000000Z'),
        parse_ts('2025-08-25T05:10:00.000000Z'),
    ]
    usage_limits = [parse_ts('2025-08-25T05:05:00.000000Z')]
    start, end = compute_session_origin(now, activities, usage_limits, None, None, gap_hours=5.0)
    assert start == parse_ts('2025-08-25T05:10:00.000000Z')
    assert end == parse_ts('2025-08-25T10:10:00.000000Z')


def test_reduce_session_filters_events_and_sums_cost():
    start = parse_ts('2025-08-25T05:00:00.000000Z')
    end = parse_ts('2025-08-25T06:00:00.000000Z')
    events = [
        {"ts": '2025-08-25T05:10:00.000000Z', "input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 100, "reasoning_output_tokens": 0},
        {"ts": '2025-08-25T06:10:00.000000Z', "input_tokens": 2000, "cached_input_tokens": 0, "output_tokens": 100, "reasoning_output_tokens": 0},
    ]
    prices = {"input": 0.005, "output": 0.015, "reasoning": 0.015, "cached_input": 0.001}
    s = reduce_session(events, start, end, prices, use_cached_pricing=False)
    # only first event in window
    assert s["input_tokens"] == 1000
    assert s["output_tokens"] == 100
    # cost: 1k*0.005 + 0.1k*0.015 = 0.0065
    assert abs(s.get("cost_usd", 0.0) - 0.0065) < 1e-9
