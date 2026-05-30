"""
Enrich top_30_departments.csv with additional alias patterns for Gemini grounding.

We add:
  - Structural prefixes (Department of X, School of X, Faculty of X, College of X)
  - Common additional phrasings per department (researched from the web)

We KEEP all existing aliases and merge new ones in. Aliases are de-duplicated
case-insensitively. Separator is ' ; ' (semicolon-space).
"""
from __future__ import annotations

import csv
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent / "config"
SRC = CONFIG_DIR / "top_30_departments.csv"
DST = CONFIG_DIR / "top_30_departments_enriched.csv"

# Per-department extra aliases (beyond structural prefixes).
# Researched from 2026-04 web searches on department naming conventions.
EXTRA_PER_DEPT: dict[str, list[str]] = {
    "Computer Science": [
        "School of Computing", "School of Computer Science",
        "Department of Computer Science", "Computer Science and Artificial Intelligence",
        "Computer and Information Science", "Computer Science Department",
    ],
    "Business Administration": [
        "School of Business", "Business School", "School of Management",
        "Graduate School of Business", "Graduate School of Management",
        "College of Business", "Faculty of Business", "Business Administration Department",
    ],
    "Economics": [
        "Department of Economics", "School of Economics", "Faculty of Economics",
        "Economics Department",
    ],
    "Mechanical Engineering": [
        "Department of Mechanical Engineering", "School of Mechanical Engineering",
        "Mechanical and Aerospace Engineering", "Mechanical Science and Engineering",
        "Mechanical & Materials Engineering",
    ],
    "Electrical Engineering": [
        "Department of Electrical Engineering", "School of Electrical Engineering",
        "Electrical and Computer Engineering", "ECE",
        "Electrical Engineering and Computer Science",
    ],
    "Medicine": [
        "School of Medicine", "Medical School", "Faculty of Medicine",
        "Faculty of Medicine and Health Sciences", "College of Medicine",
        "Medicine Faculty",
    ],
    "Mathematics": [
        "Department of Mathematics", "School of Mathematics", "Mathematical Sciences",
        "Faculty of Mathematics", "Mathematics Department",
    ],
    "Physics": [
        "Department of Physics", "School of Physics", "Physics Department",
        "Physical Sciences", "Faculty of Physics",
    ],
    "Chemistry": [
        "Department of Chemistry", "School of Chemistry", "Faculty of Chemistry",
        "Chemistry Department",
    ],
    "Biology": [
        "Department of Biology", "School of Biological Sciences",
        "Faculty of Biology", "Life Sciences Division", "Biology Department",
        "Department of Biological Sciences",
    ],
    "Civil Engineering": [
        "Department of Civil Engineering", "School of Civil Engineering",
        "Civil and Architectural Engineering",
        "Civil, Environmental and Architectural Engineering",
    ],
    "Chemical Engineering": [
        "Department of Chemical Engineering", "School of Chemical Engineering",
        "Chemical and Biological Engineering", "Chemical & Biomolecular Engineering",
    ],
    "Psychology": [
        "Department of Psychology", "School of Psychology", "Psychology Department",
        "Psychology and Neuroscience",
    ],
    "Law": [
        "School of Law", "Law School", "Faculty of Law", "College of Law",
        "Law Faculty",
    ],
    "Statistics & Data Science": [
        "Department of Statistics", "School of Statistics", "Statistics Department",
        "Data Science Institute", "Data Sciences",
    ],
    "Finance": [
        "Department of Finance", "Finance Department", "Finance Area",
    ],
    "Public Health": [
        "Department of Public Health", "Faculty of Public Health",
        "Public Health Sciences", "School of Public Health & Tropical Medicine",
    ],
    "Materials Science & Engineering": [
        "Department of Materials Science", "School of Materials",
        "Department of Materials", "Materials Science & Metallurgy Department",
    ],
    "Architecture": [
        "School of Architecture", "Department of Architecture", "Faculty of Architecture",
        "Architecture School", "College of Architecture",
    ],
    "Political Science": [
        "Department of Political Science", "School of Politics",
        "Political Studies", "Faculty of Politics", "Government Department",
    ],
    "Sociology": [
        "Department of Sociology", "School of Sociology", "Sociology Department",
        "Department of Sociology and Anthropology",
    ],
    "Earth & Environmental Sciences": [
        "Department of Earth Sciences", "School of Earth and Environment",
        "Geography and Environment", "Environmental Sciences",
        "School of Geography",
    ],
    "Communication & Media Studies": [
        "Department of Communication", "School of Communication",
        "Mass Communication", "Media and Communication", "School of Journalism",
    ],
    "Education": [
        "School of Education", "Faculty of Education", "Graduate School of Education",
        "Department of Education", "College of Education",
    ],
    "Accounting": [
        "Department of Accounting", "School of Accounting", "Accounting Department",
        "Accountancy",
    ],
    "History": [
        "Department of History", "School of History", "History Department",
        "History Faculty",
    ],
    "Philosophy": [
        "Department of Philosophy", "School of Philosophy", "Philosophy Department",
    ],
    "English Language & Literature": [
        "Department of English", "School of English", "English Department",
        "Faculty of English", "English Language and Literature",
    ],
    "Linguistics": [
        "Department of Linguistics", "School of Languages",
        "Linguistics and Applied Linguistics", "Language Sciences",
    ],
    "Art & Design": [
        "School of Art", "Fine Arts Department", "Department of Art",
        "School of Design", "Art School", "Fine Arts Faculty",
    ],
}


def split_existing(val: str) -> list[str]:
    # The CSV uses '; ' as separator
    return [s.strip() for s in val.split(";") if s.strip()]


def dedupe_case_insensitive(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for x in items:
        key = x.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(x.strip())
    return out


def main():
    rows = []
    with open(SRC, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            existing = split_existing(row.get("common_aliases", ""))
            extra = EXTRA_PER_DEPT.get(row["department_name"], [])
            merged = dedupe_case_insensitive(existing + extra)
            row["common_aliases"] = "; ".join(merged)
            rows.append(row)

    with open(DST, encoding="utf-8", newline="", mode="w") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Enriched {len(rows)} departments.")
    print(f"Written to: {DST}")
    print()
    print("=== Sample before/after for 5 departments ===")
    with open(SRC, encoding="utf-8", newline="") as f:
        src_rows = list(csv.DictReader(f))
    for i in [0, 5, 13, 18, 23]:  # CS, Medicine, Law, Architecture, Communication
        src = src_rows[i]
        dst = rows[i]
        print(f"\n{dst['department_name']}:")
        print(f"  BEFORE ({len(split_existing(src['common_aliases']))} aliases): {src['common_aliases']}")
        print(f"  AFTER  ({len(split_existing(dst['common_aliases']))} aliases): {dst['common_aliases']}")


if __name__ == "__main__":
    main()
