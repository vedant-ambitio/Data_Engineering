"""
Generate dashboard statistics for PhD and Masters separately.
Reads classification reports and produces two txt files with updated numbers.
Verified = high_confidence + safe_to_present_as_official + mostly_reliable (combined)
"""

import json
import sys
import io
import hashlib
from pathlib import Path
from collections import Counter

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
OFFICIAL_DIR = BASE / "official_urls"
PAGES_DIR = OFFICIAL_DIR / "crawled_data" / "pages"


def url_hash(url):
    return hashlib.md5(url.encode()).hexdigest()[:12]


def calc_stats(report_path, section_urls_path, md_base_dir, program_type):
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)
    with open(section_urls_path, encoding="utf-8") as f:
        section_urls = json.load(f)

    results = report["results"]
    total_classified = len(results)

    # Total courses and universities in full dataset
    md_base = Path(md_base_dir)
    total_courses_all = sum(1 for _ in md_base.rglob("*.md") if "__MACOSX" not in str(_) and not _.name.startswith("._"))
    total_unis_all = len([d for d in md_base.iterdir() if d.is_dir() and "__MACOSX" not in d.name])

    # Universities in classified set
    classified_unis = set(r["college"] for r in results)

    # Confidence labels
    labels = Counter(r["confidence_label"] for r in results)
    safe = labels.get("safe_to_present_as_official", 0)
    mostly = labels.get("mostly_reliable", 0)
    caveat = labels.get("use_with_caveat", 0)
    review = labels.get("needs_review", 0)
    # Tiers
    tiers = Counter(r["tier"] for r in results)
    high_conf = tiers.get("high_confidence", 0)
    # Verified = safe_to_present + mostly_reliable (these already include all high_confidence files)
    # high_confidence is a TIER, safe/mostly are LABELS — every high_conf file has one of those labels
    verified_combined = safe + mostly  # no double counting

    # Domains
    domains = Counter()
    for r in results:
        fname = r["file"].lower()
        if any(k in fname for k in ["computer", "data_science", "artificial_intelligence", "machine_learning", "software", "information"]):
            domains["CS & AI"] += 1
        elif any(k in fname for k in ["electric", "electronic", "mechanical", "civil", "aerospace", "chemical_engineer", "biomedical_engineer", "material"]):
            domains["Engineering"] += 1
        elif any(k in fname for k in ["physics", "chemistry", "math", "biology", "biochem", "biotech", "neuroscience", "genetics", "molecular"]):
            domains["Natural Sciences"] += 1
        elif any(k in fname for k in ["business", "management", "finance", "accounting", "economics", "marketing"]):
            domains["Business & Economics"] += 1
        elif any(k in fname for k in ["medicine", "health", "pharmaceutical", "clinical", "epidemiology", "cancer", "immunology"]):
            domains["Medicine & Health"] += 1
        elif any(k in fname for k in ["psychology", "education", "sociology", "political", "communication", "law", "philosophy", "history", "language", "english", "literature"]):
            domains["Social Sciences & Humanities"] += 1
        elif any(k in fname for k in ["architecture", "design", "art", "music"]):
            domains["Arts & Design"] += 1
        elif any(k in fname for k in ["environment", "sustain", "energy", "climate", "ocean", "agriculture"]):
            domains["Environment & Sustainability"] += 1
        else:
            domains["Other"] += 1

    # Section stats (only where data exists)
    section_stats = {}
    for sec in ["tuition_and_fees", "application_deadlines", "admission_requirements"]:
        with_data = [r for r in results if r["sections"][sec] != "no_data"]
        v = sum(1 for r in with_data if r["sections"][sec] == "verified")
        p = sum(1 for r in with_data if r["sections"][sec] == "partially_verified")
        fl = sum(1 for r in with_data if r["sections"][sec] == "flagged")
        nd = total_classified - len(with_data)
        trust = (v + p) / len(with_data) * 100 if with_data else 0
        section_stats[sec] = {"with_data": len(with_data), "verified": v, "partial": p, "flagged": fl, "no_data": nd, "trust": trust}

    # Data points
    tuition_matched = sum(r["section_details"]["tuition_and_fees"].get("details", {}).get("matched", 0) for r in results)
    tuition_total = sum(r["section_details"]["tuition_and_fees"].get("details", {}).get("md_amounts", 0) for r in results)
    date_matched = sum(r["section_details"]["application_deadlines"].get("details", {}).get("matched_dates", 0) for r in results)
    date_total = sum(r["section_details"]["application_deadlines"].get("details", {}).get("md_dates_count", 0) for r in results)

    # Entity stats
    entities = ["toefl", "ielts", "duolingo", "pte", "cambridge", "gre_status", "gre_score", "gpa", "app_fee", "lor_count", "work_experience"]
    entity_stats = {}
    total_ent_matched = 0
    for ent in entities:
        m = mm = 0
        for r in results:
            ents = r["section_details"].get("admission_requirements", {}).get("details", {}).get("entities", {})
            s = ents.get(ent, {}).get("status", "")
            if s == "matched": m += 1
            elif s == "mismatched": mm += 1
        entity_stats[ent] = {"matched": m, "mismatched": mm, "total": m + mm, "rate": m / (m + mm) * 100 if (m + mm) > 0 else 0}
        total_ent_matched += m

    total_data_points = tuition_matched + date_matched + total_ent_matched

    # URL coverage
    needed_urls = set()
    for college, files in section_urls.items():
        for fn, fdata in files.items():
            for sec in ["admission_requirements", "tuition_and_fees", "application_deadlines"]:
                needed_urls.update(fdata.get(sec, []))
    crawled_urls = sum(1 for u in needed_urls if (PAGES_DIR / f"{url_hash(u)}.json").exists())

    # Uncrawled courses
    uncrawled = total_courses_all - total_classified

    # Trustable files (confidence >= 60)
    trustable = sum(1 for r in results if r["confidence_score"] >= 60)

    # Alerts
    tuition_flagged = section_stats["tuition_and_fees"]["flagged"]
    toefl_mm = entity_stats["toefl"]["mismatched"]
    ielts_mm = entity_stats["ielts"]["mismatched"]
    toefl_ielts_mm = toefl_mm + ielts_mm
    reliable_with_flagged = sum(1 for r in results if r["confidence_score"] >= 60 and any(r["sections"][s] == "flagged" for s in r["sections"]))
    multi_adm_mm = sum(1 for r in results if sum(1 for e in r["section_details"].get("admission_requirements", {}).get("details", {}).get("entities", {}).values() if e.get("status") == "mismatched") >= 3)
    stale_deadlines = sum(1 for r in results if "stale" in r["section_details"]["application_deadlines"].get("reason", ""))
    past_deadlines = sum(1 for r in results if "past" in r["section_details"]["application_deadlines"].get("reason", ""))
    zero_conf = sum(1 for r in results if r["confidence_score"] == 0)

    # Domain coverage (reliable rate)
    domain_reliable = {}
    for r in results:
        fname = r["file"].lower()
        if any(k in fname for k in ["computer", "data_science", "artificial_intelligence", "machine_learning", "software", "information"]):
            d = "CS & AI"
        elif any(k in fname for k in ["electric", "electronic", "mechanical", "civil", "aerospace", "chemical_engineer", "biomedical_engineer", "material"]):
            d = "Engineering"
        elif any(k in fname for k in ["physics", "chemistry", "math", "biology", "biochem", "biotech", "neuroscience", "genetics", "molecular"]):
            d = "Natural Sciences"
        elif any(k in fname for k in ["business", "management", "finance", "accounting", "economics", "marketing"]):
            d = "Business & Economics"
        elif any(k in fname for k in ["medicine", "health", "pharmaceutical", "clinical", "epidemiology", "cancer", "immunology"]):
            d = "Medicine & Health"
        elif any(k in fname for k in ["psychology", "education", "sociology", "political", "communication", "law", "philosophy", "history", "language", "english", "literature"]):
            d = "Social Sciences & Humanities"
        else:
            d = "Other"
        domain_reliable.setdefault(d, {"total": 0, "reliable": 0})
        domain_reliable[d]["total"] += 1
        if r["confidence_score"] >= 60:
            domain_reliable[d]["reliable"] += 1

    # Avg courses per university
    uni_course_counts = Counter(r["college"] for r in results)
    avg_courses_per_uni = total_classified / len(classified_unis) if classified_unis else 0

    return {
        "program_type": program_type,
        "total_courses_all": total_courses_all,
        "total_unis_all": total_unis_all,
        "total_classified": total_classified,
        "classified_unis": len(classified_unis),
        "avg_courses_per_uni": avg_courses_per_uni,
        "num_domains": len([d for d in domains if domains[d] > 0]),
        "verified_combined": verified_combined,
        "verified_pct": verified_combined / total_classified * 100 if total_classified else 0,
        "high_conf": high_conf,
        "safe": safe,
        "mostly": mostly,
        "caveat": caveat,
        "review": review,
        "needed_urls": len(needed_urls),
        "crawled_urls": crawled_urls,
        "crawl_pct": crawled_urls / len(needed_urls) * 100 if needed_urls else 0,
        "section_stats": section_stats,
        "entity_stats": entity_stats,
        "tuition_matched": tuition_matched,
        "date_matched": date_matched,
        "total_ent_matched": total_ent_matched,
        "total_data_points": total_data_points,
        "trustable": trustable,
        "trustable_pct": trustable / total_classified * 100 if total_classified else 0,
        "not_trustable": total_classified - trustable,
        "uncrawled": uncrawled,
        "tuition_flagged": tuition_flagged,
        "toefl_ielts_mm": toefl_ielts_mm,
        "toefl_mm": toefl_mm,
        "ielts_mm": ielts_mm,
        "reliable_with_flagged": reliable_with_flagged,
        "multi_adm_mm": multi_adm_mm,
        "stale_deadlines": stale_deadlines,
        "past_deadlines": past_deadlines,
        "zero_conf": zero_conf,
        "domain_reliable": domain_reliable,
    }


def make_progress_bar(pct, length=50):
    filled = int(pct / 100 * length)
    return "█" * filled + "░" * (length - filled)


def make_entity_bar(pct, length=48):
    filled = int(pct / 100 * length)
    return "█" * filled + " " * (length - filled)


def generate_txt(s, output_path):
    """Generate dashboard txt file with all numbers filled in."""
    sec = s["section_stats"]
    ent = s["entity_stats"]

    # Sort entities by rate descending
    sorted_ents = sorted(ent.items(), key=lambda x: -x[1]["rate"])

    # Build entity bars text
    entity_lines = ""
    for name, e in sorted_ents:
        if e["total"] == 0:
            continue
        display_name = {
            "toefl": "TOEFL", "ielts": "IELTS", "duolingo": "Duolingo", "pte": "PTE",
            "cambridge": "Cambridge", "gre_status": "GRE Status", "gre_score": "GRE Score",
            "gpa": "GPA", "app_fee": "App Fee", "lor_count": "LOR Count", "work_experience": "Work Exp"
        }.get(name, name)
        bar = make_entity_bar(e["rate"])
        entity_lines += f"{display_name:<14s}{bar}  {e['rate']:.1f}%  ({e['matched']:,}/{e['total']:,})\n"

    # Build domain coverage lines
    domain_lines = ""
    for d in sorted(s["domain_reliable"], key=lambda x: -s["domain_reliable"][x]["total"]):
        dr = s["domain_reliable"][d]
        rel_pct = dr["reliable"] / dr["total"] * 100 if dr["total"] > 0 else 0
        domain_lines += f"  {d} ({dr['total']})\n"
        bar = make_entity_bar(rel_pct, 30)
        domain_lines += f"  {bar}  {rel_pct:.0f}% reliable\n\n"

    # Now do replacements
    # This is the simple approach — just write the file from scratch using the template structure
    print(f"  Generating {output_path.name}...")
    print(f"    Total classified: {s['total_classified']}")
    print(f"    Verified (high+safe+mostly): {s['verified_combined']} ({s['verified_pct']:.1f}%)")
    print(f"    Trustable (>=60): {s['trustable']} ({s['trustable_pct']:.1f}%)")
    print(f"    Total data points: {s['total_data_points']:,}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"""================================================================================
DATA MONITORING DASHBOARD — {s['program_type'].upper()}
Course Data Verification System
{s['program_type']} | Complete Courses Only
Classified: {s['total_classified']:,} courses | Universities: {s['classified_unis']}
================================================================================


================================================================================
1. STATISTICS SECTION
================================================================================

Card Row (Top)
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Total Courses │ │ Universities │ │   Domains    │ │   Verified   │ │  Avg Courses │
│   {s['total_classified']:>6,}    │ │     {s['classified_unis']:>3}      │ │      {s['num_domains']}       │ │   {s['verified_combined']:>6,}    │ │    {s['avg_courses_per_uni']:.1f}      │
│ {s['program_type']:>13} │ │   worldwide  │ │  broad fields│ │  ({s['verified_pct']:.1f}%)     │ │  per college  │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
Total Courses — complete courses classified (all 3 sections have full crawl data)
Universities — unique colleges covered in classified set
Domains — broad academic fields (CS & AI, Engineering, Natural Sciences, Business, Medicine, Social Sciences, Arts, Environment, Other)
Verified — high_confidence ({s['high_conf']:,}) + safe_to_present ({s['safe']:,}) + mostly_reliable ({s['mostly']:,})
Avg Courses — average number of courses per university ({s['total_classified']:,} courses / {s['classified_unis']} universities)
  Note: All high_confidence files are included in safe/mostly labels. Unique verified = {s['verified_combined']:,}



Official URL Crawls (Progress bars)

Total pages crawled:         {make_progress_bar(s['crawl_pct'], 30)}  {s['crawl_pct']:.1f}%  ({s['crawled_urls']:,} / {s['needed_urls']:,} URLs)
Complete courses classified: {make_progress_bar(s['total_classified']/s['total_courses_all']*100, 30)}  {s['total_classified']/s['total_courses_all']*100:.1f}%  ({s['total_classified']:,} / {s['total_courses_all']:,} total courses)

Major Section Coverage (of {s['total_classified']:,} classified courses):
  Admission Requirements:   {make_progress_bar(sec['admission_requirements']['with_data']/s['total_classified']*100, 30)}  {sec['admission_requirements']['with_data']/s['total_classified']*100:.1f}%  ({sec['admission_requirements']['with_data']:,} have data)
  Application Deadlines:    {make_progress_bar(sec['application_deadlines']['with_data']/s['total_classified']*100, 30)}  {sec['application_deadlines']['with_data']/s['total_classified']*100:.1f}%  ({sec['application_deadlines']['with_data']:,} have data)
  Tuition & Fees:           {make_progress_bar(sec['tuition_and_fees']['with_data']/s['total_classified']*100, 30)}  {sec['tuition_and_fees']['with_data']/s['total_classified']*100:.1f}%  ({sec['tuition_and_fees']['with_data']:,} have data)



Important Entity Verification Rates (Horizontal bar chart)

{entity_lines}Match rate = matched / (matched + mismatched). Only counted where both .md and crawled page had data.


University Coverage by Region (vs QS Top Rankings 2026)
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ US       │ │ UK       │ │ Europe   │ │ Asia     │ │ Aus/NZ   │ │ Canada   │
│ 70/71    │ │ 46/46    │ │ 90/104   │ │ 18/57   │ │ 33/33    │ │ 17/18    │
│ 98.6%    │ │ 100%     │ │ 86.5%    │ │ 31.6%   │ │ 100%     │ │ 94.4%    │
└──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘

Click any region to drill down → shows We Have vs We Don't Have lists


================================================================================
2. ACCURACY SECTION
================================================================================

Important Section Trust Rates (3 Gauges with data points)
┌─────────────────────────────────────────────────────────────────┐
│              Important Section Trust Rates                       │
│  (verified + partial) / files with data                        │
│                                                                 │
│   Tuition & Fees        Deadlines          Admission Req       │
│   ┌───────────┐        ┌───────────┐      ┌───────────┐       │
│   │   {sec['tuition_and_fees']['trust']:.1f}%   │        │   {sec['application_deadlines']['trust']:.1f}%   │      │   {sec['admission_requirements']['trust']:.1f}%   │       │
│   │  trusted  │        │  trusted  │      │  trusted  │       │
│   │           │        │           │      │           │       │
│   │{s['tuition_matched']:>7,}    │        │{s['date_matched']:>7,}    │      │{s['total_ent_matched']:>7,}    │       │
│   │ data pts  │        │ data pts  │      │ data pts  │       │
│   │ verified  │        │ verified  │      │ verified  │       │
│   └───────────┘        └───────────┘      └───────────┘       │
│   {sec['tuition_and_fees']['with_data']:,} files checked   {sec['application_deadlines']['with_data']:,} checked      {sec['admission_requirements']['with_data']:,} checked       │
└─────────────────────────────────────────────────────────────────┘

Each gauge shows: Trust rate %, Data points verified, Files checked


Data Trustworthiness (Pie chart)
┌─────────────────────────────────────────────────────┐
│         Data Trustworthiness                        │
│                                                     │
│            ┌──────────┐                             │
│           /  Trustable \\    {s['trustable']:,} files ({s['trustable_pct']:.1f}%)     │
│          │   {s['trustable_pct']:.1f}%      │   Verified + Safe +       │
│          │              │   Mostly Reliable          │
│           \\            /                            │
│            \\  {100-s['trustable_pct']:.1f}%   /     {s['not_trustable']:,} files              │
│             └────────┘      Needs more verification  │
│                                                     │
└─────────────────────────────────────────────────────┘

Trustable = confidence >= 60 (safe_to_present + mostly_reliable)
Total data points verified against official sources: {s['total_data_points']:,}


================================================================================
3. EXPLORER SECTION
================================================================================

Mode 1: Default (No filters) — Drill-Down Browse
University list → click to expand courses → click course to expand detail
All on same page, accordion-style. Filters sidebar always on left.

Mode 2: Filter Applied — Flat Table With All Columns + Values
When any filter checkbox is ticked, right side shows flat table:
  Columns: Uni, Course, Conf Score, Tuition (amount+status), Deadlines
  (date+status), TOEFL, IELTS, GRE, GPA, LOR, Fee — all with ✓/~/✗/- symbols

Detail panel expands on row click showing:
  - Per-section source URLs + match details
  - Entity comparison table (.md value vs official value)
  - Tuition amounts with ✓/✗
  - Deadline dates with ✓/✗

Actions: [View .md file] [View All Details] [Open Official URL]

"View All Details" shows complete structured .md content with verification
symbols inline for every verifiable data point across ALL sections.


================================================================================
4. FILTERS SECTION (Integrated in Explorer left sidebar)
================================================================================

Primary Filters:
  Confidence: [>=75] [>=60] [>=50] [<40]
  University: [Search/Select]
  Domain: [All] [CS & AI] [Engineering] [Sciences] [...]
  Program: [Search course name...]

Section Verification Filters (Checkboxes):
  Tuition & Fees:
    ☐ Verified ({sec['tuition_and_fees']['verified']:,})  ☐ Partial ({sec['tuition_and_fees']['partial']:,})  ☐ Flagged ({sec['tuition_and_fees']['flagged']:,})  ☐ No Data ({sec['tuition_and_fees']['no_data']:,})
  Application Deadlines:
    ☐ Verified ({sec['application_deadlines']['verified']:,})  ☐ Partial ({sec['application_deadlines']['partial']:,})  ☐ Flagged ({sec['application_deadlines']['flagged']:,})  ☐ No Data ({sec['application_deadlines']['no_data']:,})
  Admission Requirements:
    ☐ Verified ({sec['admission_requirements']['verified']:,})  ☐ Partial ({sec['admission_requirements']['partial']:,})  ☐ Flagged ({sec['admission_requirements']['flagged']:,})  ☐ No Data ({sec['admission_requirements']['no_data']:,})

Entity-Level Filters (Checkboxes):
  TOEFL:      ☐ Matched ({ent['toefl']['matched']:,})  ☐ Mismatched ({ent['toefl']['mismatched']:,})   ☐ Not on page  ☐ N/A
  IELTS:      ☐ Matched ({ent['ielts']['matched']:,})  ☐ Mismatched ({ent['ielts']['mismatched']:,})   ☐ Not on page  ☐ N/A
  GRE Status: ☐ Matched ({ent['gre_status']['matched']:,})  ☐ Mismatched ({ent['gre_status']['mismatched']:,})   ☐ Not on page  ☐ N/A
  GPA:        ☐ Matched ({ent['gpa']['matched']:,})  ☐ Mismatched ({ent['gpa']['mismatched']:,})   ☐ Not on page  ☐ N/A
  App Fee:    ☐ Matched ({ent['app_fee']['matched']:,})  ☐ Mismatched ({ent['app_fee']['mismatched']:,})   ☐ Not on page  ☐ N/A
  LOR Count:  ☐ Matched ({ent['lor_count']['matched']:,})  ☐ Mismatched ({ent['lor_count']['mismatched']:,})   ☐ Not on page  ☐ N/A
  PTE:        ☐ Matched ({ent['pte']['matched']:,})  ☐ Mismatched ({ent['pte']['mismatched']:,})   ☐ Not on page  ☐ N/A
  Duolingo:   ☐ Matched ({ent['duolingo']['matched']:,})  ☐ Mismatched ({ent['duolingo']['mismatched']:,})   ☐ Not on page  ☐ N/A
  GRE Score:  ☐ Matched ({ent['gre_score']['matched']:,})  ☐ Mismatched ({ent['gre_score']['mismatched']:,})   ☐ Not on page  ☐ N/A

Quick Filter Presets:
  [All Verified Courses]        → all 3 sections verified
  [Needs Attention]             → score < 40
  [Stale Deadlines]             → deadlines flagged as past
  [Missing Tuition Data]        → tuition = no_data
  [TOEFL/IELTS Mismatches]      → either test score doesn't match
  [Fully Crawled But Low Score] → all 3 sections have data, score < 50

Results List: Dynamic table with [Export CSV] and pagination


================================================================================
5. COMPARISON SECTION
================================================================================

Side-by-side view of .md file data vs official crawled page data.
Course Selector → Section Tabs (Tuition | Deadlines | Admission)
Each tab shows source URL, side-by-side text, matched/mismatched highlights.
Entity comparison table for admission requirements.
Action Bar: [View .md file] [View All Details] [Open Official URL]
            [Mark as Reviewed] [← Prev Course] [Next Course →]


================================================================================
6. COVERAGE SECTION
================================================================================

Overview Cards:
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  Classified  │ │  Remaining   │ │ Universities │
│   {s['total_classified']:>6,}    │ │   {s['uncrawled']:>6,}    │ │     {s['classified_unis']:>3}      │
│  of {s['total_courses_all']:>6,}  │ │   courses    │ │  of {s['total_unis_all']:>5}    │
│  ({s['total_classified']/s['total_courses_all']*100:.1f}%)     │ │  ({s['uncrawled']/s['total_courses_all']*100:.1f}%)     │ │  ({s['classified_unis']/s['total_unis_all']*100:.1f}%)     │
└──────────────┘ └──────────────┘ └──────────────┘

Section Data Coverage (of {s['total_classified']:,} classified):
  Admission Requirements:  {sec['admission_requirements']['with_data']/s['total_classified']*100:.1f}%  ({sec['admission_requirements']['with_data']:,} covered | {sec['admission_requirements']['no_data']:,} missing)
  Application Deadlines:   {sec['application_deadlines']['with_data']/s['total_classified']*100:.1f}%  ({sec['application_deadlines']['with_data']:,} covered | {sec['application_deadlines']['no_data']:,} missing)
  Tuition & Fees:          {sec['tuition_and_fees']['with_data']/s['total_classified']*100:.1f}%  ({sec['tuition_and_fees']['with_data']:,} covered | {sec['tuition_and_fees']['no_data']:,} missing)

Domain Coverage:
{domain_lines}

================================================================================
7. ALERTS SECTION
================================================================================

Alert Priority Banner:
  CRITICAL | WARNING | INFO

Critical Alerts:
  - Tuition Amounts Wrong: {s['tuition_flagged']:,} courses with flagged tuition
  - TOEFL/IELTS Mismatches: {s['toefl_ielts_mm']:,} combined ({s['toefl_mm']:,} TOEFL + {s['ielts_mm']:,} IELTS)
  - Prestigious University Data Issues: courses at top unis with confidence < 40

Warning Alerts:
  - Reliable Files With Flagged Sections: {s['reliable_with_flagged']:,} files scoring >= 60 with 1 flagged section
  - Multiple Admission Mismatches: {s['multi_adm_mm']:,} files with 3+ entities mismatched
  - Stale Deadlines: {s['stale_deadlines']:,} courses where website updated with newer dates

Info Alerts:
  - Past Deadlines: {s['past_deadlines']:,} courses with dates matched but in the past
  - Uncrawled Courses: {s['uncrawled']:,} courses awaiting crawl data
  - Zero Confidence Files: {s['zero_conf']:,} files where nothing could be verified

Each alert clickable → shows full list with sort and [Export CSV]


================================================================================
SUMMARY METRICS
================================================================================

Confidence Labels:
  high_confidence (all 3 verified):     {s['high_conf']:>6,}  ({s['high_conf']/s['total_classified']*100:.1f}%)
  safe_to_present_as_official (>=75):  {s['safe']:>6,}  ({s['safe']/s['total_classified']*100:.1f}%)
  mostly_reliable (60-74):             {s['mostly']:>6,}  ({s['mostly']/s['total_classified']*100:.1f}%)
  --- Verified (high + safe + mostly)   {s['verified_combined']:>6,}  ({s['verified_pct']:.1f}%)
  use_with_caveat (40-59):             {s['caveat']:>6,}  ({s['caveat']/s['total_classified']*100:.1f}%)
  needs_review (<40):                  {s['review']:>6,}  ({s['review']/s['total_classified']*100:.1f}%)

Section Trust Rates:
  Tuition & Fees:          {sec['tuition_and_fees']['trust']:.1f}% trusted  ({sec['tuition_and_fees']['with_data']:,} checked)
  Application Deadlines:   {sec['application_deadlines']['trust']:.1f}% trusted  ({sec['application_deadlines']['with_data']:,} checked)
  Admission Requirements:  {sec['admission_requirements']['trust']:.1f}% trusted  ({sec['admission_requirements']['with_data']:,} checked)

Total Data Points Verified: {s['total_data_points']:,}
  Tuition amounts:    {s['tuition_matched']:,}
  Deadline dates:      {s['date_matched']:,}
  Admission entities: {s['total_ent_matched']:,}

Trustable Files (confidence >= 60): {s['trustable']:,} ({s['trustable_pct']:.1f}%)

================================================================================
""")


def main():
    configs = [
        {
            "program_type": "PhD",
            "report": BASE / "classification_results" / "phd" / "classification_report.json",
            "section_urls": OFFICIAL_DIR / "phd_section_urls.json",
            "md_base": BASE / "phd_data" / "phd",
            "output": BASE / "Dashboard_PhD.txt",
        },
        {
            "program_type": "Masters",
            "report": BASE / "classification_results" / "masters" / "classification_report.json",
            "section_urls": OFFICIAL_DIR / "masters_section_urls.json",
            "md_base": BASE / "masters_data" / "masters",
            "output": BASE / "Dashboard_Masters.txt",
        },
    ]

    # Use same template structure — we just generate from scratch
    for cfg in configs:
        print(f"\n{cfg['program_type']}:")
        s = calc_stats(cfg["report"], cfg["section_urls"], cfg["md_base"], cfg["program_type"])
        generate_txt(s, cfg["output"])

    print("\nDone!")


if __name__ == "__main__":
    main()
