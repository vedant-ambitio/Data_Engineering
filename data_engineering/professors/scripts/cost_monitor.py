#!/usr/bin/env python3
"""
cost_monitor.py — sidecar that watches state.jsonl while the
professor_extract_runner is running and posts hourly progress + cost summaries
to Slack.

Behaviour:
  - Posts ONE message immediately on startup (so you know the monitor is alive).
  - Then every --interval-seconds (default 3600 = 1 hr) posts a status update.
  - Auto-detects "run done" via idle timer: if state.jsonl hasn't been modified
    for --idle-stop-seconds (default 600 = 10 min) AND at least one record
    exists, posts a final "RUN COMPLETE" message and exits.
  - Graceful Ctrl+C → posts a "monitor stopped" final message and exits.

Cost calculation uses Bedrock Opus 4.6 pricing (April 2026):
  input            $5  / 1M tokens
  output           $25 / 1M tokens
  cache read       $0.50 / 1M tokens   (90% off cached input)
  cache write      $6.25 / 1M tokens   (1.25× normal input — paid once)

Reads SLACK_WEBHOOK_URL from env (set by setup_aws_env.bat). If unset,
prints status to stdout only.

Usage:
  python Professors_info/scripts/cost_monitor.py \
      --state Professors_info/output_professors_pilot/state.jsonl \
      --total-pairs 10269

Useful flags:
  --interval-seconds 60         # for testing (post every minute instead of hour)
  --idle-stop-seconds 1200      # tolerate longer pauses (e.g. 20 min) before declaring done
  --check-every-seconds 30      # how often to read state.jsonl (default 60s)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

# Bedrock Opus 4.6 pricing (USD per 1M tokens)
PRICE_INPUT_PER_M       = 5.00
PRICE_OUTPUT_PER_M      = 25.00
PRICE_CACHE_READ_PER_M  = 0.50
PRICE_CACHE_WRITE_PER_M = 6.25


def load_state(state_path: Path) -> list[dict]:
    """Read state.jsonl, dedupe by pair_id (latest record wins).
    Returns [] if file missing or empty."""
    if not state_path.exists():
        return []
    by_pair: dict[str, dict] = {}
    try:
        with open(state_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                pid = r.get("pair_id") or f"{r.get('university','?')}__{r.get('department','?')}"
                by_pair[pid] = r
    except Exception as e:
        print(f"[load_state] error: {e}")
    return list(by_pair.values())


def aggregate(records: list[dict]) -> dict:
    n_total = len(records)
    n_ok = sum(1 for r in records if r.get("status") in ("ok", "ok_via_homepage_navigation"))
    n_skipped = sum(1 for r in records if r.get("status") == "skipped")
    n_error = sum(1 for r in records if r.get("status") == "error")
    n_parse = sum(1 for r in records if r.get("status") == "parse_error")

    n_profs = sum(int(r.get("professor_count") or 0) for r in records)
    n_md = sum(int(r.get("personal_sites_written") or 0) for r in records)

    tok_in = sum(int(r.get("tokens_in") or 0) for r in records)
    tok_out = sum(int(r.get("tokens_out") or 0) for r in records)
    cache_r = sum(int(r.get("cache_read_tokens") or 0) for r in records)
    cache_w = sum(int(r.get("cache_write_tokens") or 0) for r in records)

    cost_input    = tok_in   * PRICE_INPUT_PER_M       / 1e6
    cost_output   = tok_out  * PRICE_OUTPUT_PER_M      / 1e6
    cost_cache_r  = cache_r  * PRICE_CACHE_READ_PER_M  / 1e6
    cost_cache_w  = cache_w  * PRICE_CACHE_WRITE_PER_M / 1e6
    cost_total    = cost_input + cost_output + cost_cache_r + cost_cache_w

    # Cache hit rate = cache_reads / (fresh + cache_writes + cache_reads).
    # Per Anthropic prompt-caching docs, cache_creation_input_tokens (the
    # "writes") are NOT cache hits — they're fresh content paid at 1.25× input
    # rate that gets stored in cache for future reuse. Cache hits are *only*
    # cache_read_input_tokens. The denominator is total input tokens consumed
    # by the model (fresh + writes + reads).
    # Refs:
    #   https://docs.claude.com/en/docs/build-with-claude/prompt-caching
    #   https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html
    denom = cache_r + tok_in + cache_w
    cache_hit_rate = (cache_r / denom * 100) if denom > 0 else 0.0

    return {
        "n_total": n_total, "n_ok": n_ok, "n_skipped": n_skipped,
        "n_error": n_error, "n_parse": n_parse,
        "n_profs": n_profs, "n_md": n_md,
        "tok_in": tok_in, "tok_out": tok_out,
        "cache_r": cache_r, "cache_w": cache_w,
        "cost_input": cost_input, "cost_output": cost_output,
        "cost_cache_r": cost_cache_r, "cost_cache_w": cost_cache_w,
        "cost_total": cost_total,
        "cache_hit_rate": cache_hit_rate,
    }


def fmt_msg(stats: dict, total_pairs: int, started_at: str,
            elapsed_sec: float, header: str) -> str:
    elapsed_h = elapsed_sec / 3600
    pct = (stats["n_total"] / total_pairs * 100) if total_pairs else 0

    n_done = stats["n_total"]
    avg_cost = (stats["cost_total"] / n_done) if n_done > 0 else 0
    remaining = max(0, total_pairs - n_done)
    projected_total = avg_cost * total_pairs

    if n_done > 0 and elapsed_sec > 0:
        rate = n_done / elapsed_sec
        eta_sec = remaining / rate if rate > 0 else 0
        eta_str = f"{eta_sec/3600:.1f}h"
    else:
        eta_str = "?"

    return (
        f"{header}\n"
        f"Started: {started_at}\n"
        f"Elapsed: {elapsed_h:.2f}h\n"
        f"\n"
        f"📊 *Progress*\n"
        f"Pairs done: {stats['n_total']:,} / {total_pairs:,} ({pct:.1f}%)\n"
        f"  • verified ok: {stats['n_ok']:,}\n"
        f"  • skipped: {stats['n_skipped']:,}\n"
        f"  • errors: {stats['n_error']:,}\n"
        f"  • parse_error: {stats['n_parse']:,}\n"
        f"Professors found: {stats['n_profs']:,}\n"
        f"Personal sites .md: {stats['n_md']:,}\n"
        f"\n"
        f"🧠 *Tokens (Opus 4.6)*\n"
        f"Fresh input:    {stats['tok_in']:>15,}\n"
        f"Cache reads:    {stats['cache_r']:>15,}\n"
        f"Cache writes:   {stats['cache_w']:>15,}\n"
        f"Output:         {stats['tok_out']:>15,}\n"
        f"Cache hit rate: {stats['cache_hit_rate']:.1f}%\n"
        f"\n"
        f"💰 *Cost so far*\n"
        f"Fresh input  ($5/M):    ${stats['cost_input']:>8.2f}\n"
        f"Cache reads  ($0.50/M): ${stats['cost_cache_r']:>8.2f}\n"
        f"Cache writes ($6.25/M): ${stats['cost_cache_w']:>8.2f}\n"
        f"Output       ($25/M):   ${stats['cost_output']:>8.2f}\n"
        f"*TOTAL: ${stats['cost_total']:.2f}*\n"
        f"\n"
        f"📈 *Projection (linear)*\n"
        f"Cost/pair:        ${avg_cost:.4f}\n"
        f"Remaining pairs:  {remaining:,}\n"
        f"Projected total:  ${projected_total:.2f}\n"
        f"ETA:              {eta_str}\n"
    )


def post_slack(webhook_url: str, text: str) -> bool:
    """Post a plain-text message to a Slack incoming webhook. Returns True on 2xx."""
    if not webhook_url:
        return False
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        print(f"[slack] post failed: {e}", flush=True)
        return False


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True, help="Path to state.jsonl")
    p.add_argument("--total-pairs", type=int, default=10269,
                   help="Expected total pairs (used for progress %% and projection)")
    p.add_argument("--interval-seconds", type=int, default=3600,
                   help="How often to post status updates (default 3600 = 1h)")
    p.add_argument("--idle-stop-seconds", type=int, default=600,
                   help="Treat run as done if state.jsonl not modified for this long")
    p.add_argument("--check-every-seconds", type=int, default=60,
                   help="How often to peek at state.jsonl (default 60s)")
    p.add_argument("--label", default="Professor Extract",
                   help="Header label for messages (e.g. 'Pilot run', 'EC2 production')")
    args = p.parse_args()

    state_path = Path(args.state).resolve()
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()

    if not webhook:
        print("[monitor] WARNING: SLACK_WEBHOOK_URL is not set. Will print to stdout only.")
    else:
        print(f"[monitor] Slack webhook: {webhook[:50]}...")

    started_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    started_ts = time.time()

    print(f"[monitor] watching {state_path}")
    print(f"[monitor] interval: {args.interval_seconds}s ({args.interval_seconds//60} min)")
    print(f"[monitor] idle-stop: {args.idle_stop_seconds}s ({args.idle_stop_seconds//60} min)")
    print(f"[monitor] target total_pairs: {args.total_pairs:,}")

    # Initial state read
    records = load_state(state_path)
    stats = aggregate(records)

    # Immediate startup post
    msg = (f"🚀 *{args.label} — monitor started*\n"
           f"State file: `{state_path}`\n"
           f"Reporting every {args.interval_seconds // 60} min "
           f"(stops on {args.idle_stop_seconds // 60} min of idle)\n\n"
           + fmt_msg(stats, args.total_pairs, started_at, 0,
                     f"📍 *{args.label} — initial snapshot*"))
    print(msg)
    post_slack(webhook, msg)

    last_post_ts = time.time()
    if state_path.exists():
        last_state_size = state_path.stat().st_size
        last_state_mtime = state_path.stat().st_mtime
    else:
        last_state_size = 0
        last_state_mtime = time.time()
    last_state_change_ts = time.time()

    try:
        while True:
            time.sleep(args.check_every_seconds)
            now = time.time()

            # Detect any state-file change (size or mtime)
            if state_path.exists():
                cur_size = state_path.stat().st_size
                cur_mtime = state_path.stat().st_mtime
                if cur_size != last_state_size or cur_mtime != last_state_mtime:
                    last_state_change_ts = now
                    last_state_size = cur_size
                    last_state_mtime = cur_mtime

            # Periodic status post
            if now - last_post_ts >= args.interval_seconds:
                records = load_state(state_path)
                stats = aggregate(records)
                msg = fmt_msg(stats, args.total_pairs, started_at,
                              now - started_ts,
                              f"🔍 *{args.label} — Hourly status*")
                print(msg)
                post_slack(webhook, msg)
                last_post_ts = now

            # Idle-stop detection (only if some records exist)
            n_records = len(load_state(state_path)) if state_path.exists() else 0
            idle = now - last_state_change_ts
            if n_records > 0 and idle >= args.idle_stop_seconds:
                records = load_state(state_path)
                stats = aggregate(records)
                msg = (f"✅ *{args.label} — RUN COMPLETE* (idle timer triggered)\n"
                       f"No state activity for {idle/60:.1f} min (>= {args.idle_stop_seconds/60:.0f} min threshold).\n\n"
                       + fmt_msg(stats, args.total_pairs, started_at,
                                 now - started_ts,
                                 f"📊 *{args.label} — Final summary*"))
                print(msg)
                post_slack(webhook, msg)
                break

    except KeyboardInterrupt:
        print("\n[monitor] stopped by user (Ctrl+C)")
        records = load_state(state_path)
        stats = aggregate(records)
        msg = (f"⏹️ *{args.label} — monitor stopped manually*\n\n"
               + fmt_msg(stats, args.total_pairs, started_at,
                         time.time() - started_ts,
                         f"📊 *{args.label} — Snapshot at stop*"))
        print(msg)
        post_slack(webhook, msg)


if __name__ == "__main__":
    main()
