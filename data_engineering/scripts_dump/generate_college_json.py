#!/usr/bin/env python3
"""
generate_college_json.py — Generate college-level JSON from course merged data + BigFuture
============================================================================================

Reads all course merged JSONs per university folder, loads BigFuture raw data,
and produces one college-level JSON per university matching college_model.py schema.

Usage:
  python generate_college_json.py                                    # all universities
  python generate_college_json.py --university Pennsylvania_State_University  # one
  python generate_college_json.py --dry-run                          # preview

Reference: college_model.py, college_field_requirements_report.txt
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────

COURSE_MERGED_DIR = "university_data/merge_test_output_v3"
BIGFUTURE_RAW_DIR = "bigfuture_data/raw"
BF_MATCH_FILE = "ug_bigfuture_match.json"
COLLEGE_OUTPUT_DIR = "university_data/college_output_ug"
UG_CSV_FILE = "ug_programs_data_2026-03-30T18_31_17.92687006+05_30.csv"


# ── Helpers ─────────────────────────────────────────────────────────────────

def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {}


def safe_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def safe_int(val):
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def avg_non_null(values):
    """Average of non-None values. Returns None if no values."""
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 2) if clean else None


def union_strings(list_of_lists):
    """Flatten and deduplicate string lists, preserving order."""
    seen = set()
    result = []
    for lst in list_of_lists:
        for item in (lst or []):
            name = item.get("name", item) if isinstance(item, dict) else item
            if name and name not in seen:
                seen.add(name)
                result.append(name)
    return result


def most_common(values):
    """Most common non-None value."""
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return Counter(clean).most_common(1)[0][0]


def best_text(values):
    """Longest non-null text."""
    clean = [v for v in values if v]
    if not clean:
        return None
    return max(clean, key=len)


def load_university_id_map(csv_path):
    """Load CSV to get university_id by university name."""
    import csv
    uni_ids = {}
    if not os.path.exists(csv_path):
        return uni_ids
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            uni = (row.get("University Name") or "").strip()
            uid = (row.get("university_id") or "").strip().replace(",", "")
            if uni and uid:
                uni_ids[uni.lower()] = uid
    return uni_ids


# ── BigFuture Extractors ───────────────────────────────────────────────────

def bf_get_address(bf):
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


def bf_get_short_address(bf):
    parts = []
    if bf.get("city"):
        parts.append(bf["city"])
    if bf.get("stateName"):
        parts.append(bf["stateName"])
    return ", ".join(parts) if parts else None


def bf_get_school_type(bf):
    inst_types = bf.get("institutionTypes") or []
    for it in inst_types:
        desc = (it.get("institutionTypeDescription") or "").lower()
        if "public" in desc:
            return "Public"
        if "private" in desc:
            return "Private"
    if bf.get("privateTuition") and not bf.get("outOfStateTuition"):
        return "Private"
    if bf.get("outOfStateTuition"):
        return "Public"
    return None


def bf_get_tuition(bf):
    return safe_float(bf.get("privateTuition")) or safe_float(bf.get("outOfStateTuition")) or safe_float(bf.get("inStateTuition"))


def bf_get_living_cost(bf):
    housing = safe_float(bf.get("averageHousingCost")) or safe_float(bf.get("averageHousingCostForCampusLife")) or 0
    books = safe_float(bf.get("booksAndSuppliesCost")) or 0
    transport = safe_float(bf.get("transportationCosts")) or 0
    personal = safe_float(bf.get("estimatedPersonalExpenses")) or 0
    total = housing + books + transport + personal
    return total if total > 0 else None


def bf_get_demographics(bf):
    fields = [
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
    for field, label in fields:
        val = safe_float(bf.get(field))
        if val is not None and val > 0:
            rows.append({"label": label, "value": round(val, 1)})
    return rows


def bf_get_tags(bf):
    tags = []
    if bf.get("specializedSchoolHistoricallyBlackInd") == "Y":
        tags.append("HBCU")
    if bf.get("specializedSchoolHispanicServingInd") == "Y":
        tags.append("Hispanic-Serving")
    if bf.get("specializedSchoolWomensCollegeInd") == "Y":
        tags.append("Women's College")
    if bf.get("specializedSchoolTribalCollegeInd") == "Y":
        tags.append("Tribal College")
    return tags


def bf_get_admission_policy(bf):
    parts = []
    for field, label in [("highSchoolGpa", "GPA"), ("highSchoolRank", "Rank"),
                          ("prepCourses", "Prep Courses"), ("recommendations", "Recommendations")]:
        val = bf.get(field)
        if val:
            parts.append(f"{label}: {val}")
    return "; ".join(parts) if parts else None


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN: BUILD COLLEGE JSON
# ══════════════════════════════════════════════════════════════════════════════

def build_college_json(uni_folder, courses, bf, university_id=None):
    """
    Build college-level JSON from list of course merged JSONs + BigFuture raw.
    Returns: college dict matching college_model.py schema.
    """
    has_bf = bool(bf)

    # Extract data arrays from all courses for aggregation
    programs = [c.get("program", {}) for c in courses]
    lookups_list = [c.get("lookups", {}) for c in courses]
    uni_data = courses[0].get("university", {}) if courses else {}

    # ══════════════════════════════════════════════════════════════════════
    #  A) COLLEGE MODEL (line 29-62)
    # ══════════════════════════════════════════════════════════════════════

    college = {}

    # Identity
    college["name"] = bf.get("name") if has_bf else uni_data.get("name") or uni_folder.replace("_", " ")
    college["university_id"] = university_id
    college["collegeType"] = "UG_COLLEGE"

    # Location — DIRECT from BF
    college["address"] = bf_get_short_address(bf) if has_bf else None
    college["fullAddress"] = bf_get_address(bf) if has_bf else uni_data.get("address")
    college["phoneNumber"] = bf.get("contactPhoneFormatted") if has_bf else None
    college["schoolType"] = bf_get_school_type(bf) if has_bf else uni_data.get("type")

    # Admissions — DIRECT from BF
    college["acceptanceRate"] = safe_float(bf.get("acceptanceRate")) if has_bf else None

    # Yield rate — CALCULATED
    admitted = safe_int(bf.get("admittedApplicants")) if has_bf else None
    enrolled = safe_int(bf.get("enrolledApplicants")) if has_bf else None
    if admitted and enrolled and admitted > 0:
        college["yieldRate"] = round(enrolled / admitted * 100, 2)
    else:
        college["yieldRate"] = None

    # Majors — AGGREGATED (union of all courses' major names)
    all_majors = []
    for lk in lookups_list:
        cm = lk.get("courseMajor")
        if cm and isinstance(cm, dict) and cm.get("name"):
            if cm["name"] not in all_majors:
                all_majors.append(cm["name"])
    college["majors"] = all_majors

    # Overview — DIRECT from BF or best from courses
    college["overview_description"] = bf.get("description") if has_bf and bf.get("description") else \
        best_text([p.get("overviewDescription") for p in programs])

    # Document requirements — AGGREGATED (union across courses)
    college["document_requirements"] = union_strings(
        [p.get("entryRequirementsTags") for p in programs]
    )

    # Application fee — AGGREGATED (avg)
    app_fees = [safe_float(p.get("applicationFee")) for p in programs]
    college["application_fee"] = avg_non_null(app_fees)

    # Tuition — DIRECT from BF (international-facing)
    bf_tuition = bf_get_tuition(bf) if has_bf else None
    course_tuitions = [safe_float(p.get("totalTuitionFeePerYear")) for p in programs]
    college["tuition_fee_per_year"] = bf_tuition or avg_non_null(course_tuitions)
    college["total_tuition_fee_per_year"] = college["tuition_fee_per_year"]

    # Total tuition — CALCULATED (per_year * 4)
    if college["tuition_fee_per_year"]:
        college["tuition_fee"] = round(college["tuition_fee_per_year"] * 4, 2)
        college["total_tuition_fee"] = college["tuition_fee"]
    else:
        college["tuition_fee"] = None
        college["total_tuition_fee"] = None

    # Health insurance — AGGREGATED
    college["health_insurance_cost_per_year"] = avg_non_null(
        [safe_float((p.get("admission_requirements_data") or {}).get("health_insurance_fee"))
         for p in programs]
    )

    # Links — DIRECT from BF
    college["application_fee_page_link"] = (bf.get("applicationSiteUrl") or bf.get("commonApplicationUrl")) if has_bf else None
    college["additional_info_page_link"] = bf.get("schoolUrl") if has_bf else None

    # Recruiters — AGGREGATED (union)
    college["recruiters"] = union_strings([lk.get("recruiters") for lk in lookups_list])

    # Career — AGGREGATED
    college["career_outcome_description"] = best_text(
        [p.get("careerOutComeDescription") for p in programs]
    )
    college["avg_earning_per_year"] = avg_non_null(
        [safe_float(p.get("averageBaseSalary")) for p in programs]
    )
    college["job_placement_rate"] = avg_non_null(
        [safe_float(p.get("jobPlacementPercentage")) for p in programs]
    )

    # Graduation rate — DIRECT from BF
    college["graduation_rate"] = safe_float(bf.get("graduationRatePercent") or bf.get("graduationRate")) if has_bf else None

    # Job roles — AGGREGATED (union)
    college["job_roles"] = union_strings([lk.get("prospectiveJobRoles") for lk in lookups_list])

    # ══════════════════════════════════════════════════════════════════════
    #  B) COLLEGE APPLICATION DEADLINES (line 65-71)
    # ══════════════════════════════════════════════════════════════════════

    deadlines = []
    if has_bf:
        for dtype, bf_field in [("Early Decision", "earlyDecisionDate"),
                                 ("Early Action", "earlyActionDate"),
                                 ("Regular Decision", "regularDecisionDate")]:
            val = bf.get(bf_field)
            if val:
                deadlines.append({"deadline_type": dtype, "deadline_date": val})
    college["application_deadlines"] = deadlines

    # ══════════════════════════════════════════════════════════════════════
    #  C) APPLICATION REQUIREMENTS (line 73-84)
    # ══════════════════════════════════════════════════════════════════════

    app_reqs = []

    # SAT/ACT — AGGREGATED (most common policy + BF score ranges)
    sat_policies = [p.get("sat_required") for p in programs if p.get("sat_required")]
    sat_policy = most_common(sat_policies) or "Unknown"
    sat_range = ""
    if has_bf:
        s25 = safe_int(bf.get("satCompositeScore25thPercentile"))
        s75 = safe_int(bf.get("satCompositeScore75thPercentile"))
        a25 = safe_int(bf.get("actCompositeScore25thPercentile"))
        a75 = safe_int(bf.get("actCompositeScore75thPercentile"))
        parts = []
        if s25 and s75:
            parts.append(f"SAT: {s25}-{s75}")
        if a25 and a75:
            parts.append(f"ACT: {a25}-{a75}")
        sat_range = ". ".join(parts)
    detail = f"{sat_policy}. {sat_range}" if sat_range else sat_policy
    app_reqs.append({"requirement": "SAT_ACT", "requirement_detail": detail})

    # Application fee
    if college["application_fee"]:
        fee_detail = f"${college['application_fee']:.0f}"
        if has_bf and bf.get("feeWaiverIndicator") == "Y":
            fee_detail += ". Fee waiver available."
        app_reqs.append({"requirement": "APPLICATION_FEE", "requirement_detail": fee_detail})

    # SOP / Personal Statement — AGGREGATED
    all_entry_reqs = union_strings([p.get("entryRequirementsTags") for p in programs])
    sop_tags = [t for t in all_entry_reqs if t in ("COMMON_APP_ESSAY", "SUPPLEMENTAL_ESSAY", "PERSONAL_STATEMENT_SOP", "MASTER_SOP")]
    if sop_tags:
        app_reqs.append({"requirement": "PERSONAL_STATEMENT_SOP",
                          "requirement_detail": ", ".join(sop_tags)})

    # LOR — AGGREGATED
    lor_tags = [t for t in all_entry_reqs if "LOR" in t or "REC" in t or t == "COUNSELOR_REC" or t == "ACADEMIC_LOR"]
    if lor_tags:
        # Get most common LOR count across courses
        lor_counts = []
        for p in programs:
            for er in (p.get("entry_requirements") or []):
                if er.get("value") in ("ACADEMIC_LOR", "COUNSELOR_REC", "GENERAL_LOR"):
                    lor_counts.append(er.get("count", 1))
        total_lors = most_common(lor_counts) or 1
        app_reqs.append({"requirement": "LOR",
                          "requirement_detail": f"{total_lors} recommendation(s)"})

    # English proficiency — AGGREGATED (avg min scores)
    toefl_scores = []
    ielts_scores = []
    for p in programs:
        for test in ((p.get("admission_requirements_data") or {}).get("english_tests") or []):
            score = safe_float(test.get("min_score"))
            if test.get("test") == "TOEFL" and score:
                toefl_scores.append(score)
            elif test.get("test") == "IELTS" and score:
                ielts_scores.append(score)
    eng_parts = []
    if toefl_scores:
        eng_parts.append(f"TOEFL: {avg_non_null(toefl_scores):.0f}")
    if ielts_scores:
        eng_parts.append(f"IELTS: {avg_non_null(ielts_scores):.1f}")
    if eng_parts:
        app_reqs.append({"requirement": "ENGLISH_PROFICIENCY_IELTS",
                          "requirement_detail": ", ".join(eng_parts)})

    # Application portal
    portal = bf.get("commonApplicationUrl") if has_bf else None
    platforms = []
    for p in programs:
        for plat in (p.get("application_platforms") or []):
            if plat not in platforms:
                platforms.append(plat)
    if portal or platforms:
        detail = ", ".join(platforms) if platforms else portal
        app_reqs.append({"requirement": "APPLICATION_PORTAL", "requirement_detail": detail})

    # Class 9 / GPA
    gpa_values = [(p.get("admission_requirements_data") or {}).get("min_gpa") for p in programs]
    gpa_values = [g for g in gpa_values if g]
    if gpa_values:
        app_reqs.append({"requirement": "CLASS_9_SCORE",
                          "requirement_detail": most_common(gpa_values)})

    college["application_requirements"] = app_reqs

    # ══════════════════════════════════════════════════════════════════════
    #  D) COLLEGE METADATA (line 88-165)
    # ══════════════════════════════════════════════════════════════════════

    meta = {}

    # --- Admissions & Applicants ---
    meta["totalYears"] = 4.0
    meta["acceptanceRateWomen"] = None          # [NONE] BF doesn't split by gender
    meta["acceptanceRateMen"] = None            # [NONE]
    meta["totalApplicants"] = safe_int(bf.get("totalApplicants")) if has_bf else None
    meta["percentageWomenApplicants"] = None    # [NONE]
    meta["percentageMenApplicants"] = None      # [NONE]
    meta["admissionWebsite"] = bf.get("applicationSiteUrl") if has_bf else None
    meta["fulltimeEnrollments"] = safe_int(bf.get("fullTimeEnrolled")) if has_bf else None
    meta["admissionPolicy"] = bf_get_admission_policy(bf) if has_bf else None
    meta["internationalStudents"] = safe_float(bf.get("internationalPercent")) if has_bf else None
    meta["applicationDeadline"] = bf.get("regularDecisionDate") if has_bf else None
    meta["applicationEntryRequirements"] = all_entry_reqs

    # --- Costs & Financial ---
    meta["currency"] = "USD" if has_bf else "USD"
    meta["currencySymbol"] = "$"
    bf_living = bf_get_living_cost(bf) if has_bf else None
    meta["totalCost"] = None
    if bf_tuition and bf_living:
        meta["totalCost"] = round(bf_tuition + bf_living, 2)
    meta["inStateCost"] = safe_float(bf.get("inStateTuition")) if has_bf else None
    meta["outOfStateCost"] = str(safe_float(bf.get("outOfStateTuition"))) if has_bf and bf.get("outOfStateTuition") else None
    meta["medianSalary"] = None
    median_salaries = [safe_float(p.get("medianBaseSalary")) for p in programs]
    ms = avg_non_null(median_salaries)
    if ms:
        meta["medianSalary"] = str(int(ms))
    meta["livingCost"] = str(int(bf_living)) if bf_living else None
    meta["inStateTuitionFees"] = safe_float(bf.get("inStateTuition")) if has_bf else None
    meta["inStateFees"] = None   # BF doesn't split tuition vs fees
    meta["outOfStateTuition"] = safe_float(bf.get("outOfStateTuition")) if has_bf else None
    meta["outOfStateFees"] = None
    meta["roomAndBoardFees"] = safe_float(bf.get("averageHousingCost")) if has_bf else None
    meta["pellGrants"] = None                   # [NONE]
    meta["percentGraduatesAwardedLoans"] = None # [NONE]
    meta["avgAmountAwarded"] = safe_float(bf.get("averageAidAwarded")) if has_bf else None

    # --- Graduation & Outcomes ---
    meta["fourYearGradRate"] = safe_float(bf.get("graduationRatePercent")) if has_bf else None
    meta["sixYearGradRate"] = None              # [NONE] BF has one rate
    meta["firstyearEnrolledStudents"] = safe_int(bf.get("enrolledApplicants")) if has_bf else None
    meta["retentionRate"] = safe_float(bf.get("sophomoreYearReturnPercent")) if has_bf else None
    meta["graduationRate"] = safe_float(bf.get("graduationRatePercent")) if has_bf else None
    meta["jobPlacementRate"] = avg_non_null(
        [safe_float(p.get("jobPlacementPercentage")) for p in programs]
    )

    # --- Demographics & Enrollment ---
    meta["studentDiversityType"] = None
    demographics = bf_get_demographics(bf) if has_bf else []
    if demographics:
        # Most common group (excluding Unknown/Other)
        top = max([d for d in demographics if "Unknown" not in d["label"]], key=lambda d: d["value"], default=None)
        meta["studentDiversityType"] = top["label"] if top else None
    meta["studentDiversity"] = demographics

    meta["womenEnrolled"] = None                # [NONE]
    meta["menEnrolled"] = None                  # [NONE]
    meta["studentFacultyRatio"] = f"{bf.get('studentFacultyRatio')}:1" if has_bf and bf.get("studentFacultyRatio") else None
    meta["calendarSystem"] = None               # [NONE]
    meta["totalGraduateStudents"] = safe_float(bf.get("totalGraduates")) if has_bf else None
    meta["partTimeGraduateStudents"] = None     # [NONE]
    meta["researchAssistants"] = None           # [NONE]
    meta["teachingAssistants"] = None           # [NONE]

    # --- Test Scores ---
    sat25 = safe_int(bf.get("satCompositeScore25thPercentile")) if has_bf else None
    sat75 = safe_int(bf.get("satCompositeScore75thPercentile")) if has_bf else None
    act25 = safe_int(bf.get("actCompositeScore25thPercentile")) if has_bf else None
    act75 = safe_int(bf.get("actCompositeScore75thPercentile")) if has_bf else None

    meta["avgSATScore"] = str((sat25 + sat75) // 2) if sat25 and sat75 else None
    meta["avgACTScore"] = str((act25 + act75) // 2) if act25 and act75 else None
    meta["satRange"] = f"{sat25}-{sat75}" if sat25 and sat75 else None
    meta["actRange"] = f"{act25}-{act75}" if act25 and act75 else None
    meta["satMathScoreRange"] = None
    meta["satReadingWritingRange"] = None
    if has_bf:
        m25 = safe_int(bf.get("rsatMathScore25thPercentile"))
        m75 = safe_int(bf.get("rsatMathScore75thPercentile"))
        if m25 and m75:
            meta["satMathScoreRange"] = f"{m25}-{m75}"
        e25 = safe_int(bf.get("rsatEbrwScore25thPercentile"))
        e75 = safe_int(bf.get("rsatEbrwScore75thPercentile"))
        if e25 and e75:
            meta["satReadingWritingRange"] = f"{e25}-{e75}"
    meta["actMathScoreRange"] = None            # [NONE] BF only has composite ACT
    meta["actReadingWritingRange"] = None        # [NONE]
    meta["studentsSubmittingSAT"] = None         # [NONE]
    meta["percentSATSubmitted"] = None           # [NONE]
    meta["percentACTSubmitted"] = None           # [NONE]

    # --- Labels & Offerings ---
    meta["labels"] = bf_get_tags(bf) if has_bf else []
    meta["specialAcademicOfferings"] = [
        s.get("studyOptionDescription") for s in (bf.get("studyOptions") or [])
    ] if has_bf else []

    # --- Funding ---
    meta["scholarshipProviders"] = union_strings(
        [lk.get("scholarshipsProviders") for lk in lookups_list]
    )
    meta["fundingOptionsTags"] = union_strings(
        [p.get("fundingOptionsTags") for p in programs]
    )
    meta["scholarshipDescription"] = best_text(
        [p.get("scholarshipsDetails") for p in programs]
    )

    # --- Descriptions (7 text fields) ---
    meta["overviewDescription"] = bf.get("description") if has_bf and bf.get("description") else \
        best_text([p.get("overviewDescription") for p in programs])

    # Generate summary descriptions from data
    meta["admissionsDescription"] = None
    adm_parts = []
    if college["acceptanceRate"]:
        adm_parts.append(f"Acceptance rate: {college['acceptanceRate']}%")
    if meta["satRange"]:
        adm_parts.append(f"SAT range: {meta['satRange']}")
    if meta["actRange"]:
        adm_parts.append(f"ACT range: {meta['actRange']}")
    if sat_policy and sat_policy != "Unknown":
        adm_parts.append(f"Test policy: {sat_policy}")
    if adm_parts:
        meta["admissionsDescription"] = ". ".join(adm_parts) + "."

    meta["costDescription"] = None
    cost_parts = []
    if college["tuition_fee_per_year"]:
        cost_parts.append(f"Tuition: ${college['tuition_fee_per_year']:,.0f}/year")
    if bf_living:
        cost_parts.append(f"Estimated living costs: ${bf_living:,.0f}/year")
    if meta["avgAmountAwarded"]:
        cost_parts.append(f"Average aid awarded: ${meta['avgAmountAwarded']:,.0f}")
    if cost_parts:
        meta["costDescription"] = ". ".join(cost_parts) + "."

    meta["applicationRequirementsDescription"] = None
    areq_parts = []
    platforms_str = ", ".join(platforms) if platforms else None
    if platforms_str:
        areq_parts.append(f"Apply via {platforms_str}")
    lor_detail = next((r["requirement_detail"] for r in app_reqs if r["requirement"] == "LOR"), None)
    if lor_detail:
        areq_parts.append(lor_detail)
    if areq_parts:
        meta["applicationRequirementsDescription"] = ". ".join(areq_parts) + "."

    meta["academicsDescription"] = None
    acad_parts = []
    if all_majors:
        acad_parts.append(f"{len(all_majors)} majors offered")
    if meta["studentFacultyRatio"]:
        acad_parts.append(f"Student-faculty ratio: {meta['studentFacultyRatio']}")
    if meta["specialAcademicOfferings"]:
        acad_parts.append(f"Offerings: {', '.join(meta['specialAcademicOfferings'][:5])}")
    if acad_parts:
        meta["academicsDescription"] = ". ".join(acad_parts) + "."

    meta["studentsDescription"] = None
    stud_parts = []
    if has_bf and bf.get("totalUndergraduates"):
        stud_parts.append(f"{bf['totalUndergraduates']:,} undergraduates")
    if meta["internationalStudents"]:
        stud_parts.append(f"{meta['internationalStudents']}% international students")
    if meta["studentFacultyRatio"]:
        stud_parts.append(f"Student-faculty ratio: {meta['studentFacultyRatio']}")
    if stud_parts:
        meta["studentsDescription"] = ". ".join(stud_parts) + "."

    meta["fundingDescription"] = None
    fund_parts = []
    if has_bf and bf.get("studentsReceivingAidPercent"):
        fund_parts.append(f"{bf['studentsReceivingAidPercent']}% of students receive financial aid")
    if meta["avgAmountAwarded"]:
        fund_parts.append(f"Average award: ${meta['avgAmountAwarded']:,.0f}")
    if fund_parts:
        meta["fundingDescription"] = ". ".join(fund_parts) + "."

    meta["afterCollegeDescription"] = None
    after_parts = []
    if meta["graduationRate"]:
        after_parts.append(f"{meta['graduationRate']}% graduation rate")
    if college["job_placement_rate"]:
        after_parts.append(f"{college['job_placement_rate']}% job placement rate")
    if college["avg_earning_per_year"]:
        after_parts.append(f"Average earnings: ${college['avg_earning_per_year']:,.0f}/year")
    if after_parts:
        meta["afterCollegeDescription"] = ". ".join(after_parts) + "."

    meta["graduateStudentsDescription"] = None
    if meta["totalGraduateStudents"]:
        meta["graduateStudentsDescription"] = f"{int(meta['totalGraduateStudents']):,} graduate students enrolled."

    college["metadata"] = meta

    # ══════════════════════════════════════════════════════════════════════
    #  DATA QUALITY FLAG — computed last, but placed first in output
    # ══════════════════════════════════════════════════════════════════════

    # Count filled fields
    skip_keys = {"metadata", "application_deadlines", "application_requirements",
                 "data_quality", "university_id", "collegeType"}
    filled = 0
    total_fields = 0
    for k, v in college.items():
        if k in skip_keys:
            continue
        total_fields += 1
        if v is not None and v != "" and v != [] and v != {}:
            filled += 1
    for k, v in meta.items():
        total_fields += 1
        if v is not None and v != "" and v != [] and v != {}:
            filled += 1

    fill_pct = round(filled / total_fields * 100, 1) if total_fields > 0 else 0

    # Tier logic
    has_sat_act = bool(meta.get("satRange") or meta.get("actRange"))
    has_demographics = bool(meta.get("studentDiversity"))
    has_tuition = bool(college.get("tuition_fee_per_year"))
    has_deadlines = len(deadlines) > 0

    if has_bf and fill_pct >= 60:
        tier = "high"
    elif fill_pct >= 40:
        tier = "medium"
    else:
        tier = "low"

    data_quality = {
        "tier": tier,
        "sources": [s for s in (["markdown"] if courses else []) + (["bigfuture"] if has_bf else [])],
        "courses_count": len(courses),
        "fields_filled": filled,
        "fields_total": total_fields,
        "fill_rate_pct": fill_pct,
        "has_bigfuture": has_bf,
        "has_sat_act": has_sat_act,
        "has_demographics": has_demographics,
        "has_tuition": has_tuition,
        "has_deadlines": has_deadlines,
    }

    # Rebuild college dict with data_quality at the top
    ordered = {"data_quality": data_quality}
    ordered.update(college)
    return ordered


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate college-level JSON from course data + BigFuture")
    parser.add_argument("--university", type=str, help="Process single university folder name")
    parser.add_argument("--course-dir", type=str, default=COURSE_MERGED_DIR)
    parser.add_argument("--output-dir", type=str, default=COLLEGE_OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Load BF match
    bf_match = load_json(BF_MATCH_FILE)

    # Load university IDs from CSV
    uni_id_map = load_university_id_map(UG_CSV_FILE)

    # Find university folders
    if args.university:
        uni_folders = [args.university]
    else:
        uni_folders = sorted(
            d for d in os.listdir(args.course_dir)
            if os.path.isdir(os.path.join(args.course_dir, d))
        )

    print(f"Found {len(uni_folders)} university folders")
    os.makedirs(args.output_dir, exist_ok=True)

    if args.dry_run:
        for u in uni_folders:
            print(f"  Would process: {u}")
        return

    stats = {"total": 0, "with_bf": 0, "errors": 0, "high": 0, "medium": 0, "low": 0}

    for uni_folder in uni_folders:
        stats["total"] += 1
        uni_dir = os.path.join(args.course_dir, uni_folder)

        # Load all course merged JSONs
        course_files = sorted(f for f in os.listdir(uni_dir) if f.endswith("_merged.json"))
        if not course_files:
            continue

        courses = []
        for cf in course_files:
            c = load_json(os.path.join(uni_dir, cf))
            if c:
                courses.append(c)

        if not courses:
            stats["errors"] += 1
            continue

        # Find BF data
        bf_slug = bf_match.get(uni_folder)
        bf_data = {}
        if bf_slug:
            bf_data = load_json(os.path.join(BIGFUTURE_RAW_DIR, f"{bf_slug}.json"))
            if bf_data:
                stats["with_bf"] += 1

        # Get university_id
        uni_name = courses[0].get("university", {}).get("name", uni_folder.replace("_", " "))
        university_id = courses[0].get("university_id") or uni_id_map.get(uni_name.lower())

        # Build college JSON
        try:
            college = build_college_json(uni_folder, courses, bf_data, university_id)
        except Exception as e:
            print(f"  [ERROR] {uni_folder}: {e}")
            stats["errors"] += 1
            continue

        # Track tier
        tier = college.get("data_quality", {}).get("tier", "low")
        stats[tier] = stats.get(tier, 0) + 1

        # Save
        out_path = os.path.join(args.output_dir, f"{uni_folder}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(college, f, indent=2, ensure_ascii=False, default=str)

    print(f"\nDone: {stats['total']} universities, {stats['with_bf']} with BigFuture, {stats['errors']} errors")
    print(f"Quality: {stats['high']} high, {stats['medium']} medium, {stats['low']} low")
    print(f"Output: {args.output_dir}/")


if __name__ == "__main__":
    main()
