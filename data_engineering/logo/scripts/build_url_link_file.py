"""
build_url_link_file.py — merge source 27K + batch domains into a logo-URL-ready file.

Inputs:
  - query_result_2026-05-06T14_11_02.778206369+05_30.json  (27,281 records: id, name, isActive)
  - batches_27k/batch_000.json ... batch_054.json          (15,046 with domain)

Output:
  - query_results_url_link.json  (~15,046 records)

Each output record:
  {
    "id": "6,186",
    "name": " NMIMS",                  # preserved as-is (no whitespace stripping)
    "isActive": "true",
    "domain": "nmims.edu",
    "logo_url": "https://www.google.com/s2/favicons?domain=nmims.edu&sz=128"
  }

No API calls — purely deterministic merge. Runtime ~2 seconds.
"""

import json
import re
from pathlib import Path

HERE = Path(__file__).parent
SRC_27K = HERE / "query_result_2026-05-06T14_11_02.778206369+05_30.json"
BATCH_DIR = HERE / "batches_27k"
OUT = HERE / "query_results_url_link.json"

FAVICON_SIZE = 128
S2_TEMPLATE = "https://www.google.com/s2/favicons?domain={domain}&sz={size}"


def id_to_int(id_str: str) -> int:
    """Convert '6,185' -> 6185 for sorting."""
    return int(re.sub(r"[^\d]", "", id_str or "0"))


def main():
    # ── Load source 27K (canonical id -> {name, isActive}) ─────────────
    src_records = json.loads(SRC_27K.read_text(encoding="utf-8"))
    src_by_id = {r["id"]: r for r in src_records}
    print(f"Loaded source: {len(src_records):,} records from {SRC_27K.name}")

    # ── Walk batches, collect records with non-null domain ─────────────
    batch_files = sorted(BATCH_DIR.glob("batch_*.json"))
    if not batch_files:
        raise SystemExit(f"ERROR: no batch files found in {BATCH_DIR}")
    print(f"Found {len(batch_files)} batch files")

    merged: dict[str, dict] = {}
    no_match_in_source = 0
    for bf in batch_files:
        batch = json.loads(bf.read_text(encoding="utf-8"))
        for r in batch:
            domain = (r.get("domain") or "").strip()
            if not domain:
                continue
            src = src_by_id.get(r["id"])
            if not src:
                no_match_in_source += 1
                continue
            merged[r["id"]] = {
                "id": r["id"],
                "name": src["name"],          # canonical, no whitespace strip
                "isActive": src["isActive"],
                "domain": domain,
                "logo_url": S2_TEMPLATE.format(domain=domain, size=FAVICON_SIZE),
            }

    if no_match_in_source:
        print(f"  WARN: {no_match_in_source} batch records had no matching id in source 27K (skipped)")

    # ── Sort by numeric ID ascending (preserves original record order) ─
    out_list = sorted(merged.values(), key=lambda r: id_to_int(r["id"]))

    # ── Write output ───────────────────────────────────────────────────
    OUT.write_text(
        json.dumps(out_list, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # ── Stats ─────────────────────────────────────────────────────────
    unique_domains = len({r["domain"] for r in out_list})
    file_size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"\nWrote {len(out_list):,} records to {OUT.name}")
    print(f"  Unique domains: {unique_domains:,}")
    print(f"  File size:      {file_size_mb:.2f} MB")

    print(f"\nFirst 3 records (sanity check):")
    for r in out_list[:3]:
        print(f"  id={r['id']!r}  name={r['name']!r}  domain={r['domain']!r}")
        print(f"    logo_url={r['logo_url']}")


if __name__ == "__main__":
    main()
