"""
Re-classify ONLY the 318 patched files listed in courses_patch/patched_files.json.

For each patched file:
  1. Resolve its (college, file) key.
  2. Run the same classification logic as classify_masters.py / classify_phd.py.
  3. Update classification_report.json:
       - Remove any prior entry for (college, file)
       - Append the new entry
       - Recompute tier_counts / confidence_label_counts / section_counts
  4. Move the file copy in classification_results/<kind>/<tier>/:
       - Remove from the old tier dir
       - Copy md into the new tier dir (using the same naming convention)

This script is idempotent — re-running just refreshes the same 318 entries.

Usage:
    python reclassify_patched.py
"""

import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

# Re-use everything from the per-kind classifiers
sys.path.insert(0, str(Path(__file__).parent))
import classify_masters as cm
import classify_phd as cp


BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
PATCH_LOG = BASE / "courses_patch" / "patched_files.json"


def _norm(p: str | Path) -> str:
    """Normalize a path string for comparison."""
    return str(Path(p)).replace("\\", "/").lower()


def resolve_college_and_file(src_path: Path, md_root: Path) -> tuple[str, str] | None:
    """
    Given an md source path, return (college_folder_name, file_name)
    relative to md_root, or None if the path isn't under md_root.
    """
    try:
        rel = src_path.relative_to(md_root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    return parts[0], parts[-1]


def reclassify_kind(kind: str, mod, entries: list[dict]) -> dict:
    """
    Re-classify entries for one kind ('masters' or 'phd') using its module's
    classify_one_file, then patch the report and tier folders.
    """
    md_root = mod.MASTERS_MD_DIR if kind == "masters" else mod.PHD_MD_DIR
    report_path = mod.REPORT_FILE
    tier_dirs = {
        "high_confidence": mod.HIGH_DIR,
        "moderate_confidence": mod.MODERATE_DIR,
        "low_confidence": mod.LOW_DIR,
    }

    print(f"\n=== Re-classifying {kind} ({len(entries)} files) ===")
    if not entries:
        return {}

    print(f"  Loading section URLs + url_index...")
    with open(mod.SECTION_URLS_FILE, encoding="utf-8") as f:
        section_urls = json.load(f)
    with open(mod.INDEX_FILE, encoding="utf-8") as f:
        url_index = json.load(f)

    print(f"  Loading current report...")
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)
    results = report.get("results", [])

    # Build lookup of existing report entries
    by_key = {(r["college"], r["file"]): i for i, r in enumerate(results)}

    new_entries = []
    moves = 0
    tier_changes = []
    fails = 0

    for e in entries:
        src_path = Path(e["source_path"])
        ck = resolve_college_and_file(src_path, md_root)
        if ck is None:
            print(f"  skip (not under md_root): {src_path}")
            fails += 1
            continue
        college, fname = ck

        fdata = section_urls.get(college, {}).get(fname)
        if fdata is None:
            print(f"  skip (no section_urls entry): {college}/{fname}")
            fails += 1
            continue

        result = mod.classify_one_file((college, fname, fdata, url_index, str(md_root)))
        if result is None:
            print(f"  skip (classify_one_file returned None): {college}/{fname}")
            fails += 1
            continue

        # Find prior tier (if any) to handle the file copy move
        prior_idx = by_key.get((college, fname))
        prior_tier = results[prior_idx]["tier"] if prior_idx is not None else None
        new_tier = result["tier"]

        # Move/copy the file into the right tier folder
        dest_name = f"{college}__{fname}"
        if len(str(tier_dirs[new_tier] / dest_name)) > 250:
            dest_name = dest_name[:150] + ".md"

        # Remove any prior copies in OTHER tier dirs (defensive)
        for tname, tdir in tier_dirs.items():
            stale = tdir / dest_name
            if stale.exists() and tname != new_tier:
                stale.unlink()

        shutil.copy2(result["md_path"], tier_dirs[new_tier] / dest_name)
        if prior_tier and prior_tier != new_tier:
            tier_changes.append((college, fname, prior_tier, new_tier,
                                 result["confidence_score"]))
        moves += 1

        # Trim md_path before storing
        result_for_report = {k: v for k, v in result.items() if k != "md_path"}

        if prior_idx is not None:
            results[prior_idx] = result_for_report
        else:
            results.append(result_for_report)
        new_entries.append(result_for_report)

    # Recompute counts from final results
    tier_counts = defaultdict(int)
    label_counts = defaultdict(int)
    section_counts = {
        "tuition_and_fees": defaultdict(int),
        "application_deadlines": defaultdict(int),
        "admission_requirements": defaultdict(int),
    }
    for r in results:
        tier_counts[r["tier"]] += 1
        label_counts[r["confidence_label"]] += 1
        for sec, cls in r["sections"].items():
            section_counts[sec][cls] += 1

    report["total_processed"] = len(results)
    report["tier_counts"] = dict(tier_counts)
    report["confidence_label_counts"] = dict(label_counts)
    report["section_counts"] = {k: dict(v) for k, v in section_counts.items()}
    report["results"] = results

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    print(f"  Re-classified {moves} files, {fails} failed.")
    print(f"  Tier changes: {len(tier_changes)}")
    for college, fname, old, new, score in tier_changes[:25]:
        arrow = "->" if old != new else "=="
        print(f"    {old:>18s} {arrow} {new:<18s}  (score {score})  {college}/{fname[:60]}")
    if len(tier_changes) > 25:
        print(f"    ... and {len(tier_changes) - 25} more")

    return {
        "kind": kind,
        "reclassified": moves,
        "failed": fails,
        "tier_changes": [
            {"college": c, "file": f, "from": o, "to": n, "confidence_score": s}
            for c, f, o, n, s in tier_changes
        ],
        "tier_counts": dict(tier_counts),
    }


def main():
    log = json.loads(PATCH_LOG.read_text(encoding="utf-8"))
    by_kind = {"masters": [], "phd": []}
    for e in log:
        if e["kind"] in by_kind:
            by_kind[e["kind"]].append(e)

    print(f"Patched files to re-classify: masters={len(by_kind['masters'])}, "
          f"phd={len(by_kind['phd'])}, total={len(log)}")

    summary = {
        "masters": reclassify_kind("masters", cm, by_kind["masters"]),
        "phd": reclassify_kind("phd", cp, by_kind["phd"]),
    }

    out = BASE / "courses_patch" / "reclassify_summary.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote summary: {out}")

    # Top-line summary
    for k, s in summary.items():
        n_up = sum(1 for tc in s.get("tier_changes", [])
                   if (tc["from"], tc["to"]) in {
                       ("low_confidence", "moderate_confidence"),
                       ("low_confidence", "high_confidence"),
                       ("moderate_confidence", "high_confidence"),
                   })
        n_down = sum(1 for tc in s.get("tier_changes", [])
                     if (tc["from"], tc["to"]) in {
                         ("moderate_confidence", "low_confidence"),
                         ("high_confidence", "low_confidence"),
                         ("high_confidence", "moderate_confidence"),
                     })
        print(f"  {k}: re-classified {s.get('reclassified', 0)}, "
              f"upgraded {n_up}, downgraded {n_down}")


if __name__ == "__main__":
    main()
