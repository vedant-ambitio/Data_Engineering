"""
split_registry.py

Filters the gaps_registry.json down to only courses NOT yet processed
(no evidence file exists). Writes the remaining-only registry to a new
file that can be handed off to a colleague to run on their machine.

The output file contains ONLY the remaining course entries — no info
about which courses have already been completed.

Usage:
    python masters_v2/scripts/split_registry.py
"""

import json
import os
from pathlib import Path

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
REGISTRY_IN = BASE / "masters_v2" / "gaps" / "gaps_registry.json"
EVIDENCE_DIR = BASE / "masters_v2" / "evidence"
REGISTRY_OUT = BASE / "masters_v2" / "gaps" / "gaps_registry_remaining.json"


def main():
    print(f"Reading registry: {REGISTRY_IN}")
    with open(REGISTRY_IN, encoding="utf-8") as f:
        registry = json.load(f)
    print(f"  total courses: {len(registry)}")

    # Build set of "done" course keys from existing evidence files.
    # Evidence path is masters_v2/evidence/<college>/<file_stem>.evidence.json
    print(f"\nScanning evidence dir: {EVIDENCE_DIR}")
    done = set()
    for d, _, fs in os.walk(EVIDENCE_DIR):
        college = os.path.basename(d)
        for f in fs:
            if f.endswith(".evidence.json"):
                stem = f.replace(".evidence.json", "")
                done.add(f"{college}__{stem}.md")
    print(f"  evidence files found: {len(done)}")

    # Filter registry — keep only courses NOT in done set
    remaining = []
    skipped = 0
    for c in registry:
        key = f"{c['college']}__{c['file'].replace('.md', '')}.md"
        if key in done:
            skipped += 1
        else:
            remaining.append(c)

    print(f"\nSplit:")
    print(f"  done (skipped from output): {skipped}")
    print(f"  remaining (written to output): {len(remaining)}")
    print(f"  sanity check: {skipped + len(remaining)} == {len(registry)} "
          f"({'OK' if skipped + len(remaining) == len(registry) else 'MISMATCH'})")

    REGISTRY_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_OUT, "w", encoding="utf-8") as f:
        json.dump(remaining, f, ensure_ascii=False, indent=2)

    size_mb = os.path.getsize(REGISTRY_OUT) / (1024 * 1024)
    print(f"\nWrote: {REGISTRY_OUT}")
    print(f"  size: {size_mb:.1f} MB")
    print(f"  contains: {len(remaining)} courses (only remaining; no done info leaked)")


if __name__ == "__main__":
    main()
