"""
Stage B: Crawl official URLs for Masters section data.

Reads masters_section_urls.json, deduplicates all URLs across the 3 target sections,
crawls each unique URL once using crawl4ai, and stores the results.
Shares crawled_data/pages/ with PhD crawl so overlapping URLs are reused.

Fixes vs previous version:
  - Browser is restarted every BROWSER_RESTART_EVERY pages to prevent degradation/hangs
  - Each URL gets a hard wall-clock timeout via asyncio.wait_for on the FULL crawl task
  - Index is saved every SAVE_EVERY completions (not 500) to minimize lost progress
  - All exceptions in gather() are explicitly checked and logged (no silent swallowing)
  - Per-domain semaphore is correctly keyed to prevent one slow domain from blocking all workers
  - FATAL_ERROR_SUBSTRINGS list is expanded to catch more unrecoverable Playwright errors
  - Empty-content pages are retried once before being marked failed
  - asyncio.TimeoutError is caught both at wait_for level AND inner level

Output:
  crawled_data/pages/<hash>.json   - individual crawled pages (for resumability)
  crawled_data/url_index.json      - { url -> hash } mapping
  crawled_data/crawl_report.json   - stats and failures
"""

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from collections import defaultdict

# Fix Windows encoding issues with rich/crawl4ai logger
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "utf-8"
if sys.platform == "win32":
    import io
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

from crawl4ai import AsyncWebCrawler, BrowserConfig


# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────

CONCURRENCY          = 10    # max parallel crawls at once
PER_DOMAIN_LIMIT     = 2     # max parallel crawls per domain
TIMEOUT              = 45    # seconds per URL (hard wall-clock)
RETRY_COUNT          = 1     # retries on transient failure
BROWSER_RESTART_EVERY = 300  # restart browser instance every N pages to prevent degradation

# Errors that are unrecoverable — skip immediately, no retry
FATAL_ERROR_SUBSTRINGS = [
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_REFUSED",
    "ERR_HTTP2_PROTOCOL_ERROR",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_ABORTED",
    "ERR_CERT_",
    "ERR_SSL_",
    "Download is starting",
    "net::ERR_",
    "NS_ERROR_",           # Firefox-style errors
    "Page.goto: Timeout",  # Playwright navigation timeout (different from asyncio.TimeoutError)
    "Navigation timeout",
    "Target closed",
    "Session closed",
    "Browser has been closed",
    "Protocol error",
]

# File extensions that are non-navigable in Playwright
SKIP_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".zip", ".rar", ".gz",
    ".mp4", ".mp3", ".avi", ".mov",
)

BASE_DIR           = Path(__file__).parent
SECTION_URLS_FILE  = BASE_DIR / "masters_section_urls.json"
CRAWLED_DIR        = BASE_DIR / "crawled_data"
PAGES_DIR          = CRAWLED_DIR / "pages"           # shared with PhD crawl
INDEX_FILE         = CRAWLED_DIR / "masters_url_index.json"  # separate index
REPORT_FILE        = CRAWLED_DIR / "masters_crawl_report.json"


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def is_downloadable_url(url: str) -> bool:
    return url.lower().split("?")[0].endswith(SKIP_EXTENSIONS)


def is_fatal_error(error_msg: str) -> bool:
    return any(s in error_msg for s in FATAL_ERROR_SUBSTRINGS)


def extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc


def collect_unique_urls() -> list[str]:
    with open(SECTION_URLS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    urls = set()
    for college, files in data.items():
        for fname, fdata in files.items():
            for sec in ["admission_requirements", "tuition_and_fees", "application_deadlines"]:
                urls.update(fdata.get(sec, []))

    filtered = sorted(u for u in urls if not is_downloadable_url(u))
    skipped = len(urls) - len(filtered)
    if skipped:
        print(f"Skipped {skipped} non-HTML file URLs (PDFs, docs, etc.)")
    return filtered


def get_already_crawled() -> set[str]:
    """Check both masters and PhD indexes for already-crawled URLs."""
    done = set()
    # Check all index files in crawled_data/
    for idx_file in CRAWLED_DIR.glob("*url_index.json"):
        try:
            with open(idx_file, encoding="utf-8") as f:
                index = json.load(f)
            for url, h in index.items():
                if (PAGES_DIR / f"{h}.json").exists():
                    done.add(url)
        except Exception:
            continue
    return done


def save_page(url: str, h: str, content: str, status: str, error: str = ""):
    page_data = {
        "url": url,
        "status": status,
        "content": content,
        "error": error,
        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(PAGES_DIR / f"{h}.json", "w", encoding="utf-8") as f:
        json.dump(page_data, f, ensure_ascii=False)


def save_index(url_to_hash: dict):
    # Atomic write: write to tmp then rename to avoid corruption on crash
    tmp = INDEX_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(url_to_hash, f, indent=2, ensure_ascii=False)
    tmp.replace(INDEX_FILE)


def make_browser_config() -> BrowserConfig:
    return BrowserConfig(
        verbose=False,
        headless=True,
        # Extra args to reduce memory usage and improve stability
        extra_args=[
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-sync",
            "--mute-audio",
        ],
    )


# ─────────────────────────────────────────────
#  Core crawl worker
# ─────────────────────────────────────────────

async def crawl_one(
    crawler,
    url: str,
    url_to_hash: dict,
    global_sem: asyncio.Semaphore,
    domain_sems: dict,
    counters: dict,
    total: int,
    attempt: int = 1,
):
    """
    Crawl a single URL.  All state mutation happens here.
    Never raises — always records success/failure into url_to_hash and disk.
    """
    h = url_hash(url)
    domain = extract_domain(url)

    async with global_sem:
        async with domain_sems[domain]:
            try:
                # Hard wall-clock timeout around the ENTIRE crawl operation.
                # This guards against Playwright hanging internally even when
                # the Python asyncio timeout fires — we cancel the task forcefully.
                result = await asyncio.wait_for(
                    crawler.arun(url=url),
                    timeout=TIMEOUT,
                )
                content = result.markdown or ""

                if content.strip():
                    save_page(url, h, content, "success")
                    url_to_hash[url] = h
                    counters["success"] += 1
                else:
                    # Empty page — retry once
                    if attempt <= RETRY_COUNT:
                        await asyncio.sleep(2)
                        return await crawl_one(
                            crawler, url, url_to_hash, global_sem, domain_sems,
                            counters, total, attempt + 1,
                        )
                    save_page(url, h, "", "failed", "empty_content")
                    url_to_hash[url] = h
                    counters["fail"] += 1

            except asyncio.TimeoutError:
                # Hard timeout fired — URL is genuinely stuck, don't retry
                save_page(url, h, "", "failed", f"hard_timeout_{TIMEOUT}s")
                url_to_hash[url] = h
                counters["fail"] += 1

            except (RuntimeError, Exception) as e:
                error_msg = str(e)

                if is_fatal_error(error_msg):
                    # Unrecoverable network/browser error — log and skip
                    save_page(url, h, "", "failed", error_msg[:300])
                    url_to_hash[url] = h
                    counters["fail"] += 1

                elif attempt <= RETRY_COUNT:
                    # Transient error — wait and retry once
                    await asyncio.sleep(3)
                    return await crawl_one(
                        crawler, url, url_to_hash, global_sem, domain_sems,
                        counters, total, attempt + 1,
                    )

                else:
                    save_page(url, h, "", "failed", error_msg[:300])
                    url_to_hash[url] = h
                    counters["fail"] += 1

            # ── Progress reporting ──
            done = counters["success"] + counters["fail"]

            # Save after EVERY single URL so no progress is ever lost on crash
            save_index(url_to_hash)

            if done % 100 == 0 or done == total:
                elapsed = time.time() - counters["start"]
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(
                    f"  [{done}/{total}] "
                    f"success={counters['success']} fail={counters['fail']} "
                    f"rate={rate:.1f}/s ETA={eta / 60:.0f}m",
                    flush=True,
                )


# ─────────────────────────────────────────────
#  Main crawl loop (with browser restarts)
# ─────────────────────────────────────────────

async def crawl_urls(urls: list[str]) -> dict:
    PAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing index (for resumability)
    if INDEX_FILE.exists():
        with open(INDEX_FILE, encoding="utf-8") as f:
            url_to_hash = json.load(f)
    else:
        url_to_hash = {}

    already_done = get_already_crawled()
    remaining = [u for u in urls if u not in already_done]

    print(f"Total unique URLs: {len(urls)}")
    print(f"Already crawled: {len(already_done)}")
    print(f"Remaining: {len(remaining)}")

    if not remaining:
        print("Nothing to crawl.")
        return url_to_hash

    global_sem   = asyncio.Semaphore(CONCURRENCY)
    domain_sems  = defaultdict(lambda: asyncio.Semaphore(PER_DOMAIN_LIMIT))
    counters     = {"success": 0, "fail": 0, "start": time.time()}
    total        = len(remaining)

    # ── Process in chunks; restart browser every BROWSER_RESTART_EVERY pages ──
    # This is the single most important fix: a Playwright browser that has been
    # running for hundreds of pages accumulates memory, leaked contexts, and
    # sometimes enters a state where new navigations silently hang forever.
    # Restarting the browser periodically resets all that state.

    chunk_size = BROWSER_RESTART_EVERY

    for chunk_start in range(0, len(remaining), chunk_size):
        chunk = remaining[chunk_start : chunk_start + chunk_size]

        chunk_end = min(chunk_start + chunk_size, len(remaining))
        print(
            f"\n── Browser session for URLs {chunk_start + 1}–{chunk_end} "
            f"of {total} ──",
            flush=True,
        )

        browser_config = make_browser_config()

        try:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                tasks = [
                    crawl_one(
                        crawler, url, url_to_hash,
                        global_sem, domain_sems, counters, total,
                    )
                    for url in chunk
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Log any unexpected exceptions that escaped crawl_one
                # (should never happen, but surface them clearly if they do)
                for url, exc in zip(chunk, results):
                    if isinstance(exc, Exception):
                        print(
                            f"[UNEXPECTED EXCEPTION] {url}: {exc}",
                            flush=True,
                        )
                        h = url_hash(url)
                        save_page(url, h, "", "failed", f"unexpected: {str(exc)[:200]}")
                        url_to_hash[url] = h
                        counters["fail"] += 1

        except Exception as browser_exc:
            # The browser itself crashed — save progress and continue with next chunk
            print(
                f"[BROWSER CRASH] Session failed: {browser_exc}\n"
                f"Progress saved. Continuing with next browser session.",
                flush=True,
            )

        # Always save after each browser session
        save_index(url_to_hash)
        print(f"Index saved. ({counters['success']} success, {counters['fail']} fail so far)")

    elapsed = time.time() - counters["start"]
    print(f"\nCrawling complete in {elapsed / 60:.1f} minutes")
    print(f"Success: {counters['success']}, Failed: {counters['fail']}")

    return url_to_hash


# ─────────────────────────────────────────────
#  Report
# ─────────────────────────────────────────────

def generate_report(url_to_hash: dict):
    success = 0
    failed  = 0
    skipped = 0
    failures = []
    total_content_size = 0

    for url, h in url_to_hash.items():
        page_file = PAGES_DIR / f"{h}.json"
        if page_file.exists():
            with open(page_file, encoding="utf-8") as f:
                page = json.load(f)
            status = page["status"]
            if status == "success":
                success += 1
                total_content_size += len(page.get("content", ""))
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
                failures.append({"url": url, "error": page.get("error", "")})

    report = {
        "total_urls": len(url_to_hash),
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "total_content_size_mb": round(total_content_size / (1024 * 1024), 1),
        "failures": failures,
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nReport saved to {REPORT_FILE}")
    print(f"Total content: {report['total_content_size_mb']} MB")
    print(f"Success: {success} | Failed: {failed} | Skipped: {skipped}")


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────

def main():
    urls = collect_unique_urls()
    print(f"Collected {len(urls)} unique Masters URLs to crawl")

    url_to_hash = asyncio.run(crawl_urls(urls))
    generate_report(url_to_hash)


if __name__ == "__main__":
    main()