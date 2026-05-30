"""
check_favicons.py — classify each Google S2 favicon URL as real vs globe placeholder.

Reads:  query_results_url_link.json   (NOT modified)
Writes: favicon_analysis.json         (same records + classification fields)

For each URL:
  - GET the image bytes (fast, ~1-3 KB per response)
  - Record: status, size_bytes, content_type, sha1 hash
  - Identify the globe placeholder by finding the most-repeated small-size hash
  - Classify each record as:
      real_favicon       — unique image, looks like a real site favicon
      globe_placeholder  — matches the known-globe hash exactly
      tiny_likely_globe  — < 200 bytes (probably globe even if hash differs)
      error              — request failed (timeout, 5xx, etc.)

Usage:
  python check_favicons.py                 # full 15,046 records
  python check_favicons.py --max 100       # smoke test on first 100
  python check_favicons.py --concurrency 30  # tune parallelism
"""

import argparse
import asyncio
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import httpx
from tqdm.asyncio import tqdm_asyncio

HERE = Path(__file__).parent
SRC = HERE / "query_results_url_link.json"
OUT = HERE / "favicon_analysis.json"

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
USER_AGENT = "course-data-favicon-check/1.0"


async def fetch(client, sem, record):
    async with sem:
        try:
            r = await client.get(
                record["logo_url"],
                timeout=TIMEOUT,
                follow_redirects=True,
            )
            content = r.content
            return {
                **record,
                "status": r.status_code,
                "size_bytes": len(content),
                "content_type": r.headers.get("content-type", ""),
                "sha1": hashlib.sha1(content).hexdigest()[:16],
                "error": None,
            }
        except Exception as e:
            return {
                **record,
                "status": 0,
                "size_bytes": 0,
                "content_type": "",
                "sha1": "",
                "error": f"{type(e).__name__}: {str(e)[:100]}",
            }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None,
                    help="Process only first N records (default: all)")
    ap.add_argument("--concurrency", type=int, default=40,
                    help="Parallel requests (default 40)")
    args = ap.parse_args()

    records = json.loads(SRC.read_text(encoding="utf-8"))
    if args.max:
        records = records[: args.max]
    print(f"Records to check: {len(records):,}")
    print(f"Concurrency:      {args.concurrency}")
    print()

    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.time()
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
    ) as client:
        tasks = [fetch(client, sem, r) for r in records]
        results = await tqdm_asyncio.gather(*tasks, desc="Checking favicons")
    elapsed = time.time() - t0

    # ── Find globe placeholder hash ────────────────────────────────────
    # Strategy: any hash that repeats across 5+ different records is the
    # globe placeholder. Genuine logos almost never collide across orgs.
    hash_counts = Counter(r["sha1"] for r in results if r["sha1"])

    hash_size = {}
    for r in results:
        if r["sha1"] and r["sha1"] not in hash_size:
            hash_size[r["sha1"]] = r["size_bytes"]

    globe_hash = None
    for h, count in hash_counts.most_common():
        # Threshold: 5+ different domains returning identical bytes = placeholder.
        # No size check — actual placeholder turned out to be 726b at sz=128,
        # well above the < 500b heuristic.
        if count >= 5:
            globe_hash = h
            break

    # ── Classify each record ──────────────────────────────────────────
    # Priority order: hash match > network error > status > size.
    # (Google returns the globe image WITH a 404 status — checking hash first
    # correctly classifies those as globes, not errors.)
    real = globe = tiny = errors = 0
    for r in results:
        if globe_hash and r["sha1"] == globe_hash:
            r["classification"] = "globe_placeholder"
            globe += 1
        elif not r["sha1"]:  # truly no response body (network failure)
            r["classification"] = "error"
            errors += 1
        elif r["status"] == 200:
            r["classification"] = "real_favicon"
            real += 1
        elif r["size_bytes"] < 200:
            r["classification"] = "tiny_likely_globe"
            tiny += 1
        else:
            # Non-200 with content that isn't the known globe hash — rare edge case.
            # Treat as error so it's flagged for review.
            r["classification"] = "error"
            errors += 1

    OUT.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # ── Stats ─────────────────────────────────────────────────────────
    n = len(results)
    print(f"\n{'='*64}")
    print(f"Wrote {n:,} records to {OUT.name}")
    print(f"Elapsed: {elapsed:.1f}s  ({n/elapsed:.1f} req/sec)")
    print(f"\nClassification:")
    print(f"  Real favicons:        {real:>6,}  ({real/n*100:5.1f}%)")
    print(f"  Globe placeholder:    {globe:>6,}  ({globe/n*100:5.1f}%)")
    print(f"  Tiny (likely globe):  {tiny:>6,}  ({tiny/n*100:5.1f}%)")
    print(f"  Errors:               {errors:>6,}  ({errors/n*100:5.1f}%)")
    print(f"\nUsable logos:           {real:>6,}  ({real/n*100:5.1f}%)")
    print(f"NOT usable (globe/err): {globe + tiny + errors:>6,}  ({(globe+tiny+errors)/n*100:5.1f}%)")

    if globe_hash:
        print(f"\nGlobe placeholder hash: {globe_hash}")
        print(f"  Appeared {hash_counts[globe_hash]:,} times at {hash_size[globe_hash]} bytes")
    else:
        print(f"\nNo clear globe placeholder hash detected.")

    print(f"\nTop 5 most-repeated hashes:")
    for h, c in hash_counts.most_common(5):
        if h:
            sample = next(r for r in results if r["sha1"] == h)
            label = " <-- GLOBE" if h == globe_hash else ""
            print(f"  {h}  count={c:>5,}  size={sample['size_bytes']:>5}b  example_domain={sample['domain']}{label}")

    print(f"\nSample real favicons (first 5):")
    for r in [x for x in results if x["classification"] == "real_favicon"][:5]:
        print(f"  {r['domain']:<35s} {r['size_bytes']:>5}b   {r['name'][:40]}")

    print(f"\nSample globe placeholders (first 5):")
    for r in [x for x in results if x["classification"] in ("globe_placeholder", "tiny_likely_globe")][:5]:
        print(f"  {r['domain']:<35s} {r['size_bytes']:>5}b   {r['name'][:40]}")


if __name__ == "__main__":
    asyncio.run(main())
