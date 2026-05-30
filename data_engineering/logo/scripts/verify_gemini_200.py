"""
Verify the Gemini 3.1 Pro 200-record pilot:
- Count nulls vs fills
- HTTP HEAD/GET probe on every filled domain
- DNS resolution check (apex AND www) on anything that didn't respond
- Final precision number
"""
import json
import socket
import time
import warnings
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import urllib3

warnings.filterwarnings("ignore")
urllib3.disable_warnings()

BASE = Path(r"c:/Users/HP/OneDrive/Desktop/course_data/Logo_url_extract")
INPUT = BASE / "gemini_200_filled.json"
OUTPUT = BASE / "gemini_200_verification.json"
OUTPUT_TXT = BASE / "gemini_200_verification_summary.txt"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "*/*"}
TIMEOUT = 10
MAX_WORKERS = 25


def http_probe(domain: str) -> dict:
    """HEAD then GET; https then http; follow redirects."""
    res = {"domain": domain, "status": None, "http_code": None,
           "final_url": None, "error": None}
    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}"
        for method in ("HEAD", "GET"):
            try:
                r = requests.request(method, url, headers=HEADERS, timeout=TIMEOUT,
                                     allow_redirects=True, verify=False, stream=True)
                code = r.status_code
                res["http_code"] = code
                res["final_url"] = r.url
                r.close()
                if 200 <= code < 400:
                    res["status"] = "alive"
                    return res
                if code in (405, 501) and method == "HEAD":
                    continue
                if 400 <= code < 500:
                    res["status"] = "alive_blocked" if code in (400, 401, 403, 405, 429) else "alive_4xx"
                    return res
                if 500 <= code < 600:
                    res["status"] = "alive_5xx"
                    return res
            except requests.exceptions.SSLError:
                break
            except requests.exceptions.ConnectionError as e:
                msg = str(e).lower()
                if "getaddrinfo failed" in msg or "name or service" in msg:
                    res["error"] = "dns_fail"
                else:
                    res["error"] = "conn_error"
                break
            except requests.exceptions.Timeout:
                res["error"] = "timeout"
                break
            except Exception as e:
                res["error"] = f"{type(e).__name__}"
                break
    if res["status"] is None:
        res["status"] = res.get("error") or "no_response"
    return res


def doh_resolves(domain: str, prefix: str = "") -> bool:
    name = (prefix + domain) if prefix else domain
    try:
        req = urllib.request.Request(
            f"https://dns.google/resolve?name={urllib.parse.quote(name)}&type=A",
            headers={"Accept": "application/dns-json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        return bool(data.get("Answer"))
    except Exception:
        return False


def classify(rec: dict) -> dict:
    """Final verdict combining HTTP + DNS evidence."""
    if rec["status"] == "alive":
        rec["verdict"] = "alive"
    elif rec["status"] in ("alive_blocked", "alive_4xx"):
        rec["verdict"] = "alive_blocked"
    elif rec["status"] == "alive_5xx":
        rec["verdict"] = "alive_5xx"
    elif rec["status"] in ("timeout", "conn_error"):
        # HTTP failed but DNS may resolve — confirms domain exists
        rec["verdict"] = "alive_dns_only" if doh_resolves(rec["domain"]) else "unreachable"
    elif rec["status"] in ("dns_fail", "no_response"):
        # apex didn't resolve — try www. (very common)
        if doh_resolves(rec["domain"], prefix="www."):
            rec["verdict"] = "alive_via_www"
        elif doh_resolves(rec["domain"]):
            rec["verdict"] = "alive_dns_only"
        else:
            rec["verdict"] = "dead"
    else:
        rec["verdict"] = "dead"
    return rec


def main() -> None:
    data = json.loads(INPUT.read_text(encoding="utf-8"))
    total = len(data)
    filled = [r for r in data if r.get("domain")]
    nulls = [r for r in data if not r.get("domain")]
    print(f"Loaded {total} records: {len(filled)} filled, {len(nulls)} null")
    print()

    # Probe filled
    print(f"Probing {len(filled)} domains...")
    probe_results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(http_probe, r["domain"]): r for r in filled}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = futs[fut]
            res = fut.result()
            res["id"] = rec["id"]
            res["name"] = rec["name"]
            res["confidence"] = rec.get("confidence")
            probe_results.append(res)
            if i % 25 == 0 or i == len(filled):
                print(f"  {i}/{len(filled)} probed...")
    print()

    # DNS reclassification on the failures
    print("DNS-fallback for non-alive domains...")
    final = []
    with ThreadPoolExecutor(max_workers=15) as ex:
        final = list(ex.map(classify, probe_results))

    # Confidence-bucket the verdicts
    by_verdict = {}
    for r in final:
        by_verdict.setdefault(r["verdict"], []).append(r)

    real_alive_buckets = ["alive", "alive_blocked", "alive_5xx",
                          "alive_dns_only", "alive_via_www"]
    real_alive = sum(len(by_verdict.get(v, [])) for v in real_alive_buckets)
    dead = len(by_verdict.get("dead", []))
    unreachable = len(by_verdict.get("unreachable", []))

    OUTPUT.write_text(json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")

    # Confidence breakdown for filled
    by_conf = {"high": 0, "medium": 0, "low": 0}
    for r in filled:
        c = r.get("confidence", "high")
        by_conf[c] = by_conf.get(c, 0) + 1

    lines = []
    lines.append("Gemini 3.1 Pro — 200-record verification")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Total records:          {total}")
    lines.append(f"  Filled (has domain):  {len(filled):>4}  ({len(filled)/total*100:.1f}%)")
    lines.append(f"  Null (no domain):     {len(nulls):>4}  ({len(nulls)/total*100:.1f}%)")
    lines.append("")
    lines.append("Filled-record confidence breakdown:")
    for c in ("high", "medium", "low"):
        lines.append(f"  {c:>10}: {by_conf.get(c, 0)}")
    lines.append("")
    lines.append("Verification of filled domains:")
    lines.append(f"  Alive (direct 200/3xx):       {len(by_verdict.get('alive', [])):>4}")
    lines.append(f"  Alive but bot-blocked (4xx):  {len(by_verdict.get('alive_blocked', [])):>4}")
    lines.append(f"  Alive but server 5xx:         {len(by_verdict.get('alive_5xx', [])):>4}")
    lines.append(f"  Alive on www. only:           {len(by_verdict.get('alive_via_www', [])):>4}")
    lines.append(f"  Alive (DNS resolves, HTTP timeout): {len(by_verdict.get('alive_dns_only', [])):>4}")
    lines.append(f"  Unreachable (HTTP+DNS failed): {unreachable:>4}")
    lines.append(f"  Dead (no DNS at all):         {dead:>4}")
    lines.append("")
    lines.append("=" * 60)
    lines.append(f"REAL PRECISION on filled records: {real_alive}/{len(filled)} = {real_alive/len(filled)*100:.1f}%")
    lines.append(f"Counting unreachables as alive (TLD/network): {(real_alive+unreachable)/len(filled)*100:.1f}%")
    lines.append("=" * 60)
    if dead > 0:
        lines.append("")
        lines.append("DEAD domains (truly nonexistent):")
        for r in by_verdict.get("dead", []):
            lines.append(f"  {r['id']:>8}  {r['name'][:45]:<45}  {r['domain']}")
    if unreachable > 0:
        lines.append("")
        lines.append("UNREACHABLE (likely network-side blocked, may still be valid):")
        for r in by_verdict.get("unreachable", []):
            lines.append(f"  {r['id']:>8}  {r['name'][:45]:<45}  {r['domain']}")

    summary = "\n".join(lines)
    OUTPUT_TXT.write_text(summary, encoding="utf-8")
    print()
    print(summary)


if __name__ == "__main__":
    main()
