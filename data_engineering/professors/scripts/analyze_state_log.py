"""Analyze logs/state.jsonl (Tier A grounding log).
Show: how many records had grounding_chunk_count == 0, broken down by confidence.
This tells us why so many entries fall through the strict grounding criterion.
"""
import json
import os
from collections import Counter, defaultdict

LOG = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info\logs\state.jsonl"

records = []
with open(LOG, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except Exception:
                pass

print(f"Total records in state.jsonl: {len(records)}")
print()

# Distribution: chunk_count
chunk_counts = Counter()
for r in records:
    cc = r.get("grounding_chunk_count")
    chunk_counts[cc] += 1
print("grounding_chunk_count distribution:")
for cc, n in sorted(chunk_counts.items(), key=lambda x: (x[0] is None, x[0])):
    print(f"  {str(cc):>6}: {n}")
print()

# Confidence distribution overall
conf_dist = Counter(r.get("confidence") for r in records)
print("confidence distribution:")
for c, n in conf_dist.most_common():
    print(f"  {str(c):>15}: {n}")
print()

# Cross-tab: (chunk_count == 0) × confidence
print("Records with grounding_chunk_count == 0  by  confidence:")
zero_chunks = [r for r in records if r.get("grounding_chunk_count") == 0]
print(f"  total: {len(zero_chunks)}")
zc_conf = Counter(r.get("confidence") for r in zero_chunks)
for c, n in zc_conf.most_common():
    print(f"  {str(c):>15}: {n}")
print()

# Of zero-chunk records, how many had verification_status == ok?
zc_ok = [r for r in zero_chunks if r.get("verification_status") in ("ok", "ok_via_alternate")]
print(f"Of {len(zero_chunks)} zero-chunk records, {len(zc_ok)} had verification_status=ok")
print(f"  → these have a working URL but no grounding evidence (false negatives in strict v3)")
print()

# Cross-tab: chunk count > 0 vs confidence + verification
nonzero = [r for r in records if r.get("grounding_chunk_count", 0) > 0]
nz_ok = sum(1 for r in nonzero if r.get("verification_status") in ("ok", "ok_via_alternate"))
print(f"Records with chunks > 0:  {len(nonzero)}  (verified OK: {nz_ok})")
print()

# Final summary buckets
print("=" * 70)
print("Summary buckets (Tier A, 8278 records)")
print("=" * 70)

high_zero_ok = sum(1 for r in records
                   if r.get("grounding_chunk_count") == 0
                   and r.get("confidence") == "high"
                   and r.get("verification_status") in ("ok", "ok_via_alternate"))
high_with_chunks_ok = sum(1 for r in records
                          if r.get("grounding_chunk_count", 0) > 0
                          and r.get("confidence") == "high"
                          and r.get("verification_status") in ("ok", "ok_via_alternate"))
not_found_records = sum(1 for r in records if r.get("confidence") == "not_found")
low_records = sum(1 for r in records if r.get("confidence") == "low")
medium_records = sum(1 for r in records if r.get("confidence") == "medium")
n_a_records = sum(1 for r in records if r.get("confidence") == "not_applicable")

print(f"  high + chunks > 0 + verified OK    {high_with_chunks_ok}  (rock solid)")
print(f"  high + 0 chunks  + verified OK     {high_zero_ok}  (model from memory; strict v3 penalizes)")
print(f"  not_found                          {not_found_records}  (dept doesn't exist)")
print(f"  not_applicable                     {n_a_records}  (pre-filtered)")
print(f"  low                                {low_records}  (real coverage gaps)")
print(f"  medium                             {medium_records}")
