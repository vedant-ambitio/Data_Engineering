"""
Step 2: Convert batch JSON files from crawl_missing_fast.py into individual
page files in pages/ and update url_index.json.

Same format as our existing pages — classifiers work unchanged.
"""

import json
import hashlib
import time
import sys
import io
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
BATCH_DIR = BASE / "crawl_results_fast"
PAGES_DIR = BASE / "official_urls" / "crawled_data" / "pages"
INDEX_FILE = BASE / "official_urls" / "crawled_data" / "url_index.json"


def url_hash(url):
    return hashlib.md5(url.encode()).hexdigest()[:12]


def main():
    PAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing index
    if INDEX_FILE.exists():
        with open(INDEX_FILE, encoding="utf-8") as f:
            url_index = json.load(f)
    else:
        url_index = {}

    # Count existing
    existing = len(url_index)
    print(f"Existing index entries: {existing}")
    print(f"Existing page files: {len(list(PAGES_DIR.glob('*.json')))}")

    # Process all batch files
    batch_files = sorted(BATCH_DIR.glob("batch_*.json"))
    print(f"Batch files to process: {len(batch_files)}")

    added = 0
    skipped = 0

    for bf in batch_files:
        try:
            batch_data = json.loads(bf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  Error reading {bf.name}: {e}")
            continue

        for url, content in batch_data.items():
            h = url_hash(url)

            # Skip if already exists
            if (PAGES_DIR / f"{h}.json").exists():
                skipped += 1
                continue

            # Write page file in our standard format
            page_data = {
                "url": url,
                "status": "success",
                "content": content,
                "error": "",
                "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "source": "crawl_fast_aiohttp",
            }
            with open(PAGES_DIR / f"{h}.json", "w", encoding="utf-8") as f:
                json.dump(page_data, f, ensure_ascii=False)

            url_index[url] = h
            added += 1

        print(f"  {bf.name}: processed ({added} added, {skipped} skipped so far)", flush=True)

    # Save updated index
    tmp = INDEX_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(url_index, f, indent=2, ensure_ascii=False)
    tmp.replace(INDEX_FILE)

    print(f"\nDone!")
    print(f"  New pages added: {added}")
    print(f"  Skipped (already existed): {skipped}")
    print(f"  Total index entries: {len(url_index)}")
    print(f"  Total page files: {len(list(PAGES_DIR.glob('*.json')))}")


if __name__ == "__main__":
    main()
