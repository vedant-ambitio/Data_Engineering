"""
Re-classify the verification results more honestly:
- 4xx HTTP responses (esp. 400/403/429) MEAN the server is alive — it just blocked our UA.
- DNS resolution is the true 'does this domain exist?' check.
- Timeouts/ConnectTimeouts: do DNS check to disambiguate (network blocked vs. domain dead).
"""
import json
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = Path(r"c:/Users/HP/OneDrive/Desktop/course_data/Logo_url_extract")
INPUT = BASE / "high_conf_verification.json"
OUTPUT = BASE / "high_conf_verification_v2.json"
OUTPUT_TXT = BASE / "high_conf_verification_v2_summary.txt"


def dns_resolves(domain: str) -> bool:
    try:
        socket.gethostbyname(domain)
        return True
    except Exception:
        return False


def reclassify(rec: dict) -> dict:
    code = rec.get("http_code")
    status = rec.get("status")

    # Got an HTTP response of any kind -> server is alive
    if code is not None:
        if 200 <= code < 400:
            rec["verdict"] = "alive"  # confirmed working
        elif code in (400, 401, 403, 405, 429):
            rec["verdict"] = "alive_blocked"  # server responded, blocked our UA / rate-limit
        elif 400 <= code < 500:
            rec["verdict"] = "alive_4xx"  # got a response but the URL/path errored (still a real site)
        elif 500 <= code < 600:
            rec["verdict"] = "alive_5xx"  # server is up but having issues
        else:
            rec["verdict"] = "alive_other"
        rec["dns_ok"] = True
        return rec

    # No HTTP response — check DNS
    rec["dns_ok"] = dns_resolves(rec["domain"])
    if rec["dns_ok"]:
        # DNS works but HTTP failed -> probably geo/firewall/network, domain still exists
        rec["verdict"] = "dns_ok_no_http"
    else:
        rec["verdict"] = "dns_fail"
    return rec


def main() -> None:
    with INPUT.open(encoding="utf-8") as f:
        data = json.load(f)

    print(f"Re-classifying {len(data)} domains (parallel DNS lookups)...")
    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(reclassify, data))

    with OUTPUT.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    total = len(results)
    by_verdict = {}
    for r in results:
        by_verdict.setdefault(r["verdict"], []).append(r)

    lines = []
    lines.append(f"High-confidence fill verification (re-classified) — {total} domains")
    lines.append("=" * 70)
    lines.append("")
    lines.append("Verdict breakdown:")
    order = ["alive", "alive_blocked", "alive_4xx", "alive_5xx",
             "alive_other", "dns_ok_no_http", "dns_fail"]
    for v in order:
        items = by_verdict.get(v, [])
        pct = len(items) / total * 100
        lines.append(f"  {v:>18}: {len(items):>4}  ({pct:5.1f}%)")

    real_alive = sum(len(by_verdict.get(v, []))
                     for v in ["alive", "alive_blocked", "alive_4xx", "alive_5xx", "alive_other", "dns_ok_no_http"])
    real_dead = len(by_verdict.get("dns_fail", []))
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"REAL ALIVE (DNS resolves OR got any HTTP response): "
                 f"{real_alive}/{total}  ({real_alive/total*100:.1f}%)")
    lines.append(f"REAL DEAD  (DNS does not resolve at all):           "
                 f"{real_dead}/{total}  ({real_dead/total*100:.1f}%)")
    lines.append("=" * 70)
    lines.append("")

    if real_dead:
        lines.append(f"\nDomains that genuinely don't exist (need investigation):\n")
        lines.append("-" * 70)
        for r in by_verdict.get("dns_fail", []):
            lines.append(f"  {r['id']:>8}  {r['name'][:45]:<45}  {r['domain']}")
        lines.append("")

    if by_verdict.get("dns_ok_no_http"):
        lines.append(f"\nDomains where DNS resolves but HTTP timed out / refused "
                     f"(probably geo-blocked, firewalled, or this network can't reach):\n")
        lines.append("-" * 70)
        for r in by_verdict["dns_ok_no_http"]:
            err = r.get("error") or "?"
            lines.append(f"  {r['id']:>8}  {r['name'][:45]:<45}  "
                         f"{r['domain']:<35}  ({err})")

    summary = "\n".join(lines)
    OUTPUT_TXT.write_text(summary, encoding="utf-8")
    print()
    print(summary)


if __name__ == "__main__":
    main()
