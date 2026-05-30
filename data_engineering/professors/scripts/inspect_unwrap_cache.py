"""Inspect unwrap_cache.json: count unique real URLs, show samples."""
import json
import os
import random
from collections import Counter
from urllib.parse import urlparse

CACHE = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info\unwrap_cache.json"

with open(CACHE, "r", encoding="utf-8") as f:
    cache = json.load(f)

print(f"Total cache entries (vertex URIs): {len(cache)}")

# Successful entries with a real_url
ok_entries = [(k, v) for k, v in cache.items() if v.get("real_url")]
real_urls = [v["real_url"] for k, v in ok_entries]
unique_urls = set(real_urls)

print(f"Entries with real_url:            {len(ok_entries)}")
print(f"Unique real URLs (after dedup):   {len(unique_urls)}")
print(f"Dedup ratio:                      {len(real_urls)/max(len(unique_urls),1):.1f}x")
print()

# Status code distribution
status_codes = Counter(v.get("status") for v in cache.values())
print("Vertex response status codes:")
for code, n in status_codes.most_common():
    print(f"  {code}: {n}")
print()

# TLD/domain distribution among real URLs
def get_tld(url):
    try:
        host = urlparse(url).hostname or ""
        parts = host.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return host
    except Exception:
        return "?"

domains = Counter(get_tld(u) for u in real_urls)
print("Top 20 destination domains:")
for d, n in domains.most_common(20):
    print(f"  {n:5d}  {d}")
print()

# Sample 20 random unwraps
print("20 random unwraps (vertex_uri[:50] -> real_url):")
random.seed(42)
sample = random.sample(ok_entries, min(20, len(ok_entries)))
for vuri, info in sample:
    print(f"  {vuri[:50]}...  ->  {info['real_url'][:90]}")
