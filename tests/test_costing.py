from codex_token_usage import compute_cost_usd, summarize_with_cost


def test_compute_cost_usd_input_only():
    ev = {"input_tokens": 10000, "cached_input_tokens": 2000, "output_tokens": 500, "reasoning_output_tokens": 0}
    prices = {"input": 0.005, "output": 0.015, "reasoning": 0.015, "cached_input": 0.001}
    # input-only mode: all input at input rate; output at output rate
    cost = compute_cost_usd(ev, prices, use_cached_pricing=False)
    # 10k input -> 10 * 0.005 = 0.05; output 0.5k -> 0.5 * 0.015 = 0.0075; total 0.0575
    assert abs(cost - 0.0575) < 1e-9


def test_compute_cost_usd_with_cached_pricing():
    ev = {"input_tokens": 10000, "cached_input_tokens": 2000, "output_tokens": 500, "reasoning_output_tokens": 0}
    prices = {"input": 0.005, "output": 0.015, "reasoning": 0.015, "cached_input": 0.001}
    # cached pricing: (10k-2k)=8k at 0.005 -> 0.04; 2k at 0.001 -> 0.002; output 0.5k -> 0.0075; total 0.0495
    cost = compute_cost_usd(ev, prices, use_cached_pricing=True)
    assert abs(cost - 0.0495) < 1e-9


def test_summarize_with_cost_multiple_events():
    events = [
        {"input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 100, "reasoning_output_tokens": 0},
        {"input_tokens": 2000, "cached_input_tokens": 500, "output_tokens": 200, "reasoning_output_tokens": 0},
    ]
    prices_conf = {"input": 0.005, "output": 0.015, "reasoning": 0.015, "cached_input": 0.001}
    s = summarize_with_cost(events, prices_conf, per_model=False, forced_model=None, use_cached_pricing=True)
    # Event1: input 1k * 0.005 = 0.005; out 0.1k * 0.015 = 0.0015 -> 0.0065
    # Event2: (2k-0.5k)=1.5k*0.005=0.0075; cached 0.5k*0.001=0.0005; out 0.2k*0.015=0.003 -> 0.011
    # Total = 0.0175
    assert abs(s["cost_usd"] - 0.0175) < 1e-9

