#!/usr/bin/env python3
"""
merge_course_data.py — Merge Gemini extraction + BigFuture into final course JSON
=================================================================================

Takes structured extraction output (from structured_data_extraction.py) and
BigFuture raw college data, merges them using the 3-tier strategy, and outputs
backend-ready JSON + QA sidecar.

Pipeline:
  Step 1: structured_data_extraction.py → extracted.json  (already done)
  Step 2: THIS SCRIPT → final_course.json + qa_sidecar.json

Usage:
  python merge_course_data.py                           # merge all extracted files
  python merge_course_data.py --file extracted/nyu.json  # single file
  python merge_course_data.py --dry-run                  # show what would be merged

Reference: course_model.py, field_requirements_report.txt
Covers ALL ~115 fields across University + Program + related models.
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# ── Config ──────────────────────────────────────────────────────────────────

EXTRACTED_DIR = "university_data/structured_extraction_test"
BIGFUTURE_RAW_DIR = "bigfuture_data/raw"
BF_MATCH_FILE = "ug_bigfuture_match.json"   # {uni_folder: bf_slug}
OUTPUT_DIR = "university_data/merged_output"
QA_DIR = "university_data/merged_output/qa"

# Confidence tiers from classification
CLASSIFICATION_DIR = "classification_results/ug"
HIGH_CONF_DIR = os.path.join(CLASSIFICATION_DIR, "high_confidence")
MOD_CONF_DIR = os.path.join(CLASSIFICATION_DIR, "moderate_confidence")
LOW_CONF_DIR = os.path.join(CLASSIFICATION_DIR, "low_confidence")

# Tier 3 overlap thresholds
TUITION_FUZZY_THRESHOLD_LOW = 0.10    # <10% = verified
TUITION_FUZZY_THRESHOLD_HIGH = 0.30   # >30% = major discrepancy
DEADLINE_TOLERANCE_VERIFIED = 7       # 0-7 days = verified
DEADLINE_TOLERANCE_MISMATCH = 30      # 8-30 days = mismatch, >30 = wrong cycle

TODAY = date.today()


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def safe_float(val):
    """Convert value to float, return None if not possible."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def safe_int(val):
    """Convert value to int, return None if not possible."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def first_non_null(*values):
    """Return the first non-None, non-empty value."""
    for v in values:
        if v is not None and v != "" and v != [] and v != {}:
            return v
    return None


def parse_date(date_str):
    """Parse ISO date string to date object."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def pct_diff(a, b):
    """Percentage difference between two numbers. Returns None if either is None/0."""
    a, b = safe_float(a), safe_float(b)
    if not a or not b:
        return None
    return abs(a - b) / b


def extrapolate_date_forward(d):
    """If date is in the past, advance by 1 year at a time until it's in the future."""
    if d is None:
        return None, False
    if d >= TODAY:
        return d, False
    new_d = d
    while new_d < TODAY:
        try:
            new_d = new_d.replace(year=new_d.year + 1)
        except ValueError:
            # Feb 29 edge case
            new_d = new_d.replace(month=2, day=28, year=new_d.year + 1)
    return new_d, True


def determine_confidence_tier(md_filename, uni_folder):
    """Determine if a markdown file is high/moderate/low confidence."""
    # Check which classification directory the file is in
    for tier, tier_dir in [("high", HIGH_CONF_DIR), ("moderate", MOD_CONF_DIR), ("low", LOW_CONF_DIR)]:
        check_path = os.path.join(tier_dir, uni_folder, md_filename)
        if os.path.exists(check_path):
            return tier
    return "unknown"


def load_json(filepath):
    """Load a JSON file, return empty dict on failure."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(f"  [WARN] Could not load {filepath}: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════════════
#  TIER 3 COMPARISON STRATEGIES
# ══════════════════════════════════════════════════════════════════════════════

def compare_fuzzy_numeric(md_val, bf_val, confidence_tier, field_name):
    """
    Strategy 1: Fuzzy numeric comparison (tuition, living cost, total cost).
    Returns: (chosen_value, chosen_source, resolution, qa_entry)
    """
    md_f = safe_float(md_val)
    bf_f = safe_float(bf_val)

    qa = {
        "field": field_name,
        "strategy": "fuzzy_numeric",
        "md_value": md_f,
        "bf_value": bf_f,
    }

    # Only one source available
    if md_f and not bf_f:
        qa["resolution"] = "md_only"
        qa["chosen_source"] = "markdown"
        return md_f, "markdown", "md_only", qa

    if bf_f and not md_f:
        qa["resolution"] = "bf_only"
        qa["chosen_source"] = "bigfuture"
        return bf_f, "bigfuture", "bf_only", qa

    if not md_f and not bf_f:
        qa["resolution"] = "no_data"
        qa["chosen_source"] = None
        return None, None, "no_data", qa

    # Both exist — compare
    diff = pct_diff(md_f, bf_f)
    qa["difference_pct"] = round(diff * 100, 1) if diff is not None else None

    if diff < TUITION_FUZZY_THRESHOLD_LOW:
        # <10% — both agree
        qa["resolution"] = "verified"
        qa["chosen_source"] = "markdown"
        qa["reason"] = f"{qa['difference_pct']}% diff, within 10% tolerance"
        return md_f, "markdown", "verified", qa

    elif diff < TUITION_FUZZY_THRESHOLD_HIGH:
        # 10-30% — depends on confidence
        if confidence_tier == "high":
            qa["resolution"] = "minor_discrepancy"
            qa["chosen_source"] = "markdown"
            qa["reason"] = f"{qa['difference_pct']}% diff, high confidence → prefer MD"
            return md_f, "markdown", "minor_discrepancy", qa
        else:
            qa["resolution"] = "minor_discrepancy"
            qa["chosen_source"] = "bigfuture"
            qa["reason"] = f"{qa['difference_pct']}% diff, {confidence_tier} confidence → prefer BF (IPEDS)"
            return bf_f, "bigfuture", "minor_discrepancy", qa

    else:
        # >30% — MD likely wrong
        qa["resolution"] = "major_discrepancy"
        qa["chosen_source"] = "bigfuture"
        qa["reason"] = f"{qa['difference_pct']}% diff, MD likely has wrong figure"
        return bf_f, "bigfuture", "major_discrepancy", qa


def compare_exact_match(md_val, bf_val, field_name):
    """
    Strategy 2: Exact match comparison (application fee, test scores).
    Returns: (chosen_value, chosen_source, resolution, qa_entry)
    """
    md_f = safe_float(md_val)
    bf_f = safe_float(bf_val)

    qa = {
        "field": field_name,
        "strategy": "exact_match",
        "md_value": md_f,
        "bf_value": bf_f,
    }

    if md_f and not bf_f:
        qa["resolution"] = "md_only"
        qa["chosen_source"] = "markdown"
        return md_f, "markdown", "md_only", qa

    if bf_f and not md_f:
        qa["resolution"] = "bf_only"
        qa["chosen_source"] = "bigfuture"
        return bf_f, "bigfuture", "bf_only", qa

    if not md_f and not bf_f:
        qa["resolution"] = "no_data"
        qa["chosen_source"] = None
        return None, None, "no_data", qa

    # Both exist
    if md_f == bf_f:
        qa["resolution"] = "verified"
        qa["chosen_source"] = "markdown"
        return md_f, "markdown", "verified", qa
    else:
        qa["resolution"] = "mismatch"
        qa["chosen_source"] = "bigfuture"
        qa["reason"] = f"MD={md_f}, BF={bf_f}. BF is IPEDS authoritative."
        return bf_f, "bigfuture", "mismatch", qa


def compare_deadlines(md_deadlines, bf_data):
    """
    Strategy 3: Deadline cross-validation.
    MD owns the structure (rounds, tags, labels). BF only verifies key dates.
    Returns: (final_deadlines, qa_checks)
    """
    qa_checks = []
    final_deadlines = []

    # Extract BF dates
    bf_early_decision = parse_date(bf_data.get("earlyDecisionDate"))
    bf_early_action = parse_date(bf_data.get("earlyActionDate"))
    bf_regular = parse_date(bf_data.get("regularDecisionDate"))

    bf_dates_map = {
        "early decision": ("earlyDecisionDate", bf_early_decision),
        "early action": ("earlyActionDate", bf_early_action),
        "regular": ("regularDecisionDate", bf_regular),
    }

    if not md_deadlines:
        # MD has nothing — create from BF if available
        for round_name, (bf_field, bf_date) in bf_dates_map.items():
            if bf_date:
                display_round = round_name.replace("early ", "Early ").replace("regular", "Regular Decision")
                display_round = display_round.title()
                final_deadlines.append({
                    "intake": "fall",
                    "round": display_round,
                    "deadline_date": bf_date.isoformat(),
                    "decision_date": None,
                    "tags": ["domestic", "international"],
                    "label": None,
                })
                qa_checks.append({
                    "md_round": None,
                    "md_date": None,
                    "bf_field": bf_field,
                    "bf_date": bf_date.isoformat(),
                    "resolution": "bf_only",
                    "action_taken": "created_from_bf",
                })
        return final_deadlines, qa_checks

    # Process each MD deadline
    for dl in md_deadlines:
        md_date = parse_date(dl.get("deadline_date"))
        round_name = (dl.get("round") or "").lower()

        # Step 1: Fix past dates by extrapolating forward
        extrapolated = False
        original_date = md_date
        if md_date:
            md_date, extrapolated = extrapolate_date_forward(md_date)

        # Build the final deadline entry (always from MD structure)
        final_dl = {
            "intake": dl.get("intake"),
            "round": dl.get("round"),
            "deadline_date": md_date.isoformat() if md_date else None,
            "decision_date": dl.get("decision_date"),
            "tags": dl.get("tags", []),
            "label": dl.get("label"),
        }

        # Also extrapolate decision date if present
        dec_date = parse_date(dl.get("decision_date"))
        if dec_date:
            dec_date, _ = extrapolate_date_forward(dec_date)
            final_dl["decision_date"] = dec_date.isoformat() if dec_date else None

        final_deadlines.append(final_dl)

        # Step 2: Match MD round to BF date for verification
        matched_bf_field = None
        matched_bf_date = None
        for keyword, (bf_field, bf_date) in bf_dates_map.items():
            if keyword in round_name and bf_date:
                matched_bf_field = bf_field
                matched_bf_date = bf_date
                break

        # Step 3: Compare if we have a BF match
        qa_entry = {
            "md_round": dl.get("round"),
            "md_date_original": original_date.isoformat() if original_date else None,
            "md_date_final": md_date.isoformat() if md_date else None,
            "extrapolated": extrapolated,
            "bf_field": matched_bf_field,
            "bf_date": matched_bf_date.isoformat() if matched_bf_date else None,
        }

        if not matched_bf_date:
            qa_entry["resolution"] = "no_bf_match"
        elif not md_date:
            qa_entry["resolution"] = "md_date_null"
        else:
            diff_days = abs((md_date - matched_bf_date).days)
            qa_entry["diff_days"] = diff_days

            if diff_days <= DEADLINE_TOLERANCE_VERIFIED:
                if extrapolated:
                    qa_entry["resolution"] = "extrapolated_and_verified"
                else:
                    qa_entry["resolution"] = "verified"
            elif diff_days <= DEADLINE_TOLERANCE_MISMATCH:
                qa_entry["resolution"] = "date_mismatch"
                qa_entry["flag"] = True
                qa_entry["note"] = f"{diff_days} days off. MD kept — may be intl vs domestic difference."
            else:
                if extrapolated:
                    qa_entry["resolution"] = "extrapolated_date_mismatch"
                else:
                    qa_entry["resolution"] = "wrong_cycle"
                qa_entry["flag"] = True
                qa_entry["note"] = f"{diff_days} days off. MD date kept but flagged for review."

        qa_checks.append(qa_entry)

    # Step 4: Supplement — if MD is missing a round that BF has
    md_round_names = " ".join((dl.get("round") or "").lower() for dl in md_deadlines)
    for keyword, (bf_field, bf_date) in bf_dates_map.items():
        if bf_date and keyword not in md_round_names:
            display_round = keyword.title()
            if keyword == "regular":
                display_round = "Regular Decision"
            final_deadlines.append({
                "intake": "fall",
                "round": display_round,
                "deadline_date": bf_date.isoformat(),
                "decision_date": None,
                "tags": ["domestic", "international"],
                "label": None,
            })
            qa_checks.append({
                "md_round": None,
                "md_date": None,
                "bf_field": bf_field,
                "bf_date": bf_date.isoformat(),
                "resolution": "bf_supplemented",
                "action_taken": "added_missing_round_from_bf",
            })

    return final_deadlines, qa_checks


def compare_url_priority(md_url, bf_url, field_name):
    """
    Strategy 4: URL preference — program-specific MD wins, generic falls back to BF.
    Returns: (chosen_url, chosen_source, resolution, qa_entry)
    """
    qa = {
        "field": field_name,
        "strategy": "url_priority",
        "md_value": md_url,
        "bf_value": bf_url,
    }

    if md_url and not bf_url:
        qa["resolution"] = "md_only"
        qa["chosen_source"] = "markdown"
        return md_url, "markdown", "md_only", qa

    if bf_url and not md_url:
        qa["resolution"] = "bf_only"
        qa["chosen_source"] = "bigfuture"
        return bf_url, "bigfuture", "bf_only", qa

    if not md_url and not bf_url:
        qa["resolution"] = "no_data"
        qa["chosen_source"] = None
        return None, None, "no_data", qa

    # Both exist — check if MD is program-specific (has path depth)
    # A generic URL like "https://nyu.edu" has no path; a specific one has /admissions/program/...
    md_parsed_path = md_url.rstrip("/").count("/") if md_url else 0
    if md_parsed_path > 3:
        # MD has a deep path = program-specific
        qa["resolution"] = "md_preferred"
        qa["chosen_source"] = "markdown"
        qa["reason"] = "MD URL is program-specific (deep path)"
        return md_url, "markdown", "md_preferred", qa
    else:
        # MD is generic homepage — BF might be better
        qa["resolution"] = "bf_preferred"
        qa["chosen_source"] = "bigfuture"
        qa["reason"] = "MD URL is generic. BF has direct application link."
        return bf_url, "bigfuture", "bf_preferred", qa


# ══════════════════════════════════════════════════════════════════════════════
#  BIGFUTURE HELPER EXTRACTORS
# ══════════════════════════════════════════════════════════════════════════════

def bf_get_tuition(bf):
    """Extract international-facing tuition from BigFuture raw."""
    # For private schools: privateTuition
    # For public schools: outOfStateTuition (international = out-of-state)
    private = safe_float(bf.get("privateTuition"))
    out_of_state = safe_float(bf.get("outOfStateTuition"))
    in_state = safe_float(bf.get("inStateTuition"))
    return private or out_of_state or in_state


def bf_get_living_cost(bf):
    """Sum up living cost components from BigFuture."""
    housing = safe_float(bf.get("averageHousingCost")) or safe_float(bf.get("averageHousingCostForCampusLife")) or 0
    books = safe_float(bf.get("booksAndSuppliesCost")) or 0
    transport = safe_float(bf.get("transportationCosts")) or 0
    personal = safe_float(bf.get("estimatedPersonalExpenses")) or 0
    total = housing + books + transport + personal
    return total if total > 0 else None


def bf_get_university_type(bf):
    """Derive Public/Private from BigFuture data."""
    inst_types = bf.get("institutionTypes") or []
    for it in inst_types:
        desc = (it.get("institutionTypeDescription") or "").lower()
        if "public" in desc:
            return "Public"
        if "private" in desc:
            return "Private"
    # Fallback: if privateTuition exists and no outOfStateTuition, it's private
    if bf.get("privateTuition") and not bf.get("outOfStateTuition"):
        return "Private"
    if bf.get("outOfStateTuition") and not bf.get("privateTuition"):
        return "Public"
    return None


def bf_get_address(bf):
    """Build full address string from BigFuture fields."""
    parts = []
    if bf.get("streetAddress"):
        parts.append(bf["streetAddress"])
    city_state = []
    if bf.get("city"):
        city_state.append(bf["city"])
    if bf.get("stateName"):
        city_state.append(bf["stateName"])
    if city_state:
        parts.append(", ".join(city_state))
    if bf.get("zipCode"):
        parts.append(bf["zipCode"])
    return ", ".join(parts) if parts else None


def bf_get_admission_policy(bf):
    """Build admission policy string from BigFuture requirement fields."""
    parts = []
    for field, label in [("highSchoolGpa", "GPA"), ("highSchoolRank", "Rank"),
                          ("prepCourses", "Prep Courses"), ("recommendations", "Recommendations")]:
        val = bf.get(field)
        if val:
            parts.append(f"{label}: {val}")
    return "; ".join(parts) if parts else None


def bf_get_demographics(bf):
    """Extract StudentDiversity rows from BigFuture demographics."""
    demo_fields = [
        ("whitePercent", "White"),
        ("asianPercent", "Asian"),
        ("hispanicPercent", "Hispanic/Latino"),
        ("africanAmericanPercent", "African American"),
        ("internationalPercent", "International"),
        ("multiracialPercent", "Multiracial"),
        ("nativeAmericanPercent", "Native American"),
        ("pacificIslanderPercent", "Pacific Islander"),
        ("unknownPercent", "Unknown/Other"),
    ]
    rows = []
    for field, label in demo_fields:
        val = safe_float(bf.get(field))
        if val is not None and val > 0:
            rows.append({"label": label, "value": round(val, 1)})
    return rows


def bf_get_test_scores(bf):
    """Extract SAT/ACT score data from BigFuture."""
    scores = []

    # SAT Composite
    sat_25 = safe_int(bf.get("satCompositeScore25thPercentile"))
    sat_75 = safe_int(bf.get("satCompositeScore75thPercentile"))
    if sat_25 and sat_75:
        sat_entry = {
            "test": "SAT",
            "minScore": sat_25,
            "averageScore": (sat_25 + sat_75) // 2,
            "minScoreDescription": f"{sat_25}-{sat_75} (25th-75th percentile)",
            "sub_scores": [],
        }
        # SAT Math
        math_25 = safe_int(bf.get("rsatMathScore25thPercentile"))
        math_75 = safe_int(bf.get("rsatMathScore75thPercentile"))
        if math_25 and math_75:
            sat_entry["sub_scores"].append({"fieldName": "Math", "value": f"{math_25}-{math_75}"})
        # SAT EBRW
        ebrw_25 = safe_int(bf.get("rsatEbrwScore25thPercentile"))
        ebrw_75 = safe_int(bf.get("rsatEbrwScore75thPercentile"))
        if ebrw_25 and ebrw_75:
            sat_entry["sub_scores"].append({"fieldName": "EBRW", "value": f"{ebrw_25}-{ebrw_75}"})
        scores.append(sat_entry)

    # ACT Composite
    act_25 = safe_int(bf.get("actCompositeScore25thPercentile"))
    act_75 = safe_int(bf.get("actCompositeScore75thPercentile"))
    if act_25 and act_75:
        scores.append({
            "test": "ACT",
            "minScore": act_25,
            "averageScore": (act_25 + act_75) // 2,
            "minScoreDescription": f"{act_25}-{act_75} (25th-75th percentile)",
            "sub_scores": [],
        })

    return scores


def bf_get_tags(bf):
    """Extract special designation tags from BigFuture."""
    tags = []
    if bf.get("specializedSchoolHistoricallyBlackInd") == "Y":
        tags.append("HBCU")
    if bf.get("specializedSchoolHispanicServingInd") == "Y":
        tags.append("Hispanic-Serving")
    if bf.get("specializedSchoolWomensCollegeInd") == "Y":
        tags.append("Women's College")
    if bf.get("specializedSchoolTribalCollegeInd") == "Y":
        tags.append("Tribal College")
    if bf.get("specializedSchoolMensCollegeInd") == "Y":
        tags.append("Men's College")
    return tags


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN MERGE FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def merge_single_course(extracted, bf, confidence_tier, md_filename, uni_folder, bf_slug,
                         program_id=None, university_id=None):
    """
    Merge one extracted course JSON with BigFuture data.
    Returns: (final_course_json, qa_sidecar_json)
    """
    ge = extracted   # alias for Gemini extraction data
    qa = {
        "source_file": md_filename,
        "university_folder": uni_folder,
        "confidence_tier": confidence_tier,
        "bigfuture_slug": bf_slug,
        "bigfuture_available": bool(bf),
        "field_sources": {},       # field_name → chosen source
        "cross_validation": [],    # tier 3 comparison details
        "warnings": [],
    }

    has_bf = bool(bf)
    cost = ge.get("cost_of_attendance") or {}
    admission = ge.get("admission_requirements") or {}
    career = ge.get("career_outcomes") or {}
    class_prof = ge.get("class_profile") or {}
    links = ge.get("important_links") or {}
    contact = ge.get("contact_info") or {}

    # ══════════════════════════════════════════════════════════════════════
    #  A) UNIVERSITY-LEVEL FIELDS
    # ══════════════════════════════════════════════════════════════════════

    university = {}

    # A1. Identity
    university["name"] = ge.get("university_name")
    university["slug"] = None                                              # [CALC] auto-generated
    university["ope_id"] = safe_int(bf.get("ipedsId")) if has_bf else None
    qa["field_sources"]["uni.ope_id"] = "bigfuture" if university["ope_id"] else None

    # University type: BF is authoritative when available
    bf_type = bf_get_university_type(bf) if has_bf else None
    university["type"] = bf_type or "Unknown"
    qa["field_sources"]["uni.type"] = "bigfuture" if bf_type else "default"

    university["shortName"] = None                                         # [MANUAL]
    university["universityPageLink"] = first_non_null(
        bf.get("schoolUrl") if has_bf else None,
        links.get("program_page"),
    )
    qa["field_sources"]["uni.universityPageLink"] = "bigfuture" if (has_bf and bf.get("schoolUrl")) else "extraction"

    # A2. Location
    university["cityName"] = first_non_null(
        bf.get("city") if has_bf else None,
        None,  # would come from MD parsing
    )
    university["countryName"] = bf.get("countryName") if has_bf else None
    university["stateName"] = bf.get("stateName") if has_bf else None
    university["address"] = bf_get_address(bf) if has_bf else None
    university["lat"] = safe_float(bf.get("lat")) if has_bf else None
    university["lon"] = safe_float(bf.get("lon")) if has_bf else None

    # A3. Visuals — [MANUAL], not filled here
    university["logo"] = None
    university["shortLogo"] = None
    university["galleryImages"] = []

    # A4. Rankings — [RANK], not filled here (separate dataset)
    university["globalRank"] = None
    university["qsRank"] = None
    university["qsRankYear"] = None
    university["thRank"] = None
    university["thRankYear"] = None
    university["usnRank"] = None
    university["usnRankYear"] = None

    # A5. Demographics — TIER 1: BF-ALWAYS
    university["acceptanceRate"] = safe_float(bf.get("acceptanceRate")) if has_bf else None
    university["menPercentage"] = None   # derive from demographics if needed
    university["womenPercentage"] = None
    university["studentInternationalDiversity"] = safe_float(bf.get("internationalPercent")) if has_bf else None
    university["studentDiversity"] = bf_get_demographics(bf) if has_bf else []
    university["tags"] = bf_get_tags(bf) if has_bf else []
    qa["field_sources"]["uni.acceptanceRate"] = "bigfuture" if university["acceptanceRate"] else None
    qa["field_sources"]["uni.demographics"] = "bigfuture" if university["studentDiversity"] else None

    # Compute men/women from demographics
    if has_bf:
        # BigFuture doesn't have direct gender %, but genderCode tells us coed/men/women
        gender_code = bf.get("genderCode")
        if gender_code == "W":
            university["womenPercentage"] = 100.0
            university["menPercentage"] = 0.0
        elif gender_code == "M":
            university["menPercentage"] = 100.0
            university["womenPercentage"] = 0.0
        # For coed, we'd need external data — leave None

    # A6. Content
    university["pointers"] = [p.get("text", p) if isinstance(p, dict) else p
                               for p in (ge.get("reasons_to_consider") or [])]
    university["reasons_to_consider"] = university["pointers"]  # same source for uni level

    # ══════════════════════════════════════════════════════════════════════
    #  B) LOOKUP MODEL REFERENCES
    # ══════════════════════════════════════════════════════════════════════

    lookups = {}

    # B1. CourseMajor
    lookups["courseMajor"] = {
        "name": ge.get("program_name"),     # will be matched/created in backend
        "isSTEM": ge.get("is_stem"),
    }

    # B2. CourseSpecialization — derived from program name
    lookups["courseSpecialization"] = None    # matched from program name in backend

    # B3. CourseDegree
    lookups["courseDegree"] = ge.get("degree_type")   # e.g. "BS", "MS", "MBA"

    # B4. Companies (recruiters)
    lookups["recruiters"] = [
        {"name": r.get("name", r) if isinstance(r, dict) else r}
        for r in (career.get("top_recruiters") or [])
    ]

    # B5. JobRoles
    lookups["prospectiveJobRoles"] = [
        {"name": r.get("name", r) if isinstance(r, dict) else r}
        for r in (career.get("job_roles") or [])
    ]

    # B9. ScholarshipProviders — extracted from scholarships
    lookups["scholarshipsProviders"] = [
        {"name": s.get("name")}
        for s in (ge.get("scholarships") or [])
        if s.get("name")
    ]

    # ══════════════════════════════════════════════════════════════════════
    #  C) PROGRAM-LEVEL FIELDS
    # ══════════════════════════════════════════════════════════════════════

    program = {}

    # ─── C1. Identity & Classification ──────────────────────────────────
    program["type"] = "PROGRAM"
    # Map degree_type to courseLevel — handle both abbreviations and full names
    degree_raw = (ge.get("degree_type") or "").strip()
    degree = degree_raw.upper().replace(".", "").replace(" ", "")
    bachelor_keys = ("BS", "BA", "BFA", "BENG", "BTECH", "BSC", "BBA", "BMUS",
                     "BACHELOROFSCIENCE", "BACHELOROFARTS", "BACHELOROFENGINEERING",
                     "BACHELOROFFINEARTS", "BACHELOROFBUSINESSADMINISTRATION",
                     "BACHELORSOFSCIENCE", "BACHELORSOFARTS")
    master_keys = ("MS", "MA", "MBA", "MENG", "MSC", "MFA", "MED", "MPH", "MPA", "MSW", "LLM",
                   "MASTEROFSCIENCE", "MASTEROFARTS", "MASTEROFBUSINESSADMINISTRATION",
                   "MASTERSOFSCIENCE", "MASTERSOFARTS")
    phd_keys = ("PHD", "DPHIL", "EDD", "DBA", "MD", "JD",
                "DOCTOROFPHILOSOPHY")
    if degree in bachelor_keys or "BACHELOR" in degree:
        program["courseLevel"] = "Bachelor"
    elif degree in master_keys or "MASTER" in degree:
        program["courseLevel"] = "Master"
    elif degree in phd_keys or "DOCTOR" in degree or "PHD" in degree:
        program["courseLevel"] = "PhD"
    else:
        program["courseLevel"] = "Unknown"

    program["programRank"] = None                                          # [RANK] filled separately
    program["is_job_role"] = False

    # ─── C2. Overview & Content ─────────────────────────────────────────
    program["overviewDescription"] = ge.get("overview_description") or ""
    program["pointers"] = [p.get("text", p) if isinstance(p, dict) else p
                            for p in (ge.get("pointers") or [])]
    program["why_study_points"] = [p.get("text", p) if isinstance(p, dict) else p
                                    for p in (ge.get("why_study_points") or [])]
    program["reasons_to_consider"] = [p.get("text", p) if isinstance(p, dict) else p
                                       for p in (ge.get("reasons_to_consider") or [])]
    program["program_insight"] = None                                      # [GE] could be generated
    program["application_requirements_insight"] = None                     # [GE] could be generated
    program["test_score_insight"] = None                                   # [GE] could be generated
    program["testScoreDescription"] = None                                 # [GE]
    program["whoIsThisProgramForDescription"] = None                       # [GE]
    qa["field_sources"]["overviewDescription"] = "extraction"
    qa["field_sources"]["pointers"] = "extraction"
    qa["field_sources"]["why_study_points"] = "extraction"

    # ─── C3. School / Department ────────────────────────────────────────
    program["schoolName"] = ge.get("department")
    qa["field_sources"]["schoolName"] = "extraction" if program["schoolName"] else None

    # ─── C4. Duration & Intake ──────────────────────────────────────────
    program["courseDuration"] = safe_int(ge.get("duration_months"))
    program["intake"] = list(set(
        dl.get("intake") for dl in (ge.get("deadlines") or []) if dl.get("intake")
    )) or []
    qa["field_sources"]["courseDuration"] = "extraction" if program["courseDuration"] else None

    # ─── C5. Tuition & Fees — TIER 3 OVERLAP ───────────────────────────

    # totalTuitionFeePerYear — fuzzy numeric
    ge_tuition = safe_float(cost.get("tuition_per_year")) or safe_float(cost.get("tuition_international"))
    bf_tuition = bf_get_tuition(bf) if has_bf else None

    val, src, res, qa_entry = compare_fuzzy_numeric(ge_tuition, bf_tuition, confidence_tier, "totalTuitionFeePerYear")
    program["totalTuitionFeePerYear"] = val
    program["tuitionFeePerYear"] = val    # same value initially
    qa["field_sources"]["totalTuitionFeePerYear"] = src
    qa["cross_validation"].append(qa_entry)

    # totalTuitionFee (total program cost)
    ge_total = safe_float(cost.get("total_program_cost"))
    # BF doesn't have total program cost, but we can estimate: tuition × (duration/12)
    bf_total = None
    if bf_tuition and program["courseDuration"]:
        bf_total = bf_tuition * (program["courseDuration"] / 12)
    val, src, res, qa_entry = compare_fuzzy_numeric(ge_total, bf_total, confidence_tier, "totalTuitionFee")
    program["totalTuitionFee"] = val
    program["tuitionFee"] = val
    qa["cross_validation"].append(qa_entry)

    # applicationFee — exact match
    ge_app_fee = safe_float(cost.get("application_fee") or cost.get("application_fee_international"))
    bf_app_fee = safe_float(bf.get("applicationFeeAmount")) if has_bf else None
    val, src, res, qa_entry = compare_exact_match(ge_app_fee, bf_app_fee, "applicationFee")
    program["applicationFee"] = str(int(val)) if val else None
    qa["field_sources"]["applicationFee"] = src
    qa["cross_validation"].append(qa_entry)

    program["applicationFeeCurrency"] = cost.get("currency")
    program["applicationFeeDescription"] = cost.get("notes")
    program["currencySymbol"] = cost.get("currency") or "USD"

    # overallCostPerYear — fuzzy numeric
    ge_overall = safe_float(cost.get("overall_cost_per_year"))
    bf_living = bf_get_living_cost(bf) if has_bf else None
    bf_overall = (bf_tuition + bf_living) if (bf_tuition and bf_living) else None
    val, src, res, qa_entry = compare_fuzzy_numeric(ge_overall, bf_overall, confidence_tier, "overallCostPerYear")
    program["overallCostPerYear"] = val
    qa["cross_validation"].append(qa_entry)

    # avgCostOfLivingPerYear — fuzzy numeric
    ge_living = safe_float(cost.get("cost_of_living_per_year"))
    val, src, res, qa_entry = compare_fuzzy_numeric(ge_living, bf_living, confidence_tier, "avgCostOfLivingPerYear")
    program["avgCostOfLivingPerYear"] = val
    program["totalCostOfLivingPerYear"] = val   # same for now
    qa["cross_validation"].append(qa_entry)

    program["feesAndFundingDescription"] = cost.get("notes")
    program["roi_score"] = None                                            # [CALC]
    qa["field_sources"]["overallCostPerYear"] = src

    # ─── C6. Financial Aid & Scholarships ───────────────────────────────
    scholarships_raw = ge.get("scholarships") or []
    program["scholarships"] = scholarships_raw

    # Build scholarshipsDetails text from scholarships array
    schol_parts = []
    schol_providers = []
    for s in scholarships_raw:
        name = s.get("name", "")
        amount = s.get("amount")
        currency = s.get("currency", "")
        elig = s.get("eligibility") or ""
        part = name
        if amount:
            part += f" ({currency} {amount:,.0f})" if currency else f" (${amount:,.0f})"
        if elig:
            part += f" - {elig}"
        schol_parts.append(part)
        if name:
            schol_providers.append({"name": name})
    program["scholarshipsDetails"] = "; ".join(schol_parts) if schol_parts else None

    # Build fundingOptionsTags from scholarships + BF data
    funding_tags = []
    for s in scholarships_raw:
        sname = (s.get("name") or "").lower()
        if "merit" in sname:
            funding_tags.append("Merit Scholarship")
        elif "need" in sname:
            funding_tags.append("Need-Based Aid")
        elif "athletic" in sname or "sport" in sname:
            funding_tags.append("Athletic Scholarship")
        elif "fellowship" in sname:
            funding_tags.append("Fellowship")
        else:
            funding_tags.append("Scholarship")
    if has_bf:
        if safe_float(bf.get("needBasedAid")):
            if "Need-Based Aid" not in funding_tags:
                funding_tags.append("Need-Based Aid")
        if safe_float(bf.get("nonNeedBasedAid")):
            if "Merit Scholarship" not in funding_tags:
                funding_tags.append("Merit Scholarship")
    program["fundingOptionsTags"] = list(dict.fromkeys(funding_tags))  # dedupe preserving order

    # fundingOptionsDetails
    fund_parts = []
    if has_bf:
        aid = safe_float(bf.get("averageAidAwarded"))
        if aid:
            fund_parts.append(f"Average aid awarded: ${aid:,.0f}")
        pct = safe_float(bf.get("studentsReceivingAidPercent"))
        if pct:
            fund_parts.append(f"{pct:.0f}% of students receive financial aid")
        need_met = safe_float(bf.get("financialAidMetPercent"))
        if need_met:
            fund_parts.append(f"{need_met:.0f}% of financial need met")
    program["fundingOptionsDetails"] = ". ".join(fund_parts) if fund_parts else ""

    program["funding_benefits"] = []
    program["funding_availability"] = None
    program["expected_research_output_responsibilities"] = []

    # averageTotalAidAwarded — TIER 1: BF-ALWAYS
    program["averageTotalAidAwarded"] = None
    if has_bf and bf.get("averageAidAwarded"):
        program["averageTotalAidAwarded"] = str(bf["averageAidAwarded"])
        qa["field_sources"]["averageTotalAidAwarded"] = "bigfuture"

    # ─── C7. Links & URLs — TIER 3 URL PRIORITY ────────────────────────
    program["officialPageLink"] = links.get("program_page")
    program["admission_requirement_url"] = links.get("admissions_page") or links.get("application_portal")
    program["eligibility_criteria_url"] = None
    program["application_process_url"] = links.get("application_portal")
    program["application_checklist_page"] = None

    # Collect all available links into officialLinks
    all_links = []
    for lk in [links.get("program_page"), links.get("application_portal"), links.get("faculty_directory")]:
        if lk and lk not in all_links:
            all_links.append(lk)
    if has_bf and bf.get("schoolUrl") and bf["schoolUrl"] not in all_links:
        all_links.append(bf["schoolUrl"])
    program["officialLinks"] = all_links

    # application_link — URL priority
    ge_app_url = links.get("application_portal")
    bf_app_url = (bf.get("commonApplicationUrl") or bf.get("applicationSiteUrl")) if has_bf else None
    val, src, res, qa_entry = compare_url_priority(ge_app_url, bf_app_url, "application_link")
    program["application_link"] = val
    qa["field_sources"]["application_link"] = src
    qa["cross_validation"].append(qa_entry)

    # ─── C8. Admission Requirements — TIER 2: MD/GE-ALWAYS ─────────────

    # isGRERequired — for UG, set "No" since UG doesn't use GRE
    course_level = program.get("courseLevel", "Unknown")
    if course_level == "Bachelor":
        program["isGRERequired"] = "No"
    else:
        program["isGRERequired"] = admission.get("gre_required") or "Unknown"

    # Build entryRequirementsTags from entry_requirements enum values
    entry_reqs_raw = admission.get("entry_requirements") or []
    program["entryRequirementsTags"] = [er.get("value") for er in entry_reqs_raw if er.get("value")]

    # Build entryRequirementsDetails text from entry_requirements
    er_parts = []
    for er in entry_reqs_raw:
        name = er.get("value", "")
        count = er.get("count", 1)
        detail = er.get("detail") or ""
        if count > 1:
            er_parts.append(f"{count}x {name}" + (f" ({detail})" if detail else ""))
        else:
            er_parts.append(name + (f" ({detail})" if detail else ""))
    program["entryRequirementsDetails"] = "; ".join(er_parts) if er_parts else ""

    program["additional_requirements"] = []
    program["additional_criteria"] = []

    # interviewRequested — check extraction note field
    adm_note = admission.get("note") or ""
    if "interview" in adm_note.lower():
        if "optional" in adm_note.lower():
            program["interviewRequested"] = "Optional"
        elif "required" in adm_note.lower() or "request" in adm_note.lower():
            program["interviewRequested"] = "Yes"
        else:
            program["interviewRequested"] = "Optional"
    else:
        program["interviewRequested"] = "No"

    program["admission_requirements_data"] = admission   # full JSON from GE
    program["eligibility_criteria_data"] = ge.get("eligibility_criteria") or []

    # Build eligibilityCriteriaDescription from eligibility_criteria
    elig_raw = ge.get("eligibility_criteria") or []
    elig_parts = []
    for ec in elig_raw:
        etype = ec.get("type", "")
        details = ec.get("details") or ""
        criteria = ec.get("criteria") or {}
        if etype == "HIGH_SCHOOL_DIPLOMA":
            equivs = criteria.get("equivalent") or []
            if equivs:
                elig_parts.append(f"High school diploma or equivalent ({', '.join(equivs)})")
            else:
                elig_parts.append("High school diploma required")
        elif etype == "MINIMUM_GPA":
            gpa = criteria.get("gpa", "")
            scale = criteria.get("scale", "")
            elig_parts.append(f"Minimum GPA: {gpa}/{scale}" if scale else f"Minimum GPA: {gpa}")
        elif etype == "CLASS_RANK":
            pct = criteria.get("percentile", "")
            elig_parts.append(f"Class rank: {pct}" if pct else "Class rank considered")
        elif details:
            elig_parts.append(details)
    program["eligibilityCriteriaDescription"] = ". ".join(elig_parts) if elig_parts else None

    # UG-specific admission fields — TIER 2: GE-ALWAYS
    program["sat_required"] = admission.get("sat_required")
    program["sat_score_range"] = admission.get("sat_score_range")
    program["act_required"] = admission.get("act_required")
    program["act_score_range"] = admission.get("act_score_range")
    program["test_optional_policy"] = admission.get("test_optional_policy")
    program["superscoring"] = admission.get("superscoring")
    program["superscoring_details"] = admission.get("superscoring_details")
    program["early_decision_binding"] = admission.get("early_decision_binding")
    program["common_app_accepted"] = admission.get("common_app_accepted")
    program["coalition_app_accepted"] = admission.get("coalition_app_accepted")
    program["application_platforms"] = admission.get("application_platforms") or []
    program["recommended_courses"] = admission.get("recommended_courses")

    # admissionPolicy — TIER 1: BF-ALWAYS
    program["admissionPolicy"] = bf_get_admission_policy(bf) if has_bf else None
    qa["field_sources"]["admissionPolicy"] = "bigfuture" if program["admissionPolicy"] else None

    # School codes
    program["greSchoolCode"] = None                                        # [MANUAL]
    program["gmatSchoolCode"] = None                                       # [MANUAL]
    program["toeflSchoolCode"] = str(bf.get("diCode")) if (has_bf and bf.get("diCode")) else None
    qa["field_sources"]["toeflSchoolCode"] = "bigfuture" if program["toeflSchoolCode"] else None

    # ─── C9. Test Scores ────────────────────────────────────────────────

    # Build exam scores from GE (TOEFL, IELTS, GRE, GMAT, Duolingo, PTE)
    exam_scores = []
    for test in (admission.get("english_tests") or []):
        exam_scores.append({
            "examTest": test.get("test"),
            "isRequired": test.get("is_required") or "Unknown",
            "minScore": safe_float(test.get("min_score")),
            "averageScore": None,
            "minScoreDescription": test.get("subscore_details"),
            "sub_scores": [],
            "source": "extraction",
        })

    # GRE from GE (Masters/PhD)
    if admission.get("gre_required") and admission["gre_required"] != "No":
        exam_scores.append({
            "examTest": "GRE",
            "isRequired": admission["gre_required"],
            "minScore": None,
            "averageScore": None,
            "minScoreDescription": admission.get("gre_waiver_conditions"),
            "sub_scores": [],
            "source": "extraction",
        })

    # GMAT from GE (Masters/PhD)
    if admission.get("gmat_required") and admission["gmat_required"] != "No":
        exam_scores.append({
            "examTest": "GMAT",
            "isRequired": admission["gmat_required"],
            "minScore": None,
            "averageScore": None,
            "minScoreDescription": None,
            "sub_scores": [],
            "source": "extraction",
        })

    # SAT from GE (UG)
    if admission.get("sat_required") and admission["sat_required"] != "No":
        sat_range = admission.get("sat_score_range") or ""
        exam_scores.append({
            "examTest": "SAT",
            "isRequired": admission["sat_required"],
            "minScore": None,
            "averageScore": None,
            "minScoreDescription": sat_range if sat_range else None,
            "sub_scores": [],
            "source": "extraction",
        })

    # ACT from GE (UG)
    if admission.get("act_required") and admission["act_required"] != "No":
        act_range = admission.get("act_score_range") or ""
        exam_scores.append({
            "examTest": "ACT",
            "isRequired": admission["act_required"],
            "minScore": None,
            "averageScore": None,
            "minScoreDescription": act_range if act_range else None,
            "sub_scores": [],
            "source": "extraction",
        })

    # SAT/ACT from BF — TIER 1: BF-ALWAYS (exact match strategy for scores)
    if has_bf:
        bf_scores = bf_get_test_scores(bf)
        for bf_score in bf_scores:
            # Check if GE already has this test
            existing = [s for s in exam_scores if s["examTest"] == bf_score["test"]]
            if existing:
                # Compare with exact match — BF wins on mismatch
                ge_min = existing[0]["minScore"]
                bf_min = bf_score["minScore"]
                _, _, res, qa_entry = compare_exact_match(ge_min, bf_min, f"{bf_score['test']}_minScore")
                qa["cross_validation"].append(qa_entry)

                # Enrich existing entry with BF data
                existing[0]["averageScore"] = bf_score["averageScore"]
                if bf_score["sub_scores"]:
                    existing[0]["sub_scores"] = bf_score["sub_scores"]
                if res == "mismatch":
                    existing[0]["minScore"] = bf_min
                    existing[0]["minScoreDescription"] = bf_score["minScoreDescription"]
            else:
                # Add new test from BF
                bf_score["source"] = "bigfuture"
                bf_score["isRequired"] = "Unknown"
                exam_scores.append(bf_score)
                qa["field_sources"][f"examScore.{bf_score['test']}"] = "bigfuture"

    program["exam_scores"] = exam_scores

    # ─── C10. Deadlines — TIER 3 DATE PROXIMITY ────────────────────────
    ge_deadlines = ge.get("deadlines") or []
    final_deadlines, deadline_qa = compare_deadlines(ge_deadlines, bf if has_bf else {})
    program["deadlines"] = final_deadlines
    program["applicationDeadlineDescription"] = ge.get("deadline_note")
    qa["deadline_validation"] = {
        "total_md_deadlines": len(ge_deadlines),
        "total_final_deadlines": len(final_deadlines),
        "checks": deadline_qa,
        "verified_count": sum(1 for c in deadline_qa if "verified" in (c.get("resolution") or "")),
        "flagged_count": sum(1 for c in deadline_qa if c.get("flag")),
    }

    # Update intake from final deadlines
    program["intake"] = list(set(
        dl.get("intake") for dl in final_deadlines if dl.get("intake")
    )) or program.get("intake", [])

    # ─── C11. Entry Requirements — TIER 2: GE-ALWAYS ───────────────────
    program["entry_requirements"] = [
        {
            "value": er.get("value"),
            "count": er.get("count", 1),
            "requirementDetail": er.get("detail"),
            "description": None,
        }
        for er in (admission.get("entry_requirements") or [])
    ]
    qa["field_sources"]["entry_requirements"] = "extraction" if program["entry_requirements"] else None

    # ─── C12. Eligibility Criteria — TIER 2: GE-ALWAYS ─────────────────
    program["eligibility_criteria"] = [
        {
            "type": ec.get("type"),
            "criteria": ec.get("criteria"),
            "details": ec.get("details"),
        }
        for ec in (ge.get("eligibility_criteria") or [])
    ]
    program["eligibilityCriteriaDescription"] = None                       # [GE] could generate

    # ─── C13. Application Process — TIER 2: GE-ALWAYS ──────────────────
    program["application_process_steps"] = []   # from GE if available

    # ─── C14. Class Profile & Enrollment ────────────────────────────────
    program["classSize"] = safe_int(class_prof.get("class_size"))
    program["averageAge"] = safe_int(class_prof.get("avg_age"))
    program["averageWorkExperience"] = safe_int(class_prof.get("avg_work_experience_years"))
    program["classProfileDescription"] = None                              # [GE]
    program["studentsDescription"] = None                                  # [GE]

    # TIER 1: BF-ALWAYS for enrollment/demographics
    program["undergradEnrollment"] = safe_int(bf.get("totalUndergraduates")) if has_bf else None
    program["fulltimeEnrollments"] = safe_int(bf.get("fullTimeEnrolled")) if has_bf else None
    program["retentionRate"] = safe_float(bf.get("sophomoreYearReturnPercent")) if has_bf else None
    program["graduationRate"] = safe_float(bf.get("graduationRatePercent") or bf.get("graduationRate")) if has_bf else None
    program["internationalStudentsPercentage"] = safe_float(bf.get("internationalPercent")) if has_bf else \
                                                  safe_float(class_prof.get("international_percentage"))
    qa["field_sources"]["undergradEnrollment"] = "bigfuture" if program["undergradEnrollment"] else None
    qa["field_sources"]["retentionRate"] = "bigfuture" if program["retentionRate"] else None
    qa["field_sources"]["graduationRate"] = "bigfuture" if program["graduationRate"] else None

    # acceptanceRate — TIER 1: BF-ALWAYS, GE fallback
    program["acceptanceRate"] = safe_float(bf.get("acceptanceRate")) if has_bf else \
                                 safe_float(class_prof.get("acceptance_rate"))
    qa["field_sources"]["acceptanceRate"] = "bigfuture" if (has_bf and bf.get("acceptanceRate")) else "extraction"

    # ─── C15. Career Outcomes — TIER 2: GE-ALWAYS ──────────────────────
    program["careerOutComeDescription"] = career.get("description")
    program["averageBaseSalary"] = safe_float(career.get("avg_salary"))
    program["medianBaseSalary"] = safe_float(career.get("median_salary"))
    program["jobPlacementPercentage"] = safe_float(career.get("job_placement_rate"))
    qa["field_sources"]["careerOutComeDescription"] = "extraction" if program["careerOutComeDescription"] else None

    # ─── C16. Work Experience — TIER 2: GE-ALWAYS ──────────────────────
    program["work_experience_in_months"] = safe_int(admission.get("work_experience_months"))
    program["work_experience_description"] = admission.get("work_experience_details")

    # ─── C17. Course Structure — TIER 2: GE-ALWAYS ─────────────────────
    program["course_structure_data"] = ge.get("course_structure_data") or {}
    qa["field_sources"]["course_structure_data"] = "extraction" if program["course_structure_data"] else None

    # ─── C18. Faculty — TIER 2: GE-ALWAYS ──────────────────────────────
    program["faculty_data"] = ge.get("faculty") or []
    qa["field_sources"]["faculty_data"] = "extraction" if program["faculty_data"] else None

    # ─── C19. Miscellaneous ─────────────────────────────────────────────
    program["cost_of_living_index"] = None                                 # [GE]
    program["view_priority"] = 10                                          # [MANUAL] default
    program["isVerified"] = False                                          # set True after QA review
    program["isActive"] = True
    program["source_file_path"] = md_filename

    # ─── C20. FAQ — TIER 2: GE-ALWAYS ──────────────────────────────────
    program["faqs"] = []   # could be generated by Gemini separately

    # ─── C21. PhD / Job-specific — passthrough ──────────────────────────
    program["research_areas"] = ge.get("research_areas") or []
    program["position_type"] = None
    program["funding_type"] = None
    program["job_email"] = None
    program["job_emails"] = []
    program["job_telephone"] = None
    program["duration"] = safe_int(ge.get("duration_months"))
    program["application_type"] = None
    program["application_process"] = None
    program["job_deadline"] = None

    # BigFuture extra data removed — raw files at bigfuture_data/raw/ if needed

    # ══════════════════════════════════════════════════════════════════════
    #  QA SUMMARY
    # ══════════════════════════════════════════════════════════════════════

    # Count field coverage — exclude system/manual/PhD-only fields for accurate %
    _SKIP_FIELDS = {
        # System fields
        "type", "is_job_role", "isActive", "isVerified", "view_priority", "source_file_path",
        "slug",
        # Manual/Rank fields (can't fill from MD/BF)
        "logo", "shortLogo", "galleryImages", "shortName",
        "globalRank", "qsRank", "qsRankYear", "thRank", "thRankYear", "usnRank", "usnRankYear",
        "programRank", "greSchoolCode", "gmatSchoolCode",
        "roi_score", "cost_of_living_index",
        # PhD/Job-only fields (irrelevant for UG)
        "position_type", "funding_type", "job_email", "job_emails", "job_telephone",
        "job_deadline", "application_type", "application_process", "research_areas",
        "expected_research_output_responsibilities",
    }
    filled_count = 0
    total_fillable = 0
    for section in [university, program]:
        for k, v in section.items():
            if k in _SKIP_FIELDS:
                continue
            total_fillable += 1
            if v is not None and v != "" and v != [] and v != {} and v is not False:
                filled_count += 1

    qa["summary"] = {
        "total_fillable_fields": total_fillable,
        "filled_fields": filled_count,
        "fill_rate_pct": round(filled_count / total_fillable * 100, 1) if total_fillable > 0 else 0,
        "bf_enriched_fields": sum(1 for v in qa["field_sources"].values() if v == "bigfuture"),
        "ge_sourced_fields": sum(1 for v in qa["field_sources"].values() if v == "extraction"),
        "tier3_comparisons": len(qa["cross_validation"]),
        "tier3_verified": sum(1 for c in qa["cross_validation"] if c.get("resolution") == "verified"),
        "tier3_discrepancies": sum(1 for c in qa["cross_validation"]
                                   if "discrepancy" in (c.get("resolution") or "") or c.get("resolution") == "mismatch"),
    }

    # ══════════════════════════════════════════════════════════════════════
    #  ASSEMBLE FINAL OUTPUT
    # ══════════════════════════════════════════════════════════════════════

    final = {
        "university_id": university_id,
        "program_id": program_id,
        "university": university,
        "program": program,
        "lookups": lookups,
    }

    return final, qa


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN — PROCESS ALL FILES
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Merge extracted JSON + BigFuture into final course data")
    parser.add_argument("--file", type=str, help="Process a single extracted JSON file")
    parser.add_argument("--max", type=int, default=0, help="Max files to process (0 = all)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be merged, don't write files")
    parser.add_argument("--extracted-dir", type=str, default=EXTRACTED_DIR)
    parser.add_argument("--bf-dir", type=str, default=BIGFUTURE_RAW_DIR)
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR)
    args = parser.parse_args()

    # Load BigFuture match mapping
    bf_match = load_json(BF_MATCH_FILE)
    if not bf_match:
        print("[WARN] No BigFuture match file found. Proceeding without BF enrichment.")

    # Find extracted files
    if args.file:
        extracted_files = [args.file]
    else:
        extracted_files = sorted(
            str(p) for p in Path(args.extracted_dir).glob("*.json")
            if not p.name.startswith("_")   # skip _summary, _progress files
        )

    if args.max > 0:
        extracted_files = extracted_files[:args.max]

    print(f"Found {len(extracted_files)} extracted files to merge")
    print(f"BigFuture match mapping: {len(bf_match)} universities")

    if args.dry_run:
        for f in extracted_files:
            print(f"  Would process: {f}")
        return

    # Create output dirs
    os.makedirs(args.output_dir, exist_ok=True)
    qa_dir = os.path.join(args.output_dir, "qa")
    os.makedirs(qa_dir, exist_ok=True)

    # Process
    stats = {"total": 0, "with_bf": 0, "without_bf": 0, "errors": 0}
    all_qa_summaries = []

    for filepath in extracted_files:
        stats["total"] += 1
        filename = os.path.basename(filepath)

        # Load extracted data
        extracted = load_json(filepath)
        if not extracted:
            stats["errors"] += 1
            continue

        # Determine university folder and find BF match
        uni_name = extracted.get("university_name", "")
        # Try to find BF slug from match file
        bf_slug = None
        bf_data = {}
        for folder, slug in bf_match.items():
            # Match by university name similarity
            if uni_name.lower() in folder.lower() or folder.lower() in uni_name.lower():
                bf_slug = slug
                break

        if bf_slug:
            bf_path = os.path.join(args.bf_dir, f"{bf_slug}.json")
            bf_data = load_json(bf_path)
            if bf_data:
                stats["with_bf"] += 1
            else:
                bf_slug = None
                stats["without_bf"] += 1
        else:
            stats["without_bf"] += 1

        # Determine confidence tier
        md_name = filename.replace(".json", ".md")
        uni_folder = uni_name.lower().replace(" ", "_").replace("-", "_")
        confidence = determine_confidence_tier(md_name, uni_folder)

        # Merge
        try:
            final, qa = merge_single_course(
                extracted, bf_data, confidence, md_name, uni_folder, bf_slug
            )
        except Exception as e:
            print(f"  [ERROR] {filename}: {e}")
            stats["errors"] += 1
            continue

        # Write outputs
        out_name = filename.replace(".json", "_merged.json")
        qa_name = filename.replace(".json", "_qa.json")

        with open(os.path.join(args.output_dir, out_name), "w", encoding="utf-8") as f:
            json.dump(final, f, indent=2, ensure_ascii=False, default=str)

        with open(os.path.join(qa_dir, qa_name), "w", encoding="utf-8") as f:
            json.dump(qa, f, indent=2, ensure_ascii=False, default=str)

        all_qa_summaries.append({
            "file": filename,
            "confidence": confidence,
            "bf_matched": bool(bf_slug),
            **qa.get("summary", {}),
        })

        bf_tag = f"+ BF({bf_slug})" if bf_slug else "no BF"
        fill = qa.get("summary", {}).get("fill_rate_pct", 0)
        print(f"  {filename} [{confidence}] {bf_tag} — {fill}% filled")

    # Final summary
    print(f"\n{'='*60}")
    print(f"MERGE SUMMARY")
    print(f"{'='*60}")
    print(f"Total processed: {stats['total']}")
    print(f"With BigFuture:  {stats['with_bf']}")
    print(f"Without BF:      {stats['without_bf']}")
    print(f"Errors:          {stats['errors']}")

    if all_qa_summaries:
        avg_fill = sum(s.get("fill_rate_pct", 0) for s in all_qa_summaries) / len(all_qa_summaries)
        avg_bf_fields = sum(s.get("bf_enriched_fields", 0) for s in all_qa_summaries) / len(all_qa_summaries)
        verified = sum(s.get("tier3_verified", 0) for s in all_qa_summaries)
        discrepancies = sum(s.get("tier3_discrepancies", 0) for s in all_qa_summaries)
        print(f"\nAverage fill rate: {avg_fill:.1f}%")
        print(f"Avg BF-enriched fields per course: {avg_bf_fields:.1f}")
        print(f"Tier 3 cross-validations: {verified} verified, {discrepancies} discrepancies")

    # Save batch summary
    summary_path = os.path.join(args.output_dir, "_merge_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "stats": stats,
            "courses": all_qa_summaries,
        }, f, indent=2)
    print(f"\nSummary saved to: {summary_path}")


if __name__ == "__main__":
    main()
