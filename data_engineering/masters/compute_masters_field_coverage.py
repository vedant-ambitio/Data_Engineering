"""
compute_masters_field_coverage.py

Scans ALL masters md files in masters_data/masters/ and computes
field-level coverage percentages. Writes dashboard/masters_coverage.json
in the format expected by build_dashboard.py.

Usage:
    python dashboard/compute_masters_field_coverage.py

Output format (matches what build_dashboard.py expects):
[
  {
    "group": "Group Name",
    "section_pct": 99.7,         # % of files where AT LEAST ONE field in group hits
    "fields": [
      {"name": "Field Name", "pct": 95.3}
    ]
  }
]
"""

import re
import json
import shutil
import sys
import io
from pathlib import Path
from datetime import datetime
from collections import defaultdict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
MD_DIR = BASE / "masters_data" / "masters"
OUTPUT = BASE / "dashboard" / "masters_coverage.json"

# ── 37 fields across 7 groups ──────────────────────────────────────────
FIELDS = [
    # ── Group 1: Program Essentials ────────────────────────────────────
    ("Program Essentials", "Degree Type",
        r"\b(MSc|MA|MBA|MEng|MS\b|MFA|MEd|LLM|MPH|MPA|Master\s+of|Master[’']?s)"),
    ("Program Essentials", "Duration",
        r"\b\d+\s*(?:year|semester|month|term)s?\b"),
    ("Program Essentials", "Delivery Mode",
        r"\b(?:on-campus|online|hybrid|distance|in-person|on campus|part-time|full-time)\b"),
    ("Program Essentials", "Total Credits",
        r"\b\d+\s*(?:credit|ECTS|unit)s?\b"),
    ("Program Essentials", "Department / School",
        r"(?:department\s+of|school\s+of|faculty\s+of)\s+\w"),
    ("Program Essentials", "Program Page URL",
        r"https?://\S+"),

    # ── Group 2: Admission Requirements ────────────────────────────────
    ("Admission Requirements", "Bachelor's Degree",
        r"\bbachelor"),
    ("Admission Requirements", "GPA Requirement",
        r"\bGPA\b"),
    ("Admission Requirements", "GRE Status",
        r"\bGRE\b"),
    ("Admission Requirements", "GMAT Status",
        r"\bGMAT\b"),
    ("Admission Requirements", "Work Experience",
        r"(?:work|professional)\s+experience|years?\s+(?:of\s+)?(?:experience|work)"),
    ("Admission Requirements", "LOR Count",
        r"\bLOR\b|letter[s]?\s+of\s+recommendation"),
    ("Admission Requirements", "Statement of Purpose",
        r"statement\s+of\s+purpose|\bSOP\b|personal\s+statement"),

    # ── Group 3: English Proficiency ───────────────────────────────────
    ("English Proficiency", "TOEFL",
        r"\bTOEFL\b"),
    ("English Proficiency", "IELTS",
        r"\bIELTS\b"),
    ("English Proficiency", "Duolingo",
        r"\b(?:Duolingo|DET)\b"),
    ("English Proficiency", "PTE Academic",
        r"\bPTE\b"),
    ("English Proficiency", "Cambridge English",
        r"\b(?:Cambridge\s+English|CAE|CPE|C1\s+Advanced|C2\s+Proficiency)\b"),

    # ── Group 4: Tuition & Fees ────────────────────────────────────────
    ("Tuition & Fees", "Any Tuition Amount",
        r"[\$£€]\s*\d{1,3}|\b(?:USD|GBP|EUR|AUD|CAD|INR|CHF|SGD|HKD|NZD|JPY|SEK|NOK|DKK)\s*\d"),
    ("Tuition & Fees", "International Tuition",
        r"international.{0,30}(?:tuition|fee)|overseas.{0,30}(?:tuition|fee)|non-?(?:eu|resident).{0,30}(?:tuition|fee)"),
    ("Tuition & Fees", "Domestic Tuition",
        r"domestic.{0,30}(?:tuition|fee)|in-state.{0,30}(?:tuition|fee)|home.{0,30}(?:tuition|fee)|local.{0,30}(?:tuition|fee)"),
    ("Tuition & Fees", "Application Fee",
        r"application\s+fee"),
    ("Tuition & Fees", "Living Expenses",
        r"living\s+(?:expense|cost|fee)|cost\s+of\s+living"),
    ("Tuition & Fees", "Fees Waived / Funded",
        r"(?:tuition|fees?)\s*(?:is|are)?\s*(?:waived|covered)|fully\s+funded|tuition[\s-]free|no\s+tuition"),

    # ── Group 5: Application Deadlines ─────────────────────────────────
    ("Application Deadlines", "Fall Intake",
        r"\bfall\b"),
    ("Application Deadlines", "Spring Intake",
        r"\bspring\b"),
    ("Application Deadlines", "Summer Intake",
        r"\bsummer\b"),
    ("Application Deadlines", "Specific Date",
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}\s*,?\s*\d{4}"),
    ("Application Deadlines", "Rolling Admissions",
        r"rolling\s+(?:admission|basis)|year-round|continuous\s+intake"),

    # ── Group 6: Curriculum & Outcomes (section heading presence) ──────
    ("Curriculum & Outcomes", "Curriculum Section",
        r"^##\s+Curriculum"),
    ("Curriculum & Outcomes", "Class Profile Section",
        r"^##\s+Class\s+Profile"),
    ("Curriculum & Outcomes", "Career Outcomes Section",
        r"^##\s+Career\s+Outcomes"),
    ("Curriculum & Outcomes", "Scholarships Section",
        r"^##\s+Scholarships"),

    # ── Group 7: Direct Links & Contacts ───────────────────────────────
    ("Direct Links & Contacts", "Apply Now Link",
        r"apply\s+now|application\s+portal|\[Apply\]|apply\s+online|apply\s+here"),
    ("Direct Links & Contacts", "Admissions Email",
        r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    ("Direct Links & Contacts", "Phone Number",
        r"\+?\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}"),
    ("Direct Links & Contacts", "Sources Section",
        r"^##\s+Sources"),
]


def main():
    if not MD_DIR.exists():
        print(f"ERROR: {MD_DIR} does not exist")
        return

    # Compile patterns once
    compiled = [(g, name, re.compile(p, re.I | re.M)) for g, name, p in FIELDS]

    # Find all md files (skip Mac OS metadata files)
    md_files = []
    for f in MD_DIR.rglob("*.md"):
        if "__MACOSX" in str(f) or f.name.startswith("._"):
            continue
        md_files.append(f)
    total = len(md_files)
    print(f"Scanning {total:,} masters md files...")

    # Per-field counts + per-group "any field hit" counts
    field_counts = {(g, name): 0 for g, name, _ in compiled}
    group_any_counts = defaultdict(int)

    for i, f in enumerate(md_files):
        if i and i % 2000 == 0:
            print(f"  {i:,} / {total:,}...")
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        group_hits = set()
        for g, name, pat in compiled:
            if pat.search(text):
                field_counts[(g, name)] += 1
                group_hits.add(g)
        for g in group_hits:
            group_any_counts[g] += 1

    # Preserve order of groups as defined in FIELDS
    seen_groups = []
    for g, _, _ in compiled:
        if g not in seen_groups:
            seen_groups.append(g)

    output = []
    for g in seen_groups:
        section_pct = round(group_any_counts[g] / total * 100, 1) if total else 0
        fields_out = []
        for grp, name, _ in compiled:
            if grp == g:
                cnt = field_counts[(grp, name)]
                fields_out.append({
                    "name": name,
                    "pct": round(cnt / total * 100, 1) if total else 0,
                })
        output.append({"group": g, "section_pct": section_pct, "fields": fields_out})

    # Backup existing file
    if OUTPUT.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = OUTPUT.parent / f"masters_coverage.backup_{ts}.json"
        shutil.copy2(OUTPUT, backup)
        print(f"\nBackup: {backup.name}")

    OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUTPUT.name}")
    print(f"Total files scanned: {total:,}\n")

    # Print summary
    print("=" * 70)
    for entry in output:
        print(f"\n[{entry['group']}]  section_pct = {entry['section_pct']}%")
        for fld in entry["fields"]:
            bar = "#" * max(1, int(fld["pct"] / 5))
            print(f"  {fld['name']:<30s} {fld['pct']:>5.1f}%  {bar}")


if __name__ == "__main__":
    main()
