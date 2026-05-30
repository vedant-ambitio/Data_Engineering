"""
Split universities_top_450.csv into three tier CSVs based on country -> language family.

Tier A — faculty sites operate in English (high expected hit rate, ~78%)
Tier B — European bilingual or Latin-script non-English (medium, ~30–50%)
Tier C — East Asia / non-Latin script (low, ~0–25%)
Tier D — any country not in the mapping (flag for manual review)

Outputs:
    Professors_info/config/universities_tier_A.csv
    Professors_info/config/universities_tier_B.csv
    Professors_info/config/universities_tier_C.csv
    Professors_info/config/universities_tier_D.csv   (only if any unmapped)
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config"
SRC = CONFIG / "universities_top_450.csv"

COUNTRY_TO_TIER: dict[str, str] = {
    # ---- Tier A : faculty directories hosted in English ----
    "United States":     "A",
    "United Kingdom":    "A",
    "Canada":            "A",
    "Australia":         "A",
    "New Zealand":       "A",
    "Ireland":           "A",
    "Singapore":         "A",
    "India":             "A",
    "Hong Kong":         "A",
    "South Africa":      "A",
    "Israel":            "A",   # research-sector operates in English
    "Malaysia":          "A",
    "Saudi Arabia":      "A",

    # ---- Tier B : European bilingual + other Latin-script non-English ----
    "Germany":           "B",
    "France":            "B",
    "Netherlands":       "B",
    "Switzerland":       "B",
    "Sweden":            "B",
    "Denmark":           "B",
    "Norway":            "B",
    "Finland":           "B",
    "Belgium":           "B",
    "Austria":           "B",
    "Italy":             "B",
    "Spain":             "B",
    "Portugal":          "B",
    "Czech Republic":    "B",
    "Poland":            "B",
    "Hungary":           "B",
    "Greece":            "B",
    "Estonia":           "B",
    "Slovenia":          "B",
    "Turkey":            "B",   # Latin alphabet, bilingual English support
    "Brazil":            "B",
    "Mexico":            "B",
    "Chile":             "B",

    # ---- Tier C : East Asia / non-Latin script primary ----
    "China":             "C",
    "Japan":             "C",
    "South Korea":       "C",
    "Taiwan":            "C",
    "Russia":            "C",   # Cyrillic script
}


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Source CSV not found: {SRC}")

    with open(SRC, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    by_tier: dict[str, list[dict]] = defaultdict(list)
    unmapped_countries: Counter = Counter()
    for r in rows:
        country = (r.get("country") or "").strip()
        tier = COUNTRY_TO_TIER.get(country, "D")
        if tier == "D":
            unmapped_countries[country] += 1
        by_tier[tier].append(r)

    # Write tier files
    for tier, tier_rows in by_tier.items():
        out = CONFIG / f"universities_tier_{tier}.csv"
        with open(out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(tier_rows)
        print(f"  wrote {len(tier_rows):4d} rows -> {out.name}")

    # Summary
    print()
    print(f"Total input rows: {len(rows)}")
    print("Tier breakdown:")
    for tier in ("A", "B", "C", "D"):
        n = len(by_tier.get(tier, []))
        if n:
            countries = Counter(r["country"] for r in by_tier[tier])
            top = ", ".join(f"{c}({n})" for c, n in countries.most_common(6))
            print(f"  Tier {tier}: {n:4d}  ->  {top}{' ...' if len(countries) > 6 else ''}")

    if unmapped_countries:
        print()
        print("⚠ Unmapped countries (went to Tier D — review manually):")
        for c, n in unmapped_countries.most_common():
            print(f"  {n:3d}  {c!r}")


if __name__ == "__main__":
    main()
