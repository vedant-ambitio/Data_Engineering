"""
Crawl remaining 9,880 URLs using crawl4ai browser (for JS-heavy sites).
Low concurrency to avoid stalling. Saves to pages/ directly + updates index.
"""

import asyncio
import hashlib
import json
import os
import sys
import io
import time
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "utf-8"
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# Must run from outside course_data/crawl4ai to avoid import shadow
sys.path.insert(0, str(Path(r"c:\Users\HP\OneDrive\Desktop\course_data\crawl4ai")))

from crawl4ai import AsyncWebCrawler, BrowserConfig

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
MISSING_FILE = BASE / "still_missing_urls.txt"
PAGES_DIR = BASE / "official_urls" / "crawled_data" / "pages"
INDEX_FILE = BASE / "official_urls" / "crawled_data" / "url_index.json"

CONCURRENCY = 5       # low to avoid stalling
TIMEOUT = 25           # seconds per URL
BROWSER_RESTART = 50   # restart browser every N pages
SAVE_EVERY = 10        # save index every N successful pages


def url_hash(url):
    return hashlib.md5(url.encode()).hexdigest()[:12]


def save_page(url, content):
    h = url_hash(url)
    page_data = {
        "url": url,
        "status": "success",
        "content": content,
        "error": "",
        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "crawl4ai_browser",
    }
    with open(PAGES_DIR / f"{h}.json", "w", encoding="utf-8") as f:
        json.dump(page_data, f, ensure_ascii=False)
    return h


def save_index(idx):
    tmp = INDEX_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    tmp.replace(INDEX_FILE)


async def crawl_one(crawler, url, sem):
    async with sem:
        try:
            result = await asyncio.wait_for(crawler.arun(url=url), timeout=TIMEOUT)
            content = result.markdown or ""
            if hasattr(content, 'raw_markdown'):
                content = content.raw_markdown
            content = str(content).strip()
            if len(content) > 50:
                return (url, content)
            return (url, None)
        except Exception:
            return (url, None)


async def main():
    PAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Load URLs
    urls = [u.strip() for u in MISSING_FILE.read_text(encoding="utf-8").splitlines() if u.strip()]
    print(f"Total missing URLs: {len(urls)}")

    # Skip already in pages
    remaining = []
    for url in urls:
        if not (PAGES_DIR / f"{url_hash(url)}.json").exists():
            remaining.append(url)
    print(f"Already crawled: {len(urls) - len(remaining)}, remaining: {len(remaining)}")

    if not remaining:
        print("Nothing to crawl!")
        return

    # Load index
    if INDEX_FILE.exists():
        with open(INDEX_FILE, encoding="utf-8") as f:
            url_index = json.load(f)
    else:
        url_index = {}

    sem = asyncio.Semaphore(CONCURRENCY)
    t0 = time.time()
    ok = fail = 0
    total = len(remaining)
    unsaved = 0

    browser_config = BrowserConfig(
        verbose=False,
        headless=True,
        text_mode=True,
        light_mode=True,
        memory_saving_mode=True,
        extra_args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu",
                     "--disable-extensions", "--mute-audio"],
    )

    for chunk_start in range(0, total, BROWSER_RESTART):
        chunk = remaining[chunk_start:chunk_start + BROWSER_RESTART]
        chunk_end = min(chunk_start + BROWSER_RESTART, total)

        print(f"\n-- Browser session {chunk_start+1}-{chunk_end} of {total} --", flush=True)

        try:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                results = await asyncio.gather(
                    *[crawl_one(crawler, url, sem) for url in chunk],
                    return_exceptions=True,
                )

                for item in results:
                    if isinstance(item, Exception):
                        fail += 1
                        continue
                    url, content = item
                    if content:
                        h = save_page(url, content)
                        url_index[url] = h
                        ok += 1
                        unsaved += 1
                    else:
                        fail += 1

                    # Save index frequently
                    if unsaved >= SAVE_EVERY:
                        save_index(url_index)
                        unsaved = 0

        except Exception as e:
            print(f"[BROWSER ERROR] {e}", flush=True)

        # Always save after each browser session
        save_index(url_index)
        unsaved = 0

        elapsed = time.time() - t0
        done = ok + fail
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate / 60 if rate > 0 else 0
        print(
            f"  Progress: {done}/{total} | ok={ok} fail={fail} | "
            f"{rate:.1f}/s | ETA={eta:.0f}m | index={len(url_index)}",
            flush=True,
        )

    elapsed = time.time() - t0
    print(f"\nDone! {ok}/{total} successful in {elapsed/60:.1f} min")
    print(f"Total index entries: {len(url_index)}")
    print(f"Total page files: {len(list(PAGES_DIR.glob('*.json')))}")


if __name__ == "__main__":
    asyncio.run(main())
