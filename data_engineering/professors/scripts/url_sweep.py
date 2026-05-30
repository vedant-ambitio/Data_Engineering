"""
Verify the faculty_urls.csv pilot results by firing HTTP HEAD (with GET fallback)
against every non-empty URL. Reports true accuracy.

Output:
  - Per-row verdict printed to stdout
  - logs/url_sweep.csv with columns: university, department, url, final_url,
    status_code, verdict, elapsed_ms, error
  - Summary: count by verdict + overall accuracy
"""
from __future__ import annotations

import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import urllib.request
import urllib.error
import ssl

ROOT = Path(__file__).parent.parent
CSV_IN = ROOT / "output" / "faculty_urls.csv"
CSV_OUT = ROOT / "logs" / "url_sweep.csv"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)
TIMEOUT = 15
WORKERS = 15

# Permissive TLS context — we just want to know if the URL resolves; some
# university sites still ship outdated cert chains.
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def probe(url: str) -> dict:
    """Fire HEAD; if method-not-allowed or similar, fall back to GET. Follow
    redirects. Return dict with status, final_url, elapsed_ms, error."""
    start = time.time()
    headers = {"User-Agent": UA, "Accept": "*/*"}

    def _do(method: str) -> tuple[int, str]:
        req = urllib.request.Request(url, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as resp:
            return resp.status, resp.geturl()

    try:
        status, final = _do("HEAD")
        if status in (405, 501):
            status, final = _do("GET")
    except urllib.error.HTTPError as e:
        # HTTP error codes (404, 403, etc.) come here
        elapsed = int((time.time() - start) * 1000)
        return {"status": e.code, "final_url": url, "elapsed_ms": elapsed, "error": ""}
    except urllib.error.URLError as e:
        elapsed = int((time.time() - start) * 1000)
        return {"status": 0, "final_url": "", "elapsed_ms": elapsed, "error": f"URLError: {e.reason}"}
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return {"status": 0, "final_url": "", "elapsed_ms": elapsed, "error": f"{type(e).__name__}: {e}"}

    # If HEAD returned >=400, retry with GET — some sites answer HEAD with 403/400 but GET with 200
    if status >= 400:
        try:
            status, final = _do("GET")
        except urllib.error.HTTPError as e:
            elapsed = int((time.time() - start) * 1000)
            return {"status": e.code, "final_url": url, "elapsed_ms": elapsed, "error": ""}
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            return {"status": 0, "final_url": "", "elapsed_ms": elapsed, "error": f"{type(e).__name__}: {e}"}

    elapsed = int((time.time() - start) * 1000)
    return {"status": status, "final_url": final, "elapsed_ms": elapsed, "error": ""}


def classify(probe_result: dict, original_url: str) -> str:
    status = probe_result["status"]
    final = probe_result.get("final_url") or ""
    err = probe_result.get("error") or ""
    if err:
        if "timed out" in err.lower() or "timeout" in err.lower():
            return "timeout"
        return "connection_error"
    if status == 0:
        return "connection_error"
    if 200 <= status < 300:
        # Check if redirect landed on a homepage / generic page (host root)
        if final and final != original_url:
            from urllib.parse import urlparse
            pu = urlparse(final)
            if pu.path in ("", "/"):
                return "redirect_to_homepage"
            return "ok_redirect"
        return "ok"
    if status in (301, 302, 303, 307, 308):
        return "redirect"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "404"
    if status == 410:
        return "410_gone"
    if 400 <= status < 500:
        return f"{status}_client_error"
    if 500 <= status < 600:
        return f"{status}_server_error"
    return f"http_{status}"


def main():
    if not CSV_IN.exists():
        print(f"CSV not found: {CSV_IN}", file=sys.stderr)
        sys.exit(1)

    with open(CSV_IN, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    # Skip pre-filtered and not_found rows — no URL to probe
    to_check = [r for r in rows if (r.get("faculty_page_url") or "").strip()]
    skipped = len(rows) - len(to_check)
    print(f"Rows total: {len(rows)}  to_check: {len(to_check)}  skipped: {skipped}")
    print(f"Parallel workers: {WORKERS}, timeout: {TIMEOUT}s per request\n")

    results = []
    start = time.time()

    def task(row):
        url = row["faculty_page_url"].strip()
        pr = probe(url)
        verdict = classify(pr, url)
        return {
            "university": row.get("university", ""),
            "department": row.get("department", ""),
            "url": url,
            "final_url": pr.get("final_url", ""),
            "status_code": pr["status"],
            "verdict": verdict,
            "elapsed_ms": pr["elapsed_ms"],
            "error": pr.get("error", ""),
            "confidence": row.get("confidence", ""),
        }

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = [pool.submit(task, r) for r in to_check]
        for i, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            results.append(res)
            tag = res["verdict"]
            short_u = res["university"][:30]
            short_d = res["department"][:25]
            print(f"  [{i:3d}/{len(to_check)}] {tag:24s} {res['status_code']:4} "
                  f"{res['elapsed_ms']:5}ms  {short_u:30s}/{short_d:25s}  {res['url'][:65]}")

    elapsed = time.time() - start
    print(f"\n=== Sweep done in {elapsed:.1f}s ===\n")

    # Summary
    from collections import Counter
    by_verdict = Counter(r["verdict"] for r in results)
    total = len(results)
    oks = by_verdict.get("ok", 0) + by_verdict.get("ok_redirect", 0)
    print(f"Verdict breakdown (of {total}):")
    for v, c in by_verdict.most_common():
        pct = 100.0 * c / total
        print(f"  {v:28s} {c:4d}  ({pct:5.1f}%)")
    print()
    print(f"WORKING URLs: {oks}/{total} = {100*oks/total:.1f}%")

    # Write results CSV
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["university", "department", "confidence", "url", "final_url",
              "status_code", "verdict", "elapsed_ms", "error"]
    with open(CSV_OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"\nFull log: {CSV_OUT}")


if __name__ == "__main__":
    main()
