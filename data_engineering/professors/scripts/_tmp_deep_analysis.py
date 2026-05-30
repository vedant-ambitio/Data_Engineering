"""Deep quality analysis of output_professors_13k_final.

Computes: file counts by status, professor field completeness,
per-department coverage, country breakdown, professor-count distribution,
and per-university averages.
"""
import json, os, glob
from collections import Counter, defaultdict

base = "c:/Users/HP/OneDrive/Desktop/course_data/Professors_info/output_professors_13k_final"

all_jsons = glob.glob(os.path.join(base, "universities", "**", "*.json"), recursive=True)
md_files = glob.glob(os.path.join(base, "universities", "**", "*.md"), recursive=True)
raw_files = glob.glob(os.path.join(base, "universities", "**", "*.raw.txt"), recursive=True)

print(f"Total .json files: {len(all_jsons)}")
print(f"Total .md files: {len(md_files)}")
print(f"Total .raw.txt files: {len(raw_files)}")

all_profs = []
ok_pairs = []
all_unis = set()
ok_unis = set()
country_counts = Counter()
dept_coverage = defaultdict(lambda: {"unis_with_ok": set(), "unis_attempted": set(), "professors": 0})

# Per-uni stats
uni_prof_count = Counter()
uni_md_count = Counter()
status_count = Counter()

for fp in all_jsons:
    try:
        with open(fp, "r", encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        continue
    uni = j.get("university", "") or ""
    dept = j.get("department", "") or ""
    status = j.get("status", "")
    country = j.get("country", "") or ""

    status_count[status] += 1
    all_unis.add(uni)
    dept_coverage[dept]["unis_attempted"].add(uni)

    if status in ("ok", "ok_via_homepage_navigation"):
        ok_unis.add(uni)
        country_counts[country] += 1
        profs = j.get("professors", []) or []
        ok_pairs.append({"uni": uni, "dept": dept, "country": country, "profs": profs})
        dept_coverage[dept]["unis_with_ok"].add(uni)
        dept_coverage[dept]["professors"] += len(profs)
        uni_prof_count[uni] += len(profs)
        for p in profs:
            all_profs.append(p)
            if p.get("personal_website_content_file"):
                uni_md_count[uni] += 1

print()
print("=== STATUS COUNTS (ok+skipped JSONs only — errors don't write JSON) ===")
for k, v in status_count.most_common():
    print(f"  {k}: {v}")

print()
print(f"=== TOTAL PROFESSORS: {len(all_profs):,}")
print(f"=== OK pairs: {len(ok_pairs):,}")
print(f"=== Unis with >=1 ok: {len(ok_unis)} of {len(all_unis)} attempted")

print()
print("=== FIELD COMPLETENESS ===")
fields = ["name", "role", "email", "research_area", "lab_url", "profile_url", "personal_website_url"]
for fld in fields:
    n = sum(1 for p in all_profs if p.get(fld))
    pct = n / len(all_profs) * 100 if all_profs else 0
    print(f"  {fld:>25}: {n:>7,}  ({pct:5.1f}%)")

print()
print("=== PER-UNIVERSITY AVERAGES ===")
total_prof = sum(uni_prof_count.values())
total_md = sum(uni_md_count.values())
n_ok_unis = len(uni_prof_count)
print(f"  Across {n_ok_unis} unis with >=1 ok extraction:")
print(f"    avg professors / uni:  {total_prof/n_ok_unis:.1f}")
print(f"    avg .md files / uni:   {total_md/n_ok_unis:.1f}")
print(f"  Across all 450 input unis:")
print(f"    avg professors / uni:  {total_prof/450:.1f}")
print(f"    avg .md files / uni:   {total_md/450:.1f}")

print()
print("=== TOP 10 UNIVERSITIES BY PROF COUNT ===")
for uni, n in uni_prof_count.most_common(10):
    print(f"  {uni[:50]:>50}: {n:,}")

print()
print("=== PER-DEPARTMENT COVERAGE (sorted by professors) ===")
print(f"{'Department':>40}  {'unis_ok':>8}  {'unis_attempted':>14}  {'%':>6}  {'profs':>8}")
dept_rows = []
for d, info in dept_coverage.items():
    n_ok = len(info["unis_with_ok"])
    n_att = len(info["unis_attempted"])
    pct = n_ok / n_att * 100 if n_att > 0 else 0
    dept_rows.append((d, n_ok, n_att, pct, info["professors"]))
dept_rows.sort(key=lambda x: -x[4])
for d, n_ok, n_att, pct, profs in dept_rows:
    print(f"  {d[:40]:>40}  {n_ok:>8}  {n_att:>14}  {pct:>5.1f}%  {profs:>8,}")

print()
print("=== COUNTRY (top 25) ===")
for c, n in country_counts.most_common(25):
    print(f"  {c[:25]:>25}: {n}")

print()
print("=== PROF-PER-PAIR DISTRIBUTION ===")
counts = sorted(len(p["profs"]) for p in ok_pairs)
print(f"  median: {counts[len(counts)//2]}")
print(f"  mean: {sum(counts)/len(counts):.1f}")
print(f"  min/max: {counts[0]}/{counts[-1]}")
