#!/usr/bin/env python3
"""
Codex TUI log → token usage extractor.

Parses ~/.codex/log/codex-tui.log (by default) and emits per-event token counts
found in TokenCount(TokenUsage {...}) lines. Optionally includes the most recent
model seen from SessionConfigured lines.

Output formats: tsv (default), csv, ndjson.

Aggregation:
- Per-event (default)
- Daily totals with --daily

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
from typing import Deque, Dict, Iterable, Iterator, List, Optional, Tuple


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\S+?)\s")
EVENT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+\s+\w+\s+handle_codex_event:\s+TokenCount\(TokenUsage\b")
MODEL_RE = re.compile(r"SessionConfigured\(.*?model:\s*\"([^\"]+)\"", re.IGNORECASE)


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

        event = {
            "ts": ts,
            "input_tokens": parse_number("input_tokens", line, 0),
            "cached_input_tokens": parse_number("cached_input_tokens", line, 0),
            "output_tokens": parse_number("output_tokens", line, 0),
            "reasoning_output_tokens": parse_number("reasoning_output_tokens", line, 0),
            "total_tokens": parse_number("total_tokens", line, 0),
        }
        if include_model:
            event["model"] = current_model
        yield event


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
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    if header:
        writer.writerow(fields)
    for day, agg in rows:
        writer.writerow([
            day,
            agg.get("events", 0),
            agg.get("input_tokens", 0),
            agg.get("cached_input_tokens", 0),
            agg.get("output_tokens", 0),
            agg.get("reasoning_output_tokens", 0),
            agg.get("total_tokens", 0),
        ])


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
    writer = csv.writer(out, lineterminator="\n")
    if header:
        writer.writerow(fields)
    for day, agg in rows:
        writer.writerow([
            day,
            agg.get("events", 0),
            agg.get("input_tokens", 0),
            agg.get("cached_input_tokens", 0),
            agg.get("output_tokens", 0),
            agg.get("reasoning_output_tokens", 0),
            agg.get("total_tokens", 0),
        ])


def write_daily_ndjson(rows: List[Tuple[str, Dict[str, int]]], out) -> None:
    for day, agg in rows:
        rec = {"date": day}
        rec.update(agg)
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract token usage from Codex TUI logs")
    ap.add_argument("--log", default=os.path.expanduser("~/.codex/log/codex-tui.log"),
                    help="Path to codex-tui.log (default: ~/.codex/log/codex-tui.log)")
    ap.add_argument("--format", choices=["tsv", "csv", "ndjson"], default="tsv",
                    help="Output format (default: tsv)")
    ap.add_argument("--include-model", action="store_true",
                    help="Include most recent SessionConfigured model in output")
    ap.add_argument("--last", type=int, default=None,
                    help="Limit output to last N events (keeps memory bounded)")
    ap.add_argument("--no-header", action="store_true",
                    help="Do not print header row for csv/tsv")
    ap.add_argument("--summary", action="store_true",
                    help="Print totals summary to stderr at the end")
    ap.add_argument("--daily", action="store_true",
                    help="Aggregate by day (UTC) and output one row per date")

    args = ap.parse_args()

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

    # Emit
    if args.daily:
        rows = aggregate_daily(events)
        if args.format == "tsv":
            write_daily_tsv(rows, header=not args.no_header, out=sys.stdout)
        elif args.format == "csv":
            write_daily_csv(rows, header=not args.no_header, out=sys.stdout)
        else:
            write_daily_ndjson(rows, out=sys.stdout)
    else:
        if args.format == "tsv":
            write_tsv(events, include_model=args.include_model, header=not args.no_header, out=sys.stdout)
        elif args.format == "csv":
            write_csv(events, include_model=args.include_model, header=not args.no_header, out=sys.stdout)
        else:  # ndjson
            write_ndjson(events, out=sys.stdout)

    if args.summary:
        s = summarize(events)
        # Print concise summary to stderr
        print(
            "summary\tevents={events}\tinput={input}\tcached={cached}\toutput={output}\treasoning={reasoning}\ttotal={total}".format(
                events=s["events"],
                input=s["input_tokens"],
                cached=s["cached_input_tokens"],
                output=s["output_tokens"],
                reasoning=s["reasoning_output_tokens"],
                total=s["total_tokens"],
            ),
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
