"""
test_500_domains.py — resolve domains for 500 stratified records via 3 free sources.

Sources hit per record (in parallel):
  1. ROR (Research Organization Registry)         — universities, research, govt, nonprofits
  2. Wikidata P856 (official website)             — anything with a Wikipedia entity
  3. Logo.dev Brand Search                        — commercial brands

Outputs:
  - test_500_domains_results.csv  (one row per record, all source results + consensus)
  - test_500_domains_full.json    (same data + raw match metadata)

Run:
  pip install httpx python-dotenv tqdm
  python test_500_domains.py
"""

import asyncio
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from tqdm.asyncio import tqdm_asyncio

# Reuse the stratification categorizer so we get bucket labels in output
from sample_500_stratified import categorize

load_dotenv(Path(__file__).parent / ".env")

LOGODEV_KEY = os.environ.get("LOGODEV_SECRET_KEY")
if not LOGODEV_KEY:
    sys.exit("ERROR: LOGODEV_SECRET_KEY not set in .env")

USER_AGENT = "course-data-domain-test/1.0 (subs@ambitio.in)"

SRC = Path(__file__).parent / "test_500_stratified.json"
OUT_CSV = Path(__file__).parent / "test_500_domains_results.csv"
OUT_JSON = Path(__file__).parent / "test_500_domains_full.json"

CONCURRENCY = 8
TIMEOUT = httpx.Timeout(20.0, connect=5.0)


# Domain normalisation ─────────────────────────────────────────────────────
def normalize_domain(url_or_domain):
    if not url_or_domain or not isinstance(url_or_domain, str):
        return None
    s = url_or_domain.strip().lower()
    if not s:
        return None
    if "://" in s:
        s = urlparse(s).netloc or s.split("/", 1)[0]
    s = s.split("/")[0].split("?")[0]
    if s.startswith("www."):
        s = s[4:]
    return s or None


# ROR ──────────────────────────────────────────────────────────────────────
async def ror_lookup(client, name):
    """ROR v2 organisations search. Returns {domain, ror_id, ror_name}."""
    try:
        r = await client.get(
            "https://api.ror.org/v2/organizations",
            params={"query": name},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return {"domain": None, "ror_id": None, "ror_name": None,
                    "error": f"http_{r.status_code}"}
        data = r.json()
        items = data.get("items", [])
        if not items:
            return {"domain": None, "ror_id": None, "ror_name": None}
        top = items[0]
        domain = None
        if top.get("domains"):
            domain = top["domains"][0]
        if not domain and top.get("links"):
            for link in top["links"]:
                if link.get("type") == "website":
                    domain = link.get("value")
                    break
        ror_name = None
        for n in top.get("names", []):
            if "ror_display" in n.get("types", []):
                ror_name = n.get("value")
                break
        if not ror_name and top.get("names"):
            ror_name = top["names"][0].get("value")
        return {
            "domain": normalize_domain(domain),
            "ror_id": top.get("id"),
            "ror_name": ror_name,
        }
    except Exception as e:
        return {"domain": None, "ror_id": None, "ror_name": None,
                "error": str(e)[:100]}


# Wikidata ─────────────────────────────────────────────────────────────────
ORG_DESC_HINTS = {
    "company", "corporation", "university", "college", "school", "institute",
    "organization", "organisation", "foundation", "nonprofit", "non-profit",
    "ngo", "association", "society", "agency", "ministry", "department",
    "museum", "academy", "club", "team", "consortium", "publisher", "brand",
    "manufacturer", "retailer", "bank", "firm", "studio", "publishing",
    "religious", "charity", "research", "hospital", "clinic", "laboratory",
}


def looks_like_org(desc):
    if not desc:
        return False
    d = desc.lower()
    return any(hint in d for hint in ORG_DESC_HINTS)


async def wikidata_lookup(client, name):
    """Wikidata: search → pick top org-like result → fetch P856 (official website)."""
    try:
        r = await client.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities",
                "search": name,
                "format": "json",
                "language": "en",
                "limit": 5,
                "type": "item",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return {"domain": None, "qid": None,
                    "label": None, "description": None,
                    "error": f"http_{r.status_code}"}
        results = r.json().get("search", [])
        if not results:
            return {"domain": None, "qid": None, "label": None, "description": None}

        # Prefer top result whose description looks org-ish; else fall back to top.
        chosen = None
        for cand in results:
            if looks_like_org(cand.get("description", "")):
                chosen = cand
                break
        if not chosen:
            chosen = results[0]

        qid = chosen["id"]
        label = chosen.get("label")
        description = chosen.get("description")

        # Fetch P856 claims for the chosen entity.
        r2 = await client.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbgetentities",
                "ids": qid,
                "props": "claims",
                "format": "json",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        if r2.status_code != 200:
            return {"domain": None, "qid": qid, "label": label,
                    "description": description,
                    "error": f"http_{r2.status_code}"}
        claims = r2.json().get("entities", {}).get(qid, {}).get("claims", {})
        official = claims.get("P856", [])
        url = None
        for c in official:
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if v:
                url = v
                break
        return {
            "domain": normalize_domain(url),
            "qid": qid,
            "label": label,
            "description": description,
            "official_url": url,
        }
    except Exception as e:
        return {"domain": None, "qid": None, "label": None, "description": None,
                "error": str(e)[:100]}


# Logo.dev ─────────────────────────────────────────────────────────────────
async def logodev_lookup(client, name):
    """Logo.dev Brand Search. Returns {domain, matched_name}."""
    try:
        r = await client.get(
            "https://api.logo.dev/search",
            params={"q": name},
            headers={
                "Authorization": f"Bearer {LOGODEV_KEY}",
                "User-Agent": USER_AGENT,
            },
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return {"domain": None, "matched_name": None,
                    "error": f"http_{r.status_code}"}
        data = r.json()
        items = data if isinstance(data, list) else data.get("results", [])
        if not items:
            return {"domain": None, "matched_name": None}
        top = items[0]
        return {
            "domain": normalize_domain(top.get("domain")),
            "matched_name": top.get("name"),
        }
    except Exception as e:
        return {"domain": None, "matched_name": None, "error": str(e)[:100]}


# Per-record orchestration ─────────────────────────────────────────────────
async def process_record(client, sem, record):
    async with sem:
        name = record["name"].strip()
        ror, wd, lo = await asyncio.gather(
            ror_lookup(client, name),
            wikidata_lookup(client, name),
            logodev_lookup(client, name),
        )
        domains = [ror["domain"], wd["domain"], lo["domain"]]
        non_null = [d for d in domains if d]
        counter = Counter(non_null)
        if counter:
            consensus, agree_count = counter.most_common(1)[0]
        else:
            consensus, agree_count = None, 0
        return {
            "id": record["id"],
            "name": name,
            "bucket": categorize(name),
            "ror_domain": ror["domain"],
            "wikidata_domain": wd["domain"],
            "logodev_domain": lo["domain"],
            "consensus_domain": consensus,
            "agreement_count": agree_count,
            "ror_id": ror.get("ror_id"),
            "ror_name": ror.get("ror_name"),
            "wikidata_qid": wd.get("qid"),
            "wikidata_label": wd.get("label"),
            "wikidata_desc": wd.get("description"),
            "wikidata_official_url": wd.get("official_url"),
            "logodev_matched_name": lo.get("matched_name"),
            "ror_error": ror.get("error"),
            "wikidata_error": wd.get("error"),
            "logodev_error": lo.get("error"),
        }


# Main ─────────────────────────────────────────────────────────────────────
async def main():
    records = json.loads(SRC.read_text(encoding="utf-8"))
    print(f"Loaded {len(records)} records from {SRC.name}")
    print(f"Concurrency: {CONCURRENCY}\n")

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(http2=False, headers={"User-Agent": USER_AGENT}) as client:
        tasks = [process_record(client, sem, r) for r in records]
        results = await tqdm_asyncio.gather(*tasks, desc="Resolving domains")

    OUT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    fields = [
        "id", "name", "bucket",
        "ror_domain", "wikidata_domain", "logodev_domain",
        "consensus_domain", "agreement_count",
        "ror_name", "ror_id",
        "wikidata_label", "wikidata_desc", "wikidata_qid", "wikidata_official_url",
        "logodev_matched_name",
        "ror_error", "wikidata_error", "logodev_error",
    ]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in fields})

    # ── Stats ─────────────────────────────────────────────────────────────
    n = len(results)
    by_bucket = {}
    for r in results:
        by_bucket.setdefault(r["bucket"], []).append(r)

    print(f"\n{'='*72}")
    print(f"Wrote {n} rows to:")
    print(f"  CSV:  {OUT_CSV}")
    print(f"  JSON: {OUT_JSON}")

    print(f"\n{'='*72}")
    print(f"Overall hit rate:")
    ror_hits = sum(1 for r in results if r["ror_domain"])
    wd_hits = sum(1 for r in results if r["wikidata_domain"])
    lo_hits = sum(1 for r in results if r["logodev_domain"])
    any_hit = sum(1 for r in results if r["consensus_domain"])
    two_plus = sum(1 for r in results if r["agreement_count"] >= 2)
    triple = sum(1 for r in results if r["agreement_count"] == 3)
    print(f"  ROR:                  {ror_hits:4d} / {n}  ({ror_hits/n*100:5.1f}%)")
    print(f"  Wikidata:             {wd_hits:4d} / {n}  ({wd_hits/n*100:5.1f}%)")
    print(f"  Logo.dev:             {lo_hits:4d} / {n}  ({lo_hits/n*100:5.1f}%)")
    print(f"  Any source got data:  {any_hit:4d} / {n}  ({any_hit/n*100:5.1f}%)")
    print(f"  >=2 sources agree:    {two_plus:4d} / {n}  ({two_plus/n*100:5.1f}%)")
    print(f"  All 3 agree:          {triple:4d} / {n}  ({triple/n*100:5.1f}%)")

    print(f"\n{'='*72}")
    print(f"Per-bucket coverage (consensus_domain non-null):")
    print(f"  {'bucket':<22s} {'n':>4s}  {'ROR':>6s} {'WD':>6s} {'Logo':>6s} {'any':>6s} {'>=2':>6s}")
    for bucket, rs in sorted(by_bucket.items(), key=lambda kv: -len(kv[1])):
        bn = len(rs)
        b_ror = sum(1 for r in rs if r["ror_domain"])
        b_wd = sum(1 for r in rs if r["wikidata_domain"])
        b_lo = sum(1 for r in rs if r["logodev_domain"])
        b_any = sum(1 for r in rs if r["consensus_domain"])
        b_two = sum(1 for r in rs if r["agreement_count"] >= 2)
        print(f"  {bucket:<22s} {bn:>4d}  "
              f"{b_ror:>3d}{b_ror/bn*100:>3.0f}%  "
              f"{b_wd:>3d}{b_wd/bn*100:>3.0f}%  "
              f"{b_lo:>3d}{b_lo/bn*100:>3.0f}%  "
              f"{b_any:>3d}{b_any/bn*100:>3.0f}%  "
              f"{b_two:>3d}{b_two/bn*100:>3.0f}%")


if __name__ == "__main__":
    asyncio.run(main())
