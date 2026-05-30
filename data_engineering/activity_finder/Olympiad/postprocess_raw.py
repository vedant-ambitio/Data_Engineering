#!/usr/bin/env python3
"""
postprocess_raw.py — Reorder fields for UI-ready olympiad JSONs
================================================================

Reads from olympiad_data/raw/, writes to olympiad_data/processed_raw/

Only one transformation: REORDER fields
  Card view fields first → Detail view fields → Backend fields
  All field data stays exactly as-is. No text formatting or modification.

Usage:
  python postprocess_raw.py
"""

import json
import os
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "olympiad_data", "raw")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "olympiad_data", "processed_raw")

# Field order: Card → Detail → Backend
CARD_FIELDS = [
    "activity_name",
    "organizer",
    "level",
    "subject",
    "modality",
    "cost_chip",
    "entry_route",
    "deadline_status",
    "registration_close_date",
]

DETAIL_FIELDS = [
    "about_description",
    "eligibility_text",
    "structure_format",
    "how_to_apply",
    "rewards_outcomes",
    "official_website",
]

ORDERED_FIELDS = CARD_FIELDS + DETAIL_FIELDS


def process_one(data):
    """Reorder fields — Card → Detail → Backend. No data modification."""
    ordered = {}

    # Card fields first
    for field in CARD_FIELDS:
        if field in data:
            ordered[field] = data[field]

    # Detail fields next
    for field in DETAIL_FIELDS:
        if field in data:
            ordered[field] = data[field]

    # All remaining backend fields (preserve everything)
    for field in data:
        if field not in ordered:
            ordered[field] = data[field]

    return ordered


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.json")))
    print(f"Processing {len(files)} olympiad files...")
    print(f"  Input:  {RAW_DIR}")
    print(f"  Output: {OUTPUT_DIR}")

    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        processed = process_one(data)

        out_file = os.path.join(OUTPUT_DIR, os.path.basename(f))
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(processed, fh, indent=2, ensure_ascii=False)

    print(f"Done: {len(files)} files written to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
