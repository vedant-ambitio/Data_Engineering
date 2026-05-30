"""
Full 27,281-record domain extraction via Gemini 3.1 Pro on Vertex AI.

- Verbose output: {id, name, domain, confidence, reasoning}
- 5 parallel workers (ThreadPoolExecutor)
- 500 records per batch -> 55 batches
- Per-batch JSON files for resume capability
- Exponential backoff on transient errors
- Final merge to domains_27k_full.json + summary

Run:  python step2_full_27k.py
Resume on crash: re-run the same command. Completed batches are skipped.
"""
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ---------- config ----------
KEY = r"c:/Users/HP/OneDrive/Desktop/course_data/dashboard/gcp-key.json"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = KEY
PROJECT = json.load(open(KEY))["project_id"]
LOCATION = "global"
MODEL = "gemini-3.1-pro-preview"

BASE = Path(r"c:/Users/HP/OneDrive/Desktop/course_data/Logo_url_extract")
INPUT_FILE = BASE / "query_result_2026-05-06T14_11_02.778206369+05_30.json"
BATCHES_DIR = BASE / "batches_27k"
OUTPUT_FILE = BASE / "domains_27k_full.json"
SUMMARY_FILE = BASE / "domains_27k_summary.txt"

BATCH_SIZE = 500
MAX_WORKERS = 5
MAX_RETRIES = 5
TEMPERATURE = 0.1

# Pricing for Gemini 3.1 Pro Preview (≤200K context)
PRICE_IN_PER_M = 2.00
PRICE_OUT_PER_M = 12.00

PROMPT = """You are matching organization names to their official primary domain.

Rules:
- Only return a domain if you are at least 70% confident it is the organization's actual primary domain from your training data.
- Return null for: small/local entities, generic category names like "Hospital Systems" or "Public Relations Firms", ambiguous single-word names ("Bentley", "Icon"), or anything you'd need to web-search to verify.
- Do NOT construct domains by pattern (e.g. companyname.com). Only return domains you actually recall from training.
- Strip protocol and www prefix. Return bare domain (e.g. "amazon.com" not "https://www.amazon.com").
- For acquired/renamed companies, return the current parent domain (e.g. Sheraton -> marriott.com, Harris Corp -> l3harris.com).
- For Indian/regional companies, prefer regional domains (e.g. .in, .co.in) when those are primary.

For each input record, output a JSON object with these fields exactly:
{
  "id": "<id from input>",
  "name": "<name from input>",
  "domain": "<domain>" or null,
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one short sentence explaining your answer>"
}

Output a JSON array containing one object per input record, in the same order as the input.
Output ONLY the JSON array - no markdown fences, no commentary."""


def chunk(lst, size):
    return [lst[i:i + size] for i in range(0, len(lst), size)]


def call_gemini(client, types_mod, batch_idx, records):
    """Call Gemini with exponential backoff. Returns (parsed, usage)."""
    inputs = [{"id": r["id"], "name": r["name"]} for r in records]
    prompt = PROMPT + "\n\nInput records:\n" + json.dumps(inputs)

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types_mod.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=65536,
                    temperature=TEMPERATURE,
                ),
            )
            text = resp.text.strip()
            if text.startswith("```"):
                text = text.split("```", 2)[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip().rstrip("`").strip()
            return json.loads(text), resp.usage_metadata
        except Exception as e:
            last_err = e
            msg = str(e)[:160].replace("\n", " ")
            wait = (2 ** attempt) + random.uniform(0, 1)
            print(f"  [batch {batch_idx:03d}] attempt {attempt+1}/{MAX_RETRIES} "
                  f"failed: {type(e).__name__}: {msg}")
            if attempt < MAX_RETRIES - 1:
                print(f"  [batch {batch_idx:03d}] sleeping {wait:.1f}s before retry...")
                time.sleep(wait)
    raise last_err


def process_batch(batch_idx, records, client, types_mod):
    """Returns (batch_idx, status, info). Skips if file already exists & valid."""
    out_file = BATCHES_DIR / f"batch_{batch_idx:03d}.json"
    if out_file.exists():
        try:
            existing = json.loads(out_file.read_text(encoding="utf-8"))
            if len(existing) == len(records):
                return batch_idx, "skip", len(existing)
        except Exception:
            pass  # corrupt, rewrite

    t0 = time.time()
    try:
        parsed, usage = call_gemini(client, types_mod, batch_idx, records)
    except Exception as e:
        return batch_idx, "fail", f"{type(e).__name__}: {str(e)[:160]}"

    if not isinstance(parsed, list) or len(parsed) != len(records):
        return batch_idx, "size_mismatch", f"expected {len(records)} got {len(parsed) if isinstance(parsed, list) else type(parsed).__name__}"

    out_file.write_text(json.dumps(parsed, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    elapsed = time.time() - t0
    return batch_idx, "ok", {
        "elapsed": elapsed,
        "in_tok": usage.prompt_token_count if usage else 0,
        "out_tok": usage.candidates_token_count if usage else 0,
    }


def main():
    BATCHES_DIR.mkdir(exist_ok=True)

    with INPUT_FILE.open(encoding="utf-8") as f:
        records = json.load(f)
    print(f"Loaded {len(records):,} records from {INPUT_FILE.name}")

    batches = chunk(records, BATCH_SIZE)
    print(f"Split into {len(batches)} batches of {BATCH_SIZE} records each")

    existing = list(BATCHES_DIR.glob("batch_*.json"))
    print(f"Existing batch files: {len(existing)}  (will be reused)")
    print()
    print(f"Model:    {MODEL}")
    print(f"Project:  {PROJECT} ({LOCATION})")
    print(f"Workers:  {MAX_WORKERS} parallel")
    print()

    from google import genai
    from google.genai import types as types_mod
    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)

    t_start = time.time()
    total_in = total_out = 0
    completed = 0
    failed = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(process_batch, i, b, client, types_mod): i
            for i, b in enumerate(batches)
        }
        for fut in as_completed(futures):
            idx, status, info = fut.result()
            completed += 1
            mins = (time.time() - t_start) / 60

            if status == "ok":
                total_in += info["in_tok"]
                total_out += info["out_tok"]
                cost = total_in * PRICE_IN_PER_M / 1e6 + total_out * PRICE_OUT_PER_M / 1e6
                print(f"  [{completed:>3}/{len(batches)}]  batch {idx:03d}  OK   "
                      f"{info['elapsed']:5.1f}s  in={info['in_tok']:>6,}  "
                      f"out={info['out_tok']:>6,}  $={cost:5.2f}  "
                      f"t={mins:4.1f}min")
            elif status == "skip":
                print(f"  [{completed:>3}/{len(batches)}]  batch {idx:03d}  SKIP (cached)")
            else:
                failed.append((idx, status, info))
                print(f"  [{completed:>3}/{len(batches)}]  batch {idx:03d}  FAIL  {status}: {info}")

    elapsed_min = (time.time() - t_start) / 60
    print()
    print("=" * 70)
    print(f"Run complete in {elapsed_min:.1f} min")
    print(f"Tokens — input: {total_in:,}   output: {total_out:,}")
    cost_total = total_in * PRICE_IN_PER_M / 1e6 + total_out * PRICE_OUT_PER_M / 1e6
    print(f"Cost: ${cost_total:.2f}")
    print(f"Failed batches: {len(failed)}")
    if failed:
        for f in failed:
            print(f"  batch {f[0]:03d}: {f[1]} - {f[2]}")
        print()
        print("Re-run the script to retry failed batches.")
        return

    # Merge
    print()
    print("Merging all batches into final file...")
    all_records = []
    for i in range(len(batches)):
        f = BATCHES_DIR / f"batch_{i:03d}.json"
        all_records.extend(json.loads(f.read_text(encoding="utf-8")))
    OUTPUT_FILE.write_text(json.dumps(all_records, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"Wrote {len(all_records):,} records -> {OUTPUT_FILE.name}")

    # Summary
    filled = [r for r in all_records if r.get("domain")]
    nulls = [r for r in all_records if not r.get("domain")]
    by_conf = {}
    for r in filled:
        by_conf[r.get("confidence", "?")] = by_conf.get(r.get("confidence", "?"), 0) + 1

    summary = f"""Domain extraction — full 27,281-record run
Model:   {MODEL} (Vertex AI {LOCATION})
Format:  verbose {{id, name, domain, confidence, reasoning}}
======================================================================

Total records:   {len(all_records):>7,}
Filled:          {len(filled):>7,}  ({len(filled)/len(all_records)*100:.1f}%)
  high conf:     {by_conf.get('high', 0):>7,}
  medium conf:   {by_conf.get('medium', 0):>7,}
  low conf:      {by_conf.get('low', 0):>7,}
Null:            {len(nulls):>7,}  ({len(nulls)/len(all_records)*100:.1f}%)

======================================================================
Tokens:          in={total_in:,}   out={total_out:,}
Cost:            ${cost_total:.2f}
Wall-clock:      {elapsed_min:.1f} min
"""
    SUMMARY_FILE.write_text(summary, encoding="utf-8")
    print()
    print(summary)


if __name__ == "__main__":
    main()
