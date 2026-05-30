#!/usr/bin/env python3
"""
run_ug_pipeline.py — Full UG extraction + merge pipeline
=========================================================

Connects Step 1 (structured_data_extraction.py) and Step 2 (merge_course_data.py)
into a single end-to-end run.

Usage:
  # Test on 10 courses
  python run_ug_pipeline.py --max 10

  # Full run for all UG courses
  python run_ug_pipeline.py --all

  # Test on specific university
  python run_ug_pipeline.py --university "Pennsylvania_State_University" --max 6

  # Only run step 2 (merge) on already-extracted files
  python run_ug_pipeline.py --skip-step1

  # Resume a crashed run (skips already-extracted files)
  python run_ug_pipeline.py --all --resume
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────

UG_INPUT_DIR = "classification_results/ug/high_confidence"
STEP1_OUTPUT_DIR = "university_data/structured_extraction_ug"
MERGE_OUTPUT_DIR = "university_data/merged_output_ug"
QA_OUTPUT_DIR = "university_data/merged_output_ug_qa"
BIGFUTURE_RAW_DIR = "bigfuture_data/raw"
BF_MATCH_FILE = "ug_bigfuture_match.json"
UG_CSV_FILE = "ug_programs_data_2026-03-30T18_31_17.92687006+05_30.csv"

# Classification dirs for confidence tier lookup
CONFIDENCE_DIRS = {
    "high": "classification_results/ug/high_confidence",
    "moderate": "classification_results/ug/moderate_confidence",
    "low": "classification_results/ug/low_confidence",
}


def find_ug_files(input_dir, university_filter=None, max_count=0):
    """Find UG markdown files, optionally filtered by university."""
    all_files = []
    for root, dirs, files in os.walk(input_dir):
        for f in files:
            if f.endswith(".md"):
                if university_filter:
                    if not f.startswith(university_filter):
                        continue
                all_files.append(os.path.join(root, f))

    all_files.sort()
    if max_count > 0:
        all_files = all_files[:max_count]

    return all_files


def get_confidence_tier(md_filename):
    """Determine confidence tier from which directory the file is in."""
    for tier, tier_dir in CONFIDENCE_DIRS.items():
        if os.path.exists(os.path.join(tier_dir, md_filename)):
            return tier
    return "unknown"


def find_bf_match(uni_name, bf_match_map):
    """Find BigFuture slug for a university name."""
    # Try exact match on folder name
    for folder, slug in bf_match_map.items():
        if uni_name.lower() == folder.lower():
            return slug
        if uni_name.lower().replace(" ", "_") == folder.lower():
            return slug
    # Try substring
    for folder, slug in bf_match_map.items():
        if uni_name.lower() in folder.lower() or folder.lower() in uni_name.lower():
            return slug
    return None


def load_csv_id_map(csv_path):
    """
    Load UG programs CSV and build lookup lists per university.
    CSV: program_id, university_id, University Name, course_major_name, course_specialization_name, ...
    Returns: {uni_name_lower: [(program_id, university_id, major, specialization, degree_name), ...]}
    """
    uni_map = {}  # uni_lower -> list of (prog_id, uni_id, major, specialization, degree_name)
    if not os.path.exists(csv_path):
        print(f"[WARN] CSV not found: {csv_path}")
        return uni_map

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            uni = (row.get("University Name") or "").strip()
            major = (row.get("course_major_name") or "").strip()
            spec = (row.get("course_specialization_name") or "").strip()
            degree = (row.get("course_degree_name") or "").strip()
            prog_id = (row.get("program_id") or "").strip().replace(",", "")
            uni_id = (row.get("university_id") or "").strip().replace(",", "")
            if uni:
                key = uni.lower()
                if key not in uni_map:
                    uni_map[key] = []
                uni_map[key].append((prog_id, uni_id, major.lower(), spec.lower(), degree.lower()))
    return uni_map


def _strip_degree_prefix(text):
    """Remove degree prefixes to get the core subject."""
    if not text:
        return ""
    t = text.lower().strip()
    for prefix in ["bachelor of science in ", "bachelor of arts in ", "bachelor of science ",
                    "bachelor of arts ", "bachelor of engineering in ", "bachelor of engineering ",
                    "bachelor of fine arts in ", "bachelor of business administration in ",
                    "bs in ", "ba in ", "bs ", "ba ", "beng ", "bfa ", "bba ",
                    "b.s. in ", "b.a. in ", "b.s. ", "b.a. "]:
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t.strip()


def _word_set(text):
    """Get meaningful words from text, removing stop words."""
    stop = {"of", "in", "and", "the", "a", "for", "with", "to", "at", "on", "by"}
    return set(text.lower().split()) - stop


def _word_overlap_score(a, b):
    """Fraction of words in common between two strings."""
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def lookup_ids(uni_map, uni_name, program_name):
    """
    Look up program_id and university_id from the CSV map.
    Searches across major, specialization, and degree_name columns.
    Returns (program_id, university_id) or (None, university_id) or (None, None).
    """
    # Normalize: strip commas, periods, extra spaces for matching
    uni_lower = (uni_name or "").lower().strip().replace(",", "").replace(".", "").replace("  ", " ")
    prog_stripped = _strip_degree_prefix(program_name or "")
    prog_lower = (program_name or "").lower().strip()

    # Find university entries — try exact match first, then normalized match, then substring
    entries = uni_map.get(uni_lower, [])
    if not entries:
        # Try matching with normalized CSV keys (strip commas/periods from CSV uni names too)
        for u, elist in uni_map.items():
            u_norm = u.replace(",", "").replace(".", "").replace("  ", " ")
            if u_norm == uni_lower:
                entries = elist
                break
    if not entries:
        for u, elist in uni_map.items():
            if uni_lower in u or u in uni_lower:
                entries = elist
                break
    if not entries:
        return (None, None)

    # We have entries for this university — university_id is guaranteed
    uni_id = entries[0][1]

    # Strategy 1: exact match on major or specialization
    for prog_id, uid, major, spec, degree in entries:
        if prog_stripped == major or prog_stripped == spec:
            return (prog_id, uid)

    # Strategy 2: stripped program name is substring of major/spec or vice versa
    for prog_id, uid, major, spec, degree in entries:
        if prog_stripped in major or major in prog_stripped:
            return (prog_id, uid)
        if spec and (prog_stripped in spec or spec in prog_stripped):
            return (prog_id, uid)

    # Strategy 3: full program name contains major or spec
    for prog_id, uid, major, spec, degree in entries:
        if major and major in prog_lower:
            return (prog_id, uid)
        if spec and spec in prog_lower:
            return (prog_id, uid)

    # Strategy 4: major or spec contains stripped program name
    for prog_id, uid, major, spec, degree in entries:
        if prog_stripped and prog_stripped in major:
            return (prog_id, uid)
        if prog_stripped and spec and prog_stripped in spec:
            return (prog_id, uid)

    # Strategy 5: word overlap >= 60% on major or specialization
    best_match = None
    best_score = 0.0
    for prog_id, uid, major, spec, degree in entries:
        score_major = _word_overlap_score(prog_stripped, major)
        score_spec = _word_overlap_score(prog_stripped, spec) if spec else 0.0
        score = max(score_major, score_spec)
        if score > best_score:
            best_score = score
            best_match = (prog_id, uid)

    if best_match and best_score >= 0.6:
        return best_match

    # Strategy 6: no program match but university found — return university_id only
    return (None, uni_id)


def main():
    parser = argparse.ArgumentParser(description="UG extraction + merge pipeline")
    parser.add_argument("--max", type=int, default=0, help="Max courses to process (0 = all found)")
    parser.add_argument("--all", action="store_true", help="Process ALL UG courses")
    parser.add_argument("--university", type=str, help="Filter by university folder name prefix")
    parser.add_argument("--input-dir", type=str, default=UG_INPUT_DIR, help="Input markdown directory")
    parser.add_argument("--workers", type=int, default=30, help="Step 1 concurrent workers")
    parser.add_argument("--skip-step1", action="store_true", help="Skip extraction, only run merge")
    parser.add_argument("--skip-step2", action="store_true", help="Skip merge, only run extraction")
    parser.add_argument("--resume", action="store_true", help="Resume: skip already-extracted files")
    parser.add_argument("--step1-output", type=str, default=STEP1_OUTPUT_DIR)
    parser.add_argument("--merge-output", type=str, default=MERGE_OUTPUT_DIR)
    parser.add_argument("--qa-output", type=str, default=QA_OUTPUT_DIR, help="QA sidecar output directory (separate from merge)")
    parser.add_argument("--csv", type=str, default=UG_CSV_FILE, help="UG programs CSV for program_id/university_id lookup")
    args = parser.parse_args()

    step1_output = args.step1_output
    merge_output = args.merge_output
    qa_dir = args.qa_output

    print("=" * 60)
    print("  UG PIPELINE: Extraction + Merge")
    print("=" * 60)

    # ═══════════════════════════════════════════════════════════════
    #  STEP 1: Structured Extraction (markdown → JSON via Gemini)
    # ═══════════════════════════════════════════════════════════════

    if not args.skip_step1:
        print(f"\n{'-'*60}")
        print(f"  STEP 1: Gemini Structured Extraction")
        print(f"{'-'*60}")

        # Find files
        md_files = find_ug_files(args.input_dir, args.university, args.max if not args.all else 0)
        if not md_files:
            print("[ERROR] No markdown files found.")
            sys.exit(1)
        print(f"Found {len(md_files)} markdown files")

        # Build file list for step 1
        os.makedirs(step1_output, exist_ok=True)
        filelist_path = os.path.join(step1_output, "_filelist.txt")
        with open(filelist_path, "w") as f:
            for p in md_files:
                f.write(p + "\n")

        # Build command
        cmd = [
            sys.executable, "structured_data_extraction.py",
            "--file-list", filelist_path,
            "--output-dir", step1_output,
            "--workers", str(args.workers),
        ]
        if args.resume:
            cmd.append("--skip-existing")

        import subprocess
        print(f"Running: {' '.join(cmd)}")
        print()

        start = time.time()
        ret = subprocess.run(cmd).returncode
        elapsed = time.time() - start

        if ret != 0:
            print(f"\n[WARN] Step 1 exited with code {ret}")
        print(f"\nStep 1 completed in {elapsed:.0f}s")
    else:
        print("\nSkipping Step 1 (--skip-step1)")

    # ═══════════════════════════════════════════════════════════════
    #  STEP 2: Merge (extracted JSON + BigFuture → final JSON)
    # ═══════════════════════════════════════════════════════════════

    if not args.skip_step2:
        print(f"\n{'-'*60}")
        print(f"  STEP 2: Merge with BigFuture")
        print(f"{'-'*60}")

        # Import merge function
        from merge_course_data import merge_single_course, load_json

        # Load CSV ID mapping
        csv_id_map = load_csv_id_map(args.csv)
        total_programs = sum(len(v) for v in csv_id_map.values())
        print(f"CSV ID mapping: {total_programs} programs across {len(csv_id_map)} universities")

        # Load BF match mapping
        bf_match = {}
        if os.path.exists(BF_MATCH_FILE):
            bf_match = load_json(BF_MATCH_FILE)
            print(f"BigFuture match mapping: {len(bf_match)} universities")
        else:
            print("[WARN] No BigFuture match file found.")

        # Find extracted JSONs
        extracted_files = sorted(
            f for f in os.listdir(step1_output)
            if f.endswith(".json") and not f.startswith("_")
        )
        print(f"Found {len(extracted_files)} extracted JSON files")

        os.makedirs(merge_output, exist_ok=True)
        os.makedirs(qa_dir, exist_ok=True)

        # Cache BF data per university (avoid re-reading same file for every course)
        bf_cache = {}

        stats = {"total": 0, "merged": 0, "with_bf": 0, "with_ids": 0, "errors": 0}
        start = time.time()

        for filename in extracted_files:
            stats["total"] += 1
            extracted = load_json(os.path.join(step1_output, filename))
            if not extracted:
                stats["errors"] += 1
                continue

            uni_name = extracted.get("university_name", "")
            program_name = extracted.get("program_name", "")
            # Derive folder name from filename: "Uni_Name__Uni_Name_Course.json" -> "Uni_Name"
            uni_folder = filename.split("__")[0] if "__" in filename else uni_name.replace(" ", "_")

            # Look up program_id and university_id from CSV
            prog_id, uni_id = lookup_ids(csv_id_map, uni_name, program_name)
            if prog_id:
                stats["with_ids"] += 1

            # Find BF data (cached per university)
            if uni_folder not in bf_cache:
                bf_slug = find_bf_match(uni_folder, bf_match)
                if bf_slug:
                    bf_path = os.path.join(BIGFUTURE_RAW_DIR, f"{bf_slug}.json")
                    bf_cache[uni_folder] = (bf_slug, load_json(bf_path))
                else:
                    bf_cache[uni_folder] = (None, {})

            bf_slug, bf_data = bf_cache[uni_folder]

            # Determine confidence tier
            md_name = filename.replace(".json", ".md")
            confidence = get_confidence_tier(md_name)

            # Merge
            try:
                final, qa = merge_single_course(
                    extracted, bf_data, confidence, md_name, uni_folder, bf_slug,
                    program_id=prog_id, university_id=uni_id
                )
            except Exception as e:
                print(f"  [ERROR] {filename}: {e}")
                stats["errors"] += 1
                continue

            # Save — organized by university folder
            # "Uni_Name__Uni_Name_Course.json" → folder: "Uni_Name", file: "Course_merged.json"
            if "__" in filename:
                parts = filename.split("__", 1)
                course_name = parts[1]  # "Uni_Name_Course.json"
            else:
                course_name = filename

            uni_out_dir = os.path.join(merge_output, uni_folder)
            uni_qa_dir = os.path.join(qa_dir, uni_folder)
            os.makedirs(uni_out_dir, exist_ok=True)
            os.makedirs(uni_qa_dir, exist_ok=True)

            out_name = course_name.replace(".json", "_merged.json")
            qa_name = course_name.replace(".json", "_qa.json")
            with open(os.path.join(uni_out_dir, out_name), "w", encoding="utf-8") as f:
                json.dump(final, f, indent=2, ensure_ascii=False, default=str)
            with open(os.path.join(uni_qa_dir, qa_name), "w", encoding="utf-8") as f:
                json.dump(qa, f, indent=2, ensure_ascii=False, default=str)

            stats["merged"] += 1
            if bf_slug:
                stats["with_bf"] += 1

            if stats["merged"] % 100 == 0:
                print(f"  Merged {stats['merged']}/{stats['total']}...")

        elapsed = time.time() - start

        print(f"\nStep 2 completed in {elapsed:.0f}s")
        print(f"  Total:     {stats['total']}")
        print(f"  Merged:    {stats['merged']}")
        print(f"  With BF:   {stats['with_bf']}")
        print(f"  With IDs:  {stats['with_ids']}")
        print(f"  Errors:    {stats['errors']}")
        print(f"\nOutput: {merge_output}/")
        print(f"QA:     {qa_dir}/")
    else:
        print("\nSkipping Step 2 (--skip-step2)")

    print(f"\n{'='*60}")
    print("  PIPELINE COMPLETE")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
