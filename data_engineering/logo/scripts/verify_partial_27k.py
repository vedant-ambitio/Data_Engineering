"""
Quality check on completed batches (run while step2 is still running).
Loads all batch_*.json files, samples or probes all filled domains,
reports working-rate.
"""
import json
import socket
import urllib.request
import urllib.parse
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import urllib3

warnings.filterwarnings("ignore")
urllib3.disable_warnings()

BASE = Path(r"c:/Users/HP/OneDrive/Desktop/course_data/Logo_url_extract")
BATCHES = BASE / "batches_27k"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "*/*"}
TIMEOUT = 8
WORKERS = 60
SAMPLE = 500  # probe 500 random fills for speed


def probe(rec):
    domain = rec["domain"]
    out = {"id": rec["id"], "name": rec["name"], "domain": domain,
           "verdict": None, "code": None}
    for scheme in ("https", "http"):
        for method in ("HEAD", "GET"):
            try:
                r = requests.request(method, f"{scheme}://{domain}",
                                     headers=HEADERS, timeout=TIMEOUT,
                                     allow_redirects=True, verify=False, stream=True)
                code = r.status_code
                r.close()
                out["code"] = code
                if 200 <= code < 400:
                    out["verdict"] = "alive"
                    return out
                if code in (405, 501) and method == "HEAD":
                    continue
                if code in (400, 401, 403, 405, 429):
                    out["verdict"] = "alive_blocked"
                else:
                    out["verdict"] = "alive_other_http"
                return out
            except requests.exceptions.SSLError:
                break
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout, Exception):
                break
    # HTTP failed entirely. Try DoH for apex and www.
    for prefix in ("", "www."):
        try:
            req = urllib.request.Request(
                f"https://dns.google/resolve?name={urllib.parse.quote(prefix+domain)}&type=A",
                headers={"Accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.load(r)
            if data.get("Answer"):
                out["verdict"] = "alive_via_www" if prefix else "alive_dns_only"
                return out
        except Exception:
            pass
    out["verdict"] = "dead"
    return out


def main():
    files = sorted(BATCHES.glob("batch_*.json"))
    print(f"Loading {len(files)} completed batches...")
    all_records = []
    for f in files:
        all_records.extend(json.loads(f.read_text(encoding="utf-8")))
    total = len(all_records)
    filled = [r for r in all_records if r.get("domain")]
    nulls = total - len(filled)
    print(f"Total records: {total:,}   Filled: {len(filled):,} "
          f"({len(filled)/total*100:.1f}%)   Null: {nulls:,}")

    # Sample for speed
    import random
    random.seed(42)
    sample = random.sample(filled, min(SAMPLE, len(filled)))
    print(f"Probing {len(sample)} random fills (60 parallel workers)...")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = [ex.submit(probe, r) for r in sample]
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(sample)}")

    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    n = len(results)

    alive_count = sum(counts.get(k, 0)
                      for k in ("alive", "alive_blocked", "alive_other_http",
                                "alive_via_www", "alive_dns_only"))

    print()
    print("=" * 60)
    print(f"Quality check on {n} sampled fills (from {len(filled):,} total fills)")
    print("=" * 60)
    for verdict in ("alive", "alive_blocked", "alive_via_www",
                    "alive_dns_only", "alive_other_http", "dead"):
        c = counts.get(verdict, 0)
        if c:
            print(f"  {verdict:>20}: {c:>4}  ({c/n*100:5.1f}%)")
    print()
    print(f"REAL WORKING RATE: {alive_count}/{n} = {alive_count/n*100:.1f}%")
    if counts.get("dead", 0):
        print()
        print(f"Dead domains (verified non-existent):")
        for r in results:
            if r["verdict"] == "dead":
                print(f"  {r['id']:>8}  {r['name'][:40]:<40}  {r['domain']}")


if __name__ == "__main__":
    main()
