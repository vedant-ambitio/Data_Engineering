"""Re-emit stats from test_500_domains_full.json without re-hitting APIs."""

import json
from collections import Counter
from pathlib import Path
from difflib import SequenceMatcher

JSON_PATH = Path(__file__).parent / "test_500_domains_full.json"
results = json.loads(JSON_PATH.read_text(encoding="utf-8"))
n = len(results)

ror_hits = sum(1 for r in results if r["ror_domain"])
wd_hits = sum(1 for r in results if r["wikidata_domain"])
lo_hits = sum(1 for r in results if r["logodev_domain"])
any_hit = sum(1 for r in results if r["consensus_domain"])
two_plus = sum(1 for r in results if r["agreement_count"] >= 2)
triple = sum(1 for r in results if r["agreement_count"] == 3)

print(f"Overall hit rate ({n} records):")
print(f"  ROR:                  {ror_hits:4d}  ({ror_hits/n*100:5.1f}%)")
print(f"  Wikidata:             {wd_hits:4d}  ({wd_hits/n*100:5.1f}%)")
print(f"  Logo.dev:             {lo_hits:4d}  ({lo_hits/n*100:5.1f}%)")
print(f"  Any source:           {any_hit:4d}  ({any_hit/n*100:5.1f}%)")
print(f"  >=2 sources agree:    {two_plus:4d}  ({two_plus/n*100:5.1f}%)")
print(f"  All 3 agree:          {triple:4d}  ({triple/n*100:5.1f}%)")

# ── Quality check: ROR name similarity vs original ──
def sim(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

ror_high = ror_med = ror_low = 0
for r in results:
    if not r["ror_domain"]:
        continue
    s = sim(r["name"], r.get("ror_name") or "")
    if s >= 0.8:
        ror_high += 1
    elif s >= 0.5:
        ror_med += 1
    else:
        ror_low += 1

print(f"\nROR match quality (name similarity):")
print(f"  High (>=0.80, almost certain match):  {ror_high:4d}  ({ror_high/n*100:5.1f}%)")
print(f"  Med  (0.50-0.79, probably right):     {ror_med:4d}  ({ror_med/n*100:5.1f}%)")
print(f"  Low  (<0.50, suspect false positive): {ror_low:4d}  ({ror_low/n*100:5.1f}%)")

# ── Per-bucket breakdown ──
by_bucket = {}
for r in results:
    by_bucket.setdefault(r["bucket"], []).append(r)

print(f"\nPer-bucket coverage:")
print(f"  {'bucket':<22s} {'n':>4s}  {'ROR':>7s} {'WD':>7s} {'Logo':>7s} {'any':>7s} {'>=2':>7s}")
for bucket, rs in sorted(by_bucket.items(), key=lambda kv: -len(kv[1])):
    bn = len(rs)
    b_ror = sum(1 for r in rs if r["ror_domain"])
    b_wd = sum(1 for r in rs if r["wikidata_domain"])
    b_lo = sum(1 for r in rs if r["logodev_domain"])
    b_any = sum(1 for r in rs if r["consensus_domain"])
    b_two = sum(1 for r in rs if r["agreement_count"] >= 2)
    print(f"  {bucket:<22s} {bn:>4d}  "
          f"{b_ror:>3d}({b_ror/bn*100:>3.0f}%) "
          f"{b_wd:>3d}({b_wd/bn*100:>3.0f}%) "
          f"{b_lo:>3d}({b_lo/bn*100:>3.0f}%) "
          f"{b_any:>3d}({b_any/bn*100:>3.0f}%) "
          f"{b_two:>3d}({b_two/bn*100:>3.0f}%)")

# ── ROR quality per bucket ──
print(f"\nROR match quality per bucket:")
print(f"  {'bucket':<22s} {'n':>4s}  {'high':>6s} {'med':>6s} {'low':>6s} {'null':>6s}")
for bucket, rs in sorted(by_bucket.items(), key=lambda kv: -len(kv[1])):
    bn = len(rs)
    high = med = low = nul = 0
    for r in rs:
        if not r["ror_domain"]:
            nul += 1
            continue
        s = sim(r["name"], r.get("ror_name") or "")
        if s >= 0.8:
            high += 1
        elif s >= 0.5:
            med += 1
        else:
            low += 1
    print(f"  {bucket:<22s} {bn:>4d}  "
          f"{high:>3d}({high/bn*100:>3.0f}%) "
          f"{med:>3d}({med/bn*100:>3.0f}%) "
          f"{low:>3d}({low/bn*100:>3.0f}%) "
          f"{nul:>3d}({nul/bn*100:>3.0f}%)")

# ── Sample of low-similarity ROR matches (suspect false positives) ──
print(f"\nSample of suspect ROR matches (similarity < 0.5):")
shown = 0
for r in results:
    if not r["ror_domain"]:
        continue
    s = sim(r["name"], r.get("ror_name") or "")
    if s < 0.5:
        print(f"  [{r['bucket']:<18s}] {r['name']!r}")
        print(f"    ROR matched: {r['ror_name']!r} -> {r['ror_domain']}  (sim={s:.2f})")
        shown += 1
        if shown >= 8:
            break

# ── Sample of confident multi-source matches ──
print(f"\nSample of high-confidence matches (>=2 sources agree):")
shown = 0
for r in results:
    if r["agreement_count"] >= 2:
        print(f"  [{r['bucket']:<18s}] {r['name']!r} -> {r['consensus_domain']}  ({r['agreement_count']}/3 agree)")
        shown += 1
        if shown >= 8:
            break

# ── Records where all 3 sources fail ──
zero_hit = [r for r in results if r["agreement_count"] == 0]
print(f"\nAll-source-fail records: {len(zero_hit)}")
for r in zero_hit[:10]:
    print(f"  [{r['bucket']:<18s}] {r['name']!r}")
