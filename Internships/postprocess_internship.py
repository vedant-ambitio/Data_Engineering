#!/usr/bin/env python3
"""
postprocess_internship.py — Pre-UI processing for internships (avg_patched tier)
================================================================================

Input:  internship_data/extracted/avg_patched/ (17 files, post-Gemini-merge)
Output: internship_data/processed_internship_2/*.json (fresh folder)

Transformations:
1. Mojibake sweep — replace common UTF-8-as-cp1252 sequences if any leaked
2. Parse team_size string -> add min_team_size + max_team_size fields
3. Reorder fields: Card -> Detail -> Backend (UI-ready, matches existing
   processed_internship/ schema with min/max team_size added to card view)
4. All other original fields preserved (no data loss)

NOTE: No deadline filter applied. Internships do not have a "deadline" field
(they have "posted_date" which is when the listing was posted, not closing).
Most internships are rolling admissions — all 17 records are kept.

Usage:
  python postprocess_internship.py
"""

import json
import os
import re
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACTED_DIR = os.path.join(SCRIPT_DIR, "internship_data", "extracted")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "internship_data", "processed_internship_2")

INPUT_FOLDERS = ["avg_patched"]

# ── Field order: Card → Detail → Backend (matches existing processed_internship/ schema) ──
CARD_FIELDS = [
    "internship_title",
    "company_name",
    "company_logo",
    "mode",
    "stipend",
    "posted_date",
    "source_badge",
    "domain",
    "team_size",
    "min_team_size",   # NEW — parsed from team_size
    "max_team_size",   # NEW — parsed from team_size
]

DETAIL_FIELDS = [
    "about_description",
    "eligibility_text",
    "duration",
    "responsibilities",
    "skills_required",
    "how_to_apply",
    "application_url",
]

BACKEND_FIELDS = [
    "internship_id",
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
#  TEAM SIZE PARSING (cloned from postprocess_competition_2.py — 16 patterns)
# ══════════════════════════════════════════════════════════════════════════════

def parse_team_size(team_size):
    """Parse team_size string into (min, max) integers. Either may be None.

    Patterns checked in order; first match wins. Anything matching a number
    of "students" is treated the same as "members" (mentors/teachers ignored).
    """
    if team_size is None or team_size == "" or str(team_size).lower() == "null":
        return None, None

    ts = str(team_size).strip()
    ts_lower = ts.lower()

    # Special case: pure individual / solo
    if ts_lower in ("individual", "individual participation",
                    "solo", "individually", "solo / individual",
                    "solo / individual (1 member)"):
        return 1, 1

    # "Individual (1) or Double Delegation (2)" → (1, 2)
    m = re.search(r'individual\s*\(\s*(\d+)\s*\).*?\(\s*(\d+)\s*\)', ts_lower)
    if m:
        return int(m.group(1)), int(m.group(2))

    # "Pair (N students)" or "Pair (N members)" → (N, N)
    m = re.search(r'pair\s*\(\s*(\d+)\s+(?:students?|members?|people)\s*\)', ts_lower)
    if m:
        n = int(m.group(1))
        return n, n

    # "Solo - N members" / "Solo - N students" → (1, N)
    m = re.search(r'solo\s*[-–]\s*(\d+)\s+(?:members?|students?)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # "Solo or Team of N" → (1, N)
    m = re.search(r'solo\s+or\s+team\s+of\s+(\d+)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # "Solo or up to N members/students" → (1, N)
    m = re.search(r'solo\s+or\s+up\s+to\s+(\d+)\s+(?:members?|students?)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # "Individual (X) or N-M members (Y)" → (1, M) — parenthetical-aware
    m = re.search(
        r'individual\s*(?:\([^)]*\))?\s+or\s+(\d+)\s*[-–]\s*(\d+)\s+(?:members?|students?)',
        ts_lower)
    if m:
        return 1, int(m.group(2))

    # "N-M members (X) or Individual (Y)" → (1, M) — order-agnostic
    m = re.search(
        r'(\d+)\s*[-–]\s*(\d+)\s+(?:members?|students?)\s*(?:\([^)]*\))?\s+or\s+individual',
        ts_lower)
    if m:
        return 1, int(m.group(2))

    # "Individual or 2-X members" — solo allowed, max is higher number
    m = re.search(r'individual\s+or\s+(\d+)\s*[-–]\s*(\d+)', ts_lower)
    if m:
        return 1, int(m.group(2))

    # "1-4 members", "1 - 4 Members", "2 - 4 Members"
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)', ts)
    if m:
        return int(m.group(1)), int(m.group(2))

    # "Individual or Team of up to X" / "Individual or Team of upto X"
    m = re.search(r'(?:individual\s+or\s+)?team\s+of\s+up\s*to\s+(\d+)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # "(Individual or Team) up to X members/students"
    m = re.search(r'up\s+to\s+(\d+)\s+(?:members?|students?)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # "Team of maximum X students/members"
    m = re.search(r'team\s+of\s+(?:maximum\s+)?(\d+)\s+(?:students?|members?)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # "groups of two"
    if "groups of two" in ts_lower or "group of two" in ts_lower:
        return 1, 2

    # "N participants/students/members/people" (singular count)
    m = re.search(r'^(\d+)\s+(?:participants?|students?|members?|people)$', ts_lower)
    if m:
        n = int(m.group(1))
        return n, n

    # "Individual for Round 1; 1-3 members for subsequent rounds"
    if "individual" in ts_lower and "-" in ts:
        m = re.search(r'(\d+)\s*[-–]\s*(\d+)', ts)
        if m:
            return 1, int(m.group(2))

    # "No maximum or minimum, but 1-4 members recommended"
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
#  PROCESS ONE RECORD
# ══════════════════════════════════════════════════════════════════════════════

def process_one(data):
    """Apply mojibake sweep, parse team_size, reorder fields."""
    # Step 1: Mojibake sweep over all string values (defensive)
    data = clean_mojibake_dict(data)

    # Step 2: Parse team_size
    ts = data.get("team_size")
    min_ts, max_ts = parse_team_size(ts)
    data["min_team_size"] = min_ts
    data["max_team_size"] = max_ts

    # Step 3: Reorder fields — Card → Detail → Backend → anything else
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

        # Track team-size parse stats
        if min_ts is not None and max_ts is not None:
            stats["parsed_both"] += 1
        elif min_ts is not None or max_ts is not None:
            stats["parsed_partial"] += 1
        else:
            stats["parsed_null"] += 1
        key = f"{original_ts!r} -> min={min_ts}, max={max_ts}"
        stats["team_size_values"][key] = stats["team_size_values"].get(key, 0) + 1

        out_file = os.path.join(OUTPUT_DIR, os.path.basename(f))
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(processed, fh, indent=2, ensure_ascii=False)

        print(f"[KEEP]  {os.path.basename(f):<50}  team={min_ts}/{max_ts}")

    print(f"\n{'=' * 70}")
    print(f"  Processed: {len(all_files)} files")
    print(f"  Team-size parse:")
    print(f"    both min+max parsed:  {stats['parsed_both']}")
    print(f"    partial (one of two): {stats['parsed_partial']}")
    print(f"    null (could not parse): {stats['parsed_null']}")
    print(f"{'=' * 70}\n")

    print("Team size parse summary (distinct values):")
    for key, count in sorted(stats["team_size_values"].items(), key=lambda x: -x[1]):
        print(f"  {count:3}x  {key}")


if __name__ == "__main__":
    main()
