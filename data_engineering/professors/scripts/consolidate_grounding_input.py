#!/usr/bin/env python3
"""
consolidate_grounding_input.py — build final_grounding_input/ from the 3 tier
grounding folders + their unwrap caches.

For every (university, department) pair across Tier A / B / C, write ONE small
JSON file with exactly 4 fields:

  {
    "university":        "<canonical name from tier CSV>",
    "university_domain": "<official_website from tier CSV>",
    "department":        "<as-written in the grounding file>",
    "grounding_chunks":  [ {"url": "<real, unwrapped URL>", "title": "..."} ... ]
  }

Each grounding_chunks entry is unwrapped (no vertexaisearch.cloud.google.com
URLs leak through). Duplicates by URL are deduped. Title is preserved.

Tier-folder mapping (driven by tier CSV membership, NOT by which folder has
files — this avoids picking up stale pilot leftovers for ETH/Tokyo/Tsinghua):

  Tier A unis (276) ----> grounding/                + unwrap_cache.json
  Tier B unis (151) ----> grounding_tier_b/         + unwrap_cache_tier_b.json
  Tier C unis  (23) ----> grounding_tier_c/         + unwrap_cache_tier_c.json

Chunk-resolution priority per file (works uniformly across tiers):
  1) resolved_chunks  (Tier B/C inline — already has real_url)
  2) grounding_metadata.groundingChunks[*].web.uri  ->  unwrap_cache lookup
     (Tier A primary path; Tier B/C fallback if resolved_chunks is empty)

NEVER touches grounding/, grounding_tier_b/, grounding_tier_c/, or any
unwrap_cache file. Only writes to:

  Professors_info/final_grounding_input/<UniSlug>__<DeptSlug>.json   (per-pair)
  Professors_info/final_grounding_input/_index.jsonl                  (flat index, one per line)
  Professors_info/final_grounding_input/_summary.json                 (per-tier stats)

Empty-chunks pairs ARE still written (uniform structure for the downstream runner).

Usage:
  python Professors_info/scripts/consolidate_grounding_input.py
  python Professors_info/scripts/consolidate_grounding_input.py --dry-run
  python Professors_info/scripts/consolidate_grounding_input.py --verify-only
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
#  PATHS
# ══════════════════════════════════════════════════════════════════════════════
PROJECT_ROOT = Path(__file__).resolve().parent.parent       # .../Professors_info
CONFIG_DIR = PROJECT_ROOT / "config"

TIER_CSVS = {
    "A": CONFIG_DIR / "universities_tier_A.csv",
    "B": CONFIG_DIR / "universities_tier_B.csv",
    "C": CONFIG_DIR / "universities_tier_C.csv",
}
GROUNDING_DIRS = {
    "A": PROJECT_ROOT / "grounding",
    "B": PROJECT_ROOT / "grounding_tier_b",
    "C": PROJECT_ROOT / "grounding_tier_c",
}
UNWRAP_CACHES = {
    "A": PROJECT_ROOT / "unwrap_cache.json",
    "B": PROJECT_ROOT / "unwrap_cache_tier_b.json",
    "C": PROJECT_ROOT / "unwrap_cache_tier_c.json",
}
OUT_DIR = PROJECT_ROOT / "final_grounding_input"

log = logging.getLogger("consolidate")


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def slug(s: str) -> str:
    """Match the slug convention used by the grounding runners."""
    return re.sub(r"[^a-zA-Z0-9]+", "_", s or "").strip("_")[:60]


def load_tier_unis(csv_path: Path) -> dict[str, str]:
    """university_name -> official_website. Empty dict if file missing."""
    out: dict[str, str] = {}
    if not csv_path.exists():
        log.warning("Tier CSV not found: %s", csv_path)
        return out
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("university_name") or "").strip()
            site = (row.get("official_website") or "").strip()
            if name:
                out[name] = site
    return out


def load_unwrap_cache(path: Path) -> dict:
    if not path.exists():
        log.warning("Unwrap cache not found (will use empty): %s", path.name)
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Failed to read unwrap cache %s: %s", path.name, e)
        return {}


def extract_chunks(grounding_data: dict, unwrap_cache: dict) -> list[dict]:
    """Return a deduped list of {"url": <real>, "title": <str>} entries.

    Resolution path (in order):
      1) resolved_chunks (Tier B/C inline — real_url already populated)
      2) grounding_metadata.groundingChunks[*].web.uri  +  unwrap_cache lookup
         (Tier A primary; B/C fallback if resolved_chunks is empty/missing)

    Filters out:
      - vertexaisearch.cloud.google.com URLs (leaked redirects)
      - empty/null real URLs
      - duplicates by exact URL match
    """
    out: list[dict] = []
    seen: set[str] = set()

    def add(real_url: Optional[str], title: Optional[str]) -> None:
        if not real_url:
            return
        if "vertexaisearch.cloud.google.com" in real_url:
            return
        if real_url in seen:
            return
        seen.add(real_url)
        out.append({"url": real_url, "title": title or ""})

    # Path 1: resolved_chunks (Tier B/C)
    for c in (grounding_data.get("resolved_chunks") or []):
        add(c.get("real_url"), c.get("title"))

    # Path 2: grounding_metadata.groundingChunks via unwrap_cache (Tier A
    # primary; falls through here if Path 1 yielded nothing).
    if not out:
        gm = grounding_data.get("grounding_metadata") or {}
        for c in (gm.get("groundingChunks") or []):
            web = c.get("web") or {}
            uri = web.get("uri") or ""
            if not uri:
                continue
            info = unwrap_cache.get(uri) or {}
            add(info.get("real_url"), web.get("title"))

    return out


# ══════════════════════════════════════════════════════════════════════════════
#  PAIR PROCESSING (parallelized — OneDrive disk I/O is the bottleneck)
# ══════════════════════════════════════════════════════════════════════════════
def process_grounding_file(args: tuple) -> Optional[dict]:
    """Worker: read one grounding file, build the consolidated row, return it.
    Returns None for malformed files so the main loop can skip them."""
    fpath, tier, uni_name, uni_domain, unwrap_cache = args
    try:
        with open(fpath, encoding="utf-8") as f:
            gdata = json.load(f)
    except Exception:
        return None

    stem = fpath.stem  # "<UniSlug>__<DeptSlug>"
    if "__" not in stem:
        return None
    dept_slug = stem.split("__", 1)[1]

    # Prefer the dept name as written in the grounding file (canonical, nicely
    # cased "Computer Science" rather than "Computer_Science"). Fall back to
    # un-slugged dept_slug if the file lacks that field.
    dept_name = (gdata.get("department") or "").strip() or dept_slug.replace("_", " ")

    chunks = extract_chunks(gdata, unwrap_cache)

    # Use CSV-canonical uni name + domain. Fall back to grounding file's
    # values if the CSV side was empty (defensive).
    domain = uni_domain or (gdata.get("university_domain") or "").strip()

    return {
        "_filename": stem + ".json",
        "_tier": tier,
        "university": uni_name,
        "university_domain": domain,
        "department": dept_name,
        "grounding_chunks": chunks,
    }


def write_pair_json(out_dir: Path, row: dict) -> None:
    payload = {
        "university": row["university"],
        "university_domain": row["university_domain"],
        "department": row["department"],
        "grounding_chunks": row["grounding_chunks"],
    }
    fp = out_dir / row["_filename"]
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
        datefmt="%H:%M:%S",
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Walk inputs and tally what WOULD be written; no files written.")
    p.add_argument("--verify-only", action="store_true",
                   help="Just validate that input folders/CSVs/caches exist; print summary; exit.")
    p.add_argument("--workers", type=int, default=16,
                   help="Parallel reads (helps on OneDrive-synced disks).")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    setup_logging(args.verbose)

    log.info("Project root: %s", PROJECT_ROOT)
    log.info("Output dir:   %s", OUT_DIR)

    # ── Verify inputs ──
    missing_critical = []
    for t, csvp in TIER_CSVS.items():
        log.info("  Tier %s CSV:        %s   %s", t, "OK " if csvp.exists() else "MISSING", csvp.name)
        if not csvp.exists():
            missing_critical.append(str(csvp))
    for t, gd in GROUNDING_DIRS.items():
        n = len(list(gd.glob("*.json"))) if gd.exists() else 0
        log.info("  Tier %s grounding:  %s   %s   (%d files)", t, "OK " if gd.exists() else "MISSING", gd.name, n)
        if not gd.exists():
            missing_critical.append(str(gd))
    for t, up in UNWRAP_CACHES.items():
        log.info("  Tier %s unwrap:     %s   %s", t,
                 "OK " if up.exists() else "(missing — using empty)", up.name)

    if missing_critical:
        log.error("Critical inputs missing — aborting:\n  - " + "\n  - ".join(missing_critical))
        sys.exit(1)

    if args.verify_only:
        log.info("verify-only: inputs look good. Exiting without writing.")
        return

    # ── Load all inputs into memory once ──
    t0 = time.time()
    log.info("Loading tier CSVs and unwrap caches...")
    tier_unis = {t: load_tier_unis(p_) for t, p_ in TIER_CSVS.items()}
    unwrap_caches = {t: load_unwrap_cache(p_) for t, p_ in UNWRAP_CACHES.items()}
    for t, unis in tier_unis.items():
        log.info("  Tier %s: %d unis", t, len(unis))
    for t, c in unwrap_caches.items():
        log.info("  unwrap_cache_%s: %d entries", t, len(c))

    # ── Plan the work: tier-CSV-driven file lookup ──
    tasks: list[tuple] = []  # (fpath, tier, uni_name, uni_domain, unwrap_cache)
    skipped_unis_no_files: list[str] = []

    for t in ("A", "B", "C"):
        gdir = GROUNDING_DIRS[t]
        ucache = unwrap_caches[t]
        for uni_name, uni_domain in tier_unis[t].items():
            uni_slug = slug(uni_name)
            pattern = f"{uni_slug}__*.json"
            uni_files = sorted(gdir.glob(pattern))
            if not uni_files:
                skipped_unis_no_files.append(f"Tier {t}: {uni_name}")
                continue
            for fp in uni_files:
                tasks.append((fp, t, uni_name, uni_domain, ucache))

    log.info("Total grounding files queued: %d  (across %d unis with at least one file; %d unis had no files)",
             len(tasks),
             sum(1 for t in ("A","B","C") for u, dom in tier_unis[t].items()
                 if list(GROUNDING_DIRS[t].glob(f"{slug(u)}__*.json"))),
             len(skipped_unis_no_files))

    if args.dry_run:
        per_tier = Counter(task[1] for task in tasks)
        log.info("[DRY-RUN] Per-tier file counts:")
        for t in ("A", "B", "C"):
            log.info("  Tier %s: %d", t, per_tier.get(t, 0))
        if skipped_unis_no_files[:5]:
            log.info("[DRY-RUN] Universities with no grounding files (first 5):")
            for u in skipped_unis_no_files[:5]:
                log.info("  - %s", u)
        log.info("[DRY-RUN] No files written. Output would go to: %s", OUT_DIR)
        return

    # ── Make output dir ──
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Parallel read + per-pair JSON write ──
    log.info("Processing with %d workers...", args.workers)
    rows: list[dict] = []
    n_done = 0
    n_failed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(process_grounding_file, task) for task in tasks]
        for fut in as_completed(futs):
            res = fut.result()
            if res is None:
                n_failed += 1
                continue
            try:
                write_pair_json(OUT_DIR, res)
                rows.append(res)
                n_done += 1
                if n_done % 500 == 0:
                    log.info("  written %d / %d", n_done, len(tasks))
            except Exception as e:
                log.warning("write failed: %s — %s", res.get("_filename"), e)
                n_failed += 1

    # ── Sort rows by (uni_slug, dept_slug) for deterministic _index.jsonl ──
    rows.sort(key=lambda r: (slug(r["university"]), r["_filename"]))

    # ── Write _index.jsonl ──
    index_path = OUT_DIR / "_index.jsonl"
    with open(index_path, "w", encoding="utf-8") as f:
        for r in rows:
            payload = {
                "university": r["university"],
                "university_domain": r["university_domain"],
                "department": r["department"],
                "grounding_chunks": r["grounding_chunks"],
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    # ── Compute summary ──
    per_tier_stats: dict[str, dict] = {}
    distinct_chunk_domains: set[str] = set()
    chunk_count_hist = Counter()  # chunks_per_pair -> count

    for t in ("A", "B", "C"):
        tier_rows = [r for r in rows if r["_tier"] == t]
        n_pairs = len(tier_rows)
        n_with_chunks = sum(1 for r in tier_rows if r["grounding_chunks"])
        n_empty = n_pairs - n_with_chunks
        total_chunks = sum(len(r["grounding_chunks"]) for r in tier_rows)
        avg_chunks = (total_chunks / n_pairs) if n_pairs else 0.0
        per_tier_stats[t] = {
            "unis_in_csv": len(tier_unis[t]),
            "pairs_written": n_pairs,
            "pairs_with_chunks": n_with_chunks,
            "pairs_empty_chunks": n_empty,
            "total_chunks": total_chunks,
            "avg_chunks_per_pair": round(avg_chunks, 2),
        }

    for r in rows:
        chunk_count_hist[len(r["grounding_chunks"])] += 1
        for c in r["grounding_chunks"]:
            try:
                from urllib.parse import urlparse
                host = urlparse(c["url"]).hostname or ""
                if host:
                    distinct_chunk_domains.add(host)
            except Exception:
                pass

    summary = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_s": round(time.time() - t0, 1),
        "per_tier": per_tier_stats,
        "totals": {
            "pairs_written":         len(rows),
            "pairs_with_chunks":     sum(1 for r in rows if r["grounding_chunks"]),
            "pairs_empty_chunks":    sum(1 for r in rows if not r["grounding_chunks"]),
            "files_failed_to_read":  n_failed,
            "unis_with_no_files":    len(skipped_unis_no_files),
            "distinct_chunk_domains": len(distinct_chunk_domains),
        },
        "chunk_count_histogram": dict(sorted(chunk_count_hist.items())),
        "unis_with_no_files_sample": skipped_unis_no_files[:25],
    }
    summary_path = OUT_DIR / "_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # ── Console report ──
    log.info("=" * 60)
    log.info("DONE in %.1fs", time.time() - t0)
    log.info("Pairs written:           %d", len(rows))
    log.info("  with grounding chunks: %d (%.1f%%)",
             summary["totals"]["pairs_with_chunks"],
             100 * summary["totals"]["pairs_with_chunks"] / max(len(rows), 1))
    log.info("  empty chunks:          %d (%.1f%%)",
             summary["totals"]["pairs_empty_chunks"],
             100 * summary["totals"]["pairs_empty_chunks"] / max(len(rows), 1))
    log.info("Unique chunk domains:    %d", len(distinct_chunk_domains))
    log.info("Read-failures:           %d", n_failed)
    log.info("Universities with NO grounding files: %d", len(skipped_unis_no_files))
    log.info("")
    log.info("Per-tier:")
    for t in ("A", "B", "C"):
        s = per_tier_stats[t]
        log.info("  Tier %s — %d pairs (%d w/ chunks, %d empty), avg %.2f chunks/pair",
                 t, s["pairs_written"], s["pairs_with_chunks"], s["pairs_empty_chunks"],
                 s["avg_chunks_per_pair"])
    log.info("")
    log.info("Outputs:")
    log.info("  Per-pair JSONs:  %s", OUT_DIR)
    log.info("  Flat index:      %s", index_path)
    log.info("  Summary:         %s", summary_path)


if __name__ == "__main__":
    main()
