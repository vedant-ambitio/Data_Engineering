"""Categorize the 4,483 skipped JSONs in output_professors_13k_final by reason.

Reads skip_reason from each skipped JSON and buckets it into 4 categories:
  1. no grounding chunks provided (pre-runtime skip)
  2. dept doesn't exist (model judgment)
  3. all chunks 404 / blocked
  4. homepage navigation found nothing
  5. other
"""
import json, os, glob
from collections import Counter

base = "c:/Users/HP/OneDrive/Desktop/course_data/Professors_info/output_professors_13k_final"

cat_counts = Counter()
all_reasons = Counter()
samples = {}  # category -> first 3 example reasons

def categorize(reason: str) -> str:
    if not reason:
        return "other (no reason)"
    r = reason.lower()
    if "no grounding chunks" in r:
        return "no grounding chunks (pre-runtime)"
    if "404" in r or "unusable" in r or "blocked" in r or "cloudflare" in r or "451" in r:
        return "chunks 404 / blocked / unreachable"
    if "homepage navigation" in r or "within 2 hops" in r or "no faculty list" in r:
        return "homepage nav found nothing"
    if "does not have" in r or "no such" in r or "doesn't exist" in r or "not exist" in r or "no standalone" in r:
        return "dept doesn't exist (model judgment)"
    if "third-party" in r or "non-official" in r:
        return "all chunks point to non-official sites"
    return "other"

skipped_files = []
for fp in glob.glob(os.path.join(base, "universities", "**", "*.json"), recursive=True):
    try:
        with open(fp, "r", encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        continue
    if j.get("status") != "skipped":
        continue
    reason = j.get("skip_reason") or ""
    cat = categorize(reason)
    cat_counts[cat] += 1
    all_reasons[reason[:100]] += 1
    if cat not in samples:
        samples[cat] = []
    if len(samples[cat]) < 3:
        samples[cat].append(reason[:200])

total = sum(cat_counts.values())
print(f"=== TOTAL SKIPPED JSONS: {total} ===")
print()
print(f"{'Category':>45}  {'Count':>6}  {'%':>6}")
print("-" * 65)
for cat, n in cat_counts.most_common():
    pct = n / total * 100 if total else 0
    print(f"{cat[:45]:>45}  {n:>6}  {pct:>5.1f}%")

print()
print("=== SAMPLE REASONS PER CATEGORY ===")
for cat, samps in samples.items():
    print(f"\n[{cat}]")
    for s in samps:
        print(f"  - {s}")
