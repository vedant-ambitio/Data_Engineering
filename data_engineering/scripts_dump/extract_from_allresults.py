"""
Stream-extract matching URLs from all_results.json into pages/.

- Reads section URLs we need from phd_section_urls.json + masters_section_urls.json
- Checks which already exist in pages/ (skips those)
- Streams through all_results.json (1.4GB) without loading it all into memory
- Saves matching entries as individual page files + updates url_index.json
"""

import hashlib
import json
import re
import sys
import io
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
OFFICIAL_DIR = BASE / "official_urls"
PAGES_DIR = OFFICIAL_DIR / "crawled_data" / "pages"
INDEX_FILE = OFFICIAL_DIR / "crawled_data" / "url_index.json"
ALL_RESULTS = BASE / "all_results.json"


def url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def collect_needed_urls() -> set:
    needed = set()
    for fname in ["phd_section_urls.json", "masters_section_urls.json"]:
        fpath = OFFICIAL_DIR / fname
        if not fpath.exists():
            continue
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        for college, files in data.items():
            for fn, fdata in files.items():
                for sec in ["admission_requirements", "tuition_and_fees", "application_deadlines"]:
                    needed.update(fdata.get(sec, []))
    return needed


def get_already_in_pages(needed: set) -> set:
    already = set()
    for url in needed:
        h = url_hash(url)
        if (PAGES_DIR / f"{h}.json").exists():
            already.add(url)
    return already


def save_page(url: str, content: str, url_to_hash: dict):
    h = url_hash(url)
    page_data = {
        "url": url,
        "status": "success",
        "content": content,
        "error": "",
        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "all_results.json",
    }
    with open(PAGES_DIR / f"{h}.json", "w", encoding="utf-8") as f:
        json.dump(page_data, f, ensure_ascii=False)
    url_to_hash[url] = h


def main():
    PAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Collect needed URLs
    print("Collecting needed URLs from section files...", flush=True)
    needed = collect_needed_urls()
    print(f"  Total needed: {len(needed)}")

    # Step 2: Check which already exist
    print("Checking existing pages...", flush=True)
    already = get_already_in_pages(needed)
    to_find = needed - already
    print(f"  Already in pages/: {len(already)}")
    print(f"  Need to find: {len(to_find)}")

    if not to_find:
        print("Nothing to extract.")
        return

    # Step 3: Load existing url_index
    if INDEX_FILE.exists():
        with open(INDEX_FILE, encoding="utf-8") as f:
            url_to_hash = json.load(f)
    else:
        url_to_hash = {}

    # Step 4: Stream through all_results.json
    # The file is one big JSON dict: {"url": "content", "url2": "content2", ...}
    # We use a streaming JSON parser approach
    print(f"\nStreaming through all_results.json ({ALL_RESULTS.stat().st_size / 1024 / 1024:.0f} MB)...", flush=True)

    found = 0
    scanned = 0
    start_time = time.time()

    # Use json.JSONDecoder for streaming
    decoder = json.JSONDecoder()

    with open(ALL_RESULTS, "r", encoding="utf-8", errors="replace") as f:
        # Skip opening brace
        f.read(1)

        buffer = ""
        while True:
            chunk = f.read(65536)  # Read 64KB at a time
            if not chunk:
                break
            buffer += chunk

            # Process complete key-value pairs from buffer
            while True:
                # Skip whitespace and commas
                buffer = buffer.lstrip(" \t\n\r,")
                if not buffer or buffer[0] == "}":
                    break

                # Try to parse a key
                try:
                    key, end_idx = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    break  # Need more data

                # Skip colon
                rest = buffer[end_idx:].lstrip(" \t\n\r")
                if not rest or rest[0] != ":":
                    break  # Need more data
                rest = rest[1:].lstrip(" \t\n\r")

                # Try to parse value
                try:
                    value, val_end_idx = decoder.raw_decode(rest)
                except json.JSONDecodeError:
                    break  # Need more data

                # Successfully parsed a key-value pair
                buffer = rest[val_end_idx:]
                scanned += 1

                if key in to_find:
                    save_page(key, value, url_to_hash)
                    found += 1
                    to_find.discard(key)

                if scanned % 5000 == 0:
                    elapsed = time.time() - start_time
                    print(
                        f"  Scanned: {scanned} | Found: {found} | "
                        f"Remaining: {len(to_find)} | "
                        f"Time: {elapsed:.0f}s",
                        flush=True,
                    )

                # Early exit if we found everything
                if not to_find:
                    print("  All needed URLs found!", flush=True)
                    break

            if not to_find:
                break

    # Step 5: Save updated index
    tmp = INDEX_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(url_to_hash, f, indent=2, ensure_ascii=False)
    tmp.replace(INDEX_FILE)

    elapsed = time.time() - start_time
    print(f"\nDone in {elapsed:.0f}s")
    print(f"  Scanned: {scanned} entries")
    print(f"  Extracted: {found} new pages")
    print(f"  Total in index: {len(url_to_hash)}")
    print(f"  Total page files: {len(list(PAGES_DIR.glob('*.json')))}")
    print(f"  Still missing: {len(to_find)}")


if __name__ == "__main__":
    main()
