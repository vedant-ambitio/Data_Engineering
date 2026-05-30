"""Analyze Tier B grounding log: confidence distribution and 'no_match' patterns."""
import json
import os
from collections import Counter

LOG = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info\logs\state_tier_b.jsonl"

records = []
with open(LOG, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except Exception:
                pass

print(f"Total records in state_tier_b.jsonl: {len(records)}")

# Confidence distribution
print("\nConfidence distribution:")
conf = Counter(r.get("confidence") for r in records)
for c, n in conf.most_common():
    print(f"  {str(c):>15}: {n}")

# Status distribution
print("\nStatus distribution:")
sts = Counter(r.get("status") for r in records)
for c, n in sts.most_common():
    print(f"  {str(c):>15}: {n}")

# Verification distribution
print("\nVerification status distribution:")
vs = Counter(r.get("verification_status") for r in records)
for c, n in vs.most_common():
    print(f"  {str(c):>20}: {n}")

# Combined: confidence × status
print("\nConfidence × Verification status:")
combo = Counter((r.get("confidence"), r.get("verification_status")) for r in records)
for (c, v), n in combo.most_common(15):
    print(f"  conf={str(c):>14} verif={str(v):>15}  {n}")

# Show 5 records with confidence=not_found
print("\n5 records with confidence == 'not_found':")
nf = [r for r in records if r.get("confidence") == "not_found"]
print(f"  total: {len(nf)}")
for r in nf[:5]:
    print(f"  {r.get('university')} / {r.get('department')} / {r.get('verification_status')}")
