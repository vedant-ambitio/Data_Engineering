"""
build_gaps_registry.py  (UG edition)

Builds the per-course UG (undergraduate) gaps registry from the
classification_results/ug/classification_report.json.

UG report structure differs from Masters/PhD:
  - Uses a `components` dict (not `entities`) under admission_requirements
  - Component statuses are UPPERCASE: "MATCHED" / "MISMATCHED" / "NOT_VERIFIABLE"
  - Only 5 components tracked: SAT, ACT, TOEFL, IELTS, GPA
    (vs 11+ entities for Masters/PhD; no GRE/GMAT/Cambridge/PTE/Duolingo/LOR
    because those are not standard for US undergraduate admissions)

Sections are the same as Masters/PhD:
  - tuition_and_fees
  - application_deadlines
  - admission_requirements (rolled up via component statuses)

Gap statuses we treat as needing re-verification (medium severity):
  - section status: "flagged", "partially_verified"
  - component status: "MISMATCHED", "NOT_VERIFIABLE"

UG MDs use a different citation format than Masters/PhD:
  - Inline `(Source: url)` after each fact in the body
  - No <citation> blocks with `status: missing` markers
  - End with a `## Grounding Data` JSON section
  -> The status:missing parser is kept for parity, but will always return [].

Output (in ug_v2/gaps/):
  - gaps_registry.json : list of courses with their gaps
  - stats.json         : totals + breakdowns for quick inspection

No Gemini calls. No MD modifications. Pure summarization.

Usage:
    python ug_v2/scripts/build_gaps_registry.py
    python ug_v2/scripts/build_gaps_registry.py --limit 10   (stratified pilot)
"""

import argparse
import json
import sys
import io
from pathlib import Path
from collections import Counter

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
REPORT = BASE / "classification_results" / "ug" / "classification_report.json"
MD_DIR = BASE / "ug_data" / "ug"
OUT_DIR = BASE / "ug_v2" / "gaps"

# Section-level gap statuses (same as Masters/PhD).
GAP_SECTION_STATUSES = {"flagged", "partially_verified"}

# UG uses UPPERCASE component statuses (different from Masters/PhD's lowercase
# entity statuses). "NOT_VERIFIABLE" is UG's equivalent of "not_in_crawled".
GAP_COMPONENT_STATUSES = {"MISMATCHED", "NOT_VERIFIABLE"}


def parse_md_missing_citations(md_text):
    """
    Walk through the MD line-by-line. For each <citation>...</citation> block
    with `status: missing`, attribute it to the nearest preceding `## Heading`.
    Returns: list of {section_heading, line_number, notes, urls}
    """
    lines = md_text.split("\n")
    results = []
    in_block = False
    block_start = -1
    block_lines = []

    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped == "<citation>":
            in_block = True
            block_start = i
            block_lines = []
        elif stripped == "</citation>":
            if not in_block:
                continue
            status = None
            notes = None
            urls = []
            in_urls = False
            for bl in block_lines:
                s = bl.strip()
                if s.startswith("status:"):
                    status = s[len("status:"):].strip()
                    in_urls = False
                elif s.startswith("urls:"):
                    rest = s[len("urls:"):].strip()
                    if rest in ("", "[]"):
                        in_urls = False
                    else:
                        in_urls = True
                elif s.startswith("notes:"):
                    notes = s[len("notes:"):].strip()
                    in_urls = False
                elif in_urls and s.startswith("- "):
                    urls.append(s[2:].strip())
                elif s and not s.startswith("- "):
                    in_urls = False

            if status == "missing":
                section_heading = "Unknown"
                for j in range(block_start - 1, -1, -1):
                    if lines[j].startswith("## "):
                        section_heading = lines[j][3:].strip()
                        break
                results.append({
                    "section_heading": section_heading,
                    "line_number": block_start + 1,
                    "notes": notes,
                    "urls_already_tried": urls,
                })
            in_block = False
            block_lines = []
        elif in_block:
            block_lines.append(raw)

    return results


def extract_report_gaps(r):
    """From one UG classification_report result, return list of section + component gaps.

    admission_requirements is intentionally NOT emitted as a section-level gap:
    its section status is a roll-up of the 5 component statuses (the reason
    string follows the pattern `X_matched_Y_mismatched`). The component-level
    gaps below already cover every issue, so we skip the section to avoid
    duplicate work and to keep Gemini prompts focused on the specific mismatched
    component (SAT/ACT/TOEFL/IELTS/GPA).

    UG-specific differences from the Masters/PhD scripts:
      - Component dict key is `components`, not `entities`.
      - Values are plain status strings ("MATCHED" / "MISMATCHED" /
        "NOT_VERIFIABLE"), not dicts with `{status, md_value, crawled_value}`.
      - md_value / crawled_value are not present at component level in the UG
        report, so we omit them from the gap entries.
    """
    gaps = []
    for section, status in r.get("sections", {}).items():
        if section == "admission_requirements":
            continue
        if status in GAP_SECTION_STATUSES:
            sd = r.get("section_details", {}).get(section, {})
            gaps.append({
                "type": "section",
                "field": section,
                "status": status,
                "reason": sd.get("reason", ""),
                "crawled_urls": sd.get("crawled_urls", []),
                "details": sd.get("details", {}),
            })
    adm_details = r.get("section_details", {}).get("admission_requirements", {}).get("details", {})
    components = adm_details.get("components", {}) or {}
    for comp_name, comp_status in components.items():
        # In UG the value is a plain status string, not a dict.
        if isinstance(comp_status, dict):
            comp_status = comp_status.get("status", "")
        if comp_status in GAP_COMPONENT_STATUSES:
            gaps.append({
                "type": "component",
                "field": comp_name,
                "section": "admission_requirements",
                "status": comp_status,
            })
    return gaps


def stratified_sample(results, n):
    """Pick `n` courses spread across the three tiers (roughly 1/3 each)."""
    buckets = {"high_confidence": [], "moderate_confidence": [], "low_confidence": []}
    for r in results:
        t = r.get("tier", "")
        if t in buckets:
            buckets[t].append(r)
    per_tier = max(1, n // 3)
    sample = []
    for tier in ("high_confidence", "moderate_confidence", "low_confidence"):
        sample.extend(buckets[tier][:per_tier])
    while len(sample) < n and results:
        for r in results:
            if r not in sample:
                sample.append(r)
                if len(sample) >= n:
                    break
    return sample[:n]


def main():
    parser = argparse.ArgumentParser(description="Build per-course UG gaps registry for re-verification.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only N courses (stratified sample across tiers). Writes to *_pilot_N.json.")
    args = parser.parse_args()

    if args.limit:
        registry_out = OUT_DIR / f"gaps_registry_pilot_{args.limit}.json"
        stats_out = OUT_DIR / f"stats_pilot_{args.limit}.json"
    else:
        registry_out = OUT_DIR / "gaps_registry.json"
        stats_out = OUT_DIR / "stats.json"

    print(f"Reading classification report:\n  {REPORT}")
    with open(REPORT, encoding="utf-8") as f:
        report = json.load(f)
    results = report["results"]
    print(f"  {len(results):,} classified courses\n")

    if args.limit:
        results = stratified_sample(results, args.limit)
        print(f"PILOT MODE: processing {len(results)} stratified-sampled courses\n")

    print("Processing courses + parsing MDs for status:missing...")
    registry = []
    stats = {
        "total_courses_in_report": len(results),
        "courses_with_any_gap": 0,
        "courses_clean_skipped": 0,
        "courses_md_not_found": 0,
        "total_report_gaps": 0,
        "total_md_missing_gaps": 0,
        "report_section_gaps": Counter(),
        "report_component_gaps": Counter(),   # UG: components, not entities
        "md_section_gaps": Counter(),
        "gaps_per_course_histogram": Counter(),
        "by_tier": Counter(),
    }

    for i, r in enumerate(results):
        if i and i % 2000 == 0:
            print(f"  {i:,} / {len(results):,}...")

        college = r["college"]
        filename = r["file"]
        md_path = MD_DIR / college / filename

        report_gaps = extract_report_gaps(r)

        md_gaps = []
        if md_path.exists():
            try:
                md_text = md_path.read_text(encoding="utf-8", errors="replace")
                md_gaps = parse_md_missing_citations(md_text)
            except Exception:
                pass
        else:
            stats["courses_md_not_found"] += 1

        if not report_gaps and not md_gaps:
            stats["courses_clean_skipped"] += 1
            continue

        total = len(report_gaps) + len(md_gaps)
        stats["courses_with_any_gap"] += 1
        stats["total_report_gaps"] += len(report_gaps)
        stats["total_md_missing_gaps"] += len(md_gaps)
        stats["gaps_per_course_histogram"][total] += 1
        stats["by_tier"][r.get("tier", "unknown")] += 1
        for g in report_gaps:
            if g["type"] == "section":
                stats["report_section_gaps"][f"{g['field']} :: {g['status']}"] += 1
            else:  # type == "component" (UG-specific)
                stats["report_component_gaps"][f"{g['field']} :: {g['status']}"] += 1
        for g in md_gaps:
            stats["md_section_gaps"][g["section_heading"]] += 1

        try:
            md_rel = str(md_path.relative_to(BASE)).replace("\\", "/")
        except ValueError:
            md_rel = str(md_path).replace("\\", "/")

        registry.append({
            "course_id": f"{college}__{filename}",
            "college": college,
            "file": filename,
            "md_path": md_rel,
            "tier": r.get("tier"),
            "confidence_score": r.get("confidence_score"),
            "confidence_label": r.get("confidence_label"),
            "total_gaps": total,
            "report_gaps": report_gaps,
            "md_gaps": md_gaps,
        })

    for k in ("report_section_gaps", "report_component_gaps", "md_section_gaps",
              "gaps_per_course_histogram", "by_tier"):
        stats[k] = dict(stats[k])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    registry_out.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    stats_out.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    reg_size = registry_out.stat().st_size / (1024 * 1024)
    print(f"\nWrote {registry_out.name}  ({reg_size:.1f} MB)")
    print(f"Wrote {stats_out.name}\n")

    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"Classified courses in report:        {stats['total_courses_in_report']:>7,}")
    print(f"Courses with any gap (in registry):  {stats['courses_with_any_gap']:>7,}")
    print(f"Courses fully clean (skipped):       {stats['courses_clean_skipped']:>7,}")
    print(f"Courses where MD not found:          {stats['courses_md_not_found']:>7,}")
    print(f"Total report-based gaps:             {stats['total_report_gaps']:>7,}")
    print(f"Total MD `status: missing` gaps:     {stats['total_md_missing_gaps']:>7,}")
    grand_total = stats['total_report_gaps'] + stats['total_md_missing_gaps']
    print(f"GRAND TOTAL gaps to resolve:         {grand_total:>7,}")

    print("\nGaps per tier:")
    for k, v in sorted(stats["by_tier"].items()):
        print(f"  {k:<25s} {v:>6,}")

    print("\nReport SECTION gaps (top 10):")
    for k, v in sorted(stats["report_section_gaps"].items(), key=lambda x: -x[1])[:10]:
        print(f"  {k:<50s} {v:>6,}")

    print("\nReport COMPONENT gaps (UG: SAT/ACT/TOEFL/IELTS/GPA):")
    for k, v in sorted(stats["report_component_gaps"].items(), key=lambda x: -x[1])[:10]:
        print(f"  {k:<50s} {v:>6,}")

    print("\nMD `status: missing` by section heading (top 15):")
    for k, v in sorted(stats["md_section_gaps"].items(), key=lambda x: -x[1])[:15]:
        print(f"  {k:<50s} {v:>6,}")

    print("\nGaps-per-course distribution:")
    hist = stats["gaps_per_course_histogram"]
    for n in sorted(hist.keys())[:20]:
        bar = "#" * min(50, hist[n] // max(1, max(hist.values()) // 50))
        print(f"  {n:>3d} gaps:  {hist[n]:>6,}  {bar}")


if __name__ == "__main__":
    main()
