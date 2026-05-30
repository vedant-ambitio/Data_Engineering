"""Stage 2 (Tier C): Probe each unique real URL from Tier C Stage 1.

Source: unwrap_cache_tier_c.json
Cache:  probe_cache_tier_c.json
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

BASE = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info"
UNWRAP_CACHE = os.path.join(BASE, "unwrap_cache_tier_c.json")
PROBE_CACHE = os.path.join(BASE, "probe_cache_tier_c.json")

CONCURRENCY = 200
TIMEOUT = 5
USER_AGENT = "Mozilla/5.0 (compatible; CoverageBot/1.0)"

SESSION = requests.Session()
_adapter = HTTPAdapter(pool_connections=500, pool_maxsize=500, max_retries=0)
SESSION.mount("https://", _adapter)
SESSION.mount("http://", _adapter)
SESSION.headers.update({"User-Agent": USER_AGENT})

print("Loading unwrap_cache_tier_c.json...")
with open(UNWRAP_CACHE, "r", encoding="utf-8") as f:
    unwrap = json.load(f)

real_urls = {v["real_url"] for v in unwrap.values() if v.get("real_url")}
print(f"  unique real URLs: {len(real_urls)}")

probe_cache = {}
if os.path.exists(PROBE_CACHE):
    try:
        with open(PROBE_CACHE, "r", encoding="utf-8") as f:
            probe_cache = json.load(f)
        print(f"  loaded probe cache: {len(probe_cache)} entries")
    except Exception:
        probe_cache = {}

todo = [u for u in real_urls if u not in probe_cache]
print(f"  to probe: {len(todo)}\n")

if not todo:
    print("Nothing to do.")
    raise SystemExit(0)


def probe(url):
    try:
        r = SESSION.head(url, allow_redirects=True, timeout=TIMEOUT, verify=False)
        if r.status_code in (400, 403, 405, 406, 501) or r.status_code >= 500:
            r = SESSION.get(url, allow_redirects=True, timeout=TIMEOUT, verify=False, stream=True)
            try:
                r.close()
            except Exception:
                pass
        return url, {
            "status": r.status_code,
            "ok": r.status_code < 400,
            "final_url": r.url,
        }
    except requests.exceptions.Timeout:
        return url, {"status": None, "ok": False, "error": "timeout"}
    except requests.exceptions.SSLError as e:
        return url, {"status": None, "ok": False, "error": f"ssl: {str(e)[:60]}"}
    except requests.exceptions.ConnectionError as e:
        return url, {"status": None, "ok": False, "error": f"conn: {str(e)[:60]}"}
    except Exception as e:
        return url, {"status": None, "ok": False, "error": str(e)[:80]}


print(f"Stage 2 (Tier C): probing with {CONCURRENCY} workers (verify=False, timeout={TIMEOUT}s)")
start = time.time()
done = 0
last_save = 0
SAVE_EVERY = 500

with ThreadPoolExecutor(max_workers=CONCURRENCY) as exe:
    futs = {exe.submit(probe, u): u for u in todo}
    for fut in as_completed(futs):
        url, result = fut.result()
        probe_cache[url] = result
        done += 1
        if done % 200 == 0:
            elapsed = time.time() - start
            rate = done / elapsed if elapsed else 0
            eta = (len(todo) - done) / rate if rate else 0
            print(f"  {done}/{len(todo)}  ({rate:.1f}/s  ETA {eta:.0f}s)", flush=True)
        if done - last_save >= SAVE_EVERY:
            with open(PROBE_CACHE, "w", encoding="utf-8") as f:
                json.dump(probe_cache, f)
            last_save = done

with open(PROBE_CACHE, "w", encoding="utf-8") as f:
    json.dump(probe_cache, f)

elapsed = time.time() - start
print(f"\nDone in {elapsed:.0f}s")

ok = sum(1 for v in probe_cache.values() if v.get("ok"))
err = sum(1 for v in probe_cache.values() if v.get("error"))
not_ok = sum(1 for v in probe_cache.values() if not v.get("ok") and not v.get("error"))

print(f"\nProbe cache: {len(probe_cache)} entries")
print(f"  working (status < 400):       {ok}  ({ok/len(probe_cache)*100:.1f}%)")
print(f"  error/timeout:                {err}")
print(f"  4xx/5xx response:             {not_ok}")
print()
codes = Counter(v.get("status") for v in probe_cache.values())
print("Status code histogram (top 15):")
for code, n in codes.most_common(15):
    print(f"  {str(code):>6}: {n}")
