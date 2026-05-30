#!/usr/bin/env python3
"""
merge_avg_patch.py — Merge Gemini patch values into the original AVG summer school files.

For each of the 20 AVG-tier summer school files, replaces 9 fields with values
from the corresponding Gemini patch in patch_avg_weak_gemini3/. All other
fields are preserved exactly as in the original.

Replaced fields:
  - funding_status
  - duration
  - how_to_apply
  - curriculum
  - college_credit
  - deadline
  - program_dates
  - subjects_offered
  - residential_or_online

Additionally sets certificate_provided = "Yes" for all merged records (almost
all summer programs issue a certificate of completion).

The output folder mirrors the original AVG schema EXACTLY — no new fields
are added (no `sources`, no metadata). Files in the output folder will be
drop-in replacements for the originals.

Input:
  - originals: summer_school_data/extracted/avg/*.json (20 files)
  - patches:   summer_school_data/patch_avg_weak_gemini3/*.json (20 files)

Output:
  - merged: summer_school_data/extracted/avg_patched/*.json (20 files)

Usage:
  python merge_avg_patch.py
  python merge_avg_patch.py --dry-run         # show changes, no writes
  python merge_avg_patch.py --max 3           # only process first 3
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ORIG_DIR = os.path.join(SCRIPT_DIR, "summer_school_data", "extracted", "avg")
PATCH_DIR = os.path.join(SCRIPT_DIR, "summer_school_data", "patch_avg_weak_gemini3")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "summer_school_data", "extracted", "avg_patched")

# The 9 fields we replace from the patch
REPLACE_FIELDS = [
    "funding_status",
    "duration",
    "how_to_apply",
    "curriculum",
    "college_credit",
    "deadline",
    "program_dates",
    "subjects_offered",
    "residential_or_online",
]


def main():
    parser = argparse.ArgumentParser(description="Merge Gemini patch into AVG summer school originals")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change, no files written")
    parser.add_argument("--max", type=int, help="Process at most N files")
    args = parser.parse_args()

    if not os.path.isdir(ORIG_DIR):
        print(f"[ERROR] Original AVG folder not found: {ORIG_DIR}")
        sys.exit(1)
    if not os.path.isdir(PATCH_DIR):
        print(f"[ERROR] Patch folder not found: {PATCH_DIR}")
        sys.exit(1)

    if not args.dry_run:
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    orig_files = sorted(f for f in os.listdir(ORIG_DIR) if f.endswith(".json"))
    if args.max:
        orig_files = orig_files[:args.max]

    print(f"Originals: {ORIG_DIR}")
    print(f"Patches:   {PATCH_DIR}")
    print(f"Output:    {OUTPUT_DIR}")
    print(f"Files:     {len(orig_files)}")
    print(f"Mode:      {'DRY RUN' if args.dry_run else 'WRITE'}\n")

    written = 0
    missing_patch = 0
    field_change_count = {f: 0 for f in REPLACE_FIELDS}
    cert_set = 0

    for fname in orig_files:
        orig_path = os.path.join(ORIG_DIR, fname)
        patch_path = os.path.join(PATCH_DIR, fname)

        with open(orig_path, "r", encoding="utf-8") as f:
            orig = json.load(f)

        if not os.path.exists(patch_path):
            print(f"[SKIP] {fname}: no matching patch file")
            missing_patch += 1
            continue

        with open(patch_path, "r", encoding="utf-8") as f:
            patch = json.load(f)

        merged = dict(orig)  # shallow copy preserves all original fields
        changes = []

        for fld in REPLACE_FIELDS:
            new_val = patch.get(fld)
            old_val = orig.get(fld)
            if new_val != old_val:
                changes.append(fld)
                field_change_count[fld] += 1
            merged[fld] = new_val

        # Set certificate_provided = "Yes" (most summer programs issue certificates)
        if merged.get("certificate_provided") != "Yes":
            cert_set += 1
        merged["certificate_provided"] = "Yes"

        if changes:
            print(f"[{fname}] replaced fields: {', '.join(changes)}")
        else:
            print(f"[{fname}] no field changes")

        if not args.dry_run:
            out_path = os.path.join(OUTPUT_DIR, fname)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
            written += 1

    print(f"\n{'=' * 60}")
    print(f"  {'WOULD WRITE' if args.dry_run else 'WROTE'}: {written if not args.dry_run else len(orig_files) - missing_patch} files")
    print(f"  Missing patches: {missing_patch}")
    print(f"  certificate_provided set to 'Yes' in: {cert_set} files (where it was previously different)")
    print(f"  Per-field replacement counts:")
    for fld, n in field_change_count.items():
        print(f"    {fld:>22}: {n} files changed")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
