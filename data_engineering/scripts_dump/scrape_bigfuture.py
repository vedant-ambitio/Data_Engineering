"""
BigFuture College Data Scraper
Extracts structured college data from bigfuture.collegeboard.org
using embedded __NEXT_DATA__ JSON (no browser needed).

Phase 1: Collect all college slugs from listing page
Phase 2: Visit each college page, extract 139-field JSON, save

Usage:
  python scrape_bigfuture.py --max 10          # test with 10
  python scrape_bigfuture.py                    # all colleges
  python scrape_bigfuture.py --resume           # resume from progress
"""

import requests
import json
import re
import time
import random
import os
import argparse
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────
BASE_URL = "https://bigfuture.collegeboard.org"
OUTPUT_DIR = Path("bigfuture_data")
RAW_DIR = OUTPUT_DIR / "raw"
SLUGS_FILE = OUTPUT_DIR / "college_slugs.json"
PROGRESS_FILE = OUTPUT_DIR / "progress.json"
COMBINED_FILE = OUTPUT_DIR / "bigfuture_all.json"
COMBINED_CSV = OUTPUT_DIR / "bigfuture_all.csv"

MIN_DELAY = 3   # seconds between requests
MAX_DELAY = 7

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
]

# Fields we extract for the flat CSV
CSV_FIELDS = [
    "orgId", "name", "city", "state", "country",
    "schoolTypeByDesignation", "schoolTypeByYears", "schoolSize", "schoolSetting",
    "acceptanceRate", "totalApplicants", "admittedApplicants", "enrolledApplicants",
    "satCompositeScore25thPercentile", "satCompositeScore75thPercentile",
    "rsatMathScore25thPercentile", "rsatMathScore75thPercentile",
    "rsatEbrwScore25thPercentile", "rsatEbrwScore75thPercentile",
    "actCompositeScore25thPercentile", "actCompositeScore75thPercentile",
    "satOrAct",
    "inStateTuition", "outOfStateTuition", "privateTuition",
    "averageHousingCost", "booksAndSuppliesCost",
    "averageNetPrice", "averageAidAwarded", "studentsReceivingAidPercent",
    "financialNeedMet",
    "averageNetPriceBelow30K", "averageNetPrice30To48K",
    "averageNetPrice48To75K", "averageNetPrice75To110K", "averageNetPriceAbove110K",
    "graduationRate", "sophomoreYearReturnPercent", "studentFacultyRatio",
    "earlyActionDate", "earlyDecisionDate", "regularDecisionDate",
    "financialAidApplicationPriorityDeadline",
    "applicationFeeAmount", "highSchoolGpa", "highSchoolRank",
    "prepCourses", "recommendations",
    "totalUndergraduates", "totalGraduates",
    "internationalPercent", "outOfStatePercent",
    "degreesOffered",
]


def get_session():
    """Create a requests session with random User-Agent."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    })
    return s


def load_progress():
    """Load progress from file."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {"completed": [], "failed": [], "last_index": 0}


def save_progress(progress):
    """Save progress to file."""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


# ── Phase 1: Collect slugs ─────────────────────────────────────────────

def collect_slugs_from_listing(session):
    """
    Collect all college slugs from the college search listing.
    The listing page shows 15 colleges at a time with 'Show More'.
    Instead, we use the college-search API endpoint if available,
    or scrape the listing page with pagination.
    """
    print("Phase 1: Collecting college slugs...")

    # Try the listing page - it embeds all college data in __NEXT_DATA__
    url = f"{BASE_URL}/college-search/filters"
    r = session.get(url, timeout=30)
    r.raise_for_status()

    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        r.text
    )
    if not match:
        print("ERROR: Could not find __NEXT_DATA__ on listing page")
        return []

    data = json.loads(match.group(1))
    page_props = data.get("props", {}).get("pageProps", {})

    # Check if all colleges are embedded
    colleges = page_props.get("colleges", [])
    if colleges:
        slugs = []
        for c in colleges:
            slug = c.get("vanityUri") or c.get("slug")
            if slug:
                slugs.append(slug)
        print(f"  Found {len(slugs)} slugs from embedded data")
        return slugs

    # If not embedded, check buildId for API approach
    build_id = data.get("buildId", "")
    print(f"  Build ID: {build_id}")
    print(f"  Page props keys: {list(page_props.keys())}")

    # Fallback: extract slugs from links on the page
    slug_pattern = re.compile(r'/colleges/([a-z0-9\-]+)"')
    found = set(slug_pattern.findall(r.text))
    # Filter out non-college slugs
    exclude = {"college-search", "filters", "admissions", "academics",
               "tuition-and-costs", "campus-life"}
    slugs = [s for s in found if s not in exclude and len(s) > 3]
    print(f"  Found {len(slugs)} slugs from page links")
    return slugs


def collect_slugs_via_sitemap(session):
    """Try to get slugs from sitemap."""
    print("  Trying sitemap...")
    try:
        r = session.get(f"{BASE_URL}/sitemap.xml", timeout=15)
        if r.status_code == 200:
            slugs = re.findall(r'/colleges/([a-z0-9\-]+)', r.text)
            unique = list(set(slugs))
            print(f"  Found {len(unique)} slugs from sitemap")
            return unique
    except Exception as e:
        print(f"  Sitemap failed: {e}")
    return []


def collect_all_slugs(session):
    """Collect slugs using best available method."""
    # Check if we already have slugs saved
    if SLUGS_FILE.exists():
        with open(SLUGS_FILE, "r") as f:
            slugs = json.load(f)
        print(f"  Loaded {len(slugs)} slugs from {SLUGS_FILE}")
        return slugs

    # Try sitemap first (fastest, gets all)
    slugs = collect_slugs_via_sitemap(session)

    # Fallback to listing page
    if len(slugs) < 100:
        listing_slugs = collect_slugs_from_listing(session)
        slugs = list(set(slugs + listing_slugs))

    if slugs:
        # Save for reuse
        slugs.sort()
        with open(SLUGS_FILE, "w") as f:
            json.dump(slugs, f, indent=2)
        print(f"  Saved {len(slugs)} slugs to {SLUGS_FILE}")

    return slugs


# ── Phase 2: Scrape college data ───────────────────────────────────────

def scrape_college(session, slug):
    """Scrape a single college page and extract __NEXT_DATA__."""
    url = f"{BASE_URL}/colleges/{slug}"
    r = session.get(url, timeout=30)
    r.raise_for_status()

    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        r.text
    )
    if not match:
        return None

    data = json.loads(match.group(1))
    college_data = data.get("props", {}).get("pageProps", {}).get("collegeData")
    return college_data


def save_college_raw(slug, college_data):
    """Save raw college JSON to file."""
    filepath = RAW_DIR / f"{slug}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(college_data, f, indent=2, ensure_ascii=False)


def build_combined_csv(raw_dir, output_path):
    """Build combined CSV from all raw JSON files."""
    import csv
    files = sorted(raw_dir.glob("*.json"))
    if not files:
        return

    rows = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        row = {}
        for field in CSV_FIELDS:
            val = data.get(field, "")
            if isinstance(val, list):
                val = "; ".join(str(v) for v in val) if val else ""
            row[field] = val

        # Add slug and majors count
        row["slug"] = f.stem
        row["majors_count"] = len(data.get("collegeMajors", []) or [])
        row["majors_list"] = "; ".join(
            m.get("name", "") for m in (data.get("collegeMajors") or [])
        )
        rows.append(row)

    fieldnames = ["slug"] + CSV_FIELDS + ["majors_count", "majors_list"]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV saved: {output_path} ({len(rows)} colleges)")


def build_combined_json(raw_dir, output_path):
    """Build combined JSON from all raw files."""
    files = sorted(raw_dir.glob("*.json"))
    combined = {}
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            combined[f.stem] = json.load(fh)

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(combined, fh, indent=2, ensure_ascii=False)
    print(f"Combined JSON saved: {output_path} ({len(combined)} colleges)")


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape BigFuture college data")
    parser.add_argument("--max", type=int, default=0, help="Max colleges to scrape (0=all)")
    parser.add_argument("--resume", action="store_true", help="Resume from progress file")
    parser.add_argument("--delay-min", type=float, default=MIN_DELAY)
    parser.add_argument("--delay-max", type=float, default=MAX_DELAY)
    parser.add_argument("--skip-phase1", action="store_true", help="Skip slug collection")
    args = parser.parse_args()

    # Create directories
    OUTPUT_DIR.mkdir(exist_ok=True)
    RAW_DIR.mkdir(exist_ok=True)

    session = get_session()
    start_time = datetime.now()

    # Phase 1: Collect slugs
    if args.skip_phase1 and SLUGS_FILE.exists():
        with open(SLUGS_FILE, "r") as f:
            slugs = json.load(f)
        print(f"Loaded {len(slugs)} slugs from file")
    else:
        slugs = collect_all_slugs(session)

    if not slugs:
        print("ERROR: No slugs found. Cannot proceed.")
        return

    # Apply max limit
    if args.max > 0:
        slugs = slugs[:args.max]

    print(f"\nPhase 2: Scraping {len(slugs)} colleges...")

    # Load progress
    progress = load_progress() if args.resume else {"completed": [], "failed": [], "last_index": 0}
    completed_set = set(progress["completed"])

    scraped = 0
    failed = 0
    skipped = 0

    for i, slug in enumerate(slugs):
        # Skip already completed
        if slug in completed_set:
            skipped += 1
            continue

        # Rotate user agent occasionally
        if i % 20 == 0:
            session.headers["User-Agent"] = random.choice(USER_AGENTS)

        try:
            college_data = scrape_college(session, slug)
            if college_data:
                save_college_raw(slug, college_data)
                name = college_data.get("name", slug)
                sat25 = college_data.get("satCompositeScore25thPercentile", "N/A")
                sat75 = college_data.get("satCompositeScore75thPercentile", "N/A")
                tuition = college_data.get("inStateTuition") or college_data.get("privateTuition") or "N/A"
                acc = college_data.get("acceptanceRate", "N/A")
                print(f"  [{i+1}/{len(slugs)}] OK {name} | SAT:{sat25}-{sat75} | Tuition:${tuition} | Acc:{acc}%")
                progress["completed"].append(slug)
                completed_set.add(slug)
                scraped += 1
            else:
                print(f"  [{i+1}/{len(slugs)}] FAIL {slug} -no data found")
                progress["failed"].append(slug)
                failed += 1

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            print(f"  [{i+1}/{len(slugs)}] FAIL {slug} -HTTP {status}")
            progress["failed"].append(slug)
            failed += 1

            # If rate limited, wait longer
            if status == 429:
                wait = random.uniform(30, 60)
                print(f"    Rate limited! Waiting {wait:.0f}s...")
                time.sleep(wait)

        except Exception as e:
            print(f"  [{i+1}/{len(slugs)}] FAIL {slug} -{e}")
            progress["failed"].append(slug)
            failed += 1

        # Save progress after each college
        progress["last_index"] = i
        save_progress(progress)

        # Random delay
        if i < len(slugs) - 1:
            delay = random.uniform(args.delay_min, args.delay_max)
            time.sleep(delay)

    # Phase 3: Build combined files
    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n{'='*60}")
    print(f"Scraping complete in {elapsed/60:.1f} minutes")
    print(f"  Scraped: {scraped}")
    print(f"  Skipped: {skipped}")
    print(f"  Failed:  {failed}")
    print(f"{'='*60}")

    print("\nBuilding combined files...")
    build_combined_csv(RAW_DIR, COMBINED_CSV)
    build_combined_json(RAW_DIR, COMBINED_FILE)

    # Summary of failed
    if progress["failed"]:
        print(f"\nFailed slugs ({len(progress['failed'])}):")
        for s in progress["failed"]:
            print(f"  - {s}")


if __name__ == "__main__":
    main()
