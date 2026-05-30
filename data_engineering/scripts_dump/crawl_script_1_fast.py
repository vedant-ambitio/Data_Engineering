"""
Fast crawl of missing URLs using aiohttp + html2text (no browser).
Reads from missing_urls.txt, outputs batch JSON files.
"""

import asyncio
import json
import sys
import io
import time
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import html2text

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
MISSING_FILE = BASE / "missing_urls.txt"
OUTPUT_DIR = BASE / "crawl_results_fast"

WORKERS = 80
BATCH_SIZE = 1000
TIMEOUT = 20

SKIP_EXT = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            '.zip', '.gz', '.tar', '.rar', '.mp4', '.mp3',
            '.jpg', '.jpeg', '.png', '.gif', '.svg', '.ico'}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


def make_converter():
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    return h


def should_skip(url):
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in SKIP_EXT)


async def fetch_one(session, url, converter, sem):
    if should_skip(url):
        return (url, None)
    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                   allow_redirects=True, ssl=False) as resp:
                if resp.status != 200:
                    return (url, None)
                ct = resp.headers.get('Content-Type', '')
                if 'text/html' not in ct and 'application/xhtml' not in ct:
                    return (url, None)
                html = await resp.text(errors='ignore')
                if not html or len(html) < 100:
                    return (url, None)
                md = converter.handle(html)
                if md and len(md.strip()) > 50:
                    return (url, md.strip())
                return (url, None)
        except Exception:
            return (url, None)


async def run():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load URLs
    urls = [u.strip() for u in MISSING_FILE.read_text().splitlines() if u.strip()]
    print(f"Total missing URLs: {len(urls)}")

    # Skip already done
    done = set()
    for f in OUTPUT_DIR.glob("batch_*.json"):
        try:
            done.update(json.loads(f.read_text()).keys())
        except:
            pass
    failed_file = OUTPUT_DIR / "failed_urls.txt"
    if failed_file.exists():
        done.update(failed_file.read_text().strip().split("\n"))

    urls = [u for u in urls if u not in done]
    print(f"Already attempted: {len(done)}, remaining: {len(urls)}")

    if not urls:
        print("Nothing to crawl!")
        return

    sem = asyncio.Semaphore(WORKERS)
    converter = make_converter()
    batch_num = len(list(OUTPUT_DIR.glob("batch_*.json")))
    t0 = time.time()
    total = len(urls)
    ok = fail = completed = 0

    connector = aiohttp.TCPConnector(limit=WORKERS, ttl_dns_cache=300, force_close=False)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        for i in range(0, total, BATCH_SIZE):
            batch = urls[i:i + BATCH_SIZE]
            batch_num += 1

            results = await asyncio.gather(
                *[fetch_one(session, u, converter, sem) for u in batch],
                return_exceptions=True,
            )

            batch_data = {}
            batch_failed = []
            for item in results:
                if isinstance(item, Exception):
                    fail += 1
                    continue
                url, content = item
                completed += 1
                if content:
                    batch_data[url] = content
                    ok += 1
                else:
                    fail += 1
                    batch_failed.append(url)

            if batch_data:
                with open(OUTPUT_DIR / f"batch_{batch_num:04d}.json", "w", encoding="utf-8") as bf:
                    json.dump(batch_data, bf, ensure_ascii=False)

            if batch_failed:
                with open(OUTPUT_DIR / "failed_urls.txt", "a") as f:
                    f.write("\n".join(batch_failed) + "\n")

            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (total - completed) / rate if rate > 0 else 0
            print(
                f"  Batch {batch_num}: {completed}/{total} "
                f"| ok={ok} fail={fail} "
                f"| {rate:.1f}/s ETA={eta:.0f}s",
                flush=True,
            )

    elapsed = time.time() - t0
    print(f"\nDone! {ok}/{total} successful in {elapsed:.0f}s ({ok/elapsed:.1f}/s)")
    print(f"Saved to: {OUTPUT_DIR}")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
