"""Show ROR coverage and quality at different similarity thresholds."""

import json
from pathlib import Path
from difflib import SequenceMatcher

results = json.loads((Path(__file__).parent / "test_500_domains_full.json").read_text(encoding="utf-8"))
n = len(results)


def sim(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# Compute similarity for each ROR hit
ror_hits = []
for r in results:
    if r["ror_domain"]:
        s = sim(r["name"], r.get("ror_name") or "")
        ror_hits.append({
            "name": r["name"],
            "bucket": r["bucket"],
            "ror_name": r.get("ror_name"),
            "domain": r["ror_domain"],
            "sim": s,
        })

print(f"Total ROR hits in test: {len(ror_hits)} / {n}\n")

print("Coverage at various similarity thresholds (from 500 test, projected to 27K):\n")
print(f"{'Threshold':<12s} {'Test kept':>10s}  {'Test %':>8s}  {'27K projection':>16s}")
print("-" * 56)

for thr in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
    kept = sum(1 for h in ror_hits if h["sim"] >= thr)
    pct = kept / n * 100
    proj_27k = int(pct / 100 * 27281)
    print(f"  >= {thr:.2f}     {kept:>4d}        {pct:>5.1f}%   ~{proj_27k:>6,}")


print("\n\nSample borderline matches around 0.6 threshold:")
print("(Records that PASS at 0.5 but FAIL at 0.7 - these are the 'maybe' cases)\n")

borderline = sorted(
    [h for h in ror_hits if 0.50 <= h["sim"] < 0.70],
    key=lambda h: h["sim"]
)

print("Lowest similarity (closer to 0.5):")
for h in borderline[:8]:
    print(f"  sim={h['sim']:.2f}  '{h['name']}' =>'{h['ror_name']}' ({h['domain']})")

print("\nMid (0.55-0.65, the 0.6 boundary):")
mid = [h for h in borderline if 0.55 <= h['sim'] < 0.65]
for h in mid[:8]:
    print(f"  sim={h['sim']:.2f}  '{h['name']}' =>'{h['ror_name']}' ({h['domain']})")

print("\nHighest similarity (closer to 0.7 - these almost certainly survive):")
for h in borderline[-8:]:
    print(f"  sim={h['sim']:.2f}  '{h['name']}' =>'{h['ror_name']}' ({h['domain']})")
