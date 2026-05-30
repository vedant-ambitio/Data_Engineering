#!/usr/bin/env python3
"""
filter_hs_only.py — Filter extracted competitions to keep only HS-eligible ones
================================================================================

Reads all JSONs from competition_data/extracted/
Moves non-HS competitions to competition_data/extracted/rejected/

KEEP if:
  - grade_levels contains any of [8,9,10,11,12]
  - OR age_limit mentions teens ("13-18", "14-18", "No age limit", "all ages")
  - OR eligibility_text contains "high school" / "open to everyone" / "all ages"
    / "school students" / "ages 13" / "ages 14"

DROP if:
  - eligibility_text says Bachelor/Master/PhD/undergraduate/collegiate ONLY
  - AND grade_levels is null
  - AND age_limit doesn't mention teens

Usage:
  python filter_hs_only.py              # filter all
  python filter_hs_only.py --dry-run    # show what would be filtered
"""

import json
import os
import shutil
import sys
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACTED_DIR = os.path.join(SCRIPT_DIR, "competition_data", "extracted")
REJECTED_DIR = os.path.join(EXTRACTED_DIR, "rejected")

HS_GRADES = {8, 9, 10, 11, 12}

# Keywords that indicate HS eligibility
HS_KEYWORDS = [
    "high school", "high-school", "highschool",
    "school students", "school student",
    "open to everyone", "open to all",
    "all ages", "no age limit",
    "ages 13", "ages 14", "ages 15",
    "13-18", "13-19", "14-18", "14-19", "15-18",
    "grade 8", "grade 9", "grade 10", "grade 11", "grade 12",
    "class 8", "class 9", "class 10", "class 11", "class 12",
    "k-12", "k12",
    "teens", "teenager", "youth",
    "under 18", "under 19", "under 20",
]

# Keywords that indicate college-only (when NO HS keywords present)
COLLEGE_ONLY_KEYWORDS = [
    "bachelor only", "master only", "phd only",
    "undergraduate only", "postgraduate only",
    "collegiate", "university students only",
    "college students only",
]


def is_hs_eligible(data, source="devpost"):
    """Check if a competition is eligible for high school students."""

    age = str(data.get("age_limit") or "").lower()
    elig = str(data.get("eligibility_text") or "").lower()
    about = str(data.get("about_description") or "").lower()
    cost = str(data.get("cost") or "").lower()
    grades = data.get("grade_levels")
    combined = (elig + " " + about).lower()

    # ── HARD REJECT — applies to ALL sources ──

    # Age 18+ explicitly stated → reject
    for adult_kw in ["18+", "18 years or older", "18 and above", "21+", "adults only"]:
        if adult_kw in age or adult_kw in elig:
            return False, f"adult-only: age says '{adult_kw}'"

    # ── DEVPOST — already filtered by "high school" search, trust it ──

    if source == "devpost":
        if grades and isinstance(grades, list) and any(g in HS_GRADES for g in grades):
            return True, "grade_levels contains HS grades"
        # Devpost search was "high school" — trust it
        return True, "devpost HS search (trusted)"

    # ── STUDENTCOMP — stricter filtering ──

    # Explicit HS mention → keep
    for kw in ["high school", "high-school", "school students", "k-12",
               "grade 8", "grade 9", "grade 10", "grade 11", "grade 12",
               "class 8", "class 9", "class 10", "class 11", "class 12"]:
        if kw in elig or kw in about:
            return True, f"explicitly mentions: {kw}"

    # Age limit includes teens → keep
    for kw in ["13-", "14-", "15-", "no age limit", "all ages"]:
        if kw in age:
            # But check if it's a professional competition despite "no age limit"
            if kw in ["no age limit", "all ages"]:
                # Check cost — professional competitions often have high fees
                cost_num = 0
                import re
                cost_match = re.search(r'\$(\d+)', cost)
                if cost_match:
                    cost_num = int(cost_match.group(1))
                inr_match = re.search(r'inr\s*(\d+)', cost)
                if inr_match:
                    cost_num = int(inr_match.group(1)) // 80  # rough INR to USD

                if cost_num > 100:
                    return False, f"no age limit BUT cost ${cost_num} (professional)"

                # Check professional keywords in about/eligibility
                pro_keywords = ["professional", "architect", "hospitality",
                                "enterprise", "industry", "corporate",
                                "b-school", "mba", "startup founder"]
                for pk in pro_keywords:
                    if pk in combined:
                        return False, f"no age limit BUT professional topic: {pk}"

                return True, f"age_limit: {age} (student-appropriate)"
            else:
                return True, f"age includes teens: {age}"

    # grade_levels set to HS by Gemini → trust only if no age contradiction
    if grades and isinstance(grades, list) and any(g in HS_GRADES for g in grades):
        # Double check — did Gemini contradict itself?
        if "18+" not in age and "18 years" not in elig:
            return True, "grade_levels contains HS grades (verified)"

    # Explicit "teens" / "youth" / "under 18" mention
    for kw in ["teens", "teenager", "youth", "under 18", "under 19", "young people"]:
        if kw in combined:
            return True, f"mentions: {kw}"

    # College-only keywords → reject
    for kw in COLLEGE_ONLY_KEYWORDS:
        if kw in combined:
            return False, f"college-only: {kw}"

    # "Open to everyone" without any HS/teen indicator — check if student-appropriate
    if "open to everyone" in elig or "open to all" in elig:
        # Cost check
        cost_num = 0
        import re
        cost_match = re.search(r'\$(\d+)', cost)
        if cost_match:
            cost_num = int(cost_match.group(1))
        if cost_num > 100:
            return False, f"open to everyone BUT cost ${cost_num}"

        # Topic check — is this something a 15-year-old would do?
        student_topics = ["essay", "writing", "art", "science", "math", "coding",
                          "hackathon", "quiz", "innovation", "environment", "social",
                          "photography", "poster", "poetry", "debate", "spelling"]
        for st in student_topics:
            if st in combined:
                return True, f"open to everyone + student topic: {st}"

        # Default: ambiguous "open to everyone" with no student topic → reject
        return False, "open to everyone BUT no student-relevant topic"

    return False, "no HS indicators found"


def main():
    dry_run = "--dry-run" in sys.argv

    os.makedirs(REJECTED_DIR, exist_ok=True)

    files = sorted(glob.glob(os.path.join(EXTRACTED_DIR, "*.json")))
    print(f"Checking {len(files)} extracted competitions...\n")

    kept = []
    rejected = []

    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        comp_id = data.get("competition_id", os.path.basename(f))
        # Determine source: sc_ prefix = studentcompetitions, else = devpost
        source = "studentcomp" if comp_id.startswith("sc_") else "devpost"
        eligible, reason = is_hs_eligible(data, source=source)

        if eligible:
            kept.append((comp_id, reason))
            print(f"  KEEP  {comp_id:<55} {reason}")
        else:
            rejected.append((comp_id, reason))
            print(f"  DROP  {comp_id:<55} {reason}")
            if not dry_run:
                dest = os.path.join(REJECTED_DIR, os.path.basename(f))
                shutil.move(f, dest)

    print(f"\n{'='*60}")
    print(f"  Kept:     {len(kept)}")
    print(f"  Rejected: {len(rejected)}")
    if dry_run:
        print(f"  (dry run — no files moved)")
    else:
        print(f"  Rejected files moved to: {REJECTED_DIR}/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
