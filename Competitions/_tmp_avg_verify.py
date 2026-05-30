"""Verify field fill percentages for AVG tier (31 deep-research competition files)."""
import json, os, glob

base = "c:/Users/HP/OneDrive/Desktop/course_data/Competitions/competition_data/extracted/avg"
files = sorted(glob.glob(os.path.join(base, "*.json")))
print(f"Files in extracted/avg/: {len(files)}")

# 20 UI fields per the report
UI_FIELDS = [
    "activity_name", "organizer", "organizer_logo", "mode", "deadline",
    "cost_chip", "cost", "domain", "team_size", "is_verified",
    "about_description", "eligibility_text", "how_to_apply", "prizes_detail",
    "prize_amount", "structure_format", "judging_criteria", "submission_format",
    "official_website", "registration_url"
]

# Counter of filled vs null per field
counts = {f: {"filled": 0, "null": 0} for f in UI_FIELDS}
sample_records = []

for fp in files:
    try:
        with open(fp, "r", encoding="utf-8") as fh:
            j = json.load(fh)
    except Exception as e:
        print(f"PARSE FAIL {fp}: {e}")
        continue
    sample_records.append((os.path.basename(fp), j))
    for fld in UI_FIELDS:
        v = j.get(fld)
        # "filled" if non-null AND not empty string AND not empty list
        if v is None:
            counts[fld]["null"] += 1
        elif isinstance(v, str) and v.strip() == "":
            counts[fld]["null"] += 1
        elif isinstance(v, list) and len(v) == 0:
            counts[fld]["null"] += 1
        else:
            counts[fld]["filled"] += 1

# Now compare against the report numbers
REPORT_VALUES = {
    "activity_name":      (30, 1, 96.8),
    "organizer":          (31, 0, 100.0),
    "organizer_logo":     (0, 31, 0.0),
    "mode":               (28, 3, 90.3),
    "deadline":           (6, 25, 19.4),
    "cost_chip":          (28, 3, 90.3),
    "cost":               (26, 5, 83.9),
    "domain":             (30, 1, 96.8),
    "team_size":          (16, 15, 51.6),
    "is_verified":        (31, 0, 100.0),
    "about_description":  (31, 0, 100.0),
    "eligibility_text":   (29, 2, 93.5),
    "how_to_apply":       (23, 8, 74.2),
    "prizes_detail":      (11, 20, 35.5),
    "prize_amount":       (2, 29, 6.5),
    "structure_format":   (25, 6, 80.6),
    "judging_criteria":   (2, 29, 6.5),
    "submission_format":  (18, 13, 58.1),
    "official_website":   (31, 0, 100.0),
    "registration_url":   (31, 0, 100.0),
}

print()
print(f"{'Field':>22}  {'actual':>15}  {'report':>15}  {'match?':>7}")
print("-" * 70)
total_filled_actual = 0
total_filled_report = 0
mismatches = []
for fld in UI_FIELDS:
    a_filled = counts[fld]["filled"]
    a_null = counts[fld]["null"]
    a_pct = a_filled / 31 * 100 if (a_filled + a_null) > 0 else 0
    r_filled, r_null, r_pct = REPORT_VALUES[fld]
    actual_str = f"{a_filled}/{a_null} ({a_pct:.1f}%)"
    report_str = f"{r_filled}/{r_null} ({r_pct:.1f}%)"
    match = (a_filled == r_filled and a_null == r_null)
    print(f"{fld:>22}  {actual_str:>15}  {report_str:>15}  {'OK' if match else 'DIFF':>7}")
    total_filled_actual += a_filled
    total_filled_report += r_filled
    if not match:
        mismatches.append((fld, a_filled, a_null, r_filled, r_null))

print("-" * 70)
print(f"Total filled cells: actual={total_filled_actual}/620 = {total_filled_actual/620*100:.1f}%")
print(f"Total filled cells: report={total_filled_report}/620 = {total_filled_report/620*100:.1f}%")

if mismatches:
    print()
    print("=== MISMATCHES ===")
    for fld, af, an, rf, rn in mismatches:
        print(f"  {fld}: actual {af}/{an}  vs  report {rf}/{rn}  (diff: filled {af-rf:+d})")
        # Find which files differ for this field
        for fname, j in sample_records:
            v = j.get(fld)
            is_filled = v is not None and not (isinstance(v, str) and v.strip() == "") and not (isinstance(v, list) and len(v) == 0)
            # would help to show edge cases
        # Show a few null/filled samples
        nulls = [n for n, j in sample_records if j.get(fld) in (None, "", []) or
                 (isinstance(j.get(fld), str) and j.get(fld).strip() == "") or
                 (isinstance(j.get(fld), list) and len(j.get(fld)) == 0)]
        filleds = [n for n, j in sample_records if not (j.get(fld) in (None, "", [])) and
                   not (isinstance(j.get(fld), str) and j.get(fld).strip() == "") and
                   not (isinstance(j.get(fld), list) and len(j.get(fld)) == 0)]
        print(f"    nulls ({len(nulls)}): {nulls[:5]}")
        print(f"    filleds ({len(filleds)}): {filleds[:3]}")
