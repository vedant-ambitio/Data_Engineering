"""
Merge all chunk indexes + original index into one url_index.json.
Run this after all chunk crawls complete.
"""
import json
from pathlib import Path

CRAWLED_DIR = Path(__file__).parent / "crawled_data"
OUTPUT = CRAWLED_DIR / "url_index.json"

def main():
    merged = {}

    # Load all index files
    for idx_file in sorted(CRAWLED_DIR.glob("*url_index*.json")):
        with open(idx_file, encoding="utf-8") as f:
            data = json.load(f)
        print(f"  {idx_file.name}: {len(data)} entries")
        merged.update(data)

    # Write merged
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"\nMerged into {OUTPUT}: {len(merged)} total unique entries")

if __name__ == "__main__":
    main()
