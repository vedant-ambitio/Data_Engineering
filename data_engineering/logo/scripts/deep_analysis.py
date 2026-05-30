"""Deep analysis: which single source is best for 27K production run?

Computes for each source:
- Raw hit rate
- Accuracy-adjusted hit rate (using string similarity vs source's matched name)
- Per-bucket performance
- Unique coverage (records only this source got)
- Agreement-validated correct rate (when 2+ sources agree, that's ground truth)
"""

import json
from collections import Counter, defaultdict
from pathlib import Path
from difflib import SequenceMatcher

JSON_PATH = Path(__file__).parent / "test_500_domains_full.json"
results = json.loads(JSON_PATH.read_text(encoding="utf-8"))
n = len(results)


def sim(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ─────────────────────────────────────────────────────────────────────────
# 1. Per-source raw vs accuracy-adjusted hit rate
# ─────────────────────────────────────────────────────────────────────────
print("=" * 78)
print("1. PER-SOURCE RAW vs ACCURACY-ADJUSTED HIT RATE")
print("=" * 78)
print()
print("Methodology: a hit is 'likely correct' if matched-entity name has")
print("string similarity >= 0.5 to the query name.")
print()
print(f"{'Source':<12s} {'Raw hits':>10s} {'High sim':>10s} {'Med sim':>10s} {'Low sim':>10s} {'Likely correct':>16s}")

for source in ["ror", "wikidata", "logodev"]:
    raw = high = med = low = 0
    for r in results:
        domain = r[f"{source}_domain"]
        if not domain:
            continue
        raw += 1
        # Pick the matched-name field for similarity
        if source == "ror":
            matched = r.get("ror_name") or ""
        elif source == "wikidata":
            matched = r.get("wikidata_label") or ""
        else:
            matched = r.get("logodev_matched_name") or ""
        s = sim(r["name"], matched)
        if s >= 0.8:
            high += 1
        elif s >= 0.5:
            med += 1
        else:
            low += 1
    likely_correct = high + med
    print(f"{source.upper():<12s} {raw:>4d} ({raw/n*100:>4.1f}%) "
          f"{high:>4d} ({high/n*100:>4.1f}%) "
          f"{med:>4d} ({med/n*100:>4.1f}%) "
          f"{low:>4d} ({low/n*100:>4.1f}%) "
          f"{likely_correct:>4d} ({likely_correct/n*100:>4.1f}%)")


# ─────────────────────────────────────────────────────────────────────────
# 2. Validated accuracy: when 2+ sources agree, that's ground truth
# ─────────────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print("2. AGREEMENT-VALIDATED ACCURACY")
print("=" * 78)
print()
print("Methodology: records where 2+ sources independently returned the SAME domain")
print("are treated as ground-truth correct. We then check which sources got those")
print("records right (or wrong) on their own.")
print()

# For each record where >=2 sources agree, that's our truth
gt_records = [r for r in results if r["agreement_count"] >= 2]
print(f"Ground-truth-able records (>=2 sources agree): {len(gt_records)}")
print()

# How often did each source get the GT-correct answer?
print(f"{'Source':<12s} {'GT records':>12s} {'Agreed':>10s} {'Disagreed':>10s} {'Missed':>10s}")
for source in ["ror", "wikidata", "logodev"]:
    agreed = disagreed = missed = 0
    for r in gt_records:
        consensus = r["consensus_domain"]
        d = r[f"{source}_domain"]
        if d == consensus:
            agreed += 1
        elif d is None:
            missed += 1
        else:
            disagreed += 1
    print(f"{source.upper():<12s} {len(gt_records):>12d} "
          f"{agreed:>4d} ({agreed/len(gt_records)*100:>4.1f}%) "
          f"{disagreed:>4d} ({disagreed/len(gt_records)*100:>4.1f}%) "
          f"{missed:>4d} ({missed/len(gt_records)*100:>4.1f}%)")


# ─────────────────────────────────────────────────────────────────────────
# 3. Per-bucket: which source wins per bucket?
# ─────────────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print("3. PER-BUCKET WINNER (likely-correct hits, sim >= 0.5)")
print("=" * 78)
print()

by_bucket = defaultdict(list)
for r in results:
    by_bucket[r["bucket"]].append(r)

print(f"{'bucket':<22s} {'n':>4s}  {'ROR good':>9s} {'WD good':>9s} {'Logo good':>9s}  WINNER")
for bucket, rs in sorted(by_bucket.items(), key=lambda kv: -len(kv[1])):
    bn = len(rs)
    counts = {}
    for source in ["ror", "wikidata", "logodev"]:
        good = 0
        for r in rs:
            d = r[f"{source}_domain"]
            if not d:
                continue
            if source == "ror":
                m = r.get("ror_name") or ""
            elif source == "wikidata":
                m = r.get("wikidata_label") or ""
            else:
                m = r.get("logodev_matched_name") or ""
            if sim(r["name"], m) >= 0.5:
                good += 1
        counts[source] = good
    winner = max(counts.items(), key=lambda kv: kv[1])
    winner_name = winner[0].upper() if winner[1] > 0 else "(none)"
    print(f"  {bucket:<20s} {bn:>4d}  "
          f"{counts['ror']:>3d} ({counts['ror']/bn*100:>3.0f}%)  "
          f"{counts['wikidata']:>3d} ({counts['wikidata']/bn*100:>3.0f}%)  "
          f"{counts['logodev']:>3d} ({counts['logodev']/bn*100:>3.0f}%)  "
          f" -> {winner_name}")


# ─────────────────────────────────────────────────────────────────────────
# 4. Unique coverage: records only this source got (likely-correct)
# ─────────────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print("4. UNIQUE COVERAGE (likely-correct only)")
print("=" * 78)
print()
print("Records where this source got a likely-correct answer AND others didn't.")
print()


def is_likely_correct(r, source):
    d = r[f"{source}_domain"]
    if not d:
        return False
    if source == "ror":
        m = r.get("ror_name") or ""
    elif source == "wikidata":
        m = r.get("wikidata_label") or ""
    else:
        m = r.get("logodev_matched_name") or ""
    return sim(r["name"], m) >= 0.5


for source in ["ror", "wikidata", "logodev"]:
    others = [s for s in ["ror", "wikidata", "logodev"] if s != source]
    unique = 0
    for r in results:
        if not is_likely_correct(r, source):
            continue
        if all(not is_likely_correct(r, o) for o in others):
            unique += 1
    print(f"  {source.upper():<10s} unique correct: {unique:>4d}  ({unique/n*100:>4.1f}%)")


# ─────────────────────────────────────────────────────────────────────────
# 5. Final scorecard
# ─────────────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print("5. FINAL SCORECARD — single-source production-run estimate")
print("=" * 78)
print()
print("If you run ONE source on all 27,281 records, expected outcomes:")
print()

for source in ["ror", "wikidata", "logodev"]:
    high = med = low = nul = 0
    for r in results:
        d = r[f"{source}_domain"]
        if not d:
            nul += 1
            continue
        if source == "ror":
            m = r.get("ror_name") or ""
        elif source == "wikidata":
            m = r.get("wikidata_label") or ""
        else:
            m = r.get("logodev_matched_name") or ""
        s = sim(r["name"], m)
        if s >= 0.8:
            high += 1
        elif s >= 0.5:
            med += 1
        else:
            low += 1
    likely_correct_pct = (high + med) / n
    expected_correct_27k = int(likely_correct_pct * 27281)
    expected_garbage_27k = int(low / n * 27281)
    expected_null_27k = int(nul / n * 27281)
    print(f"  {source.upper()}:")
    print(f"    Likely-correct hits at 27K: ~{expected_correct_27k:>6,}  ({likely_correct_pct*100:>4.1f}%)")
    print(f"    Likely-WRONG (false-positive) hits: ~{expected_garbage_27k:>6,}")
    print(f"    Null (no match): ~{expected_null_27k:>6,}")
    print()
