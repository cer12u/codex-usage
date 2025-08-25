from datetime import datetime, timedelta, timezone
from codex_token_usage import (
    update_session_state_with_line,
    parse_ts,
    tail_first_activity_after,
    first_event_dt_after,
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

