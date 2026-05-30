"""
build_v2_filtered.py — filter favicon_analysis.json down to real favicons only.

Reads:  favicon_analysis.json  (15,046 records with classification)
Writes: query_results_url_link_v2.json  (12,847 records — real favicons only)

Drops globe placeholders + errors, keeps only the original 5-field schema:
  {id, name, isActive, domain, logo_url}

Does NOT touch query_results_url_link.json.
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "favicon_analysis.json"
OUT = HERE / "query_results_url_link_v2.json"

OUTPUT_FIELDS = ["id", "name", "isActive", "domain", "logo_url"]


def main():
    data = json.loads(SRC.read_text(encoding="utf-8"))
    print(f"Loaded {len(data):,} records from {SRC.name}")

    # Filter to real favicons only
    real = [r for r in data if r.get("classification") == "real_favicon"]
    print(f"  real_favicon:      {len(real):,}")
    print(f"  globe_placeholder: {sum(1 for r in data if r.get('classification') == 'globe_placeholder'):,}")
    print(f"  error:             {sum(1 for r in data if r.get('classification') == 'error'):,}")

    # Strip analysis fields, keep only the original 5
    filtered = [{k: r[k] for k in OUTPUT_FIELDS if k in r} for r in real]

    OUT.write_text(
        json.dumps(filtered, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    file_size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"\nWrote {len(filtered):,} records to {OUT.name}")
    print(f"  File size: {file_size_mb:.2f} MB")

    print(f"\nFirst 3 records (sanity check):")
    for r in filtered[:3]:
        print(f"  id={r['id']!r}  name={r['name']!r}")
        print(f"    domain={r['domain']!r}")
        print(f"    logo_url={r['logo_url']}")


if __name__ == "__main__":
    main()
