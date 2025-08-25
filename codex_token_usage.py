#!/usr/bin/env python3
"""
Codex TUI log → token usage extractor.

Parses ~/.codex/log/codex-tui.log (by default) and emits per-event token counts
found in TokenCount(TokenUsage {...}) lines. Optionally includes the most recent
model seen from SessionConfigured lines.

Output formats: table (default), tsv, csv, ndjson, json.

Aggregation:
- Per-event (default)
- Daily totals with --daily

Costing:
- Compute pay-as-you-go cost from token usage with --prices or per-1k flags
- Include cost in daily rows and summary when pricing is provided

Example:
  python3 codex_token_usage.py --format tsv --include-model --summary
  python3 codex_token_usage.py --last 100 --format ndjson
  python3 codex_token_usage.py --log /path/to/log --format csv --no-header
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import deque
from typing import Deque, Dict, Iterable, Iterator, List, Optional, Tuple, Any, Set
from datetime import datetime, timedelta, timezone
import time
import signal
import urllib.request
import urllib.error
import time
import signal


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\S+?)\s")
EVENT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+\s+\w+\s+handle_codex_event:\s+TokenCount\(TokenUsage\b")
MODEL_RE = re.compile(r"SessionConfigured\(.*?model:\s*\"([^\"]+)\"", re.IGNORECASE)
SESSION_CONFIG_RE = re.compile(r"handle_codex_event:\s+SessionConfigured\(SessionConfiguredEvent\b", re.IGNORECASE)
USAGE_LIMIT_RE = re.compile(r"handle_codex_event:\s+Error\(ErrorEvent\b.*?usage\s+limit", re.IGNORECASE)


def parse_number(field: str, line: str, default: int = 0) -> int:
    """Extract a numeric field which may appear as `X: 123`, `X: Some(123)` or `X: None`.
    Returns `default` (0) when not present or None.
    """
    # Match order: Some(n) | bare n | None
    m = re.search(fr"{re.escape(field)}:\s*(?:Some\((\d+)\)|(\d+)|None)", line)
    if not m:
        return default
    g1, g2 = m.group(1), m.group(2)
    if g1 is not None:
        return int(g1)
    if g2 is not None:
        return int(g2)
    return default


def parse_number_any(fields: List[str], line: str, default: int = 0) -> int:
    """Try multiple field names until one matches (including zero or None as explicit values)."""
    sentinel = -1_000_000_001  # unlikely token count
    for f in fields:
        v = parse_number(f, line, sentinel)
        if v != sentinel:
            return v
    return default


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def iter_events(lines: Iterable[str], include_model: bool = False) -> Iterator[Dict[str, object]]:
    """Yield dicts for each TokenCount event found.

    Dict keys: ts, input_tokens, cached_input_tokens, output_tokens,
               reasoning_output_tokens, total_tokens, (optional) model.
    """
    current_model: Optional[str] = None
    for raw in lines:
        line = strip_ansi(raw.rstrip("\n"))

        # Track latest configured model if requested
        if include_model and "SessionConfigured" in line and "model:" in line:
            mm = MODEL_RE.search(line)
            if mm:
                current_model = mm.group(1)

        # Only accept real event lines; avoid matches from diffs or quoted commands
        if not EVENT_RE.match(line):
            continue

        ts_match = TS_RE.search(line)
        ts = ts_match.group(1) if ts_match else ""

        # Field aliases to be robust to differing log keys
        input_tokens = parse_number_any([
            "input_tokens", "prompt_tokens", "prompt_input_tokens", "tokens_in"
        ], line, 0)
        cached_tokens = parse_number_any([
            "cached_input_tokens", "prompt_cached", "cache_read_tokens", "cache_read", "cached_tokens", "cached_prompt_tokens",
        ], line, 0)
        output_tokens = parse_number_any([
            "output_tokens", "completion_tokens", "tokens_out"
        ], line, 0)
        reasoning_tokens = parse_number_any([
            "reasoning_output_tokens", "reasoning_tokens"
        ], line, 0)
        total_tokens = parse_number_any([
            "total_tokens", "total"
        ], line, 0)

        event = {
            "ts": ts,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
        }
        if include_model:
            event["model"] = current_model
        yield event


def parse_ts(ts: str) -> Optional[datetime]:
    """Parse ISO-like timestamp prefix to aware UTC datetime when possible."""
    if not ts:
        return None
    s = ts.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def filter_since(events: Iterable[Dict[str, Any]], since_dt: Optional[datetime]) -> List[Dict[str, Any]]:
    if since_dt is None:
        return list(events)
    kept: List[Dict[str, Any]] = []
    for ev in events:
        ts = str(ev.get("ts", ""))
        dt = parse_ts(ts)
        if dt is None:
            continue
        if dt >= since_dt:
            kept.append(ev)
    return kept


def write_tsv(events: Iterable[Dict[str, object]], include_model: bool, header: bool, out) -> None:
    fields = [
        "ts",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ]
    if include_model:
        fields.append("model")
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    if header:
        writer.writerow(fields)
    for ev in events:
        writer.writerow([ev.get(k, "") for k in fields])


def write_csv(events: Iterable[Dict[str, object]], include_model: bool, header: bool, out) -> None:
    fields = [
        "ts",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ]
    if include_model:
        fields.append("model")
    writer = csv.writer(out, lineterminator="\n")
    if header:
        writer.writerow(fields)
    for ev in events:
        writer.writerow([ev.get(k, "") for k in fields])


def write_ndjson(events: Iterable[Dict[str, object]], out) -> None:
    for ev in events:
        out.write(json.dumps(ev, ensure_ascii=False) + "\n")


def write_json_array(events: List[Dict[str, Any]], out) -> None:
    out.write(json.dumps(events, ensure_ascii=False) + "\n")


def is_number_like(val: Any) -> bool:
    if isinstance(val, (int, float)):
        return True
    if isinstance(val, str):
        try:
            float(val)
            return True
        except Exception:
            return False
    return False


def _trim_float_str(s: str) -> str:
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def format_tokens(n: int) -> str:
    try:
        n = int(n)
    except Exception:
        return str(n)
    if n >= 1_000_000:
        return _trim_float_str(f"{n/1_000_000:.2f}") + "M"
    if n >= 1_000:
        return _trim_float_str(f"{n/1_000:.2f}") + "k"
    return str(n)


def render_table(headers: List[str], rows: List[List[Any]], border: str = "unicode", header_row: bool = True, out = sys.stdout, right_align_columns: Optional[Set[int]] = None, rule_before_rows: Optional[Set[int]] = None) -> None:
    # Convert values to strings and determine alignments
    str_rows: List[List[str]] = []
    right_align: List[bool] = [False] * len(headers)
    for r in rows:
        sr: List[str] = []
        for i, v in enumerate(r):
            if v is None:
                v = ""
            if isinstance(v, float):
                # Preserve up to 6 decimals for floats
                s = f"{v:.6f}".rstrip("0").rstrip(".") if not v.is_integer() else str(int(v))
            else:
                s = str(v)
            sr.append(s)
            if is_number_like(v):
                right_align[i] = True
        str_rows.append(sr)

    # Force right alignment for specified columns (e.g., annotated numerics like "123 (45)")
    if right_align_columns:
        for idx in right_align_columns:
            if 0 <= idx < len(right_align):
                right_align[idx] = True

    widths = [len(h) for h in headers]
    for sr in str_rows:
        for i, s in enumerate(sr):
            if i < len(widths):
                widths[i] = max(widths[i], len(s))

    if border == "ascii":
        tl, tc, tr = "+", "+", "+"
        ml, mc, mr = "+", "+", "+"
        bl, bc, br = "+", "+", "+"
        v, h = "|", "-"
    else:
        tl, tc, tr = "┌", "┬", "┐"
        ml, mc, mr = "├", "┼", "┤"
        bl, bc, br = "└", "┴", "┘"
        v, h = "│", "─"

    def line(left: str, mid: str, right: str) -> str:
        parts = [left]
        for i, w in enumerate(widths):
            parts.append(h * (w + 2))
            parts.append(mid if i < len(widths) - 1 else right)
        return "".join(parts)

    def fmt_row(cells: List[str]) -> str:
        parts = [v]
        for i, w in enumerate(widths):
            cell = cells[i] if i < len(cells) else ""
            if right_align[i]:
                cell = cell.rjust(w)
            else:
                cell = cell.ljust(w)
            parts.append(" " + cell + " ")
            parts.append(v)
        return "".join(parts)

    # Top border
    out.write(line(tl, tc, tr) + "\n")
    # Header
    if header_row:
        out.write(fmt_row(headers) + "\n")
        out.write(line(ml, mc, mr) + "\n")
    # Body
    for idx, sr in enumerate(str_rows):
        if rule_before_rows and idx in rule_before_rows:
            out.write(line(ml, mc, mr) + "\n")
        out.write(fmt_row(sr) + "\n")
    # Bottom border
    out.write(line(bl, bc, br) + "\n")


def local_tzinfo():
    try:
        return datetime.now().astimezone().tzinfo or timezone.utc
    except Exception:
        return timezone.utc


def detect_session_starts_and_state(log_path: str) -> Dict[str, Any]:
    """Scan the log once to detect the latest 5h session start based on rules:
    1) The SessionConfigured immediately after a 'usage limit' error.
    2) A SessionConfigured where the gap from the previous log line is >= 5 hours.
    Returns a dict with keys: session_start (datetime|None), last_ts (datetime|None), usage_limit_pending (bool)
    """
    state: Dict[str, Any] = {"session_start": None, "last_ts": None, "usage_limit_pending": False, "last_session_configured": None}
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = strip_ansi(raw.rstrip("\n"))
                ts_match = TS_RE.search(line)
                ts = ts_match.group(1) if ts_match else ""
                dt = parse_ts(ts)
                # Rule (a): mark pending when usage limit error appears
                if USAGE_LIMIT_RE.search(line):
                    state["usage_limit_pending"] = True
                # Rule (b): on SessionConfigured, check pending or gap>=5h
                if SESSION_CONFIG_RE.search(line):
                    if state.get("usage_limit_pending"):
                        state["session_start"] = dt
                        state["usage_limit_pending"] = False
                    else:
                        last_dt = state.get("last_ts")
                        if last_dt is not None and dt is not None and (dt - last_dt) >= timedelta(hours=5):
                            state["session_start"] = dt
                    # track most recent SessionConfigured regardless
                    if dt is not None:
                        state["last_session_configured"] = dt
                if dt is not None:
                    state["last_ts"] = dt
    except Exception:
        pass
    return state


def update_session_state_with_line(state: Dict[str, Any], raw_line: str) -> None:
    """Incrementally update session state with a new log line."""
    line = strip_ansi(raw_line.rstrip("\n"))
    ts_match = TS_RE.search(line)
    ts = ts_match.group(1) if ts_match else ""
    dt = parse_ts(ts)
    if USAGE_LIMIT_RE.search(line):
        state["usage_limit_pending"] = True
    if SESSION_CONFIG_RE.search(line):
        if state.get("usage_limit_pending"):
            state["session_start"] = dt
            if dt is not None:
                state["session_end"] = dt + timedelta(hours=5)
            state["usage_limit_pending"] = False
        else:
            last_dt = state.get("last_ts")
            if last_dt is not None and dt is not None and (dt - last_dt) >= timedelta(hours=5):
                state["session_start"] = dt
                state["session_end"] = dt + timedelta(hours=5)
        if dt is not None:
            state["last_session_configured"] = dt
    if dt is not None:
        state["last_ts"] = dt


def tail_first_session_configured_after(log_path: str, base_dt: datetime, tail_lines: int = 50000) -> Optional[datetime]:
    """Scan only the tail of the log to find the earliest SessionConfigured at/after base_dt.
    Returns that datetime or None when not found.
    """
    try:
        from collections import deque as _deque
        dq = _deque(maxlen=tail_lines)
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                dq.append(raw)
        candidate: Optional[datetime] = None
        for raw in dq:
            line = strip_ansi(raw.rstrip("\n"))
            if not SESSION_CONFIG_RE.search(line):
                continue
            ts_match = TS_RE.search(line)
            ts = ts_match.group(1) if ts_match else ""
            dt = parse_ts(ts)
            if dt is None:
                continue
            if dt >= base_dt and (candidate is None or dt < candidate):
                candidate = dt
        return candidate
    except Exception:
        return None


def first_event_dt_after(base_dt: datetime, events: List[Dict[str, Any]]) -> Optional[datetime]:
    cand: Optional[datetime] = None
    for ev in events:
        dt = parse_ts(str(ev.get("ts", "")))
        if dt is None:
            continue
        if dt >= base_dt and (cand is None or dt < cand):
            cand = dt
    return cand


def summarize(events: Iterable[Dict[str, object]]) -> Dict[str, int]:
    total = {
        "events": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }
    for ev in events:
        total["events"] += 1
        total["input_tokens"] += int(ev.get("input_tokens", 0) or 0)
        total["cached_input_tokens"] += int(ev.get("cached_input_tokens", 0) or 0)
        total["output_tokens"] += int(ev.get("output_tokens", 0) or 0)
        total["reasoning_output_tokens"] += int(ev.get("reasoning_output_tokens", 0) or 0)
        total["total_tokens"] += int(ev.get("total_tokens", 0) or 0)
    return total


def aggregate_daily(events: Iterable[Dict[str, object]]) -> List[Tuple[str, Dict[str, int]]]:
    """Aggregate events by UTC date string (YYYY-MM-DD). Returns a sorted list.
    Each value dict mirrors summarize() keys (without per-event details).
    """
    daily: Dict[str, Dict[str, int]] = {}
    for ev in events:
        ts = str(ev.get("ts", ""))
        if not ts or "T" not in ts:
            # Skip if timestamp missing or malformed
            continue
        day = ts.split("T", 1)[0]
        bucket = daily.setdefault(
            day,
            {
                "events": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
                "total_tokens": 0,
            },
        )
        bucket["events"] += 1
        bucket["input_tokens"] += int(ev.get("input_tokens", 0) or 0)
        bucket["cached_input_tokens"] += int(ev.get("cached_input_tokens", 0) or 0)
        bucket["output_tokens"] += int(ev.get("output_tokens", 0) or 0)
        bucket["reasoning_output_tokens"] += int(ev.get("reasoning_output_tokens", 0) or 0)
        bucket["total_tokens"] += int(ev.get("total_tokens", 0) or 0)

    # Return sorted by date ascending
    return sorted(daily.items(), key=lambda kv: kv[0])


def fill_missing_days(rows: List[Tuple[str, Dict[str, Any]]], start_date: Optional[datetime], end_date: Optional[datetime]) -> List[Tuple[str, Dict[str, Any]]]:
    """Ensure continuous daily rows between start_date and end_date (UTC),
    filling missing dates with zeros. Dates are strings YYYY-MM-DD.
    """
    if not rows and not start_date:
        return rows
    # Determine date bounds
    if start_date is None:
        # use first row's date
        first = rows[0][0] if rows else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start = datetime.fromisoformat(first).replace(tzinfo=timezone.utc)
    else:
        start = start_date
    if end_date is None:
        # today UTC
        end = datetime.now(timezone.utc)
    else:
        end = end_date
    start_d = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_d = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)

    # Build map
    mp: Dict[str, Dict[str, Any]] = {d: dict(agg) for d, agg in rows}
    out: List[Tuple[str, Dict[str, Any]]] = []
    cur = start_d
    while cur <= end_d:
        key = cur.strftime("%Y-%m-%d")
        if key in mp:
            # ensure zeros for missing numeric fields
            agg = mp[key]
            for k in ["events", "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"]:
                agg[k] = int(agg.get(k, 0) or 0)
            if "cost_usd" in agg:
                agg["cost_usd"] = float(agg.get("cost_usd", 0.0) or 0.0)
        else:
            agg = {
                "events": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            }
        out.append((key, agg))
        cur = cur + timedelta(days=1)
    return out


def trim_leading_zero_days(rows: List[Tuple[str, Dict[str, Any]]]) -> List[Tuple[str, Dict[str, Any]]]:
    """Drop leading days that have only zeros (tokens and cost)."""
    def is_zero_day(agg: Dict[str, Any]) -> bool:
        fields = [
            int(agg.get("input_tokens", 0) or 0),
            int(agg.get("cached_input_tokens", 0) or 0),
            int(agg.get("output_tokens", 0) or 0),
            int(agg.get("reasoning_output_tokens", 0) or 0),
            int(agg.get("total_tokens", 0) or 0),
        ]
        cost = float(agg.get("cost_usd", 0.0) or 0.0)
        return all(v == 0 for v in fields) and cost == 0.0

    i = 0
    while i < len(rows) and is_zero_day(rows[i][1]):
        i += 1
    return rows[i:]


def write_daily_tsv(rows: List[Tuple[str, Dict[str, int]]], header: bool, out) -> None:
    fields = [
        "date",
        "events",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ]
    if rows and any("cost_usd" in agg for _, agg in rows):
        fields.append("cost_usd")
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    if header:
        writer.writerow(fields)
    for day, agg in rows:
        row = [
            day,
            agg.get("events", 0),
            agg.get("input_tokens", 0),
            agg.get("cached_input_tokens", 0),
            agg.get("output_tokens", 0),
            agg.get("reasoning_output_tokens", 0),
            agg.get("total_tokens", 0),
        ]
        if "cost_usd" in agg:
            row.append(f"{agg.get('cost_usd', 0.0):.2f}")
        writer.writerow(row)


def write_daily_csv(rows: List[Tuple[str, Dict[str, int]]], header: bool, out) -> None:
    fields = [
        "date",
        "events",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ]
    if rows and any("cost_usd" in agg for _, agg in rows):
        fields.append("cost_usd")
    writer = csv.writer(out, lineterminator="\n")
    if header:
        writer.writerow(fields)
    for day, agg in rows:
        row = [
            day,
            agg.get("events", 0),
            agg.get("input_tokens", 0),
            agg.get("cached_input_tokens", 0),
            agg.get("output_tokens", 0),
            agg.get("reasoning_output_tokens", 0),
            agg.get("total_tokens", 0),
        ]
        if "cost_usd" in agg:
            row.append(f"{agg.get('cost_usd', 0.0):.2f}")
        writer.writerow(row)


def write_daily_ndjson(rows: List[Tuple[str, Dict[str, int]]], out) -> None:
    for day, agg in rows:
        rec = {"date": day}
        rec.update(agg)
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_daily_json(rows: List[Tuple[str, Dict[str, Any]]], out) -> None:
    arr: List[Dict[str, Any]] = []
    for day, agg in rows:
        rec = {"date": day}
        rec.update(agg)
        arr.append(rec)
    out.write(json.dumps(arr, ensure_ascii=False) + "\n")


def write_events_table(events: List[Dict[str, Any]], include_model: bool, header: bool, border: str, summary: Optional[Dict[str, Any]] = None) -> None:
    # Headers: ts, input (cached), output (reasoning), total, $, [model]
    headers = ["ts", "input (cached)", "output (reasoning)", "total", "$"]
    if include_model:
        headers.append("model")
    # We compute cost externally when rendering live; here we leave it blank; main() will pass cost via ev["__cost_usd"] if available
    rows: List[List[Any]] = []
    for ev in events:
        inp = int(ev.get("input_tokens", 0) or 0)
        cached = int(ev.get("cached_input_tokens", 0) or 0)
        outp = int(ev.get("output_tokens", 0) or 0)
        reas = int(ev.get("reasoning_output_tokens", 0) or 0)
        total = int(ev.get("total_tokens", 0) or 0)
        cost = ev.get("__cost_usd")
        inp_cell = f"{format_tokens(inp)} ({format_tokens(cached)})"
        out_cell = f"{format_tokens(outp)} ({format_tokens(reas)})"
        row: List[Any] = [
            ev.get("ts", ""),
            inp_cell,
            out_cell,
            format_tokens(total),
            (f"{float(cost):.2f}" if cost is not None else ""),
        ]
        if include_model:
            row.append(ev.get("model", ""))
        rows.append(row)
    # Append summary row to the bottom if provided
    if summary is not None:
        rows.append([
            "sum",
            f"{format_tokens(int(summary.get('input_tokens', 0) or 0))} ({format_tokens(int(summary.get('cached_input_tokens', 0) or 0))})",
            f"{format_tokens(int(summary.get('output_tokens', 0) or 0))} ({format_tokens(int(summary.get('reasoning_output_tokens', 0) or 0))})",
            format_tokens(int(summary.get('total_tokens', 0) or 0)),
            (f"{float(summary.get('cost_usd', 0.0) or 0.0):.2f}" if 'cost_usd' in summary else ""),
        ] + ([""] if include_model else []))
    # Right align columns: 1 (i(c)), 2 (o(r)), 3 (t), 4 ($)
    render_table(headers, rows, border=border, header_row=not header is False, right_align_columns={1,2,3,4})


def write_daily_table(rows: List[Tuple[str, Dict[str, Any]]], header: bool, border: str) -> None:
    # Headers: date, input (cached), output (reasoning), total, [$]
    include_cost = bool(rows and any("cost_usd" in agg for _, agg in rows))
    headers = ["date", "input (cached)", "output (reasoning)", "total"]
    if include_cost:
        headers.append("$")
    body: List[List[Any]] = []
    for day, agg in rows:
        inp = int(agg.get("input_tokens", 0) or 0)
        cached = int(agg.get("cached_input_tokens", 0) or 0)
        outp = int(agg.get("output_tokens", 0) or 0)
        reas = int(agg.get("reasoning_output_tokens", 0) or 0)
        total = int(agg.get("total_tokens", 0) or 0)
        row: List[Any] = [day, f"{format_tokens(inp)} ({format_tokens(cached)})", f"{format_tokens(outp)} ({format_tokens(reas)})", format_tokens(total)]
        if include_cost:
            row.append(f"{float(agg.get('cost_usd', 0.0) or 0.0):.2f}")
        body.append(row)
    # Compute and append summary row at the bottom
    sum_i = sum((int(agg.get("input_tokens", 0) or 0) for _, agg in rows), 0)
    sum_c = sum((int(agg.get("cached_input_tokens", 0) or 0) for _, agg in rows), 0)
    sum_o = sum((int(agg.get("output_tokens", 0) or 0) for _, agg in rows), 0)
    sum_r = sum((int(agg.get("reasoning_output_tokens", 0) or 0) for _, agg in rows), 0)
    sum_t = sum((int(agg.get("total_tokens", 0) or 0) for _, agg in rows), 0)
    sum_cost = sum((float(agg.get("cost_usd", 0.0) or 0.0) for _, agg in rows), 0.0) if include_cost else None
    body.append([
        "sum",
        f"{format_tokens(sum_i)} ({format_tokens(sum_c)})",
        f"{format_tokens(sum_o)} ({format_tokens(sum_r)})",
        format_tokens(sum_t),
        (f"{float(sum_cost):.2f}" if sum_cost is not None else ""),
    ] if include_cost else [
        "sum",
        f"{format_tokens(sum_i)} ({format_tokens(sum_c)})",
        f"{format_tokens(sum_o)} ({format_tokens(sum_r)})",
        format_tokens(sum_t),
    ])
    # Right align columns (1: input(cached), 2: output(reasoning), 3: total, 4: $ if present)
    right_cols = {1, 2, 3}
    if include_cost:
        right_cols.add(4)
    # Draw a horizontal rule before the final sum row
    rule_idx = {len(body) - 1} if body else set()
    render_table(headers, body, border=border, header_row=not header is False, right_align_columns=right_cols, rule_before_rows=rule_idx)


def load_prices(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading prices file: {e}", file=sys.stderr)
        return None


def xdg_cache_home() -> str:
    return os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))


def prices_cache_path(provider: str = "openai") -> str:
    cache_dir = os.path.join(xdg_cache_home(), "codex-usage")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"prices.helicone.{provider}.json")


def fetch_helicone_prices(provider: str = "openai", timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    url = f"https://www.helicone.ai/api/llm-costs?provider={provider}"
    req = urllib.request.Request(url, headers={"User-Agent": "codex-usage/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except Exception as e:
        print(f"Warning: failed to fetch Helicone prices: {e}", file=sys.stderr)
        return None


def normalize_helicone_prices(raw: Any) -> Optional[Dict[str, Any]]:
    """Try to normalize Helicone pricing JSON into {default, models, aliases} format.
    Best-effort: looks for per-1k input/output/… under common key names.
    """
    if raw is None:
        return None

    def extract_rates(obj: Dict[str, Any]) -> Optional[Dict[str, float]]:
        # candidate keys for per-1k USD
        key_sets = {
            "input": [
                "input_cost_per_1k_tokens",
                "input_per_1k",
                "prompt_cost_per_1k_tokens",
                "prompt_per_1k",
                "prompt_input_cost_per_1k_tokens",
                "input_per_1k_tokens",
                "input_1k",
                "input",
                "prompt",
            ],
            "output": [
                "output_cost_per_1k_tokens",
                "output_per_1k",
                "completion_cost_per_1k_tokens",
                "completion_per_1k",
                "prompt_output_cost_per_1k_tokens",
                "output",
                "completion",
            ],
            "reasoning": [
                "reasoning_cost_per_1k_tokens",
                "reasoning_per_1k",
                "reasoning",
            ],
            "cached_input": [
                "cached_input_cost_per_1k_tokens",
                "cached_input_per_1k",
                "cached_input",
                "cache",
            ],
        }
        rates: Dict[str, float] = {}
        for key, candidates in key_sets.items():
            val = None
            for c in candidates:
                if c in obj and isinstance(obj[c], (int, float)):
                    val = float(obj[c])
                    break
            if val is not None:
                rates[key] = float(val)

        # Additional per-million fields (convert to per-1k by dividing by 1000)
        per_million_map = {
            "input": [
                "input_cost_per_1m",
                "prompt_input_cost_per_1m",
                "input_per_1m",
                "prompt_per_1m",
                "input_cost_per_million_tokens",
                "input_per_million",
            ],
            "output": [
                "output_cost_per_1m",
                "completion_cost_per_1m",
                "output_per_1m",
                "completion_per_1m",
                "output_cost_per_million_tokens",
                "output_per_million",
            ],
            "reasoning": [
                "reasoning_cost_per_1m",
                "reasoning_per_1m",
                "reasoning_cost_per_million_tokens",
                "reasoning_per_million",
            ],
            "cached_input": [
                "prompt_cache_read_per_1m",
                "cache_read_per_1m",
                "prompt_cache_read_per_million",
                "cached_input_per_1m",
            ],
        }
        for key, candidates in per_million_map.items():
            if key in rates and rates[key] > 0:
                continue
            for c in candidates:
                if c in obj and isinstance(obj[c], (int, float)):
                    try:
                        val = float(obj[c]) / 1000.0  # 1M -> per-1k
                        rates[key] = val
                        break
                    except Exception:
                        pass

        if not rates:
            return None
        # Fill fallback relationships
        if "cached_input" not in rates and "input" in rates:
            rates["cached_input"] = rates["input"]
        if "reasoning" not in rates and "output" in rates:
            rates["reasoning"] = rates["output"]
        for k in ["input", "output", "reasoning", "cached_input"]:
            rates.setdefault(k, 0.0)
        return rates

    models: Dict[str, Any] = {}
    default_rates: Dict[str, float] = {"input": 0.0, "output": 0.0, "reasoning": 0.0, "cached_input": 0.0}

    if isinstance(raw, dict):
        # may be {"models": {...}} or {"data": [...]}
        if "models" in raw and isinstance(raw["models"], dict):
            for name, info in raw["models"].items():
                r = extract_rates(info) or {}
                models[name] = r
        if "data" in raw and isinstance(raw["data"], list):
            for item in raw["data"]:
                if not isinstance(item, dict):
                    continue
                name = item.get("model") or item.get("name") or item.get("id")
                if not name:
                    continue
                r = extract_rates(item) or {}
                models[str(name)] = r
        # Fallback: maybe flat dict with rates
        flat = extract_rates(raw)
        if flat:
            default_rates = flat
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("model") or item.get("name") or item.get("id")
            if not name:
                continue
            r = extract_rates(item) or {}
            models[str(name)] = r

    # Simple aliasing: add "-latest" aliases pointing to the base name
    aliases: Dict[str, str] = {}
    for name in list(models.keys()):
        lat = f"{name}-latest"
        if lat not in models:
            aliases[lat] = name

    return {"default": default_rates, "models": models, "aliases": aliases}


def load_or_fetch_helicone(provider: str, ttl_hours: int, refresh: bool) -> Optional[Dict[str, Any]]:
    path = prices_cache_path(provider)
    # Check cache
    try:
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            age_h = (time.time() - mtime) / 3600.0
            if not refresh and age_h <= float(ttl_hours):
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                return normalize_helicone_prices(raw)
    except Exception:
        pass

    raw = fetch_helicone_prices(provider=provider)
    if raw is None:
        return None
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
    except Exception:
        pass
    return normalize_helicone_prices(raw)


def resolve_model_prices(model: Optional[str], prices: Dict[str, Any]) -> Dict[str, float]:
    """Resolve per-1k USD rates for a given model from a prices dict.
    Prices dict may be either a flat rates object or {models: {name: rates}, default: rates}.
    Missing fields default to 0.0.
    """
    def to_rates(obj: Dict[str, Any]) -> Dict[str, float]:
        return {
            "input": float(obj.get("input", 0.0) or 0.0),
            "cached_input": float(obj.get("cached_input", obj.get("input", 0.0)) or 0.0),
            "output": float(obj.get("output", 0.0) or 0.0),
            "reasoning": float(obj.get("reasoning", obj.get("output", 0.0)) or 0.0),
        }

    if not prices:
        return {"input": 0.0, "cached_input": 0.0, "output": 0.0, "reasoning": 0.0}

    if "models" not in prices:
        return to_rates(prices)

    models = prices.get("models", {}) or {}
    default_rates = to_rates(prices.get("default", {}))
    aliases = prices.get("aliases", {}) or {}
    name = model or ""
    if name in aliases:
        name = aliases[name]
    rates = models.get(name)
    if rates is None:
        return default_rates
    return to_rates(rates)


DEFAULT_FORCED_RATES: Dict[str, float] = {
    "input": 0.005,
    "output": 0.015,
    "reasoning": 0.015,
    "cached_input": 0.005,
}


def effective_rates(ev_model: Optional[str], prices_conf: Optional[Dict[str, Any]], forced_model: Optional[str]) -> Dict[str, float]:
    """Resolve rates using event model if available, otherwise forced model; if still zero, use built-in fallback.
    """
    prices_conf = prices_conf or {}
    # If forced model specified, prefer it
    if forced_model:
        rates = resolve_model_prices(forced_model, prices_conf)
    else:
        rates = resolve_model_prices(ev_model, prices_conf)
    # If all zero, fallback
    if (rates.get("input", 0.0) + rates.get("output", 0.0) + rates.get("reasoning", 0.0) + rates.get("cached_input", 0.0)) == 0.0:
        return dict(DEFAULT_FORCED_RATES)
    return rates


def compute_cost_usd(ev: Dict[str, Any], prices: Dict[str, float], use_cached_pricing: bool = False) -> float:
    input_tokens = float(ev.get("input_tokens", 0) or 0)
    cached_input_tokens = float(ev.get("cached_input_tokens", 0) or 0)
    output_tokens = float(ev.get("output_tokens", 0) or 0)
    reasoning_tokens = float(ev.get("reasoning_output_tokens", 0) or 0)

    cost = 0.0
    # Auto-fallback: if input is zero but cached > 0, treat as cached-pricing even when flag is off
    auto_cached = (not use_cached_pricing) and (input_tokens <= 0 and cached_input_tokens > 0)
    if use_cached_pricing or auto_cached:
        # Bill cached reads at cached rate, and only the input-cached delta at input rate
        billable_input = max(input_tokens - cached_input_tokens, 0.0)
        cost += (billable_input / 1000.0) * prices.get("input", 0.0)
        cost += (cached_input_tokens / 1000.0) * prices.get("cached_input", prices.get("input", 0.0))
    else:
        # Codex-mode default: bill all input at input rate; ignore cached
        cost += (input_tokens / 1000.0) * prices.get("input", 0.0)

    cost += (output_tokens / 1000.0) * prices.get("output", 0.0)
    cost += (reasoning_tokens / 1000.0) * prices.get("reasoning", prices.get("output", 0.0))
    return cost


def summarize_with_cost(events: Iterable[Dict[str, Any]], prices_conf: Optional[Dict[str, Any]], per_model: bool = False, forced_model: Optional[str] = None, use_cached_pricing: bool = False) -> Dict[str, Any]:
    base_totals = summarize(events)
    total_cost = 0.0
    by_model: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        model = str(ev.get("model") or "")
        rates = effective_rates(model if per_model else None, prices_conf or {}, forced_model)
        c = compute_cost_usd(ev, rates, use_cached_pricing=use_cached_pricing)
        total_cost += c
        if per_model:
            key = model or "(unknown)"
            agg = by_model.setdefault(key, {"events": 0, "cost_usd": 0.0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 0})
            agg["events"] += 1
            agg["cost_usd"] += c
            agg["input_tokens"] += int(ev.get("input_tokens", 0) or 0)
            agg["cached_input_tokens"] += int(ev.get("cached_input_tokens", 0) or 0)
            agg["output_tokens"] += int(ev.get("output_tokens", 0) or 0)
            agg["reasoning_output_tokens"] += int(ev.get("reasoning_output_tokens", 0) or 0)
            agg["total_tokens"] += int(ev.get("total_tokens", 0) or 0)
    base_totals["cost_usd"] = total_cost
    if per_model:
        base_totals["by_model"] = by_model
    return base_totals


def run_live(log_path: str, args: argparse.Namespace, prices_conf: Optional[Dict[str, Any]]) -> int:
    """Tail the log and periodically refresh a rolling window view.

    - Window: last N hours (args.since_hours or 5)
    - Refresh: 2 seconds
    - Output: table (forced)
    """
    refresh_sec = 2
    window_hours = args.since_hours or 5
    max_rows = 200

    # Force table for live output
    if args.format != "table":
        print("[info] Live mode forces table output.", file=sys.stderr)
        args.format = "table"

    # Load initial events within window
    events: List[Dict[str, Any]] = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for ev in iter_events(f, include_model=args.include_model):
                events.append(ev)
    except Exception as e:
        print(f"Error opening log: {e}", file=sys.stderr)
        return 1

    # Prepare tail
    try:
        f = open(log_path, "r", encoding="utf-8", errors="ignore")
        f.seek(0, os.SEEK_END)
    except Exception as e:
        print(f"Error tailing log: {e}", file=sys.stderr)
        return 1

    running = True

    def _stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while running:
        # Read any new lines
        while True:
            pos = f.tell()
            line = f.readline()
            if not line:
                f.seek(pos)
                break
            for ev in iter_events([line], include_model=args.include_model):
                events.append(ev)

        # Rolling window filter (now-based)
        now = datetime.now(timezone.utc)
        since_dt = now - timedelta(hours=window_hours)
        filtered = filter_since(events, since_dt)
        # Limit rows for display (most recent last)
        if len(filtered) > max_rows:
            filtered = filtered[-max_rows:]

        # Annotate per-event cost when pricing available or forced model present
        if prices_conf or getattr(args, 'forced_model', None):
            for ev in filtered:
                model = str(ev.get("model") or "")
                rates = effective_rates(model, prices_conf or {}, getattr(args, 'forced_model', None))
                ev["__cost_usd"] = compute_cost_usd(ev, rates, use_cached_pricing=getattr(args, 'cached_pricing', False))

        # Render
        sys.stdout.write("\x1b[2J\x1b[H")  # clear screen, cursor home
        lt = local_tzinfo()
        title = f"Live (last {window_hours}h) — {now.astimezone(lt).strftime('%Y-%m-%d %H:%M:%S %Z')}"
        print(title)
        # Summary row included in table
        if prices_conf or getattr(args, 'forced_model', None):
            s = summarize_with_cost(filtered, prices_conf or {}, per_model=False, forced_model=getattr(args, 'forced_model', None), use_cached_pricing=getattr(args, 'cached_pricing', False))
        else:
            s = summarize(filtered)

        write_events_table(filtered, include_model=args.include_model, header=not args.no_header, border=args.border, summary=s)

        # Sleep until next refresh
        time.sleep(refresh_sec)

    f.close()
    return 0


def build_sessions(events: List[Dict[str, Any]], gap_minutes: int) -> List[Dict[str, Any]]:
    """Group chronologically ordered events into sessions by inactivity gap.
    Returns a list of sessions with totals, cost, start/end, and duration.
    """
    if not events:
        return []
    # Ensure chronological order by timestamp
    def ev_dt(ev: Dict[str, Any]) -> Optional[datetime]:
        return parse_ts(str(ev.get("ts", "")))

    evs = [e for e in events if ev_dt(e) is not None]
    evs.sort(key=lambda e: ev_dt(e))
    sessions: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    last_dt: Optional[datetime] = None
    gap = timedelta(minutes=gap_minutes)

    for ev in evs:
        dt = ev_dt(ev)  # type: ignore[assignment]
        if dt is None:
            continue
        if cur is None:
            cur = {
                "start": dt,
                "end": dt,
                "events": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            }
        else:
            # If gap exceeded, close session and start a new one
            if last_dt is not None and dt - last_dt > gap:
                sessions.append(cur)
                cur = {
                    "start": dt,
                    "end": dt,
                    "events": 0,
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                }
        # accumulate
        cur["events"] += 1  # type: ignore[index]
        cur["end"] = dt  # type: ignore[index]
        cur["input_tokens"] += int(ev.get("input_tokens", 0) or 0)  # type: ignore[index]
        cur["cached_input_tokens"] += int(ev.get("cached_input_tokens", 0) or 0)  # type: ignore[index]
        cur["output_tokens"] += int(ev.get("output_tokens", 0) or 0)  # type: ignore[index]
        cur["reasoning_output_tokens"] += int(ev.get("reasoning_output_tokens", 0) or 0)  # type: ignore[index]
        cur["total_tokens"] += int(ev.get("total_tokens", 0) or 0)  # type: ignore[index]
        cur["cost_usd"] += float(ev.get("__cost_usd", 0.0) or 0.0)  # type: ignore[index]
        last_dt = dt

    if cur is not None:
        sessions.append(cur)

    # Add gap_to_next_sec for each session (except last)
    for i in range(len(sessions) - 1):
        cur_end = sessions[i]["end"]
        next_start = sessions[i + 1]["start"]
        sessions[i]["gap_to_next_sec"] = int((next_start - cur_end).total_seconds())
    if sessions:
        sessions[-1]["gap_to_next_sec"] = None

    # Add duration
    for s in sessions:
        s["duration_sec"] = int((s["end"] - s["start"]).total_seconds())
    return sessions


def run_live_sessions(log_path: str, args: argparse.Namespace, prices_conf: Optional[Dict[str, Any]]) -> int:
    """Live view focused on the latest official 5h session (usage-limit based).
    Determine session_start by rules:
      (1) usage limit error -> next SessionConfigured()
      (2) gap >= 5h before a SessionConfigured()
    Then aggregate from session_start to now, and render a single row with a bar.
    """
    refresh_sec = 2
    fallback_hours = args.since_hours or 5

    # Ensure we have costs on events
    def annotate_cost(ev: Dict[str, Any]) -> None:
        model = str(ev.get("model") or "")
        rates = effective_rates(model, prices_conf or {}, getattr(args, 'forced_model', None))
        ev["__cost_usd"] = compute_cost_usd(ev, rates, use_cached_pricing=getattr(args, 'cached_pricing', False))

    # Initial scan for session state and load initial events
    session_state = detect_session_starts_and_state(log_path)
    events: List[Dict[str, Any]] = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for ev in iter_events(f, include_model=args.include_model):
                events.append(ev)
    except Exception as e:
        print(f"Error opening log: {e}", file=sys.stderr)
        return 1

    # Prepare tail
    try:
        f = open(log_path, "r", encoding="utf-8", errors="ignore")
        f.seek(0, os.SEEK_END)
    except Exception as e:
        print(f"Error tailing log: {e}", file=sys.stderr)
        return 1

    running = True

    def _stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while running:
        # Consume new lines
        while True:
            pos = f.tell()
            line = f.readline()
            if not line:
                f.seek(pos)
                break
            # Update session state from raw log line
            update_session_state_with_line(session_state, line)
            for ev in iter_events([line], include_model=args.include_model):
                events.append(ev)

        now = datetime.now(timezone.utc)
        # Initialize fixed session window once unless updated by triggers
        if session_state.get("session_start") is None and session_state.get("session_end") is None:
            base = now - timedelta(hours=5)
            # Pick the oldest SessionConfigured within the last 5h as the fixed origin
            init_start = tail_first_session_configured_after(log_path, base_dt=base)
            if init_start is not None:
                session_state["session_start"] = init_start
                session_state["session_end"] = init_start + timedelta(hours=5)

        since_dt = session_state.get("session_start")
        end_dt = session_state.get("session_end")
        up_to = min(now, end_dt) if isinstance(end_dt, datetime) else now
        if since_dt is not None:
            window_events = [
                ev for ev in events
                if (parse_ts(str(ev.get("ts", ""))) or now) >= since_dt and (parse_ts(str(ev.get("ts", ""))) or now) <= up_to
            ]
        else:
            window_events = []
        # Annotate costs
        if prices_conf or getattr(args, 'forced_model', None):
            for ev in window_events:
                annotate_cost(ev)

        # Summarize current session window
        if prices_conf or getattr(args, 'forced_model', None):
            s = summarize_with_cost(window_events, prices_conf or {}, per_model=False, forced_model=getattr(args, 'forced_model', None), use_cached_pricing=getattr(args, 'cached_pricing', False))
        else:
            s = summarize(window_events)
        total_tokens = int(s.get("total_tokens", 0) or 0)
        total_cost = float(s.get("cost_usd", 0.0) or 0.0)

        # Compute scale for single bar (relative to rolling fallback window tokens/cost)
        bar_width = 42
        def bar(v: float, vmax: float) -> str:
            if vmax <= 0:
                return ""
            n = max(1, int((v / vmax) * bar_width)) if v > 0 else 0
            return "█" * n

        vmax = float(total_cost) if args.session_bar == "cost" else float(total_tokens)

        # Render screen (latest official session only, local timezone)
        sys.stdout.write("\x1b[2J\x1b[H")
        lt = local_tzinfo()
        start_label = since_dt.astimezone(lt).strftime('%Y-%m-%d %H:%M') if since_dt else '—'
        end_label = end_dt.astimezone(lt).strftime('%H:%M') if isinstance(end_dt, datetime) else now.astimezone(lt).strftime('%H:%M')
        title = f"Live Session — start {start_label} | end {end_label} | now {now.astimezone(lt).strftime('%H:%M:%S %Z')}"
        print(title)
        print()
        # Header line (latest session only)
        print("start — end    dur        input (cached)      output (reasoning)    total      $    graph")
        print("──────────────────────────────────────────────────────────────────────────────────────────────────────")

        if window_events and since_dt is not None:
            inp = int(s.get("input_tokens", 0) or 0)
            cached = int(s.get("cached_input_tokens", 0) or 0)
            outp = int(s.get("output_tokens", 0) or 0)
            reas = int(s.get("reasoning_output_tokens", 0) or 0)
            dur_sec = int((up_to - since_dt).total_seconds())
            dur_str = f"{dur_sec//60:02d}:{dur_sec%60:02d}"
            b = bar(float(total_cost) if args.session_bar == "cost" else float(total_tokens), vmax if vmax > 0 else 1.0)
            print(
                f"{since_dt.astimezone(lt).strftime('%H:%M')}—{up_to.astimezone(lt).strftime('%H:%M')}  {dur_str}   "
                f"{format_tokens(inp):>8} ({format_tokens(cached):>6})   "
                f"{format_tokens(outp):>8} ({format_tokens(reas):>6})   "
                f"{format_tokens(total_tokens):>8}   {total_cost:>6.2f}  {b}"
            )
        else:
            # Avoid an all-zero look: print a minimal header with N/A tokens/cost instead of zeros
            reference_start = since_dt or (now - timedelta(hours=5))
            reference_end = (end_dt if isinstance(end_dt, datetime) else now)
            dur_sec = int((reference_end - reference_start).total_seconds())
            dur_str = f"{dur_sec//60:02d}:{dur_sec%60:02d}"
            print(
                f"{reference_start.astimezone(lt).strftime('%H:%M')}—{reference_end.astimezone(lt).strftime('%H:%M')}  {dur_str}   "
                f"      — (     —)          — (     —)         —       —    "
            )

        time.sleep(refresh_sec)

    f.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract token usage from Codex TUI logs")
    ap.add_argument("--log", default=os.path.expanduser("~/.codex/log/codex-tui.log"),
                    help="Path to codex-tui.log (default: ~/.codex/log/codex-tui.log)")
    # Leave --format unset by default to allow contextual defaulting (table or tsv)
    ap.add_argument("--format", choices=["tsv", "csv", "ndjson", "table", "json"], default=None,
                    help="Output format (default: table; or tsv if --no-table)")
    ap.add_argument("--no-table", action="store_true",
                    help="Disable table output as default; fallback to TSV unless --format is given")
    ap.add_argument("--border", choices=["unicode", "ascii"], default="unicode",
                    help="Table border style when --format table (default: unicode)")
    ap.add_argument("--include-model", action="store_true",
                    help="Include most recent SessionConfigured model in output")
    ap.add_argument("--last", type=int, default=None,
                    help="Limit output to last N events (keeps memory bounded)")
    ap.add_argument("--since-hours", type=int, default=None,
                    help="Only include events in the last N hours (e.g., 5 for live view)")
    ap.add_argument("--live", action="store_true",
                    help="Live mode (defaults to sessions). With no args, shows last 5 hours")
    ap.add_argument("--live-sessions", action="store_true",
                    help="Force live sessions view (default)")
    ap.add_argument("--live-events", action="store_true",
                    help="Legacy live events table instead of sessions")
    ap.add_argument("--session-gap-minutes", type=int, default=10,
                    help="Inactivity threshold (minutes) to split sessions (default: 10)")
    ap.add_argument("--session-bar", choices=["tokens", "cost"], default="tokens",
                    help="Bar chart metric for sessions: total tokens or cost (default: tokens)")
    ap.add_argument("--since-days", type=int, default=None,
                    help="Only include events in the last N days (e.g., 30 for last month)")
    ap.add_argument("--since-date", type=str, default=None,
                    help="Only include events since YYYY-MM-DD (UTC)")
    ap.add_argument("--last-month", action="store_true",
                    help="Shortcut for --since-days 30")
    ap.add_argument("--no-header", action="store_true",
                    help="Do not print header row for csv/tsv")
    ap.add_argument("--summary", action="store_true",
                    help="Print totals summary to stderr at the end")
    ap.add_argument("--daily", action="store_true",
                    help="Aggregate by day (UTC) and output one row per date")

    # Pricing options
    ap.add_argument("--prices", type=str, default=None,
                    help="Path to JSON pricing config. Either flat {input,output,reasoning?,cached_input?} per 1k USD or {default,models,aliases}")
    ap.add_argument("--no-auto-prices", action="store_true",
                    help="Disable auto-fetching Helicone prices on first run")
    ap.add_argument("--refresh-prices", action="store_true",
                    help="Force refresh cached Helicone prices")
    ap.add_argument("--cache-ttl-hours", type=int, default=24,
                    help="Price cache TTL in hours (default: 24)")
    ap.add_argument("--provider", type=str, default="openai",
                    help="Provider for auto price fetch (default: openai)")
    ap.add_argument("--forced-model", type=str, default="gpt-5",
                    help="Force cost calculation to assume this model name (default: gpt-5)")
    ap.add_argument("--usd-per-1k-input", type=float, default=None,
                    help="Override price per 1k input tokens in USD")
    ap.add_argument("--usd-per-1k-output", type=float, default=None,
                    help="Override price per 1k output tokens in USD")
    ap.add_argument("--usd-per-1k-reasoning", type=float, default=None,
                    help="Override price per 1k reasoning tokens in USD")
    ap.add_argument("--usd-per-1k-cached-input", type=float, default=None,
                    help="Override price per 1k cached input tokens in USD")
    ap.add_argument("--cost-by-model", action="store_true",
                    help="When --include-model and prices include models, include per-model cost breakdown in summary")
    ap.add_argument("--cached-pricing", action="store_true",
                    help="Bill cached_input_tokens at cached rate and input-cached at input rate (default off: input-only)")

    args = ap.parse_args()

    # Determine if invoked without any user flags
    no_cli_args = len(sys.argv) == 1

    # Default format: table unless disabled
    if args.format is None:
        args.format = "tsv" if args.no_table else "table"

    log_path = os.path.expanduser(args.log)
    if not os.path.exists(log_path):
        print(f"Error: log file not found: {log_path}", file=sys.stderr)
        return 1

    # For --last, store in a bounded deque; otherwise stream directly.
    events_iter: Iterator[Dict[str, object]]
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            if args.last is not None:
                dq: Deque[Dict[str, object]] = deque(maxlen=args.last)
                for ev in iter_events(f, include_model=args.include_model):
                    dq.append(ev)
                events: List[Dict[str, object]] = list(dq)
            else:
                # Collect into list only if we need summary for reuse, else stream
                if args.summary or args.format == "ndjson":
                    events = list(iter_events(f, include_model=args.include_model))
                else:
                    # For csv/tsv without summary, we can stream twice only if header; simpler to collect
                    events = list(iter_events(f, include_model=args.include_model))
    except Exception as e:
        print(f"Error reading log: {e}", file=sys.stderr)
        return 1

    # Default modes
    # - No args: live snapshot of last 5 hours (per-event view)
    if no_cli_args:
        args.live = True
        if args.since_hours is None:
            args.since_hours = 5

    # - If args provided but no explicit time window and not live: monthly daily report
    no_time_flags = not any([
        args.last_month,
        args.since_days is not None,
        args.since_date is not None,
        args.since_hours is not None,
        args.last is not None,
        args.live,
    ])
    if (not no_cli_args) and no_time_flags:
        args.last_month = True
        args.daily = True

    # Date filters
    since_dt: Optional[datetime] = None
    if args.last_month and args.since_days is None and args.since_date is None:
        since_dt = datetime.now(timezone.utc) - timedelta(days=30)
    elif args.since_days is not None:
        since_dt = datetime.now(timezone.utc) - timedelta(days=args.since_days)
    elif args.since_hours is not None:
        since_dt = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    elif args.since_date:
        try:
            since_dt = datetime.fromisoformat(args.since_date).replace(tzinfo=timezone.utc)
        except Exception:
            print("Error: --since-date must be YYYY-MM-DD", file=sys.stderr)
            return 2

    if since_dt is not None:
        events = filter_since(events, since_dt)

    # Pricing resolution
    prices_conf = load_prices(args.prices) if args.prices else None
    if prices_conf is None and not args.no_auto_prices:
        auto = load_or_fetch_helicone(provider=args.provider, ttl_hours=args.cache_ttl_hours, refresh=args.refresh_prices)
        if auto:
            prices_conf = auto
    # Apply CLI overrides as a flat default
    if any(v is not None for v in [args.usd_per_1k_input, args.usd_per_1k_output, args.usd_per_1k_reasoning, args.usd_per_1k_cached_input]):
        base = prices_conf or {}
        flat = base if "models" not in base else base.get("default", {})
        flat = dict(flat)
        if args.usd_per_1k_input is not None:
            flat["input"] = args.usd_per_1k_input
        if args.usd_per_1k_output is not None:
            flat["output"] = args.usd_per_1k_output
        if args.usd_per_1k_reasoning is not None:
            flat["reasoning"] = args.usd_per_1k_reasoning
        if args.usd_per_1k_cached_input is not None:
            flat["cached_input"] = args.usd_per_1k_cached_input
        prices_conf = flat if "models" not in (prices_conf or {}) else {**(prices_conf or {}), "default": flat}

    # If pricing provided, compute per-day cost by augmenting aggregates
    per_day_cost: Optional[Dict[str, float]] = None
    if prices_conf or args.forced_model:
        per_day_cost = {}
        for ev in events:
            model = str(ev.get("model") or "")
            rates = effective_rates(model, prices_conf, args.forced_model)
            c = compute_cost_usd(ev, rates, use_cached_pricing=args.cached_pricing)
            ts = str(ev.get("ts", ""))
            if ts and "T" in ts:
                day = ts.split("T", 1)[0]
                per_day_cost[day] = per_day_cost.get(day, 0.0) + c
        # Also annotate per-event cost for non-daily outputs
        for ev in events:
            model = str(ev.get("model") or "")
            rates = effective_rates(model, prices_conf, args.forced_model)
            ev["__cost_usd"] = compute_cost_usd(ev, rates, use_cached_pricing=args.cached_pricing)

    # Emit
    if args.live:
        # Default is sessions; allow opting into legacy events table
        if args.live_events:
            return run_live(log_path, args, prices_conf)
        return run_live_sessions(log_path, args, prices_conf)
    if args.daily:
        rows = aggregate_daily(events)
        # Fill zero rows for missing dates if we have a time window
        start_dt = since_dt if 'since_dt' in locals() else None
        end_dt = datetime.now(timezone.utc)
        rows = fill_missing_days(rows, start_dt, end_dt)
        if per_day_cost:
            rows = [
                (day, {**agg, "cost_usd": per_day_cost.get(day, 0.0)})
                for (day, agg) in rows
            ]
        # Trim leading all-zero days from the front of the month range
        rows = trim_leading_zero_days(rows)
        if args.format == "tsv":
            write_daily_tsv(rows, header=not args.no_header, out=sys.stdout)
        elif args.format == "csv":
            write_daily_csv(rows, header=not args.no_header, out=sys.stdout)
        elif args.format == "ndjson":
            write_daily_ndjson(rows, out=sys.stdout)
        elif args.format == "json":
            write_daily_json(rows, out=sys.stdout)
        else:  # table
            write_daily_table(rows, header=not args.no_header, border=args.border)
    else:
        # Optional summary for table view
        table_summary: Optional[Dict[str, Any]] = None
        if args.format == "table":
            if prices_conf or args.forced_model:
                table_summary = summarize_with_cost(events, prices_conf, per_model=False, forced_model=args.forced_model)
            else:
                table_summary = summarize(events)

        if args.format == "tsv":
            write_tsv(events, include_model=args.include_model, header=not args.no_header, out=sys.stdout)
        elif args.format == "csv":
            write_csv(events, include_model=args.include_model, header=not args.no_header, out=sys.stdout)
        elif args.format == "ndjson":
            write_ndjson(events, out=sys.stdout)
        elif args.format == "json":
            write_json_array(events, out=sys.stdout)
        else:  # table
            # Only include summary row when --summary is requested
            write_events_table(events, include_model=args.include_model, header=not args.no_header, border=args.border, summary=(table_summary if args.summary else None))

    if args.summary and args.format != "table":
        if prices_conf or args.forced_model:
            s = summarize_with_cost(events, prices_conf, per_model=False, forced_model=args.forced_model, use_cached_pricing=args.cached_pricing)
        else:
            s = summarize(events)
        # Prefix-style summary (compact): i(c)=...  o(r)=...  t=...  $=...
        base_msg = (
            "summary\ti(c)={i}({c})\to(r)={o}({r})\tt={t}"
        ).format(
            i=s.get("input_tokens", 0) or 0,
            c=s.get("cached_input_tokens", 0) or 0,
            o=s.get("output_tokens", 0) or 0,
            r=s.get("reasoning_output_tokens", 0) or 0,
            t=s.get("total_tokens", 0) or 0,
        )
        if isinstance(s, dict) and "cost_usd" in s:
            base_msg += f"\t$={s['cost_usd']:.2f}"
        print(base_msg, file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
