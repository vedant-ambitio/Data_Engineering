#!/usr/bin/env python3
"""
postprocess_competition_2.py — Pre-UI processing for AVG-patched competitions
==============================================================================

Input:  competition_data/extracted/avg_patched/*.json (31 files, post-Gemini-merge)
Output: competition_data/processed_competition_2/*.json

Differences from postprocess_competition.py:
  - Reads from `extracted/avg_patched/` (not devpost+good)
  - Filters out competitions whose deadline is in the past (today = 2026-05-05)
  - Includes records with deadline=null (could be rolling / unannounced)
  - Extended team_size parser for new patterns seen in patched data
  - Defensive mojibake cleanup on every string (safety net)

Transformations:
1. Mojibake sweep — replace common UTF-8-as-cp1252 sequences if any leaked
2. Parse team_size string -> add min_team_size + max_team_size fields
3. Filter past-deadline records (skip writing them)
4. Reorder fields: Card -> Detail -> Backend (UI-ready)
5. All other original fields preserved (no data loss)

Usage:
  python postprocess_competition_2.py
"""

import json
import os
import re
import glob
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(SCRIPT_DIR, "competition_data", "extracted", "avg_patched")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "competition_data", "processed_competition_2")

# Hardcoded "today" date for deadline filtering.
# Anything strictly before this is considered past and excluded.
TODAY = date(2026, 5, 5)

# ── Field order: Card → Detail → Backend (matches processed_competition/ schema) ──
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
#  TEAM SIZE PARSING (cloned from postprocess_competition.py + new patterns)
# ══════════════════════════════════════════════════════════════════════════════

def parse_team_size(team_size):
    """Parse team_size string into (min, max) integers. Either may be None.

    Patterns checked in order; first match wins. Anything matching a number
    of "students" is treated the same as "members" (we ignore mentors/teachers).
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

    # ── NEW: "Individual (1) or Double Delegation (2)" → (1, 2) ──
    m = re.search(r'individual\s*\(\s*(\d+)\s*\).*?\(\s*(\d+)\s*\)', ts_lower)
    if m:
        return int(m.group(1)), int(m.group(2))

    # ── NEW: "Pair (N students)" or "Pair (N members)" → (N, N) ──
    m = re.search(r'pair\s*\(\s*(\d+)\s+(?:students?|members?|people)\s*\)', ts_lower)
    if m:
        n = int(m.group(1))
        return n, n

    # ── NEW: "Solo - N members" / "Solo - N students" → (1, N) ──
    m = re.search(r'solo\s*[-–]\s*(\d+)\s+(?:members?|students?)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # ── NEW: "Solo or Team of N" → (1, N) ──
    m = re.search(r'solo\s+or\s+team\s+of\s+(\d+)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # ── NEW: "Solo or up to N members/students" → (1, N) ──
    m = re.search(r'solo\s+or\s+up\s+to\s+(\d+)\s+(?:members?|students?)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # ── NEW: "Individual (X) or N-M members (Y)" → (1, M) ──
    # Handles parenthetical context strings like:
    #   "Individual (Delegates) or 2-5 members (Impact Initiative)"
    m = re.search(
        r'individual\s*(?:\([^)]*\))?\s+or\s+(\d+)\s*[-–]\s*(\d+)\s+(?:members?|students?)',
        ts_lower)
    if m:
        return 1, int(m.group(2))

    # ── NEW: "N-M members (X) or Individual (Y)" → (1, M) — order-agnostic ──
    m = re.search(
        r'(\d+)\s*[-–]\s*(\d+)\s+(?:members?|students?)\s*(?:\([^)]*\))?\s+or\s+individual',
        ts_lower)
    if m:
        return 1, int(m.group(2))

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

    # Pattern: "(Individual or Team) up to X members/students"
    m = re.search(r'up\s+to\s+(\d+)\s+(?:members?|students?)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # Pattern: "Team of maximum X students/members"
    m = re.search(r'team\s+of\s+(?:maximum\s+)?(\d+)\s+(?:students?|members?)', ts_lower)
    if m:
        return 1, int(m.group(1))

    # Pattern: "groups of two"
    if "groups of two" in ts_lower or "group of two" in ts_lower:
        return 1, 2

    # Pattern: "N participants/students/members/people" (singular count)
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
#  DEADLINE FILTER
# ══════════════════════════════════════════════════════════════════════════════

def is_deadline_past(deadline_str):
    """Return True if deadline is a valid YYYY-MM-DD date strictly before TODAY.
    Returns False for null, 'Rolling', or future / today dates.
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
    """Apply mojibake sweep, parse team_size, reorder fields. Caller decides
    whether to write based on deadline filter."""
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
    # Preserve anything else (e.g. unknown fields)
    for field in data:
        if field not in ordered:
            ordered[field] = data[field]

    return ordered


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if not os.path.isdir(INPUT_DIR):
        print(f"[ERROR] Input folder not found: {INPUT_DIR}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.json")))
    print(f"Input:  {INPUT_DIR}")
    print(f"Output: {OUTPUT_DIR}")
    print(f"Today:  {TODAY.isoformat()}  (records with deadline strictly before today are excluded)\n")
    print(f"Total input files: {len(files)}\n")

    stats = {
        "total": 0,
        "written": 0,
        "skipped_past": 0,
        "kept_null_deadline": 0,
        "kept_rolling": 0,
        "parsed_both": 0,
        "parsed_partial": 0,
        "parsed_null": 0,
        "team_size_values": {},
    }
    skipped_log = []

    for fp in files:
        with open(fp, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        stats["total"] += 1

        deadline = data.get("deadline")

        # Filter past-deadline records
        if is_deadline_past(deadline):
            stats["skipped_past"] += 1
            skipped_log.append((os.path.basename(fp), deadline))
            print(f"[SKIP-PAST]  {os.path.basename(fp):<45}  deadline={deadline}")
            continue

        # Track null vs Rolling for transparency
        if deadline is None:
            stats["kept_null_deadline"] += 1
        elif str(deadline).strip().lower() == "rolling":
            stats["kept_rolling"] += 1

        # Process and save
        original_ts = data.get("team_size")
        processed = process_one(data)
        min_ts = processed.get("min_team_size")
        max_ts = processed.get("max_team_size")

        if min_ts is not None and max_ts is not None:
            stats["parsed_both"] += 1
        elif min_ts is not None or max_ts is not None:
            stats["parsed_partial"] += 1
        else:
            stats["parsed_null"] += 1
        key = f"{original_ts!r} -> min={min_ts}, max={max_ts}"
        stats["team_size_values"][key] = stats["team_size_values"].get(key, 0) + 1

        out_file = os.path.join(OUTPUT_DIR, os.path.basename(fp))
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(processed, fh, indent=2, ensure_ascii=False)
        stats["written"] += 1
        print(f"[KEEP]       {os.path.basename(fp):<45}  deadline={deadline}  team={min_ts}/{max_ts}")

    print(f"\n{'=' * 70}")
    print(f"  Total input:                 {stats['total']}")
    print(f"  Written to processed folder: {stats['written']}")
    print(f"  Skipped (past deadline):     {stats['skipped_past']}")
    print(f"    (of which kept):  null deadlines = {stats['kept_null_deadline']},  Rolling = {stats['kept_rolling']}")
    print(f"  Team-size parse:")
    print(f"    both min+max parsed:  {stats['parsed_both']}")
    print(f"    partial (one of two): {stats['parsed_partial']}")
    print(f"    null (could not parse): {stats['parsed_null']}")
    print(f"{'=' * 70}\n")

    if skipped_log:
        print("Past-deadline records skipped:")
        for name, dl in skipped_log:
            print(f"  - {name}: {dl}")
        print()

    print("Team size parse summary (distinct values):")
    for key, count in sorted(stats["team_size_values"].items(), key=lambda x: -x[1]):
        print(f"  {count:3}x  {key}")


if __name__ == "__main__":
    main()
