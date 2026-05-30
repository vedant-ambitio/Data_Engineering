"""
Classify PhD files based on Tuition & Fees verification.

For each PhD .md file:
1. Extract currency-amount pairs from the .md Tuition & Fees section
2. Load the crawled content for that section's official URLs
3. Compare using 4 heuristics:
   H1: Currency-amount match (±5% tolerance)
   H2: "No tuition" / "waived" language match
   H3: Academic year cross-check
   H4: Domestic/International distinction check
4. Classify as: verified / partially_verified / flagged

Outputs files into 3 folders + a classification report.
"""

import json
import re
import shutil
from pathlib import Path
from collections import defaultdict


# ---------- Config ----------

BASE_DIR = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
PHD_MD_DIR = BASE_DIR / "phd_data" / "phd"
OFFICIAL_DIR = BASE_DIR / "official_urls"
SECTION_URLS_FILE = OFFICIAL_DIR / "phd_section_urls.json"
INDEX_FILE = OFFICIAL_DIR / "crawled_data" / "url_index.json"
PAGES_DIR = OFFICIAL_DIR / "crawled_data" / "pages"

OUTPUT_DIR = BASE_DIR / "classification_results" / "phd_fees"
VERIFIED_DIR = OUTPUT_DIR / "verified"
PARTIAL_DIR = OUTPUT_DIR / "partially_verified"
FLAGGED_DIR = OUTPUT_DIR / "flagged"
REPORT_FILE = OUTPUT_DIR / "classification_report.json"

MAX_FILES = 1000  # limit for this test run
TOLERANCE = 0.05  # 5% tolerance for amount matching


# ---------- Extraction helpers ----------

# Matches: $61,878 | £34,700 | €11,000 | CHF 42 | 56,120 AUD | DKK 114,900 | SGD 10,150
# Also handles: S$48,270 | US$61,878
CURRENCY_AMOUNT_RE = re.compile(
    r'(?:'
    # Symbol before number: $61,878 or £34,700 or €11,000 or S$48,270
    r'(?P<sym>[£€$]|(?:US|S|A|C|HK|NZ)\$)\s*(?P<amt1>[\d,]+(?:\.\d{1,2})?)'
    r'|'
    # Code before number: CHF 42, DKK 114,900, SGD 10,150, EUR 144
    r'(?P<code1>AUD|CAD|CHF|DKK|EUR|GBP|HKD|INR|JPY|KRW|MYR|NOK|NZD|SEK|SGD|THB|USD|ZAR)\s*(?P<amt2>[\d,]+(?:\.\d{1,2})?)'
    r'|'
    # Number before code: 56,120 AUD, 114,900 DKK
    r'(?P<amt3>[\d,]+(?:\.\d{1,2})?)\s*(?P<code2>AUD|CAD|CHF|DKK|EUR|GBP|HKD|INR|JPY|KRW|MYR|NOK|NZD|SEK|SGD|THB|USD|ZAR)'
    r')',
    re.IGNORECASE
)

SYMBOL_TO_CURRENCY = {
    '$': 'USD', '£': 'GBP', '€': 'EUR',
    'US$': 'USD', 'S$': 'SGD', 'A$': 'AUD', 'C$': 'CAD',
    'HK$': 'HKD', 'NZ$': 'NZD',
}

# Academic year patterns: 2026-27, 2025-2026, AY2024/2025, academic year 2026
YEAR_RE = re.compile(
    r'(?:AY\s*)?(?:20\d{2})\s*[-/]\s*(?:20)?\d{2}'
    r'|'
    r'academic year\s*20\d{2}'
    r'|'
    r'20\d{2}\s*(?:academic year)',
    re.IGNORECASE
)

# "No tuition" / waived language
WAIVED_PATTERNS = [
    r'no tuition fees',
    r'tuition (?:is|are) (?:typically )?(?:waived|covered)',
    r'tuition (?:fees? )?(?:is|are) (?:typically )?(?:waived|covered)',
    r'fully funded',
    r'fees? (?:are|is) (?:typically )?(?:waived|covered)',
    r'tuition[- ]free',
    r'not (?:charged|paying) tuition',
    r'(?:full )?tuition (?:fee )?waiver',
    r'does not charge tuition',
    r'no tuition',
]
WAIVED_RE = re.compile('|'.join(WAIVED_PATTERNS), re.IGNORECASE)

# Domestic/International keywords near amounts
DOMESTIC_RE = re.compile(r'(?:home|domestic|in-state|EU/EEA|local)', re.IGNORECASE)
INTERNATIONAL_RE = re.compile(r'(?:overseas|international|out-of-state|non-EU|foreign)', re.IGNORECASE)

# Tuition-proximity keywords (amount should be near these to count)
TUITION_KEYWORDS_RE = re.compile(
    r'(?:tuition|fees?|cost|per\s+year|per\s+annum|per\s+semester|per\s+term|annual|yearly|'
    r'doctoral|phd|program(?:me)?)',
    re.IGNORECASE
)


def parse_amount(amt_str: str) -> float:
    """Parse a number string like '61,878' or '10150.50' into a float."""
    return float(amt_str.replace(',', ''))


def extract_currency_amounts(text: str) -> list[tuple[str, float]]:
    """Extract all (currency, amount) pairs from text.
    Only keeps amounts > 10 (filters out things like page numbers).
    """
    pairs = []
    for m in CURRENCY_AMOUNT_RE.finditer(text):
        try:
            if m.group('sym') and m.group('amt1'):
                sym = m.group('sym').upper()
                currency = SYMBOL_TO_CURRENCY.get(sym, sym)
                amount = parse_amount(m.group('amt1'))
            elif m.group('code1') and m.group('amt2'):
                currency = m.group('code1').upper()
                amount = parse_amount(m.group('amt2'))
            elif m.group('code2') and m.group('amt3'):
                currency = m.group('code2').upper()
                amount = parse_amount(m.group('amt3'))
            else:
                continue

            if amount > 10:  # filter noise
                pairs.append((currency, amount))
        except (ValueError, AttributeError):
            continue

    return pairs


def extract_amounts_near_tuition_keywords(text: str) -> list[tuple[str, float]]:
    """Extract currency-amount pairs that appear within ~200 chars of tuition keywords."""
    results = []
    for km in TUITION_KEYWORDS_RE.finditer(text):
        # Window: 200 chars before and after the keyword
        start = max(0, km.start() - 200)
        end = min(len(text), km.end() + 200)
        window = text[start:end]
        pairs = extract_currency_amounts(window)
        results.extend(pairs)
    # Deduplicate
    return list(set(results))


def extract_years(text: str) -> set[str]:
    """Extract academic year references."""
    matches = YEAR_RE.findall(text)
    # Normalize: strip whitespace
    return {re.sub(r'\s+', ' ', m.strip().lower()) for m in matches}


def has_waived_language(text: str) -> bool:
    """Check if text contains 'no tuition' / 'waived' language."""
    return bool(WAIVED_RE.search(text))


def has_domestic_international(text: str) -> tuple[bool, bool]:
    """Check if text distinguishes domestic and international fees."""
    return bool(DOMESTIC_RE.search(text)), bool(INTERNATIONAL_RE.search(text))


# ---------- Section extraction from .md ----------

def extract_tuition_section(content: str) -> str:
    """Extract the Tuition & Fees section text (without citation blocks)."""
    pattern = r'^## Tuition & Fees\s*\n(.*?)(?=\n## [A-Z]|\Z)'
    matches = re.findall(pattern, content, re.DOTALL | re.MULTILINE)
    if not matches:
        return ""
    # Join all occurrences (handles duplicated content)
    full = "\n".join(matches)
    # Remove citation blocks for cleaner text comparison
    clean = re.sub(r'<citation>.*?</citation>', '', full, flags=re.DOTALL)
    return clean.strip()


# ---------- Comparison & Classification ----------

def amounts_match(md_amount: float, crawled_amount: float) -> bool:
    """Check if two amounts match within tolerance."""
    if md_amount == 0 or crawled_amount == 0:
        return False
    diff = abs(md_amount - crawled_amount) / max(md_amount, crawled_amount)
    return diff <= TOLERANCE


def classify_file(md_content: str, crawled_texts: list[str]) -> dict:
    """
    Classify a single file's Tuition & Fees section.
    Returns classification result with details.
    """
    tuition_section = extract_tuition_section(md_content)
    if not tuition_section:
        return {
            "classification": "flagged",
            "reason": "no_tuition_section_in_md",
            "details": {}
        }

    if not crawled_texts:
        return {
            "classification": "flagged",
            "reason": "no_crawled_data_available",
            "details": {}
        }

    combined_crawled = "\n".join(crawled_texts)

    # --- Heuristic 1: Currency-amount matching ---
    md_amounts = extract_currency_amounts(tuition_section)
    # For crawled content, use proximity to tuition keywords to reduce false matches
    crawled_amounts = extract_amounts_near_tuition_keywords(combined_crawled)
    # Also try full extraction as fallback
    if not crawled_amounts:
        crawled_amounts = extract_currency_amounts(combined_crawled)

    matched_amounts = []
    unmatched_amounts = []
    for (md_cur, md_amt) in md_amounts:
        found = False
        for (cr_cur, cr_amt) in crawled_amounts:
            if md_cur == cr_cur and amounts_match(md_amt, cr_amt):
                matched_amounts.append((md_cur, md_amt, cr_amt))
                found = True
                break
        if not found:
            unmatched_amounts.append((md_cur, md_amt))

    # --- Heuristic 2: Waived/no-tuition language ---
    md_waived = has_waived_language(tuition_section)
    crawled_waived = has_waived_language(combined_crawled)
    waived_match = md_waived and crawled_waived

    # --- Heuristic 3: Academic year cross-check ---
    md_years = extract_years(tuition_section)
    crawled_years = extract_years(combined_crawled)
    year_overlap = md_years & crawled_years
    year_match = len(year_overlap) > 0

    # --- Heuristic 4: Domestic/International distinction ---
    md_dom, md_intl = has_domestic_international(tuition_section)
    cr_dom, cr_intl = has_domestic_international(combined_crawled)
    md_has_distinction = md_dom and md_intl
    cr_has_distinction = cr_dom and cr_intl
    distinction_match = (not md_has_distinction) or (md_has_distinction and cr_has_distinction)

    # --- Classification logic ---
    details = {
        "md_amounts": [(c, a) for c, a in md_amounts],
        "matched_amounts": [(c, ma, ca) for c, ma, ca in matched_amounts],
        "unmatched_amounts": [(c, a) for c, a in unmatched_amounts],
        "md_waived": md_waived,
        "crawled_waived": crawled_waived,
        "waived_match": waived_match,
        "year_match": year_match,
        "md_years": list(md_years),
        "crawled_years": list(crawled_years),
        "distinction_match": distinction_match,
    }

    # Case 1: No amounts in .md — check waived language
    if not md_amounts:
        if waived_match:
            return {
                "classification": "verified",
                "reason": "waived_language_match",
                "details": details,
            }
        elif md_waived and not crawled_waived:
            return {
                "classification": "partially_verified",
                "reason": "md_says_waived_but_crawled_unclear",
                "details": details,
            }
        else:
            return {
                "classification": "flagged",
                "reason": "no_amounts_and_no_waived_match",
                "details": details,
            }

    # Case 2: Amounts exist — compare
    total_md = len(md_amounts)
    total_matched = len(matched_amounts)

    if total_matched == total_md:
        # All amounts matched
        if year_match and distinction_match:
            return {
                "classification": "verified",
                "reason": f"all_{total_matched}_amounts_matched_with_year_and_distinction",
                "details": details,
            }
        elif year_match or distinction_match:
            return {
                "classification": "verified",
                "reason": f"all_{total_matched}_amounts_matched",
                "details": details,
            }
        else:
            return {
                "classification": "verified",
                "reason": f"all_{total_matched}_amounts_matched_no_year_check",
                "details": details,
            }

    elif total_matched > 0:
        # Some amounts matched
        return {
            "classification": "partially_verified",
            "reason": f"{total_matched}_of_{total_md}_amounts_matched",
            "details": details,
        }

    else:
        # No amounts matched
        if waived_match:
            return {
                "classification": "partially_verified",
                "reason": "amounts_mismatch_but_waived_language_matches",
                "details": details,
            }
        return {
            "classification": "flagged",
            "reason": f"0_of_{total_md}_amounts_matched",
            "details": details,
        }


# ---------- Main ----------

def main():
    # Create output directories
    for d in [VERIFIED_DIR, PARTIAL_DIR, FLAGGED_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # Load section URLs mapping
    with open(SECTION_URLS_FILE, encoding="utf-8") as f:
        section_urls = json.load(f)

    # Load crawled URL index
    with open(INDEX_FILE, encoding="utf-8") as f:
        url_index = json.load(f)

    # Process files
    results = []
    counts = {"verified": 0, "partially_verified": 0, "flagged": 0}
    processed = 0

    for college, files in section_urls.items():
        if processed >= MAX_FILES:
            break

        for fname, fdata in files.items():
            if processed >= MAX_FILES:
                break

            tuition_urls = fdata.get("tuition_and_fees", [])

            # Load crawled content for tuition URLs
            crawled_texts = []
            crawled_urls_used = []
            for url in tuition_urls:
                h = url_index.get(url)
                if not h:
                    continue
                page_file = PAGES_DIR / f"{h}.json"
                if not page_file.exists():
                    continue
                try:
                    page = json.loads(page_file.read_text(encoding="utf-8"))
                    if page.get("status") == "success" and page.get("content"):
                        crawled_texts.append(page["content"])
                        crawled_urls_used.append(url)
                except Exception:
                    continue

            # Skip files where none of the tuition URLs have been crawled yet
            if not crawled_texts and tuition_urls:
                continue

            # Load the .md file
            md_path = PHD_MD_DIR / college / fname
            if not md_path.exists():
                continue

            try:
                md_content = md_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Classify
            result = classify_file(md_content, crawled_texts)
            classification = result["classification"]
            counts[classification] += 1

            # Copy .md file to the appropriate folder
            dest_dir = {
                "verified": VERIFIED_DIR,
                "partially_verified": PARTIAL_DIR,
                "flagged": FLAGGED_DIR,
            }[classification]
            # Truncate filename to avoid Windows 260-char path limit
            dest_name = f"{college}__{fname}"
            if len(str(dest_dir / dest_name)) > 250:
                dest_name = dest_name[:150] + ".md"
            dest_file = dest_dir / dest_name
            shutil.copy2(md_path, dest_file)

            # Store result for report
            results.append({
                "college": college,
                "file": fname,
                "classification": classification,
                "reason": result["reason"],
                "tuition_urls": tuition_urls,
                "crawled_urls_used": crawled_urls_used,
                "details": result["details"],
            })

            processed += 1
            if processed % 200 == 0:
                print(f"  Processed {processed} files...", flush=True)

    # Save report
    report = {
        "total_processed": processed,
        "counts": counts,
        "results": results,
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n{'='*50}")
    print(f"CLASSIFICATION RESULTS")
    print(f"{'='*50}")
    print(f"Total processed: {processed}")
    print(f"  Verified:           {counts['verified']}")
    print(f"  Partially verified: {counts['partially_verified']}")
    print(f"  Flagged:            {counts['flagged']}")
    print(f"\nFiles copied to: {OUTPUT_DIR}")
    print(f"Report saved to: {REPORT_FILE}")


if __name__ == "__main__":
    main()
