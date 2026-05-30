"""
View scraped BigFuture college data in a readable format.

Usage:
  python view_college.py ferris-state-university
  python view_college.py texas          # partial match
  python view_college.py --list         # list all scraped colleges
"""

import json
import sys
import os
from pathlib import Path

RAW_DIR = Path("bigfuture_data/raw")


def find_college(query):
    """Find college JSON file by exact slug or partial match."""
    exact = RAW_DIR / f"{query}.json"
    if exact.exists():
        return exact

    matches = [f for f in RAW_DIR.glob("*.json") if query.lower() in f.stem.lower()]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        print(f"Multiple matches for '{query}':")
        for m in matches:
            print(f"  {m.stem}")
        return None
    else:
        print(f"No match found for '{query}'")
        return None


def fmt_money(val):
    if val is None:
        return "N/A"
    return f"${val:,.0f}" if isinstance(val, (int, float)) else f"${val}"


def fmt_pct(val):
    if val is None:
        return "N/A"
    return f"{val}%"


def fmt_date(val):
    if val is None:
        return "None"
    # BigFuture uses MM/DD/9999 for recurring dates
    if "9999" in str(val):
        parts = str(val).split("/")
        months = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
                  "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
                  "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"}
        return f"{months.get(parts[0], parts[0])} {int(parts[1])}"
    return str(val)


def display(d):
    name = d.get("name", "Unknown")
    sep = "=" * 60

    print(f"\n{sep}")
    print(f"  {name}")
    print(f"{sep}\n")

    # ── OVERVIEW ──
    print("OVERVIEW")
    print(f"  Location:    {d.get('city')}, {d.get('state')} ({d.get('schoolSetting', 'N/A')})")
    print(f"  Address:     {d.get('streetAddress', 'N/A')}, {d.get('zipCode', '')}")
    print(f"  Type:        {d.get('schoolTypeByYears')}-year {d.get('schoolTypeByDesignation', '')}")
    print(f"  Size:        {d.get('schoolSize', 'N/A')}")
    print(f"  Coed:        {'Yes' if d.get('genderCode') == 'C' else d.get('genderCode', 'N/A')}")
    print(f"  Website:     {d.get('schoolUrl', 'N/A')}")
    print(f"  CB Code:     {d.get('diCode', 'N/A')}")
    degrees = [deg.get("degreeDescription", "") for deg in (d.get("degreesOffered") or [])]
    print(f"  Degrees:     {', '.join(degrees) if degrees else 'N/A'}")

    religion = d.get("religiousAffiliation")
    if religion:
        print(f"  Religion:    {religion}")

    special = []
    if d.get("specializedSchoolHistoricallyBlackInd") == "Y": special.append("HBCU")
    if d.get("specializedSchoolHispanicServingInd") == "Y": special.append("Hispanic-Serving")
    if d.get("specializedSchoolWomensCollegeInd") == "Y": special.append("Women's College")
    if d.get("specializedSchoolMensCollegeInd") == "Y": special.append("Men's College")
    if d.get("specializedSchoolTribalCollegeInd") == "Y": special.append("Tribal College")
    if special:
        print(f"  Special:     {', '.join(special)}")

    # ── ADMISSIONS ──
    print(f"\nADMISSIONS")
    print(f"  Acceptance Rate:  {fmt_pct(d.get('acceptanceRate'))}")
    ta = d.get('totalApplicants')
    aa = d.get('admittedApplicants')
    ea = d.get('enrolledApplicants')
    print(f"  Applicants:       {f'{ta:,}' if ta else 'N/A'} applied -> {f'{aa:,}' if aa else 'N/A'} admitted -> {f'{ea:,}' if ea else 'N/A'} enrolled")
    print(f"  SAT Composite:    {d.get('satCompositeScore25thPercentile', 'N/A')}-{d.get('satCompositeScore75thPercentile', 'N/A')}")
    print(f"  SAT Math:         {d.get('rsatMathScore25thPercentile', 'N/A')}-{d.get('rsatMathScore75thPercentile', 'N/A')}")
    print(f"  SAT Reading:      {d.get('rsatEbrwScore25thPercentile', 'N/A')}-{d.get('rsatEbrwScore75thPercentile', 'N/A')}")
    print(f"  ACT Composite:    {d.get('actCompositeScore25thPercentile', 'N/A')}-{d.get('actCompositeScore75thPercentile', 'N/A')}")
    print(f"  SAT/ACT Policy:   {d.get('satOrAct', 'N/A')}")
    print(f"  HS GPA:           {d.get('highSchoolGpa', 'N/A')}")
    print(f"  HS Rank:          {d.get('highSchoolRank', 'N/A')}")
    print(f"  Prep Courses:     {d.get('prepCourses', 'N/A')}")
    print(f"  Recommendations:  {d.get('recommendations', 'N/A')}")
    print(f"  App Fee:          {fmt_money(d.get('applicationFeeAmount'))}")
    print(f"  Fee Waiver:       {'Yes' if d.get('feeWaiverIndicator') == 'Y' else 'No'}")
    app_urls = d.get("applicationsAccepted") or []
    if app_urls:
        if isinstance(app_urls[0], dict):
            types = [a.get("applicationTypeDescription", "") for a in app_urls]
        else:
            types = [str(a) for a in app_urls]
        print(f"  App Types:        {', '.join(types)}")

    # GPA distribution
    gpa_fields = ["gpa400","gpa375To399","gpa350To374","gpa325To349",
                  "gpa300To324","gpa250To299","gpa200To249","gpa100To199","gpaBelow100"]
    gpa_labels = ["4.0","3.75-3.99","3.50-3.74","3.25-3.49",
                  "3.00-3.24","2.50-2.99","2.00-2.49","1.00-1.99","<1.00"]
    has_gpa = any(d.get(f) for f in gpa_fields)
    if has_gpa:
        print(f"  GPA Distribution:")
        for label, field in zip(gpa_labels, gpa_fields):
            val = d.get(field)
            if val:
                bar = "#" * int(val)
                print(f"    {label:>9}: {val:>3}% {bar}")

    # ── DEADLINES ──
    print(f"\nDEADLINES")
    print(f"  Regular:          {fmt_date(d.get('regularDecisionDate'))}")
    print(f"  Early Action:     {fmt_date(d.get('earlyActionDate'))}")
    print(f"  Early Decision:   {fmt_date(d.get('earlyDecisionDate'))}")
    print(f"  Fin Aid Priority: {fmt_date(d.get('financialAidApplicationPriorityDeadline'))}")
    print(f"  Fin Aid Regular:  {fmt_date(d.get('financialAidApplicationRegularDeadline'))}")
    print(f"  Notification:     {fmt_date(d.get('notificationDate'))}")
    print(f"  Response Due:     {fmt_date(d.get('responseDeadline'))}")

    # ── COSTS ──
    print(f"\nCOSTS")
    print(f"  In-State Tuition:   {fmt_money(d.get('inStateTuition'))}")
    print(f"  Out-State Tuition:  {fmt_money(d.get('outOfStateTuition'))}")
    if d.get("privateTuition"):
        print(f"  Private Tuition:    {fmt_money(d.get('privateTuition'))}")
    print(f"  Housing:            {fmt_money(d.get('averageHousingCost'))}")
    print(f"  Books & Supplies:   {fmt_money(d.get('booksAndSuppliesCost'))}")
    print(f"  Personal Expenses:  {fmt_money(d.get('estimatedPersonalExpenses'))}")
    if d.get("transportationCosts"):
        print(f"  Transportation:     {fmt_money(d.get('transportationCosts'))}")
    print(f"  Avg Net Price:      {fmt_money(d.get('averageNetPrice'))}")
    print(f"  Net Price by Family Income:")
    print(f"    <$30K:            {fmt_money(d.get('averageNetPriceBelow30K'))}")
    print(f"    $30-48K:          {fmt_money(d.get('averageNetPrice30To48K'))}")
    print(f"    $48-75K:          {fmt_money(d.get('averageNetPrice48To75K'))}")
    print(f"    $75-110K:         {fmt_money(d.get('averageNetPrice75To110K'))}")
    print(f"    >$110K:           {fmt_money(d.get('averageNetPriceAbove110K'))}")

    # ── FINANCIAL AID ──
    print(f"\nFINANCIAL AID")
    print(f"  Students Receiving: {fmt_pct(d.get('studentsReceivingAidPercent'))}")
    print(f"  Freshmen w/ Need:  {fmt_pct(d.get('freshmenWithNeedAidPercent'))}")
    print(f"  Need Met:          {fmt_pct(d.get('financialNeedMet'))}")
    print(f"  Avg Aid Awarded:   {fmt_money(d.get('averageAidAwarded'))}")
    print(f"  Need-Based Grant:  {fmt_money(d.get('needBasedAward'))}")
    print(f"  Need-Based Loan:   {fmt_money(d.get('needBasedLoanAmount'))}")
    print(f"  Non-Need Aid:      {fmt_money(d.get('nonNeedBasedAid'))}")
    print(f"  Avg Debt at Grad:  {fmt_money(d.get('graduationDebt'))}")
    print(f"  Intl Need-Based:   {'Yes' if d.get('needBasedFinAidForIntlStudentsInd') else 'No'}")
    print(f"  Intl Non-Need:     {'Yes' if d.get('nonNeedBasedFinAidForIntlStudentsInd') else 'No'}")

    # ── ACADEMICS ──
    print(f"\nACADEMICS")
    majors = d.get("collegeMajors") or []
    print(f"  Majors Offered:    {len(majors)}")
    print(f"  Graduation Rate:   {fmt_pct(d.get('graduationRatePercent'))}")
    print(f"  Retention Rate:    {fmt_pct(d.get('sophomoreYearReturnPercent'))}")
    print(f"  Student:Faculty:   {d.get('studentFacultyRatio', 'N/A')}:1")

    study = d.get("studyOptions") or []
    if study:
        opts = [s.get("studyOptionDescription", "") for s in study]
        print(f"  Study Options:     {', '.join(opts)}")

    if majors:
        print(f"  All Majors:")
        for m in majors:
            cip = m.get("cipCode", "")
            print(f"    [{cip}] {m.get('name', '')}")

    # ── AP CREDITS ──
    ap = d.get("apExamTypes") or []
    if ap:
        print(f"\nAP CREDIT POLICY ({len(ap)} exams accepted)")
        if d.get("apInstPolicyDescription"):
            desc = d["apInstPolicyDescription"]
            if len(desc) > 150:
                desc = desc[:150] + "..."
            print(f"  Policy: {desc}")
        print(f"  {'Exam':<40} {'Min Score':>9} {'Credits':>8} {'Equivalent':<25} {'Used For'}")
        print(f"  {'-'*40} {'-'*9} {'-'*8} {'-'*25} {'-'*20}")
        for exam in ap:
            name = exam.get("examType") or ""
            for v in exam.get("values", []):
                score = v.get("apcpMinScoreRequired") or "?"
                credits = v.get("apcpCreditsAwarded") or "?"
                equiv = v.get("apcpCourseEquivalent") or "N/A"
                used = v.get("apcpCreditUsedFor") or "N/A"
                print(f"  {str(name):<40} {str(score):>9} {str(credits):>8} {str(equiv):<25} {str(used)}")

    # ── CAMPUS LIFE ──
    print(f"\nCAMPUS LIFE")
    ug = d.get('totalUndergraduates')
    gr = d.get('totalGraduates')
    ft = d.get('fullTimeEnrolled')
    pt = d.get('partTimeEnrolled')
    print(f"  Total Undergrads:  {f'{ug:,}' if ug else 'N/A'}")
    print(f"  Total Graduates:   {f'{gr:,}' if gr else 'N/A'}")
    print(f"  Full-Time:         {f'{ft:,}' if ft else 'N/A'}")
    print(f"  Part-Time:         {f'{pt:,}' if pt else 'N/A'}")
    print(f"  Housing Type:      {d.get('commuterOrResidential', 'N/A')}")
    print(f"  Freshmen Housing:  {fmt_pct(d.get('firstYearCollegeHousingPercent'))}")
    print(f"  Out-of-State:      {fmt_pct(d.get('outOfStatePercent'))}")
    print(f"  International:     {fmt_pct(d.get('internationalPercent'))}")

    print(f"  Demographics:")
    demo = [
        ("White", d.get("whitePercent")),
        ("Asian", d.get("asianPercent")),
        ("Hispanic", d.get("hispanicPercent")),
        ("African American", d.get("africanAmericanPercent")),
        ("Multiracial", d.get("multiracialPercent")),
        ("Native American", d.get("nativeAmericanPercent")),
        ("Pacific Islander", d.get("pacificIslanderPercent")),
        ("International", d.get("internationalPercent")),
        ("Unknown", d.get("unknownPercent")),
    ]
    for label, val in sorted(demo, key=lambda x: x[1] or 0, reverse=True):
        if val:
            bar = "#" * int(val)
            print(f"    {label:<20} {val:>5}% {bar}")

    housing = d.get("housingOptions") or []
    if housing:
        opts = [h.get("housingOptionDescription", "") for h in housing]
        print(f"  Housing Options:   {', '.join(opts)}")

    sports = d.get("studentSports") or []
    if sports:
        # Group sports by gender for cleaner display
        by_gender = {}
        for s in sports:
            gender = s.get("sportGender", "").strip() or "Unknown"
            name = s.get("sportDescription", "").strip()
            division = s.get("sportDivision", "").strip()
            if name:
                label = f"{name} ({division})" if division else name
                by_gender.setdefault(gender, []).append(label)
        print(f"  Sports ({len(sports)}):")
        for gender in sorted(by_gender.keys()):
            names = sorted(set(by_gender[gender]))
            print(f"    {gender}: {', '.join(names)}")

    activities = d.get("studentActivities") or []
    if activities:
        acts = [a.get("activityDescription", "").strip() for a in activities]
        acts = [a for a in acts if a]  # filter empty
        if acts:
            print(f"  Activities:        {', '.join(acts)}")

    # ── CONTACT & SOCIAL ──
    print(f"\nCONTACT")
    print(f"  Phone:             {d.get('contactPhoneFormatted', 'N/A')}")
    print(f"  Fin Aid Phone:     {d.get('financialAidOfficePhoneNumber', 'N/A')}")
    print(f"  Apply:             {d.get('applicationSiteUrl', 'N/A')}")
    print(f"  Net Price Calc:    {d.get('netPriceCalculatorUrl', 'N/A')}")
    social = d.get("socialMedia") or {}
    for platform, links in social.items():
        if links:
            print(f"  {platform:<18}: {links[0]}")

    print(f"\n{sep}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python view_college.py <slug-or-partial-name>")
        print("       python view_college.py --list")
        return

    if sys.argv[1] == "--list":
        files = sorted(RAW_DIR.glob("*.json"))
        print(f"\n{len(files)} colleges scraped:\n")
        for f in files:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            print(f"  {f.stem:<50} {d.get('name', '')}")
        return

    query = sys.argv[1]
    filepath = find_college(query)
    if not filepath:
        return

    with open(filepath, encoding="utf-8") as f:
        d = json.load(f)

    display(d)


if __name__ == "__main__":
    main()
