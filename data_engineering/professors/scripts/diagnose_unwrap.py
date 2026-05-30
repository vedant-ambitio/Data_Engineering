"""Stage 1 diagnostic: how fast can we unwrap vertex grounding URLs?

Picks 20 random vertex URIs from grounding/ files, does sequential
HEAD allow_redirects=False, prints per-call timing.
"""
import json
import os
import random
import time

import requests

GROUNDING = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info\grounding"

print("Collecting sample URIs from random grounding files...")
all_uris = []
files = os.listdir(GROUNDING)
random.seed(42)  # reproducible
random.shuffle(files)
for fn in files[:60]:
    fp = os.path.join(GROUNDING, fn)
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        continue
    chunks = (data.get("grounding_metadata") or {}).get("groundingChunks") or []
    for c in chunks:
        web = (c or {}).get("web") or {}
        uri = web.get("uri", "")
        domain = web.get("domain", "")
        if uri:
            all_uris.append((uri, domain))

if len(all_uris) < 20:
    sample = all_uris
else:
    sample = random.sample(all_uris, 20)

print(f"Sample size: {len(sample)}\n")
print(f"{'#':>3} {'time':>6} {'status':>6}  {'expected_domain':<24} location")
print("-" * 110)

results = []
for i, (uri, domain) in enumerate(sample, 1):
    t0 = time.time()
    try:
        r = requests.head(uri, allow_redirects=False, timeout=10,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; CoverageBot/1.0)"})
        dt = time.time() - t0
        loc = r.headers.get("Location", "")
        results.append({"time": dt, "status": r.status_code, "loc": loc, "expected": domain})
        print(f"{i:>3} {dt:5.2f}s {r.status_code:>6}  {domain:<24} {loc[:80]}")
    except Exception as e:
        dt = time.time() - t0
        results.append({"time": dt, "status": None, "error": str(e)[:80], "expected": domain})
        print(f"{i:>3} {dt:5.2f}s   ERR  {domain:<24} {type(e).__name__}: {str(e)[:60]}")

print()
times = [r["time"] for r in results]
redirect_codes = (301, 302, 303, 307, 308)
succ = [r for r in results if r.get("status") in redirect_codes and r.get("loc")]
domain_matches = [r for r in succ if r["expected"].lower() in r["loc"].lower()]

print(f"Avg time:               {sum(times)/len(times):.2f}s")
print(f"Min / Max:              {min(times):.2f}s  /  {max(times):.2f}s")
print(f"Successful unwraps:     {len(succ)}/{len(results)}")
print(f"  with domain match:    {len(domain_matches)}/{len(succ)}")
print(f"Status code histogram:")
from collections import Counter
codes = Counter(r.get("status") for r in results)
for code, n in codes.most_common():
    print(f"  {code}: {n}")
