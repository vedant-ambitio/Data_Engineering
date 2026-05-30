"""
HEAD test on all 14,915 high-confidence filled domains from the 27k run.

- HTTPS HEAD -> HTTPS GET -> HTTP HEAD -> HTTP GET (4 fallbacks per domain)
- 4xx (esp. 403) treated as 'alive_blocked' (server responded, just rejected our UA)
- On total HTTP failure: DNS-over-HTTPS check on apex AND www. (catches sites where only www. has A record)
- 80 parallel workers
- LIVE progress every 200 domains, with elapsed/ETA
- Writes verification JSON + summary report
"""
import json
import sys
import time
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
INPUT_FILE = BASE / "domains_27k_full.json"
OUTPUT_VERIFICATION = BASE / "high_conf_27k_verification.json"
OUTPUT_SUMMARY = BASE / "high_conf_27k_summary.txt"
OUTPUT_DEAD = BASE / "high_conf_27k_dead_domains.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "*/*"}
HTTP_TIMEOUT = 8
DNS_TIMEOUT = 6
WORKERS = 80


def probe(rec):
    domain = rec["domain"]
    out = {"id": rec["id"], "name": rec["name"], "domain": domain,
           "verdict": None, "code": None}

    for scheme in ("https", "http"):
        for method in ("HEAD", "GET"):
            try:
                r = requests.request(method, f"{scheme}://{domain}",
                                     headers=HEADERS, timeout=HTTP_TIMEOUT,
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

    # HTTP completely failed - try DNS-over-HTTPS for apex and www
    for prefix in ("", "www."):
        try:
            req = urllib.request.Request(
                f"https://dns.google/resolve?name={urllib.parse.quote(prefix+domain)}&type=A",
                headers={"Accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=DNS_TIMEOUT) as r:
                data = json.load(r)
            if data.get("Answer"):
                out["verdict"] = "alive_via_www" if prefix else "alive_dns_only"
                return out
        except Exception:
            pass

    out["verdict"] = "dead"
    return out


def fmt_time(seconds):
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m{s:02d}s"


def main():
    with INPUT_FILE.open(encoding="utf-8") as f:
        all_records = json.load(f)

    high_conf = [r for r in all_records
                 if r.get("domain") and r.get("confidence") == "high"]
    print(f"Loaded {len(all_records):,} total records.", flush=True)
    print(f"High-confidence filled: {len(high_conf):,}", flush=True)
    print(f"Probing with {WORKERS} parallel workers...", flush=True)
    print(flush=True)

    t_start = time.time()
    results = []
    counts = {"alive": 0, "alive_blocked": 0, "alive_via_www": 0,
              "alive_dns_only": 0, "alive_other_http": 0, "dead": 0}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = [ex.submit(probe, r) for r in high_conf]
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

            n = len(results)
            if n % 200 == 0 or n == len(high_conf):
                elapsed = time.time() - t_start
                rate = n / elapsed if elapsed > 0 else 0
                eta = (len(high_conf) - n) / rate if rate > 0 else 0
                alive_so_far = sum(counts[k] for k in counts if k != "dead")
                pct = alive_so_far / n * 100
                print(f"  [{n:>5,}/{len(high_conf):,}]  "
                      f"alive={alive_so_far:>5,} ({pct:5.1f}%)  "
                      f"dead={counts['dead']:>3}  "
                      f"elapsed={fmt_time(elapsed)}  "
                      f"eta={fmt_time(eta)}  "
                      f"rate={rate:.1f}/s",
                      flush=True)

    elapsed = time.time() - t_start
    print(flush=True)
    print("=" * 70, flush=True)
    print(f"DONE in {fmt_time(elapsed)}", flush=True)
    print("=" * 70, flush=True)

    n = len(results)
    alive_count = sum(counts[k] for k in counts if k != "dead")

    print(flush=True)
    print("Verdict breakdown:", flush=True)
    for v in ("alive", "alive_blocked", "alive_via_www",
              "alive_dns_only", "alive_other_http", "dead"):
        c = counts[v]
        if c:
            print(f"  {v:>20}: {c:>6,}  ({c/n*100:5.2f}%)", flush=True)

    print(flush=True)
    print(f"REAL WORKING RATE: {alive_count:,}/{n:,} = {alive_count/n*100:.2f}%", flush=True)
    print(f"Truly dead:        {counts['dead']:,}/{n:,} = {counts['dead']/n*100:.2f}%", flush=True)

    OUTPUT_VERIFICATION.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
    print(f"\nVerification JSON -> {OUTPUT_VERIFICATION.name}", flush=True)

    dead = [r for r in results if r["verdict"] == "dead"]
    OUTPUT_DEAD.write_text(json.dumps(dead, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"Dead domains -> {OUTPUT_DEAD.name}", flush=True)

    summary = []
    summary.append(f"High-conf fill HEAD verification (full 27k run)")
    summary.append("=" * 70)
    summary.append(f"Probed: {n:,} high-confidence filled domains")
    summary.append(f"Wall-clock: {fmt_time(elapsed)}")
    summary.append("")
    for v in ("alive", "alive_blocked", "alive_via_www",
              "alive_dns_only", "alive_other_http", "dead"):
        c = counts[v]
        if c:
            summary.append(f"  {v:>20}: {c:>6,}  ({c/n*100:5.2f}%)")
    summary.append("")
    summary.append(f"REAL WORKING RATE: {alive_count:,}/{n:,} = {alive_count/n*100:.2f}%")
    summary.append(f"Truly dead:        {counts['dead']:,}/{n:,} = {counts['dead']/n*100:.2f}%")
    if dead:
        summary.append("")
        summary.append("First 50 dead domains:")
        for r in dead[:50]:
            summary.append(f"  {r['id']:>8}  {r['name'][:45]:<45}  {r['domain']}")
    OUTPUT_SUMMARY.write_text("\n".join(summary), encoding="utf-8")
    print(f"Summary -> {OUTPUT_SUMMARY.name}", flush=True)


if __name__ == "__main__":
    main()
