"""
Full analysis of the 27,281-record run:
1. Coverage check (every input id covered)
2. Filled / null breakdown by confidence
3. HEAD test on EVERY filled domain (HTTP + DNS fallback)
"""
import json
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
INPUT_FILE = BASE / "query_result_2026-05-06T14_11_02.778206369+05_30.json"
OUTPUT_FILE = BASE / "domains_27k_full.json"
VERIFICATION_FILE = BASE / "domains_27k_verification.json"
REPORT_FILE = BASE / "domains_27k_report.txt"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "*/*"}
TIMEOUT = 8
WORKERS = 80


def probe(rec):
    """HTTP probe with DNS fallback. Returns verdict."""
    domain = rec["domain"]
    out = {"id": rec["id"], "name": rec["name"], "domain": domain,
           "confidence": rec.get("confidence"), "verdict": None, "code": None}
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
    # HTTP failed — DoH for apex and www.
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
    # ---- 1. Coverage check ----
    with INPUT_FILE.open(encoding="utf-8") as f:
        input_records = json.load(f)
    input_ids = {r["id"] for r in input_records}

    with OUTPUT_FILE.open(encoding="utf-8") as f:
        output_records = json.load(f)
    output_ids = {r["id"] for r in output_records}

    print("=" * 70)
    print("STEP 1 — COVERAGE")
    print("=" * 70)
    print(f"Input file records:        {len(input_records):>7,}  (unique ids: {len(input_ids):,})")
    print(f"Output file records:       {len(output_records):>7,}  (unique ids: {len(output_ids):,})")
    missing = input_ids - output_ids
    extra = output_ids - input_ids
    duplicates = len(output_records) - len(output_ids)
    print(f"Missing (in input not in output): {len(missing):>4}")
    print(f"Extra (in output not in input):   {len(extra):>4}")
    print(f"Duplicate ids in output:          {duplicates:>4}")
    if missing:
        print(f"  First 5 missing ids: {list(missing)[:5]}")
    if extra:
        print(f"  First 5 extra ids:   {list(extra)[:5]}")
    coverage_pct = len(input_ids & output_ids) / len(input_ids) * 100
    print(f"COVERAGE: {coverage_pct:.2f}%")

    # ---- 2. Filled vs null breakdown ----
    print()
    print("=" * 70)
    print("STEP 2 — FILLED / NULL BREAKDOWN")
    print("=" * 70)
    filled = [r for r in output_records if r.get("domain")]
    nulls = [r for r in output_records if not r.get("domain")]

    filled_conf = {"high": 0, "medium": 0, "low": 0, "?": 0}
    for r in filled:
        c = r.get("confidence", "?")
        filled_conf[c] = filled_conf.get(c, 0) + 1

    null_conf = {"high": 0, "medium": 0, "low": 0, "?": 0}
    for r in nulls:
        c = r.get("confidence", "?")
        null_conf[c] = null_conf.get(c, 0) + 1

    total = len(output_records)
    print(f"Total records:    {total:>7,}")
    print(f"Filled:           {len(filled):>7,}  ({len(filled)/total*100:5.2f}%)")
    print(f"  high confidence:    {filled_conf['high']:>7,}  ({filled_conf['high']/total*100:5.2f}%)")
    print(f"  medium confidence:  {filled_conf['medium']:>7,}  ({filled_conf['medium']/total*100:5.2f}%)")
    print(f"  low confidence:     {filled_conf['low']:>7,}  ({filled_conf['low']/total*100:5.2f}%)")
    print(f"Null:             {len(nulls):>7,}  ({len(nulls)/total*100:5.2f}%)")
    print(f"  null + low conf:    {null_conf['low']:>7,}  (gemini explicitly low-conf nulls)")
    print(f"  null + high conf:   {null_conf['high']:>7,}  (gemini confident-generic nulls)")
    print(f"  null + medium conf: {null_conf['medium']:>7,}")

    # ---- 3. HEAD probe on ALL filled domains ----
    print()
    print("=" * 70)
    print(f"STEP 3 — HEAD PROBE on all {len(filled):,} filled domains "
          f"({WORKERS} parallel workers)")
    print("=" * 70)
    print("This will take ~5-10 minutes...")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = [ex.submit(probe, r) for r in filled]
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if done % 500 == 0 or done == len(filled):
                print(f"  {done}/{len(filled):,} probed...")

    # Save full verification
    VERIFICATION_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                 encoding="utf-8")

    # Tally verdicts
    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    n = len(results)
    alive_buckets = ["alive", "alive_blocked", "alive_via_www",
                     "alive_dns_only", "alive_other_http"]
    alive_count = sum(counts.get(k, 0) for k in alive_buckets)
    dead_count = counts.get("dead", 0)

    print()
    print(f"Verdict breakdown:")
    for v in ("alive", "alive_blocked", "alive_via_www",
              "alive_dns_only", "alive_other_http", "dead"):
        c = counts.get(v, 0)
        if c:
            print(f"  {v:>20}: {c:>6,}  ({c/n*100:5.2f}%)")

    print()
    print(f"REAL WORKING RATE: {alive_count:,}/{n:,} = {alive_count/n*100:.2f}%")
    print(f"Truly dead domains:  {dead_count:,}/{n:,} = {dead_count/n*100:.2f}%")

    # Save report
    report_lines = []
    report_lines.append("Full 27,281-record run analysis")
    report_lines.append("=" * 70)
    report_lines.append("")
    report_lines.append(f"Coverage: {coverage_pct:.2f}% ({len(input_ids & output_ids):,} of {len(input_ids):,} input ids)")
    report_lines.append(f"Missing ids: {len(missing)}")
    report_lines.append(f"Extra ids:   {len(extra)}")
    report_lines.append(f"Duplicates:  {duplicates}")
    report_lines.append("")
    report_lines.append(f"Total processed:  {total:,}")
    report_lines.append(f"Filled:           {len(filled):,}  ({len(filled)/total*100:.2f}%)")
    report_lines.append(f"  high conf:      {filled_conf['high']:,}")
    report_lines.append(f"  medium conf:    {filled_conf['medium']:,}")
    report_lines.append(f"  low conf:       {filled_conf['low']:,}")
    report_lines.append(f"Null:             {len(nulls):,}  ({len(nulls)/total*100:.2f}%)")
    report_lines.append(f"  null+high conf: {null_conf['high']:,}")
    report_lines.append(f"  null+low conf:  {null_conf['low']:,}")
    report_lines.append("")
    report_lines.append("HEAD probe on filled domains:")
    for v in ("alive", "alive_blocked", "alive_via_www",
              "alive_dns_only", "alive_other_http", "dead"):
        c = counts.get(v, 0)
        if c:
            report_lines.append(f"  {v:>20}: {c:>6,}  ({c/n*100:5.2f}%)")
    report_lines.append("")
    report_lines.append(f"REAL WORKING RATE: {alive_count:,}/{n:,} = {alive_count/n*100:.2f}%")
    report_lines.append(f"Truly dead:        {dead_count:,}/{n:,} = {dead_count/n*100:.2f}%")

    if dead_count:
        report_lines.append("")
        report_lines.append(f"All {dead_count} dead domains:")
        for r in results:
            if r["verdict"] == "dead":
                report_lines.append(f"  {r['id']:>8}  {r['name'][:45]:<45}  {r['domain']}")

    REPORT_FILE.write_text("\n".join(report_lines), encoding="utf-8")
    print()
    print(f"Report -> {REPORT_FILE.name}")
    print(f"Verification details -> {VERIFICATION_FILE.name}")


if __name__ == "__main__":
    main()
