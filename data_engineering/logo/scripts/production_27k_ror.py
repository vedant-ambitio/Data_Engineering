"""
production_27k_ror.py — ROR domain resolution for the full 27K dataset.

Usage:
  python production_27k_ror.py              # full run (all 27,281 records)
  python production_27k_ror.py --max 10     # smoke test on first 10 records
  python production_27k_ror.py --max 100    # 100-record test

Output schema (per record, ALL records included regardless of similarity):
  {
    "id": "...",
    "name": "...",
    "domain": "caarya.in" | null,
    "website": "https://www.caarya.in" | null,
    "ror_id": "https://ror.org/..." | null,
    "ror_matched_name": "..." | null,
    "similarity": 0.95
  }

No write-time filter. Apply >= 0.6 (or any other) downstream.
"""

import argparse
import asyncio
import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import httpx
from tqdm.asyncio import tqdm_asyncio

# ── Smart-rule config ────────────────────────────────────────────────────
STOPWORDS = {
    "the", "a", "an", "of", "and", "for", "co", "company", "companies",
    "inc", "ltd", "limited", "llc", "corp", "corporation", "pvt",
    "private", "plc", "group", "holdings", "international", "global",
    "services", "solutions", "systems", "technologies",
}

SIM_HIGH = 0.7   # tier A threshold


def meaningful_tokens(name):
    """Return lowercase tokens of length>=3 that aren't stopwords."""
    cleaned = re.sub(r"[^\w\s]", " ", (name or "").lower())
    return [t for t in cleaned.split() if t not in STOPWORDS and len(t) >= 3]


def is_match(query_name, matched_name, domain, sim_score):
    """Smart rule combining similarity + domain containment."""
    tokens = meaningful_tokens(query_name)
    if not tokens:
        return False, False, False
    first = tokens[0]
    matched_lower = (matched_name or "").lower()
    domain_lower = (domain or "").lower()
    name_in_matched = first in matched_lower
    name_in_domain = first in domain_lower

    # Tier A: high similarity AND first word confirms in matched_name OR domain
    if sim_score >= SIM_HIGH and (name_in_matched or name_in_domain):
        return True, name_in_matched, name_in_domain
    # Tier B: first word confirmed in domain (catches abbreviations like NMIMS)
    if name_in_domain:
        return True, name_in_matched, name_in_domain
    # Otherwise reject
    return False, name_in_matched, name_in_domain

HERE = Path(__file__).parent
SRC = HERE / "query_result_2026-05-06T14_11_02.778206369+05_30.json"
DEFAULT_OUT = HERE / "domains_27k_ror.json"

USER_AGENT = "course-data-ror-prod/1.0 (subs@ambitio.in)"
CONCURRENCY = 20
TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def normalize_domain(s):
    if not s or not isinstance(s, str):
        return None
    s = s.strip().lower()
    if "://" in s:
        s = urlparse(s).netloc or s.split("/", 1)[0]
    s = s.split("/")[0].split("?")[0]
    if s.startswith("www."):
        s = s[4:]
    return s or None


def sim(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


async def ror_lookup(client, name):
    try:
        r = await client.get(
            "https://api.ror.org/v2/organizations",
            params={"query": name},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        items = r.json().get("items", [])
        return items[0] if items else None
    except Exception:
        return None


async def process(client, sem, record):
    async with sem:
        name = record["name"].strip()
        top = await ror_lookup(client, name)
        if not top:
            return {
                "id": record["id"],
                "name": record["name"],
                "domain": None,
                "website": None,
                "ror_id": None,
                "ror_matched_name": None,
                "similarity": 0.0,
                "name_in_matched": False,
                "name_in_domain": False,
                "accepted": False,
            }

        domain = top["domains"][0] if top.get("domains") else None
        website = None
        for link in top.get("links") or []:
            if link.get("type") == "website":
                website = link.get("value")
                break

        if not domain and website:
            domain = normalize_domain(website)
        if domain and not website:
            website = f"https://www.{domain}"

        ror_name = None
        for nm in top.get("names") or []:
            if "ror_display" in (nm.get("types") or []):
                ror_name = nm.get("value")
                break
        if not ror_name and top.get("names"):
            ror_name = top["names"][0].get("value")

        norm_domain = normalize_domain(domain)
        sim_score = round(sim(name, ror_name or ""), 3)
        accepted, name_in_matched, name_in_domain = is_match(
            name, ror_name, norm_domain, sim_score
        )

        return {
            "id": record["id"],
            "name": record["name"],
            "domain": norm_domain if accepted else None,
            "website": website if accepted else None,
            "ror_id": top.get("id"),
            "ror_matched_name": ror_name,
            "similarity": sim_score,
            "name_in_matched": name_in_matched,
            "name_in_domain": name_in_domain,
            "accepted": accepted,
        }


async def main():
    parser = argparse.ArgumentParser(description="Resolve domains for 27K records via ROR")
    parser.add_argument("--max", type=int, default=None,
                        help="Process only the first N records (smoke test)")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY,
                        help=f"Parallel requests (default {CONCURRENCY})")
    args = parser.parse_args()

    records = json.loads(SRC.read_text(encoding="utf-8"))
    total = len(records)

    if args.max is not None:
        records = records[: args.max]
        out_path = HERE / f"domains_27k_ror.max{args.max}.json"
    else:
        out_path = DEFAULT_OUT

    print(f"Source:        {SRC.name}")
    print(f"Total in src:  {total:,}")
    print(f"Processing:    {len(records):,}")
    print(f"Concurrency:   {args.concurrency}")
    print(f"Output file:   {out_path.name}")
    print()

    t0 = time.time()
    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        tasks = [process(client, sem, r) for r in records]
        results = await tqdm_asyncio.gather(*tasks, desc="ROR lookup")
    elapsed = time.time() - t0

    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    n = len(results)
    raw_hits = sum(1 for r in results if r["ror_matched_name"])
    accepted = sum(1 for r in results if r["accepted"])
    tier_a = sum(1 for r in results if r["accepted"] and r["similarity"] >= SIM_HIGH)
    tier_b = sum(1 for r in results if r["accepted"] and r["similarity"] < SIM_HIGH and r["name_in_domain"])
    s06_legacy = sum(1 for r in results if r["similarity"] >= 0.6)

    print(f"\n{'='*60}")
    print(f"Wrote {n:,} records to {out_path.name}")
    print(f"Elapsed:           {elapsed:.1f}s  ({n/elapsed:.1f} req/sec)")
    print(f"\nSmart-rule results:")
    print(f"  Raw ROR hits:                      {raw_hits:>6,} / {n:,}  ({raw_hits/n*100:5.1f}%)")
    print(f"  ACCEPTED (final domain non-null):  {accepted:>6,} / {n:,}  ({accepted/n*100:5.1f}%)")
    print(f"    via Tier A (sim>={SIM_HIGH} + word match): {tier_a:>6,}")
    print(f"    via Tier B (word in domain):     {tier_b:>6,}")
    print(f"\nFor comparison:")
    print(f"  Pure sim>=0.6 (old rule) would keep: {s06_legacy:,}")

    if args.max is not None:
        proj = elapsed / n * total
        print(f"\nFull-run projection ({total:,} records at concurrency={args.concurrency}):")
        print(f"  ~{proj:.0f}s = ~{proj/60:.1f} min")


if __name__ == "__main__":
    asyncio.run(main())
