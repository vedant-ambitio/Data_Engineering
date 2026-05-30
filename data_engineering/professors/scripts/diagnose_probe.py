"""Diagnose why probing is slow. Time individual requests."""
import json
import time
import requests

CACHE = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info\probe_cache_v3.json"

# Get a few unprobed URLs from a grounding file
import os
GROUNDING = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info\grounding"
sample = []
for fn in sorted(os.listdir(GROUNDING))[:10]:
    with open(os.path.join(GROUNDING, fn), encoding="utf-8") as f:
        data = json.load(f)
    chunks = (data.get("grounding_metadata") or {}).get("groundingChunks") or []
    for c in chunks:
        web = (c or {}).get("web") or {}
        if web.get("uri"):
            sample.append(web["uri"])
            break

print(f"Got {len(sample)} sample URLs")

# Time HEAD with redirects (current approach)
print("\n--- HEAD allow_redirects=True, 6s timeout ---")
for url in sample[:5]:
    t0 = time.time()
    try:
        r = requests.head(url, allow_redirects=True, timeout=6,
                          headers={"User-Agent": "Mozilla/5.0"})
        dt = time.time() - t0
        print(f"  {dt:.2f}s  status={r.status_code}  final={r.url[:80]}")
    except Exception as e:
        dt = time.time() - t0
        print(f"  {dt:.2f}s  ERROR: {type(e).__name__}: {str(e)[:80]}")

# Time HEAD without redirects (just hit vertex)
print("\n--- HEAD allow_redirects=False (vertex only) ---")
for url in sample[:5]:
    t0 = time.time()
    try:
        r = requests.head(url, allow_redirects=False, timeout=6,
                          headers={"User-Agent": "Mozilla/5.0"})
        dt = time.time() - t0
        print(f"  {dt:.2f}s  status={r.status_code}  loc={r.headers.get('Location', '')[:80]}")
    except Exception as e:
        dt = time.time() - t0
        print(f"  {dt:.2f}s  ERROR: {type(e).__name__}: {str(e)[:80]}")

# Time GET stream=True allow_redirects=True
print("\n--- GET stream=True, allow_redirects=True ---")
for url in sample[:5]:
    t0 = time.time()
    try:
        r = requests.get(url, allow_redirects=True, timeout=6, stream=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        dt = time.time() - t0
        print(f"  {dt:.2f}s  status={r.status_code}  final={r.url[:80]}")
        r.close()
    except Exception as e:
        dt = time.time() - t0
        print(f"  {dt:.2f}s  ERROR: {type(e).__name__}: {str(e)[:80]}")
