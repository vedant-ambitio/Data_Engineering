import json
import os
from collections import Counter, defaultdict

OUTPUT_DIR = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info\output\universities"
RECOVERY_DIR = r"c:\Users\HP\OneDrive\Desktop\course_data\Professors_info\output_recovery\universities"

confidence_counts = Counter()
verification_counts = Counter()
recovery_outcomes = Counter()

total_entries = 0
entries_with_url = 0
entries_without_url = 0

# Track per-university for cross-reference
output_data = {}  # uni -> {dept -> entry}
recovery_data = {}  # uni -> {dept -> entry}

# Process output folder
for fn in sorted(os.listdir(OUTPUT_DIR)):
    if not fn.endswith(".json"):
        continue
    fp = os.path.join(OUTPUT_DIR, fn)
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"ERR reading {fn}: {e}")
        continue
    uni = data.get("university", fn.replace(".json", ""))
    depts = data.get("departments", {})
    output_data[uni] = depts
    for dept, entry in depts.items():
        total_entries += 1
        conf = entry.get("confidence")
        confidence_counts[conf] += 1
        vs = entry.get("verification_status")
        verification_counts[vs] += 1
        if entry.get("faculty_page_url"):
            entries_with_url += 1
        else:
            entries_without_url += 1

# Process recovery folder
recovered_entries = 0
recovered_with_url = 0
for fn in sorted(os.listdir(RECOVERY_DIR)):
    if not fn.endswith(".json"):
        continue
    fp = os.path.join(RECOVERY_DIR, fn)
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"ERR reading recovery {fn}: {e}")
        continue
    uni = data.get("university", fn.replace(".json", ""))
    depts = data.get("departments", {})
    recovery_data[uni] = depts
    for dept, entry in depts.items():
        recovered_entries += 1
        outcome = entry.get("recovery_outcome")
        recovery_outcomes[outcome] += 1
        if entry.get("faculty_page_url"):
            recovered_with_url += 1

# Now compute the *merged* picture: how many of the originally non-high entries
# got fixed by recovery
merged_status = Counter()
recovery_filled_low = 0
recovery_filled_notfound = 0
recovery_filled_other = 0
recovery_overlap_high = 0
recovery_no_match_in_output = 0

for uni, depts in recovery_data.items():
    out_depts = output_data.get(uni, {})
    for dept, rec_entry in depts.items():
        rec_url = rec_entry.get("faculty_page_url")
        outcome = rec_entry.get("recovery_outcome")
        if outcome != "recovered" or not rec_url:
            continue
        orig = out_depts.get(dept)
        if orig is None:
            recovery_no_match_in_output += 1
            continue
        orig_conf = orig.get("confidence")
        if orig_conf == "high":
            recovery_overlap_high += 1
        elif orig_conf == "low":
            recovery_filled_low += 1
        elif orig_conf == "not_found":
            recovery_filled_notfound += 1
        else:
            recovery_filled_other += 1

# After recovery, what's the final state?
final_high = 0
final_low = 0
final_not_found = 0
final_recovered = 0  # was low/not_found/other, now has a URL via recovery
final_other = 0
final_total = 0

for uni, depts in output_data.items():
    rec_depts = recovery_data.get(uni, {})
    for dept, entry in depts.items():
        final_total += 1
        conf = entry.get("confidence")
        rec_entry = rec_depts.get(dept)
        recovered_url = None
        if rec_entry and rec_entry.get("recovery_outcome") == "recovered" and rec_entry.get("faculty_page_url"):
            recovered_url = rec_entry["faculty_page_url"]

        if conf == "high":
            final_high += 1
        elif recovered_url:
            final_recovered += 1
        elif conf == "low":
            final_low += 1
        elif conf == "not_found":
            final_not_found += 1
        else:
            final_other += 1

print("=" * 70)
print("OUTPUT FOLDER (original run)")
print("=" * 70)
print(f"Total university files:    {len(output_data)}")
print(f"Total dept entries:        {total_entries}")
print(f"  with faculty_page_url:   {entries_with_url}")
print(f"  without url:             {entries_without_url}")
print()
print("Confidence breakdown:")
for k, v in confidence_counts.most_common():
    pct = 100.0 * v / total_entries
    print(f"  {str(k):15s} {v:6d}   ({pct:5.2f}%)")
print()
print("Verification status breakdown:")
for k, v in verification_counts.most_common():
    pct = 100.0 * v / total_entries
    print(f"  {str(k):15s} {v:6d}   ({pct:5.2f}%)")
print()

print("=" * 70)
print("OUTPUT_RECOVERY FOLDER")
print("=" * 70)
print(f"Total university files:    {len(recovery_data)}")
print(f"Total dept entries:        {recovered_entries}")
print(f"  with faculty_page_url:   {recovered_with_url}")
print()
print("Recovery outcome breakdown:")
for k, v in recovery_outcomes.most_common():
    pct = 100.0 * v / max(recovered_entries, 1)
    print(f"  {str(k):20s} {v:6d}   ({pct:5.2f}%)")
print()
print("How recovery interacts with original confidence:")
print(f"  Recovered entries that were originally low:        {recovery_filled_low}")
print(f"  Recovered entries that were originally not_found:  {recovery_filled_notfound}")
print(f"  Recovered entries that were originally high:       {recovery_overlap_high}")
print(f"  Recovered entries with other/None original conf:   {recovery_filled_other}")
print(f"  Recovered entries with NO match in output:         {recovery_no_match_in_output}")
print()

print("=" * 70)
print("FINAL MERGED PICTURE  (output + recovery)")
print("=" * 70)
print(f"Total dept entries:                    {final_total}")
print(f"  HIGH confidence (original):          {final_high}   ({100.0*final_high/final_total:.2f}%)")
print(f"  RECOVERED (URL found via recovery):  {final_recovered}   ({100.0*final_recovered/final_total:.2f}%)")
print(f"  LOW (still low, not recovered):      {final_low}   ({100.0*final_low/final_total:.2f}%)")
print(f"  NOT_FOUND (still):                   {final_not_found}   ({100.0*final_not_found/final_total:.2f}%)")
print(f"  Other / null:                        {final_other}   ({100.0*final_other/final_total:.2f}%)")
print()
print(f"  Working URLs (HIGH + RECOVERED):     {final_high + final_recovered}   ({100.0*(final_high+final_recovered)/final_total:.2f}%)")
