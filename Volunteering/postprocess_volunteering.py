#!/usr/bin/env python3
"""
postprocess_volunteering.py — Pre-UI processing for volunteering (avg_patched tier)
====================================================================================

Input:  volunteering_data/extracted/avg_patched/ (30 files, post-Gemini-merge)
Output: volunteering_data/processed_volunteering_2/*.json (fresh folder)

Transformations:
1. Mojibake sweep — replace common UTF-8-as-cp1252 sequences if any leaked
2. Reorder fields: Card -> Detail -> Backend (UI-ready)
3. All other original fields preserved (no data loss)

NOTE: No team_size parser (volunteering has no team_size field — it's individual).
NOTE: No deadline filter (volunteering has no deadline field — most are rolling).
NOTE: No prize_amount handling (volunteering has no prize concept).

Usage:
  python postprocess_volunteering.py
"""

import json
import os
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACTED_DIR = os.path.join(SCRIPT_DIR, "volunteering_data", "extracted")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "volunteering_data", "processed_volunteering_2")

INPUT_FOLDERS = ["avg_patched"]

# Field order: Card → Detail → Backend (matches existing processed_volunteering/ schema)
CARD_FIELDS = [
    "program_name",
    "organization_name",
    "organization_logo",
    "mode",
    "cause_area",
    "is_verified",
]

DETAIL_FIELDS = [
    "about_description",
    "eligibility_text",
    "minimum_hours",
    "commitment_type",
    "certificate_provided",
    "responsibilities",
    "how_to_apply",
    "application_url",
]

BACKEND_FIELDS = [
    "opportunity_id",
]


# ══════════════════════════════════════════════════════════════════════════════
#  MOJIBAKE CLEANUP (safety net — patches were already cleaned, but defensive)
# ══════════════════════════════════════════════════════════════════════════════
_MOJIBAKE_PAIRS = [
    ("â‚¹", "₹"),
    ("â\x82¬", "€"),
    ("Â£", "£"),
    ("Â¥", "¥"),
    ("Â°", "°"),
    ("Ã©", "é"),
    ("Ã¨", "è"),
    ("Ã¢", "â"),
    ("Ã§", "ç"),
    ("Ã¶", "ö"),
    ("Ã¼", "ü"),
    ("Ã¤", "ä"),
    ("Ã±", "ñ"),
    ("â€™", "'"),
    ("â€œ", '"'),
    ("â€\x9d", '"'),
    ("â€”", "—"),
    ("â€“", "–"),
    ("â€¦", "…"),
]


def clean_mojibake(s):
    if not isinstance(s, str):
        return s
    for bad, good in _MOJIBAKE_PAIRS:
        if bad in s:
            s = s.replace(bad, good)
    return s


def clean_mojibake_dict(d):
    if isinstance(d, dict):
        return {k: clean_mojibake_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [clean_mojibake_dict(v) for v in d]
    if isinstance(d, str):
        return clean_mojibake(d)
    return d


# ══════════════════════════════════════════════════════════════════════════════
#  PROCESS ONE RECORD
# ══════════════════════════════════════════════════════════════════════════════

def process_one(data):
    """Apply mojibake sweep and reorder fields (Card -> Detail -> Backend)."""
    # Step 1: Mojibake sweep over all string values (defensive)
    data = clean_mojibake_dict(data)

    # Step 2: Reorder fields — Card → Detail → Backend → anything else
    ordered = {}
    for field in CARD_FIELDS:
        if field in data:
            ordered[field] = data[field]
    for field in DETAIL_FIELDS:
        if field in data:
            ordered[field] = data[field]
    for field in BACKEND_FIELDS:
        if field in data:
            ordered[field] = data[field]
    # Preserve anything else (unknown / future fields)
    for field in data:
        if field not in ordered:
            ordered[field] = data[field]
    return ordered


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_files = []
    for folder in INPUT_FOLDERS:
        folder_path = os.path.join(EXTRACTED_DIR, folder)
        files = sorted(glob.glob(os.path.join(folder_path, "*.json")))
        all_files.extend(files)
        print(f"  Input {folder}/: {len(files)} files")

    print(f"\nTotal input: {len(all_files)} files")
    print(f"Output: {OUTPUT_DIR}/\n")

    for f in all_files:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        processed = process_one(data)
        out_file = os.path.join(OUTPUT_DIR, os.path.basename(f))
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(processed, fh, indent=2, ensure_ascii=False)
        print(f"[KEEP]  {os.path.basename(f)}")

    print(f"\n{'=' * 60}")
    print(f"  Done: {len(all_files)} files processed.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
