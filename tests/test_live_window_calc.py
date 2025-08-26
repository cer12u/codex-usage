from datetime import timedelta
from codex_token_usage import parse_ts, compute_session_origin, reduce_session


def test_gap_window_fixed_after_first_activity():
    now = parse_ts('2025-08-25T11:00:00.000000Z')
    # No usage limit; 5h以上のギャップを挟んで07:45に再開
    activities = [
        parse_ts('2025-08-25T01:10:00.000000Z'),
        parse_ts('2025-08-25T07:45:00.000000Z'),
        parse_ts('2025-08-25T09:13:00.000000Z'),
    ]
    usage_limits = []
    start, end = compute_session_origin(now, activities, usage_limits, None, None, gap_hours=5)
    assert start == parse_ts('2025-08-25T07:45:00.000000Z')
    assert end == parse_ts('2025-08-25T12:45:00.000000Z')

    # 窓内/窓外イベントで集計が固定されること
    events = [
        {"ts": '2025-08-25T07:46:00.000000Z', "input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 100, "reasoning_output_tokens": 0},
        {"ts": '2025-08-25T09:13:00.000000Z', "input_tokens": 2000, "cached_input_tokens": 0, "output_tokens": 100, "reasoning_output_tokens": 0},
        {"ts": '2025-08-25T12:46:00.000000Z', "input_tokens": 3000, "cached_input_tokens": 0, "output_tokens": 100, "reasoning_output_tokens": 0},  # 窓外
    ]
    s = reduce_session(events, start, end, prices_conf={"input": 0.005, "output": 0.015, "reasoning": 0.015, "cached_input": 0.001}, use_cached_pricing=False)
    # 窓内2件のみ合算
    assert s["input_tokens"] == 3000
    assert s["output_tokens"] == 200
    # コスト: input 3k*0.005=0.015, out 0.2k*0.015=0.003 => 0.018
    assert abs(s.get("cost_usd", 0.0) - 0.018) < 1e-9


def test_usage_limit_then_first_activity_window():
    now = parse_ts('2025-08-25T10:30:00.000000Z')
    activities = [
        parse_ts('2025-08-25T05:10:00.000000Z'),  # before usage-limit
        parse_ts('2025-08-25T05:20:00.000000Z'),  # after usage-limit
        parse_ts('2025-08-25T06:00:00.000000Z'),
    ]
    usage_limits = [parse_ts('2025-08-25T05:15:00.000000Z')]
    start, end = compute_session_origin(now, activities, usage_limits, None, None, gap_hours=5)
    assert start == parse_ts('2025-08-25T05:20:00.000000Z')
    assert end == parse_ts('2025-08-25T10:20:00.000000Z')

    events = [
        {"ts": '2025-08-25T05:19:00.000000Z', "input_tokens": 999, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 999},  # before
        {"ts": '2025-08-25T05:21:00.000000Z', "input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 1000},  # in
        {"ts": '2025-08-25T10:21:00.000000Z', "input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 1000},  # after
    ]
    s = reduce_session(events, start, end, prices_conf=None, use_cached_pricing=False)
    assert s["input_tokens"] == 1000
    assert s["total_tokens"] == 1000
