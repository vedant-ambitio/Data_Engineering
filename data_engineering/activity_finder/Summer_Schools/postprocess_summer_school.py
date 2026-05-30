#!/usr/bin/env python3
"""
postprocess_summer_school.py — Pre-UI processing for summer schools (avg_patched tier)
=======================================================================================

Input:  summer_school_data/extracted/avg_patched/ (20 files, post-Gemini-merge)
Output: summer_school_data/processed_summer_school_2/*.json (fresh folder)

Transformations:
1. Mojibake sweep — replace common UTF-8-as-cp1252 sequences if any leaked
2. Filter out programs whose application deadline is in the past (TODAY = 2026-05-05)
3. Reorder fields: Card -> Detail -> Backend (UI-ready, matches existing
   processed_summer_school/ schema)
4. All other original fields preserved (no data loss)

NOTE: No team_size parser (summer schools are individual programs — no team_size field).
NOTE: Past-deadline filter applies to the application deadline only. Some excluded
      records may still have upcoming program_dates, but applications are closed.

Usage:
  python postprocess_summer_school.py
"""

import json
import os
import re
import glob
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACTED_DIR = os.path.join(SCRIPT_DIR, "summer_school_data", "extracted")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "summer_school_data", "processed_summer_school_2")

INPUT_FOLDERS = ["avg_patched"]

# Hardcoded "today" date for deadline filtering.
# Anything strictly before this is considered past and excluded.
TODAY = date(2026, 5, 5)

# Field order: Card → Detail → Backend (matches existing processed_summer_school/ schema)
CARD_FIELDS = [
    "program_name",
    "institution_name",
    "institution_logo",
    "funding_status",
    "mode",
    "location_tag",
    "duration",
    "is_verified",
]

DETAIL_FIELDS = [
    "about_description",
    "eligibility_text",
    "curriculum",
    "how_to_apply",
    "application_url",
    "cost_range",
    "college_credit",
    "residential_or_online",
    "certificate_provided",
    "deadline",
    "program_dates",
    "subjects_offered",
]

BACKEND_FIELDS = [
    "program_id",
]


# ══════════════════════════════════════════════════════════════════════════════
#  MOJIBAKE CLEANUP (1 avg_patched file actually has mojibake — masters-union-goa)
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
#  DEADLINE FILTER
# ══════════════════════════════════════════════════════════════════════════════

def is_deadline_past(deadline_str):
    """Return True if deadline is a valid YYYY-MM-DD date strictly before TODAY.
    Returns False for null, 'Rolling', or future / today / unparseable dates.
    """
    if deadline_str is None or deadline_str == "":
        return False
    s = str(deadline_str).strip()
    if s.lower() in ("rolling", "null", "tbd", "tba", "ongoing"):
        return False
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', s)
    if not m:
        return False  # Unparseable date — keep the record (don't filter)
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    return d < TODAY


# ══════════════════════════════════════════════════════════════════════════════
#  PROCESS ONE RECORD
# ══════════════════════════════════════════════════════════════════════════════

def process_one(data):
    """Apply mojibake sweep and reorder fields (Card -> Detail -> Backend)."""
    # Step 1: Mojibake sweep over all string values (defensive — fixes the
    # masters-union-goa.json mojibake observed in audit)
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
    print(f"Output:      {OUTPUT_DIR}/")
    print(f"Today:       {TODAY.isoformat()}  (records with application deadline strictly before today are excluded)\n")

    written = 0
    skipped_past = 0
    skipped_log = []

    for f in all_files:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        deadline = data.get("deadline")

        # Filter past-deadline records
        if is_deadline_past(deadline):
            skipped_past += 1
            skipped_log.append((os.path.basename(f), deadline))
            print(f"[SKIP-PAST]  {os.path.basename(f):<48}  deadline={deadline}")
            continue

        processed = process_one(data)
        out_file = os.path.join(OUTPUT_DIR, os.path.basename(f))
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(processed, fh, indent=2, ensure_ascii=False)
        written += 1
        print(f"[KEEP]       {os.path.basename(f):<48}  deadline={deadline}")

    print(f"\n{'=' * 70}")
    print(f"  Total input:                 {len(all_files)}")
    print(f"  Written to processed folder: {written}")
    print(f"  Skipped (past deadline):     {skipped_past}")
    print(f"{'=' * 70}\n")

    if skipped_log:
        print("Past-deadline records skipped:")
        for name, dl in skipped_log:
            print(f"  - {name}: {dl}")


if __name__ == "__main__":
    main()
