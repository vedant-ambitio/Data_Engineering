"""Try 4 different ways of hitting vertex to find one that works:
   1. requests with verify=True (default)
   2. requests with verify=False
   3. urllib.request (stdlib)
   4. curl via subprocess
"""
import json
import os
import random
import time
import urllib.request
import subprocess
import ssl

GROUNDING = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info\grounding"

# Get one sample URI
random.seed(42)
files = os.listdir(GROUNDING)
random.shuffle(files)
sample_uri = None
for fn in files[:5]:
    with open(os.path.join(GROUNDING, fn), "r", encoding="utf-8") as f:
        data = json.load(f)
    chunks = (data.get("grounding_metadata") or {}).get("groundingChunks") or []
    for c in chunks:
        web = (c or {}).get("web") or {}
        uri = web.get("uri", "")
        if uri:
            sample_uri = uri
            break
    if sample_uri:
        break

print(f"Sample URI: {sample_uri[:120]}...\n")

# Method 1: requests verify=True
print("--- Method 1: requests (verify=True) ---")
try:
    import requests
    t0 = time.time()
    r = requests.head(sample_uri, allow_redirects=False, timeout=8,
                      headers={"User-Agent": "Mozilla/5.0"})
    print(f"  {time.time()-t0:.2f}s  status={r.status_code}  loc={r.headers.get('Location','')[:80]}")
except Exception as e:
    print(f"  {time.time()-t0:.2f}s  FAIL: {type(e).__name__}: {str(e)[:120]}")

# Method 2: requests verify=False
print("\n--- Method 2: requests (verify=False) ---")
try:
    import requests
    import warnings
    warnings.filterwarnings("ignore")
    t0 = time.time()
    r = requests.head(sample_uri, allow_redirects=False, timeout=8,
                      headers={"User-Agent": "Mozilla/5.0"}, verify=False)
    print(f"  {time.time()-t0:.2f}s  status={r.status_code}  loc={r.headers.get('Location','')[:80]}")
except Exception as e:
    print(f"  {time.time()-t0:.2f}s  FAIL: {type(e).__name__}: {str(e)[:120]}")

# Method 3: urllib.request
print("\n--- Method 3: urllib.request (stdlib) ---")
try:
    t0 = time.time()
    req = urllib.request.Request(sample_uri, method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0"})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        resp = opener.open(req, timeout=8)
        print(f"  {time.time()-t0:.2f}s  status={resp.status}  loc={resp.headers.get('Location','')[:80]}")
    except urllib.error.HTTPError as e:
        # Redirects come through as HTTPError because we blocked them
        print(f"  {time.time()-t0:.2f}s  status={e.code}  loc={e.headers.get('Location','')[:80]}")
except Exception as e:
    print(f"  {time.time()-t0:.2f}s  FAIL: {type(e).__name__}: {str(e)[:120]}")

# Method 4: curl
print("\n--- Method 4: curl ---")
try:
    t0 = time.time()
    out = subprocess.run(
        ["curl", "-sS", "-I", "--max-time", "8", "-A", "Mozilla/5.0", sample_uri],
        capture_output=True, text=True, timeout=12)
    print(f"  {time.time()-t0:.2f}s  rc={out.returncode}")
    if out.stdout:
        first_lines = out.stdout.splitlines()[:5]
        for line in first_lines:
            print(f"    {line[:100]}")
    if out.stderr:
        print(f"  stderr: {out.stderr[:120]}")
except Exception as e:
    print(f"  {time.time()-t0:.2f}s  FAIL: {type(e).__name__}: {str(e)[:120]}")

# Also check Python/OpenSSL versions
print(f"\n--- Environment ---")
print(f"  ssl.OPENSSL_VERSION: {ssl.OPENSSL_VERSION}")
import sys
print(f"  Python: {sys.version}")
try:
    import requests
    print(f"  requests: {requests.__version__}")
    import urllib3
    print(f"  urllib3: {urllib3.__version__}")
except Exception as e:
    print(f"  imports: {e}")
