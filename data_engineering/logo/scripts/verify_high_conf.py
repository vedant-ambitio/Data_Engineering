"""
Verify high-confidence domains in test_500_filled.json by HTTP HEAD/GET request.
For each domain: try https first, fall back to http; follow redirects.
Classify as: alive | client_error | server_error | dns_fail | timeout | other_error.
"""
import json
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import urllib3

warnings.filterwarnings("ignore")
urllib3.disable_warnings()

BASE = Path(r"c:/Users/HP/OneDrive/Desktop/course_data/Logo_url_extract")
INPUT = BASE / "test_500_filled.json"
OUTPUT_JSON = BASE / "high_conf_verification.json"
OUTPUT_TXT = BASE / "high_conf_verification_summary.txt"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "*/*"}
TIMEOUT = 10
MAX_WORKERS = 25


def probe(domain: str) -> dict:
    """Try HEAD then GET (some sites block HEAD). Try https then http."""
    result = {
        "domain": domain,
        "status": None,
        "http_code": None,
        "final_url": None,
        "elapsed_ms": None,
        "error": None,
        "method_used": None,
    }
    start = time.time()
    last_err = None

    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}"
        for method in ("HEAD", "GET"):
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=HEADERS,
                    timeout=TIMEOUT,
                    allow_redirects=True,
                    verify=False,
                    stream=True,
                )
                code = resp.status_code
                result["http_code"] = code
                result["final_url"] = resp.url
                result["method_used"] = f"{scheme}/{method}"
                result["elapsed_ms"] = int((time.time() - start) * 1000)
                resp.close()

                if 200 <= code < 400:
                    result["status"] = "alive"
                    return result
                if code in (405, 501) and method == "HEAD":
                    # method not allowed -> retry with GET
                    continue
                if 400 <= code < 500:
                    result["status"] = "client_error"
                    return result
                if 500 <= code < 600:
                    result["status"] = "server_error"
                    return result
                result["status"] = "other_error"
                return result
            except requests.exceptions.SSLError as e:
                last_err = f"ssl: {type(e).__name__}"
                # try http fallback
                break
            except requests.exceptions.ConnectionError as e:
                msg = str(e).lower()
                if "name or service not known" in msg or "nodename nor servname" in msg or "getaddrinfo failed" in msg:
                    last_err = "dns_fail"
                else:
                    last_err = f"conn: {type(e).__name__}"
                break
            except requests.exceptions.Timeout:
                last_err = "timeout"
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {str(e)[:80]}"
                break

    result["error"] = last_err
    result["elapsed_ms"] = int((time.time() - start) * 1000)
    if last_err == "dns_fail":
        result["status"] = "dns_fail"
    elif last_err == "timeout":
        result["status"] = "timeout"
    else:
        result["status"] = "other_error"
    return result


def main() -> None:
    with INPUT.open(encoding="utf-8") as f:
        data = json.load(f)

    high_conf = [r for r in data if r["domain"] and r["confidence"] == "high"]
    print(f"Verifying {len(high_conf)} high-confidence domains "
          f"({MAX_WORKERS} parallel workers, {TIMEOUT}s timeout)...")
    print()

    results = []
    completed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(probe, r["domain"]): r for r in high_conf}
        for fut in as_completed(futures):
            rec = futures[fut]
            res = fut.result()
            res["id"] = rec["id"]
            res["name"] = rec["name"]
            results.append(res)
            completed += 1
            if completed % 25 == 0 or completed == len(high_conf):
                print(f"  {completed}/{len(high_conf)} done...")

    # Sort by id for stable output
    results.sort(key=lambda r: r["id"])

    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Summary
    total = len(results)
    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    lines = []
    lines.append(f"High-confidence fill verification — {total} domains probed\n")
    lines.append("=" * 60)
    lines.append("")
    for status in ("alive", "client_error", "server_error",
                   "dns_fail", "timeout", "other_error"):
        items = by_status.get(status, [])
        pct = len(items) / total * 100
        lines.append(f"{status:>15}: {len(items):>4}  ({pct:5.1f}%)")
    lines.append("")
    lines.append("=" * 60)
    lines.append("")

    failures = [r for r in results
                if r["status"] not in ("alive",)]
    if failures:
        lines.append(f"\n{len(failures)} non-alive domains (need attention):\n")
        lines.append("-" * 60)
        for r in failures:
            extra = r.get("error") or f"HTTP {r.get('http_code')}"
            lines.append(
                f"  {r['id']:>8}  {r['name'][:45]:<45}  "
                f"{r['domain']:<40}  [{r['status']}] {extra}"
            )

    summary = "\n".join(lines)
    OUTPUT_TXT.write_text(summary, encoding="utf-8")
    print()
    print(summary)


if __name__ == "__main__":
    main()
