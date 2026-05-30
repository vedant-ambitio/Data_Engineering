"""
test_500_ror.py — ROR-only domain resolution for the 500 stratified records.

Output schema (per record):
  {
    "id": "...",
    "name": "...",
    "domain": "caarya.in" | null,
    "website": "https://www.caarya.in" | null,
    "ror_id": "https://ror.org/..." | null,
    "ror_matched_name": "..." | null,
    "similarity": 0.95
  }

No filter applied — apply >= 0.6 (or other) downstream.
"""

import asyncio
import json
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import httpx
from tqdm.asyncio import tqdm_asyncio

SRC = Path(__file__).parent / "test_500_stratified.json"
OUT = Path(__file__).parent / "test_500_ror.json"
USER_AGENT = "course-data-ror-test/1.0 (subs@ambitio.in)"
CONCURRENCY = 10
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

        return {
            "id": record["id"],
            "name": record["name"],
            "domain": normalize_domain(domain),
            "website": website,
            "ror_id": top.get("id"),
            "ror_matched_name": ror_name,
            "similarity": round(sim(name, ror_name or ""), 3),
        }


async def main():
    records = json.loads(SRC.read_text(encoding="utf-8"))
    print(f"Loaded {len(records)} records")
    print(f"Concurrency: {CONCURRENCY}")
    print()

    t0 = time.time()
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        tasks = [process(client, sem, r) for r in records]
        results = await tqdm_asyncio.gather(*tasks, desc="ROR lookup")
    elapsed = time.time() - t0

    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    n = len(results)
    hits = sum(1 for r in results if r["domain"])
    s06 = sum(1 for r in results if r["similarity"] >= 0.6)
    s07 = sum(1 for r in results if r["similarity"] >= 0.7)
    s08 = sum(1 for r in results if r["similarity"] >= 0.8)

    print(f"\n{'='*60}")
    print(f"Wrote {n} records to {OUT.name}")
    print(f"Elapsed: {elapsed:.1f}s  ({n/elapsed:.1f} req/sec)")
    print(f"\nResults:")
    print(f"  Raw ROR hits:       {hits:>4d} / {n}  ({hits/n*100:5.1f}%)")
    print(f"  sim >= 0.6 (kept):  {s06:>4d} / {n}  ({s06/n*100:5.1f}%)")
    print(f"  sim >= 0.7:         {s07:>4d} / {n}  ({s07/n*100:5.1f}%)")
    print(f"  sim >= 0.8:         {s08:>4d} / {n}  ({s08/n*100:5.1f}%)")

    print(f"\n{'='*60}")
    print(f"27K extrapolation:")
    proj_27k = int(elapsed / n * 27281)
    print(f"  At concurrency={CONCURRENCY}: ~{proj_27k}s = ~{proj_27k/60:.0f} min")
    print(f"  At concurrency=20:           ~{proj_27k//2}s = ~{proj_27k/120:.0f} min (estimate)")
    print(f"  At concurrency=30:           ~{proj_27k//3}s = ~{proj_27k/180:.0f} min (estimate)")


if __name__ == "__main__":
    asyncio.run(main())
