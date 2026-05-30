"""Stage 1 (Tier C): Unwrap vertex grounding redirects for Tier C universities.

Source: grounding_tier_c/*.json
Cache:  unwrap_cache_tier_c.json
"""
import json
import os
import time
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import InsecureRequestWarning

warnings.simplefilter("ignore", InsecureRequestWarning)

SESSION = requests.Session()
_adapter = HTTPAdapter(pool_connections=200, pool_maxsize=200, max_retries=0)
SESSION.mount("https://", _adapter)
SESSION.mount("http://", _adapter)
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; CoverageBot/1.0)"})

BASE = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info"
GROUNDING = os.path.join(BASE, "grounding_tier_c")
CACHE_PATH = os.path.join(BASE, "unwrap_cache_tier_c.json")

CONCURRENCY = 100
TIMEOUT = 8

print("Scanning grounding_tier_c/ for chunk URIs...")
all_uris = set()
files = sorted(f for f in os.listdir(GROUNDING) if f.endswith(".json"))
for fn in files:
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
        if uri:
            all_uris.add(uri)

print(f"  {len(files)} files scanned")
print(f"  {len(all_uris)} unique vertex URIs")

cache = {}
if os.path.exists(CACHE_PATH):
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  loaded cache: {len(cache)} entries")
    except Exception as e:
        print(f"  cache load error: {e}; starting fresh")
        cache = {}

unprobed = [u for u in all_uris if u not in cache]
print(f"  to unwrap: {len(unprobed)}\n")

if not unprobed:
    print("Nothing to do.")
    raise SystemExit(0)


def unwrap(uri):
    try:
        r = SESSION.head(uri, allow_redirects=False, timeout=TIMEOUT, verify=False)
        return uri, {
            "status": r.status_code,
            "real_url": r.headers.get("Location", "") or "",
        }
    except requests.exceptions.Timeout:
        return uri, {"status": None, "real_url": "", "error": "timeout"}
    except requests.exceptions.SSLError as e:
        return uri, {"status": None, "real_url": "", "error": f"ssl: {str(e)[:60]}"}
    except Exception as e:
        return uri, {"status": None, "real_url": "", "error": str(e)[:80]}


print(f"Stage 1 (Tier C): unwrapping with {CONCURRENCY} workers (verify=False, timeout={TIMEOUT}s)")
start = time.time()
done = 0
last_save = 0
SAVE_EVERY = 500

with ThreadPoolExecutor(max_workers=CONCURRENCY) as exe:
    futs = {exe.submit(unwrap, u): u for u in unprobed}
    for fut in as_completed(futs):
        uri, result = fut.result()
        cache[uri] = result
        done += 1
        if done % 200 == 0:
            elapsed = time.time() - start
            rate = done / elapsed if elapsed else 0
            eta = (len(unprobed) - done) / rate if rate else 0
            print(f"  {done}/{len(unprobed)}  ({rate:.1f}/s  ETA {eta:.0f}s)", flush=True)
        if done - last_save >= SAVE_EVERY:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            last_save = done

with open(CACHE_PATH, "w", encoding="utf-8") as f:
    json.dump(cache, f)

elapsed = time.time() - start
print(f"\nDone in {elapsed:.0f}s")

codes = Counter(r.get("status") for r in cache.values())
ok_redirects = sum(1 for r in cache.values()
                   if r.get("status") in (301, 302, 303, 307, 308) and r.get("real_url"))
err = sum(1 for r in cache.values() if r.get("error"))

print(f"\nCache: {len(cache)} entries")
print(f"  successful unwraps:  {ok_redirects}")
print(f"  errors/timeouts:     {err}")
print(f"  status codes:")
for code, n in codes.most_common():
    print(f"    {code}: {n}")
