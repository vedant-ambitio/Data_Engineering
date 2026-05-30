#!/usr/bin/env python3
"""
postprocess_competition.py — Pre-UI processing for competitions
================================================================

Input:  extracted/devpost/ (25) + extracted/good/ (43) = 68 files
Output: processed_competition/*.json

Transformations:
1. Parse team_size string → add min_team_size + max_team_size fields
2. Reorder fields: Card → Detail → Backend (UI-ready order)
3. All original fields preserved (no data loss)

Usage:
  python postprocess_competition.py
"""

import json
import os
import re
import glob
import shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACTED_DIR = os.path.join(SCRIPT_DIR, "competition_data", "extracted")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "competition_data", "processed_competition")

INPUT_FOLDERS = ["devpost", "good"]

# Field order: Card → Detail → Backend
CARD_FIELDS = [
    "activity_name",
    "organizer",
    "organizer_logo",
    "mode",
    "deadline",
    "cost_chip",
    "cost",
    "domain",
    "team_size",
    "min_team_size",
    "max_team_size",
    "is_verified",
]

DETAIL_FIELDS = [
    "about_description",
    "eligibility_text",
    "how_to_apply",
    "prizes_detail",
    "prize_amount",
    "structure_format",
    "judging_criteria",
    "submission_format",
    "official_website",
    "registration_url",
]

BACKEND_FIELDS = [
    "competition_id",
    "source_url",
    "registration_open_date",
    "country",
    "grade_levels",
    "age_limit",
]


# ══════════════════════════════════════════════════════════════════════════════
#  TEAM SIZE PARSING
# ══════════════════════════════════════════════════════════════════════════════

def parse_team_size(team_size):
    """
    Parse team_size string into (min, max) integers.

    Returns (min_team_size, max_team_size) — either can be None if unparseable.
    """
    if team_size is None or team_size == "" or str(team_size).lower() == "null":
        return None, None

    ts = str(team_size).strip()
    ts_lower = ts.lower()

    # Special case: Individual
    if ts_lower in ("individual", "individual participation", "solo", "individually"):
        return 1, 1

    # Pattern: "Individual or 2-X members" — solo allowed, max is higher number
    m = re.search(r'individual\s+or\s+(\d+)\s*[-–]\s*(\d+)', ts_lower)
    if m:
        return 1, int(m.group(2))

    # Pattern: "1-4 members", "1 - 4 Members", "2 - 4 Members"
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)', ts)
    if m:
        return int(m.group(1)), int(m.group(2))

    # Pattern: "Individual or Team of up to X" / "Individual or Team of upto X"
    m = re.search(r'(?:individual\s+or\s+)?team\s+of\s+up\s*to\s+(\d+)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # Pattern: "Individual or Team (up to X members)"
    m = re.search(r'up\s+to\s+(\d+)\s+members?', ts_lower)
    if m:
        return 1, int(m.group(1))

    # Pattern: "Team of maximum X students"
    m = re.search(r'team\s+of\s+(?:maximum\s+)?(\d+)\s+(?:students?|members?)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # Pattern: "Individual or groups of two"
    if "groups of two" in ts_lower or "group of two" in ts_lower:
        return 1, 2

    # Pattern: "Individual or 2-X members"
    m = re.search(r'individual\s+or\s+(\d+)\s*[-–]\s*(\d+)\s+members?', ts_lower)
    if m:
        return 1, int(m.group(2))

    # Pattern: "2 participants", "3 students"
    m = re.search(r'^(\d+)\s+(?:participants?|students?|members?|people)$', ts_lower)
    if m:
        n = int(m.group(1))
        return n, n

    # Pattern: "Individual for Round 1; 1-3 members for subsequent rounds"
    if "individual" in ts_lower and "-" in ts:
        m = re.search(r'(\d+)\s*[-–]\s*(\d+)', ts)
        if m:
            return 1, int(m.group(2))

    # Pattern: "No maximum or minimum, but 1-4 members recommended"
    if "recommended" in ts_lower:
        m = re.search(r'(\d+)\s*[-–]\s*(\d+)', ts)
        if m:
            return int(m.group(1)), int(m.group(2))

    # Fallbacks: ambiguous "team" references → default 1-4
    ambiguous_team_patterns = [
        "individual or team", "team required", "student teams",
    ]
    if any(p in ts_lower for p in ambiguous_team_patterns) or ts_lower == "team":
        return 1, 4

    # Couldn't parse → null
    return None, None


# ══════════════════════════════════════════════════════════════════════════════
#  PROCESSING
# ══════════════════════════════════════════════════════════════════════════════

def process_one(data):
    """Parse team_size + reorder fields."""

    # Step 1: Parse team_size
    ts = data.get("team_size")
    min_ts, max_ts = parse_team_size(ts)
    data["min_team_size"] = min_ts
    data["max_team_size"] = max_ts

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
    # Preserve anything else (e.g. unknown fields)
    for field in data:
        if field not in ordered:
            ordered[field] = data[field]

    return ordered


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

    # Stats
    stats = {
        "parsed_both": 0,
        "parsed_partial": 0,
        "parsed_null": 0,
        "team_size_values": {},
    }

    for f in all_files:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        original_ts = data.get("team_size")
        processed = process_one(data)
        min_ts = processed.get("min_team_size")
        max_ts = processed.get("max_team_size")

        # Track stats
        if min_ts is not None and max_ts is not None:
            stats["parsed_both"] += 1
        elif min_ts is not None or max_ts is not None:
            stats["parsed_partial"] += 1
        else:
            stats["parsed_null"] += 1
        key = f"{original_ts} -> min={min_ts}, max={max_ts}"
        stats["team_size_values"][key] = stats["team_size_values"].get(key, 0) + 1

        # Save
        out_file = os.path.join(OUTPUT_DIR, os.path.basename(f))
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(processed, fh, indent=2, ensure_ascii=False)

    print(f"Processed: {len(all_files)} files")
    print(f"  Parsed both min+max:   {stats['parsed_both']}")
    print(f"  Parsed partial:        {stats['parsed_partial']}")
    print(f"  Null min+max:          {stats['parsed_null']}")
    print()
    print("Team size parse summary (distinct):")
    for key, count in sorted(stats["team_size_values"].items(), key=lambda x: -x[1]):
        print(f"  {count:3}x  {key}")


if __name__ == "__main__":
    main()
