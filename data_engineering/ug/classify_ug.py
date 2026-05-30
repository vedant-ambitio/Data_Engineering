"""
Unified 3-Tier Classification for UG (Undergraduate) files.

Classifies each UG .md file by verifying 3 sections against crawled official data:
  1. Tuition & Fees
  2. Application Deadlines
  3. Admission Requirements

Per-section classification: verified / partially_verified / flagged

File-level tier:
  Tier 1 (High Confidence):    All 3 sections verified
  Tier 2 (Moderate Confidence): Mix of verified/partial/flagged (< 2 flagged)
  Tier 3 (Low Confidence):     2+ sections flagged

Output:
  classification_results/ug/high_confidence/     ← Tier 1 .md files
  classification_results/ug/moderate_confidence/  ← Tier 2 .md files
  classification_results/ug/low_confidence/       ← Tier 3 .md files
  classification_results/ug/classification_report.json
"""

import json
import re
import shutil
from pathlib import Path
from datetime import datetime, date


# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────

BASE_DIR = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
UG_MD_DIR = BASE_DIR / "ug_data_0k_tokens" / "ug"
OFFICIAL_DIR = BASE_DIR / "official_urls"
SECTION_URLS_FILE = OFFICIAL_DIR / "ug_section_urls.json"
INDEX_FILE = OFFICIAL_DIR / "crawled_data" / "url_index.json"
PAGES_DIR = OFFICIAL_DIR / "crawled_data" / "pages"

OUTPUT_DIR = BASE_DIR / "classification_results" / "ug"
HIGH_DIR = OUTPUT_DIR / "high_confidence"
MODERATE_DIR = OUTPUT_DIR / "moderate_confidence"
LOW_DIR = OUTPUT_DIR / "low_confidence"
REPORT_FILE = OUTPUT_DIR / "classification_report.json"

TOLERANCE = 0.05  # 5% for amount matching
TODAY = date.today()


# ─────────────────────────────────────────────
#  Shared extraction helpers
# ─────────────────────────────────────────────

# Currency-amount regex (includes ₹ for Indian Rupees)
CURRENCY_AMOUNT_RE = re.compile(
    r'(?:'
    r'(?P<sym>[£€$₹]|(?:US|S|A|C|HK|NZ)\$)\s*(?P<amt1>[\d,]+(?:\.\d{1,2})?)'
    r'|'
    r'(?P<code1>AUD|CAD|CHF|DKK|EUR|GBP|HKD|INR|JPY|KRW|MYR|NOK|NZD|SEK|SGD|THB|USD|ZAR)\s*(?P<amt2>[\d,]+(?:\.\d{1,2})?)'
    r'|'
    r'(?P<amt3>[\d,]+(?:\.\d{1,2})?)\s*(?P<code2>AUD|CAD|CHF|DKK|EUR|GBP|HKD|INR|JPY|KRW|MYR|NOK|NZD|SEK|SGD|THB|USD|ZAR)'
    r')',
    re.IGNORECASE
)

SYMBOL_TO_CURRENCY = {
    '$': 'USD', '£': 'GBP', '€': 'EUR', '₹': 'INR',
    'US$': 'USD', 'S$': 'SGD', 'A$': 'AUD', 'C$': 'CAD',
    'HK$': 'HKD', 'NZ$': 'NZD',
}

# Tuition-free / no-tuition detection (European public universities, funded programs)
TUITION_FREE_RE = re.compile(
    r'no tuition fees?|tuition[- ]free|zero tuition|tuition (?:is|are) (?:typically )?(?:waived|covered)|'
    r'free of charge|does not charge tuition|nicht erhoben|no tuition|'
    r'fully funded|tuition (?:fee )?waiver|fees? (?:are|is) (?:typically )?(?:waived|covered)',
    re.IGNORECASE
)

# Tuition keywords — includes UG-specific terms alongside generic ones
TUITION_KEYWORDS_RE = re.compile(
    r'(?:tuition|fees?|cost|per\s+year|per\s+annum|per\s+semester|annual|yearly|'
    r'undergraduate|bachelor|program(?:me)?|home\s+student|overseas|international\s+student|'
    r'in-state|out-of-state|subsidized|cost\s+of\s+attendance)',
    re.IGNORECASE
)

YEAR_RE = re.compile(
    r'(?:AY\s*)?(?:20\d{2})\s*[-/]\s*(?:20)?\d{2}|academic year\s*20\d{2}|20\d{2}\s*academic year',
    re.IGNORECASE
)

DOMESTIC_RE = re.compile(r'(?:home|domestic|in-state|EU/EEA|local|singapore\s+citizen)', re.IGNORECASE)
INTERNATIONAL_RE = re.compile(r'(?:overseas|international|out-of-state|non-EU|foreign)', re.IGNORECASE)

# ── Date patterns for deadlines ──

MONTH_NAMES = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

# Full date with year: "December 1, 2025" or "1 March 2026" or "2026-01-15"
DATE_FULL_RE = re.compile(
    r'(?:(?P<m1>' + '|'.join(MONTH_NAMES.keys()) + r')\s+(?P<d1>\d{1,2})\s*,?\s*(?P<y1>20\d{2}))'
    r'|(?:(?P<d2>\d{1,2})\s+(?P<m2>' + '|'.join(MONTH_NAMES.keys()) + r')\s*,?\s*(?P<y2>20\d{2}))'
    r'|(?:(?P<y3>20\d{2})-(?P<m3>\d{2})-(?P<d3>\d{2}))',
    re.IGNORECASE
)

# Month+day without year: "November 1" or "1 March" or "January 15"
DATE_MONTHDAY_RE = re.compile(
    r'(?:(?P<m1>' + '|'.join(MONTH_NAMES.keys()) + r')\s+(?P<d1>\d{1,2})(?!\s*,?\s*20\d{2}))'
    r'|(?:(?P<d2>\d{1,2})\s+(?P<m2>' + '|'.join(MONTH_NAMES.keys()) + r')(?!\s*,?\s*20\d{2}))',
    re.IGNORECASE
)

# "for XXXX entry" pattern — infer year from nearby context
ENTRY_YEAR_RE = re.compile(r'(?:for\s+)?(\d{4})\s*(?:entry|intake|admission|start)', re.IGNORECASE)

ROLLING_RE = re.compile(
    r'rolling basis|accepted at any time|no specific deadline|no fixed deadline|'
    r'throughout the year|open at any time|applications? (?:are )?accepted (?:at )?any time|'
    r'no deadlines?|at any time',
    re.IGNORECASE
)

INTAKE_RE = re.compile(
    r'(?:fall|spring|autumn|winter|summer|august|january|october|february|september)\s+20\d{2}\s*(?:intake|entry|admission|start)?'
    r'|(?:20\d{2})\s*(?:fall|spring|autumn|winter|summer)\s*(?:intake|entry|admission|start)?',
    re.IGNORECASE
)


# ─────────────────────────────────────────────
#  Extraction functions
# ─────────────────────────────────────────────

def extract_section(content: str, heading: str) -> str:
    """Extract section text, stripping (Source: ...) inline citations and <citation> blocks."""
    pattern = rf'^## {re.escape(heading)}\s*\n(.*?)(?=\n## [A-Z]|\Z)'
    matches = re.findall(pattern, content, re.DOTALL | re.MULTILINE)
    full = "\n".join(matches)
    # Strip citation blocks (masters/phd format)
    full = re.sub(r'<citation>.*?</citation>', '', full, flags=re.DOTALL)
    # Strip inline (Source: URL) citations (UG format)
    full = re.sub(r'\s*\(Source:\s*https?://[^\)]+\)', '', full)
    return full.strip()


# Indian lakh/crore notation: "2,50,000" or "12,34,567" or "2.5 lakhs" or "1.2 crore"
INDIAN_LAKH_RE = re.compile(
    r'(?:₹|INR|Rs\.?)\s*(\d{1,2},(?:\d{2},)*\d{3}(?:\.\d{1,2})?)',
    re.IGNORECASE
)
LAKH_WORD_RE = re.compile(
    r'(?:₹|INR|Rs\.?)?\s*(\d+(?:\.\d+)?)\s*(?:lakh|lac)s?',
    re.IGNORECASE
)
CRORE_WORD_RE = re.compile(
    r'(?:₹|INR|Rs\.?)?\s*(\d+(?:\.\d+)?)\s*crores?',
    re.IGNORECASE
)


def parse_amount(amt_str: str) -> float:
    return float(amt_str.replace(',', ''))


def parse_indian_amount(amt_str: str) -> float:
    """Parse Indian comma format: 2,50,000 → 250000, 12,34,567 → 1234567."""
    return float(amt_str.replace(',', ''))


def extract_currency_amounts(text: str) -> list[tuple[str, float]]:
    pairs = []
    seen_amounts = set()

    # First: extract Indian lakh/crore word notation (e.g., "2.5 lakhs", "1.2 crore")
    for m in LAKH_WORD_RE.finditer(text):
        try:
            val = float(m.group(1)) * 100000  # 1 lakh = 100,000
            if val > 10 and val not in seen_amounts:
                pairs.append(('INR', val))
                seen_amounts.add(val)
        except ValueError:
            continue

    for m in CRORE_WORD_RE.finditer(text):
        try:
            val = float(m.group(1)) * 10000000  # 1 crore = 10,000,000
            if val > 10 and val not in seen_amounts:
                pairs.append(('INR', val))
                seen_amounts.add(val)
        except ValueError:
            continue

    # Second: extract Indian comma format (₹2,50,000 or INR 12,34,567)
    for m in INDIAN_LAKH_RE.finditer(text):
        try:
            amount = parse_indian_amount(m.group(1))
            if amount > 10 and amount not in seen_amounts:
                pairs.append(('INR', amount))
                seen_amounts.add(amount)
        except ValueError:
            continue

    # Third: standard currency amounts (handles $, £, €, S$, etc.)
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
            if amount > 10 and amount not in seen_amounts:
                pairs.append((currency, amount))
                seen_amounts.add(amount)
        except (ValueError, AttributeError):
            continue

    return pairs


def extract_amounts_near_keywords(text: str, keyword_re) -> list[tuple[str, float]]:
    results = []
    for km in keyword_re.finditer(text):
        start = max(0, km.start() - 200)
        end = min(len(text), km.end() + 200)
        results.extend(extract_currency_amounts(text[start:end]))
    return list(set(results)) if results else extract_currency_amounts(text)


def amounts_match(a: float, b: float) -> bool:
    if a == 0 or b == 0:
        return False
    return abs(a - b) / max(a, b) <= TOLERANCE


def extract_years(text: str) -> set[str]:
    return {re.sub(r'\s+', ' ', m.strip().lower()) for m in YEAR_RE.findall(text)}


def extract_dates_full(text: str) -> list[tuple[int, int, int]]:
    """Extract (year, month, day) tuples from text — requires year."""
    dates = []
    for m in DATE_FULL_RE.finditer(text):
        try:
            if m.group('m1'):
                dates.append((int(m.group('y1')), MONTH_NAMES[m.group('m1').lower()], int(m.group('d1'))))
            elif m.group('m2'):
                dates.append((int(m.group('y2')), MONTH_NAMES[m.group('m2').lower()], int(m.group('d2'))))
            elif m.group('y3'):
                dates.append((int(m.group('y3')), int(m.group('m3')), int(m.group('d3'))))
        except (ValueError, TypeError, KeyError):
            continue
    return dates


def extract_dates_monthday(text: str) -> list[tuple[int, int]]:
    """Extract (month, day) tuples from dates that have no year."""
    monthdays = []
    for m in DATE_MONTHDAY_RE.finditer(text):
        try:
            if m.group('m1'):
                monthdays.append((MONTH_NAMES[m.group('m1').lower()], int(m.group('d1'))))
            elif m.group('m2'):
                monthdays.append((MONTH_NAMES[m.group('m2').lower()], int(m.group('d2'))))
        except (ValueError, TypeError, KeyError):
            continue
    return monthdays


def infer_year_from_context(text: str, month: int, day: int) -> int | None:
    """Try to infer year from nearby 'XXXX entry' or 'XXXX intake' context."""
    # Search each line for the month+day and check for year context
    month_names_rev = {v: k for k, v in MONTH_NAMES.items() if len(k) > 3}
    month_str = [k for k, v in MONTH_NAMES.items() if v == month and len(k) > 3]
    if not month_str:
        return None
    month_str = month_str[0]

    for line in text.split('\n'):
        if re.search(rf'{month_str}\s+{day}', line, re.IGNORECASE):
            ym = ENTRY_YEAR_RE.search(line)
            if ym:
                return int(ym.group(1))
    return None


def extract_intakes(text: str) -> set[str]:
    return {m.strip().lower() for m in INTAKE_RE.findall(text)}


# ─────────────────────────────────────────────
#  Tuition & Fees classifier
# ─────────────────────────────────────────────

# PRIMARY core: the most important tuition lines (annual tuition, home/overseas fee)
PRIMARY_TUITION_RE = re.compile(
    r'(?:annual\s+tuition|tuition\s*:|tuition\s+fee|tuition\s+alone|'
    r'full\s+regular\s+tuition|university\s+tuition|'
    r'home\s+student|overseas\s+student|'
    r'international\s+student|domestic\s+student|'
    r'in-state|out-of-state|'
    r'singapore\s+citizen|permanent\s+resident|'
    r'subsidized|non-subsidized|'
    r'program(?:me)?\s+fee|course\s+fee|'
    r'per\s+term|per\s+semester)',
    re.IGNORECASE
)

# NOT core at all — application fee is trivially correct, don't count it
APPLICATION_FEE_RE = re.compile(r'application\s+fee', re.IGNORECASE)

# Everything else in the tuition section (housing, books, insurance, totals, etc.)
NOT_CORE_RE = re.compile(
    r'(?:'
    # Housing & living
    r'housing|room\s*(?:&|and)?\s*board|accommodation|meal\s+plan|food|dining|'
    r'living\s+expense|living\s+cost|cost\s+of\s+living|'
    # Books, personal, transport
    r'books?\s+(?:and\s+)?suppli|personal\s+expense|transportation|travel|'
    # Insurance & misc fees
    r'health\s+insurance|student\s+activity|technology\s+fee|miscellaneous|'
    # Total/aggregate costs (these include tuition + housing + books + everything)
    r'total\s+estimated\s+cost|total\s+program\s+cost|total\s+cost|'
    r'cost\s+of\s+attendance|estimated\s+total|'
    r'\d-year(?:s)?\s+(?:cost|price|tuition|degree|program)|'
    r'entire\s+degree|projected\s+\d-year|sticker\s+price|'
    r'estimated\s+\d-year|based\s+on\s+current\s+tuition\s+and\s+change|'
    # Financial aid / net price
    r'net\s+price|average\s+(?:net|aid)|financial\s+aid|scholarship|'
    r'average\s+(?:university\s+)?scholarship|'
    # Other non-core
    r'class\s+of\s+20\d{2}|estimated\s+annual\s+cost'
    r')',
    re.IGNORECASE
)


def extract_core_tuition_amounts(section_text: str) -> list[tuple[str, float]]:
    """Extract only the primary tuition fee amounts — not housing, books, COA, app fee, etc.
    Looks at each line individually."""
    core = []
    seen = set()

    for line in section_text.split('\n'):
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Skip lines that match NOT_CORE (totals, housing, books, etc.)
        if NOT_CORE_RE.search(line_stripped):
            continue

        # Skip application fee lines — trivially correct, don't count
        if APPLICATION_FEE_RE.search(line_stripped):
            continue

        # Must have a primary tuition keyword on this line
        if not PRIMARY_TUITION_RE.search(line_stripped):
            continue

        # Extract amounts from this line
        amounts = extract_currency_amounts(line_stripped)
        for (c, a) in amounts:
            key = (c, a)
            if key not in seen:
                seen.add(key)
                core.append((c, a))

    return core


def classify_tuition(md_content: str, crawled_texts: list[str]) -> dict:
    section = extract_section(md_content, "Tuition & Fees")
    if not section:
        return {"classification": "no_data", "reason": "no_tuition_section"}
    if not crawled_texts:
        return {"classification": "no_data", "reason": "no_crawled_data"}

    combined = "\n".join(crawled_texts)

    # Check tuition-free language (European public universities, funded programs)
    # Only trigger if there are NO actual tuition amounts in the section — otherwise
    # it's just financial aid language like "attend tuition-free if income < $200K"
    md_free = bool(TUITION_FREE_RE.search(section))
    crawled_free = bool(TUITION_FREE_RE.search(combined))
    md_has_amounts = bool(extract_currency_amounts(section))

    if md_free and not md_has_amounts:
        if crawled_free:
            return {"classification": "verified", "reason": "tuition_free_confirmed", "details": {"md_free": True, "crawled_free": True}}
        else:
            return {"classification": "partially_verified", "reason": "md_says_free_crawled_unclear", "details": {"md_free": True, "crawled_free": False}}

    # Extract only primary tuition amounts (not housing/books/COA/app fee)
    core_amounts = extract_core_tuition_amounts(section)
    crawled_amounts = extract_amounts_near_keywords(combined, TUITION_KEYWORDS_RE)

    md_years = extract_years(section)
    crawled_years = extract_years(combined)
    year_match = bool(md_years & crawled_years)

    md_dom, md_intl = bool(DOMESTIC_RE.search(section)), bool(INTERNATIONAL_RE.search(section))
    cr_dom, cr_intl = bool(DOMESTIC_RE.search(combined)), bool(INTERNATIONAL_RE.search(combined))
    distinction_match = (not (md_dom and md_intl)) or ((md_dom and md_intl) and (cr_dom and cr_intl))

    # Match core amounts against crawled
    core_matched = []
    core_unmatched = []
    for (mc, ma) in core_amounts:
        found = any(mc == cc and amounts_match(ma, ca) for cc, ca in crawled_amounts)
        (core_matched if found else core_unmatched).append((mc, ma))

    details = {
        "core_amounts": len(core_amounts), "core_matched": len(core_matched), "core_unmatched": len(core_unmatched),
        "core_values": [(c, a) for c, a in core_amounts],
        "year_match": year_match, "distinction_match": distinction_match,
    }

    if not core_amounts:
        return {"classification": "flagged", "reason": "no_core_tuition_amounts_found", "details": details}

    total_core = len(core_amounts)
    match_ratio = len(core_matched) / total_core

    # Classification tiers:
    #   >=50% core matched → verified (majority of primary fees confirmed)
    #   20-49% matched     → partially_verified (some key fees match)
    #   <20% matched       → flagged (almost nothing matches official data)
    if match_ratio >= 0.5:
        return {"classification": "verified", "reason": f"{len(core_matched)}_of_{total_core}_core_matched", "details": details}
    elif match_ratio >= 0.2:
        return {"classification": "partially_verified", "reason": f"{len(core_matched)}_of_{total_core}_core_matched", "details": details}
    else:
        return {"classification": "flagged", "reason": f"{len(core_matched)}_of_{total_core}_core_matched", "details": details}


# ─────────────────────────────────────────────
#  Placeholder classifiers (to be filled in)
# ─────────────────────────────────────────────

def classify_deadlines(md_content: str, crawled_texts: list[str]) -> dict:
    section = extract_section(md_content, "Application Deadlines")
    if not section:
        return {"classification": "no_data", "reason": "no_deadline_section"}
    if not crawled_texts:
        return {"classification": "no_data", "reason": "no_crawled_data"}

    combined = "\n".join(crawled_texts)

    # Heuristic 1: Rolling admissions
    md_rolling = bool(ROLLING_RE.search(section))
    crawled_rolling = bool(ROLLING_RE.search(combined))

    if md_rolling:
        if crawled_rolling:
            return {"classification": "verified", "reason": "rolling_match", "details": {"md_rolling": True, "crawled_rolling": True}}
        crawled_dates = extract_dates_full(combined)
        if crawled_dates:
            return {"classification": "flagged", "reason": "md_says_rolling_crawled_has_dates", "details": {"crawled_dates_count": len(crawled_dates)}}
        return {"classification": "partially_verified", "reason": "md_rolling_crawled_unclear", "details": {}}

    # Check "information not available"
    if re.search(r'information not available', section, re.IGNORECASE) and len(section) < 200:
        return {"classification": "partially_verified", "reason": "info_not_available", "details": {}}

    # Heuristic 2: Full date matching (with year)
    md_dates_full = extract_dates_full(section)
    crawled_dates_full = extract_dates_full(combined)

    md_full_set = set(md_dates_full)
    crawled_full_set = set(crawled_dates_full)
    strong_matches = md_full_set & crawled_full_set

    # Heuristic 3: Month+day matching (no year — fallback)
    md_monthdays = extract_dates_monthday(section)
    crawled_monthdays_from_full = {(m, d) for (y, m, d) in crawled_dates_full}
    crawled_monthdays_raw = set(extract_dates_monthday(combined))
    crawled_all_monthdays = crawled_monthdays_from_full | crawled_monthdays_raw

    # Try to upgrade month+day matches using year inference from context
    upgraded_strong = 0
    weak_matches = 0
    for (month, day) in md_monthdays:
        md_pair = (month, day)
        if md_pair in crawled_all_monthdays:
            # Try to infer year from "XXXX entry" context
            inferred_year = infer_year_from_context(section, month, day)
            if inferred_year and (inferred_year, month, day) in crawled_full_set:
                upgraded_strong += 1
            else:
                weak_matches += 1

    # Heuristic 4: Intake terms
    md_intakes = extract_intakes(section)
    crawled_intakes = extract_intakes(combined)
    intake_match = bool(md_intakes & crawled_intakes)

    # Helper for stale date detection
    def safe_date(y, m, d):
        try:
            return date(y, m, d) if y and m and d and 1 <= m <= 12 and 1 <= d <= 31 else None
        except ValueError:
            return None

    md_safe_dates = [sd for y, m, d in md_dates_full if (sd := safe_date(y, m, d)) is not None]
    all_past = all(sd < TODAY for sd in md_safe_dates) if md_safe_dates else False
    any_current = any(sd >= TODAY for sd in md_safe_dates) if md_safe_dates else False

    total_strong = len(strong_matches) + upgraded_strong
    total_md_dates = len(md_full_set) + len(set(md_monthdays))

    details = {
        "md_full_dates": len(md_full_set),
        "md_monthday_only": len(set(md_monthdays)),
        "strong_matches": len(strong_matches),
        "upgraded_strong": upgraded_strong,
        "weak_matches": weak_matches,
        "total_strong": total_strong,
        "total_md_dates": total_md_dates,
        "intake_match": intake_match,
        "all_dates_past": all_past,
    }

    # No dates at all in .md
    if total_md_dates == 0:
        if intake_match:
            return {"classification": "partially_verified", "reason": "intake_terms_match_no_dates", "details": details}
        return {"classification": "partially_verified", "reason": "no_dates_extractable", "details": details}

    # Classification:
    #   >=50% matched → verified
    #   20-49% matched → partially_verified
    #   <20% matched → flagged
    #   All dates past → flagged (needs re-scrape)
    #   Zero matches → flagged
    total_all_matches = total_strong + weak_matches
    match_ratio = total_all_matches / total_md_dates if total_md_dates > 0 else 0

    # All dates in past → F1 (dates correct but stale, needs re-scrape)
    if all_past and md_safe_dates:
        return {"classification": "flagged", "flag_type": "F1_stale",
                "reason": f"all_dates_past_{total_all_matches}_of_{total_md_dates}_matched", "details": details}

    if match_ratio >= 0.5:
        return {"classification": "verified", "reason": f"{total_strong}_strong_{weak_matches}_weak_of_{total_md_dates}_dates_matched", "details": details}

    if match_ratio >= 0.2:
        return {"classification": "partially_verified", "reason": f"{total_strong}_strong_{weak_matches}_weak_of_{total_md_dates}_dates_low", "details": details}

    if total_all_matches > 0:
        # <20% match — F2 (dates don't match official page)
        return {"classification": "flagged", "flag_type": "F2_mismatch",
                "reason": f"low_match_{total_all_matches}_of_{total_md_dates}", "details": details}

    # Zero matches — F2 (no dates matched)
    if intake_match:
        return {"classification": "partially_verified", "reason": "dates_mismatch_but_intakes_match", "details": details}

    return {"classification": "flagged", "flag_type": "F2_mismatch",
            "reason": "no_dates_matched", "details": details}


# ─────────────────────────────────────────────
#  Admission Requirements helpers
# ─────────────────────────────────────────────

# Score extraction patterns
TOEFL_SCORE_RE = re.compile(r'TOEFL.*?(?:score\s+(?:of\s+)?)?(?:at\s+least\s+)?(?:minimum\s+)?(\d{2,3}(?:\.\d)?)', re.IGNORECASE)
IELTS_SCORE_RE = re.compile(r'IELTS.*?(?:score\s+(?:of\s+)?)?(?:at\s+least\s+)?(?:minimum\s+)?(\d\.\d)', re.IGNORECASE)
DET_SCORE_RE = re.compile(r'(?:Duolingo|DET).*?(?:score\s+(?:of\s+)?)?(?:minimum\s+)?(\d{2,3})', re.IGNORECASE)
PTE_SCORE_RE = re.compile(r'PTE.*?(?:score\s+(?:of\s+)?)?(?:minimum\s+)?(\d{2,3})', re.IGNORECASE)
CAMBRIDGE_SCORE_RE = re.compile(r'(?:Cambridge|CAE|CPE|C1 Advanced|C2 Proficiency).*?(\d{3})', re.IGNORECASE)

# SAT/ACT patterns
SAT_SCORE_RE = re.compile(r'SAT.*?(\d{3,4})', re.IGNORECASE)
ACT_SCORE_RE = re.compile(r'ACT.*?(?:composite|score|range)?.*?(\d{2})', re.IGNORECASE)

# SAT/ACT status patterns
SAT_REQUIRED_RE = re.compile(r'(?:SAT|ACT).*?required|requires?\s+(?:the\s+)?(?:SAT|ACT)|reinstated.*?(?:SAT|ACT)', re.IGNORECASE)
SAT_NOT_REQUIRED_RE = re.compile(r'(?:SAT|ACT).*?not\s+required|not\s+(?:require|accept).*?(?:SAT|ACT)|test[- ]blind|(?:SAT|ACT).*?not\s+accepted', re.IGNORECASE)
SAT_OPTIONAL_RE = re.compile(r'test[- ]optional|(?:SAT|ACT).*?optional', re.IGNORECASE)

# GPA
GPA_RE = re.compile(r'GPA.*?(\d\.\d+)', re.IGNORECASE)

# LOR
LOR_COUNT_RE = re.compile(
    r'(\d)\s*(?:letters?\s+of\s+recommendation|letters?\s+are\s+required|references?\s+(?:are\s+)?required|academic\s+referees?|letters?\s+required)',
    re.IGNORECASE
)
WORD_TO_NUM = {'two': 2, 'three': 3, 'four': 4, 'five': 5}
LOR_WORD_RE = re.compile(
    r'(two|three|four|five)\s+(?:letters?\s+of\s+recommendation|letters?|references?|referees?)',
    re.IGNORECASE
)

# Application fee
APP_FEE_RE = re.compile(r'application\s+fee', re.IGNORECASE)

# IB points
IB_POINTS_RE = re.compile(r'(?:IB|International Baccalaureate).*?(\d{2})\s*(?:points|overall)', re.IGNORECASE)


def find_numbers_near_keyword(text: str, keyword_pattern: str, window: int = 200) -> list[float]:
    """Find all numbers within `window` chars of a keyword match."""
    numbers = []
    for km in re.finditer(keyword_pattern, text, re.IGNORECASE):
        start = max(0, km.start() - window)
        end = min(len(text), km.end() + window)
        chunk = text[start:end]
        for nm in re.finditer(r'\b(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\b', chunk):
            try:
                val = float(nm.group(1).replace(',', ''))
                if val > 0:
                    numbers.append(val)
            except ValueError:
                continue
    return numbers


def check_entity(md_text: str, crawled_text: str, keyword: str, md_values: list[float],
                 valid_range: tuple = None) -> dict:
    """Per-entity check: keyword present in both? Numbers near keyword match?"""
    md_has = bool(re.search(keyword, md_text, re.IGNORECASE))
    crawled_has = bool(re.search(keyword, crawled_text, re.IGNORECASE))

    if not md_has:
        return {"status": "not_applicable"}
    if not crawled_has:
        return {"status": "not_in_crawled", "md_values": md_values}
    if not md_values:
        return {"status": "keyword_only_no_values"}

    crawled_numbers = find_numbers_near_keyword(crawled_text, keyword)
    if valid_range and crawled_numbers:
        lo, hi = valid_range
        crawled_numbers = [n for n in crawled_numbers if lo <= n <= hi]

    for mv in md_values:
        for cn in crawled_numbers:
            if amounts_match(mv, cn):
                return {"status": "matched", "md_value": mv, "crawled_value": cn}

    return {"status": "mismatched", "md_values": md_values, "crawled_numbers": crawled_numbers[:10]}


def check_status_entity(md_text: str, crawled_text: str, keyword: str,
                        positive_patterns: list[str], negative_patterns: list[str]) -> dict:
    """Check a status entity (required / not required / optional)."""
    md_has = bool(re.search(keyword, md_text, re.IGNORECASE))
    crawled_has = bool(re.search(keyword, crawled_text, re.IGNORECASE))

    if not md_has:
        return {"status": "not_applicable"}
    if not crawled_has:
        return {"status": "not_in_crawled"}

    def get_status(text):
        for p in negative_patterns:
            if re.search(p, text, re.IGNORECASE):
                return "negative"
        for p in positive_patterns:
            if re.search(p, text, re.IGNORECASE):
                return "positive"
        return "unclear"

    md_status = get_status(md_text)
    crawled_status = get_status(crawled_text)

    if md_status == "unclear" or crawled_status == "unclear":
        return {"status": "keyword_only_no_status", "md_status": md_status, "crawled_status": crawled_status}
    if md_status == crawled_status:
        return {"status": "matched", "value": md_status}
    return {"status": "mismatched", "md_status": md_status, "crawled_status": crawled_status}


def extract_lor_count(text: str) -> int:
    m = LOR_COUNT_RE.search(text)
    if m:
        return int(m.group(1))
    m = LOR_WORD_RE.search(text)
    if m:
        return WORD_TO_NUM.get(m.group(1).lower(), 0)
    return 0


# ─────────────────────────────────────────────
#  Admission Requirements classifier
# ─────────────────────────────────────────────

# "Not required" patterns per component
NOT_REQUIRED_PATTERNS = {
    'sat': re.compile(r'(?:SAT|ACT).*?not\s+required|not\s+(?:require|accept).*?(?:SAT|ACT)|test[- ]blind|(?:SAT|ACT).*?not\s+accepted|not\s+required.*?(?:SAT|ACT)', re.IGNORECASE),
    'act': re.compile(r'(?:ACT|SAT).*?not\s+required|not\s+(?:require|accept).*?(?:ACT|SAT)|test[- ]blind|(?:ACT|SAT).*?not\s+accepted|not\s+required.*?(?:ACT|SAT)', re.IGNORECASE),
    'toefl': re.compile(r'TOEFL.*?not\s+required|not\s+require.*?TOEFL|English.*?not\s+required|waived', re.IGNORECASE),
    'ielts': re.compile(r'IELTS.*?not\s+required|not\s+require.*?IELTS|English.*?not\s+required|waived', re.IGNORECASE),
    'gpa': re.compile(r'no\s+(?:minimum\s+)?GPA|GPA.*?not\s+required|does\s+not\s+(?:require|specify).*?GPA', re.IGNORECASE),
}


def check_component(md_text: str, crawled_text: str, keyword: str,
                    md_values: list[float], valid_range: tuple,
                    not_required_re: re.Pattern) -> str:
    """
    Check a single admission component. Returns: MATCHED / MISMATCHED / NOT_VERIFIABLE

    Logic:
      md has score + crawled has score → compare (match/mismatch)
      md says "not required" + crawled says "not required" → MATCHED
      md says "not required" + crawled has score → MISMATCHED
      md has score + crawled says "not required" → MISMATCHED
      md silent + crawled has score → MISMATCHED (md missed real data)
      md silent + crawled says "not required" → MATCHED (both agree: not needed)
      md silent + crawled silent → NOT_VERIFIABLE
      md has data + crawled silent → NOT_VERIFIABLE
    """
    md_has_keyword = bool(re.search(keyword, md_text, re.IGNORECASE))
    crawled_has_keyword = bool(re.search(keyword, crawled_text, re.IGNORECASE))

    md_not_required = bool(not_required_re.search(md_text))
    crawled_not_required = bool(not_required_re.search(crawled_text))

    md_has_scores = bool(md_values)

    # Get crawled scores near keyword
    crawled_scores = []
    if crawled_has_keyword:
        crawled_scores = find_numbers_near_keyword(crawled_text, keyword)
        if valid_range:
            lo, hi = valid_range
            crawled_scores = [n for n in crawled_scores if lo <= n <= hi]

    crawled_has_scores = bool(crawled_scores)

    # Case 1: md has scores
    if md_has_scores:
        if crawled_has_scores:
            for mv in md_values:
                for cv in crawled_scores:
                    if amounts_match(mv, cv):
                        return "MATCHED"
            return "MISMATCHED"
        elif crawled_not_required:
            return "MISMATCHED"
        else:
            return "NOT_VERIFIABLE"

    # Case 2: md says "not required" or has keyword but no scores
    if md_not_required or (md_has_keyword and not md_has_scores):
        if crawled_not_required:
            return "MATCHED"
        elif crawled_has_scores:
            return "MISMATCHED"
        else:
            return "NOT_VERIFIABLE"

    # Case 3: md silent (doesn't mention this component)
    if not md_has_keyword:
        if crawled_has_scores:
            return "MISMATCHED"
        elif crawled_not_required:
            return "MATCHED"
        else:
            return "NOT_VERIFIABLE"

    return "NOT_VERIFIABLE"


def classify_admission(md_content: str, crawled_texts: list[str]) -> dict:
    section = extract_section(md_content, "Admission Requirements")
    if not section:
        return {"classification": "no_data", "reason": "no_admission_section"}
    if not crawled_texts:
        return {"classification": "no_data", "reason": "no_crawled_data"}

    combined = "\n".join(crawled_texts)

    if not re.search(r'admission|requirement|apply|application|eligibility|toefl|ielts|sat|act|proficiency|a-level', combined, re.IGNORECASE):
        return {"classification": "partially_verified", "reason": "crawled_page_not_admission_related", "details": {}}

    # Extract values from .md
    md_sat = [float(m.group(1)) for m in SAT_SCORE_RE.finditer(section) if 800 <= float(m.group(1)) <= 1600]
    md_act = [float(m.group(1)) for m in ACT_SCORE_RE.finditer(section) if 15 <= float(m.group(1)) <= 36]
    md_toefl = [float(m.group(1)) for m in TOEFL_SCORE_RE.finditer(section) if 50 <= float(m.group(1)) <= 120]
    md_ielts = [float(m.group(1)) for m in IELTS_SCORE_RE.finditer(section) if 4 <= float(m.group(1)) <= 9.5]
    md_gpa = [float(m.group(1)) for m in GPA_RE.finditer(section) if 1.0 <= float(m.group(1)) <= 4.5]

    # Check 5 major components
    components = {}
    components["sat"] = check_component(section, combined, r'SAT', md_sat, (800, 1600), NOT_REQUIRED_PATTERNS['sat'])
    components["act"] = check_component(section, combined, r'ACT', md_act, (15, 36), NOT_REQUIRED_PATTERNS['act'])
    components["toefl"] = check_component(section, combined, r'TOEFL', md_toefl, (50, 120), NOT_REQUIRED_PATTERNS['toefl'])
    components["ielts"] = check_component(section, combined, r'IELTS', md_ielts, (4, 9.5), NOT_REQUIRED_PATTERNS['ielts'])
    components["gpa"] = check_component(section, combined, r'GPA', md_gpa, (1.0, 4.5), NOT_REQUIRED_PATTERNS['gpa'])

    # Majority voting
    matched = sum(1 for v in components.values() if v == "MATCHED")
    mismatched = sum(1 for v in components.values() if v == "MISMATCHED")
    not_verifiable = sum(1 for v in components.values() if v == "NOT_VERIFIABLE")

    details = {
        "components": components,
        "matched": matched,
        "mismatched": mismatched,
        "not_verifiable": not_verifiable,
    }

    # Majority voting classification:
    #   ≥3 matched → verified
    #   2 matched + 0 mismatched → verified
    #   some matched + some mismatched → partial
    #   0 matched + 0 mismatched (all not verifiable) → partial
    #   0 matched + 1 mismatched → partial (single noisy mismatch, not enough to flag)
    #   0 matched + ≥2 mismatched → flagged
    if matched >= 3:
        return {"classification": "verified", "reason": f"{matched}_of_5_matched", "details": details}
    if matched >= 2 and mismatched == 0:
        return {"classification": "verified", "reason": f"{matched}_matched_0_mismatched", "details": details}
    if matched > 0 and mismatched > 0:
        return {"classification": "partially_verified", "reason": f"{matched}_matched_{mismatched}_mismatched", "details": details}
    if matched == 0 and mismatched <= 1:
        return {"classification": "partially_verified", "reason": f"0_matched_{mismatched}_mismatched_{not_verifiable}_not_verifiable", "details": details}
    return {"classification": "flagged", "reason": f"0_matched_{mismatched}_mismatched", "details": details}


# ─────────────────────────────────────────────
#  Tier assignment (based on Tuition + Deadlines only)
# ─────────────────────────────────────────────

def assign_tier(sections: dict) -> str:
    """Assign tier based on 2 sections: tuition_and_fees + application_deadlines."""
    vals = [sections[s] for s in ['tuition_and_fees', 'application_deadlines']]
    v_count = vals.count('verified')
    p_count = vals.count('partially_verified')
    f_count = vals.count('flagged')
    nd_count = vals.count('no_data')

    # Both no data
    if nd_count == 2:
        return "low_confidence"

    # Both verified → high
    if v_count == 2:
        return "high_confidence"

    # One verified + one no_data → high (the one with data is verified)
    if v_count == 1 and nd_count == 1:
        return "high_confidence"

    # Both flagged → low
    if f_count == 2:
        return "low_confidence"

    # One flagged + one no_data → low
    if f_count == 1 and nd_count == 1:
        return "low_confidence"

    # Everything else → moderate
    return "moderate_confidence"


# ─────────────────────────────────────────────
#  Confidence score (50 points per section, max 100)
# ─────────────────────────────────────────────

def calc_confidence_score(section_classifications: dict, section_results: dict) -> float:
    """Calculate a 0-100 confidence score. Each of 2 sections contributes up to 50 points."""
    total = 0.0

    for sec in ['tuition_and_fees', 'application_deadlines']:
        cls = section_classifications[sec]
        det = section_results[sec]

        if cls == 'verified':
            total += 50.0
        elif cls == 'no_data':
            total += 25.0  # neutral half-credit
        elif cls == 'partially_verified':
            if sec == 'tuition_and_fees':
                d = det.get("details", {})
                core_m = d.get("core_matched", 0)
                core_t = d.get("core_amounts", 0)
                total += 37.5 if (core_t > 0 and core_m / core_t >= 0.3) else 25.0
            else:  # deadlines
                reason = det.get("reason", "")
                total += 37.5 if "matched" in reason else 25.0
        elif cls == 'flagged':
            flag_type = det.get("flag_type", "")
            if flag_type == "F1_stale":
                total += 10.0  # dates were correct, just old
            else:
                total += 0

    return round(total, 1)


# ─────────────────────────────────────────────
#  Summary generation
# ─────────────────────────────────────────────

def generate_summary(section_classifications: dict, tuition_result: dict,
                     deadline_result: dict, admission_result: dict) -> str:
    parts = []

    tc = section_classifications["tuition_and_fees"].upper()
    tr = tuition_result["reason"]
    if tc == "NO_DATA":
        parts.append("Tuition: NO DATA - no crawled data available.")
    elif tc == "VERIFIED":
        parts.append(f"Tuition: VERIFIED - {tr}.")
    elif tc == "PARTIALLY_VERIFIED":
        parts.append(f"Tuition: PARTIAL - {tr}.")
    else:
        parts.append(f"Tuition: FLAGGED - {tr}.")

    dc = section_classifications["application_deadlines"].upper()
    dr = deadline_result["reason"]
    if dc == "NO_DATA":
        parts.append("Deadlines: NO DATA - no crawled data available.")
    elif dc == "VERIFIED":
        parts.append(f"Deadlines: VERIFIED - {dr}.")
    elif dc == "PARTIALLY_VERIFIED":
        parts.append(f"Deadlines: PARTIAL - {dr}.")
    else:
        parts.append(f"Deadlines: FLAGGED - {dr}.")

    ac = section_classifications["admission_requirements"].upper()
    ar = admission_result["reason"]
    if ac == "NO_DATA":
        parts.append("Admission: NO DATA - no crawled data available.")
    elif ac == "VERIFIED":
        parts.append(f"Admission: VERIFIED - {ar}.")
    elif ac == "PARTIALLY_VERIFIED":
        parts.append(f"Admission: PARTIAL - {ar}.")
    else:
        parts.append(f"Admission: FLAGGED - {ar}.")

    return " ".join(parts)


# ─────────────────────────────────────────────
#  Load crawled content
# ─────────────────────────────────────────────

def load_crawled_content(urls: list[str], url_index: dict) -> list[str]:
    texts = []
    for url in urls:
        h = url_index.get(url)
        if not h:
            continue
        page_file = PAGES_DIR / f"{h}.json"
        if not page_file.exists():
            continue
        try:
            page = json.loads(page_file.read_text(encoding="utf-8"))
            if page.get("status") == "success" and page.get("content"):
                texts.append(page["content"])
        except Exception:
            continue
    return texts


# ─────────────────────────────────────────────
#  Classify one file
# ─────────────────────────────────────────────

def classify_one_file(args):
    college, fname, fdata, url_index, md_base_dir = args

    md_path = Path(md_base_dir) / college / fname
    if not md_path.exists():
        return None

    try:
        md_content = md_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    tuition_texts = load_crawled_content(fdata.get("tuition_and_fees", []), url_index)
    deadline_texts = load_crawled_content(fdata.get("application_deadlines", []), url_index)
    admission_texts = load_crawled_content(fdata.get("admission_requirements", []), url_index)

    if not tuition_texts and not deadline_texts and not admission_texts:
        return None

    tuition_result = classify_tuition(md_content, tuition_texts)
    deadline_result = classify_deadlines(md_content, deadline_texts)
    admission_result = classify_admission(md_content, admission_texts)

    section_classifications = {
        "tuition_and_fees": tuition_result["classification"],
        "application_deadlines": deadline_result["classification"],
        "admission_requirements": admission_result["classification"],
    }

    tier = assign_tier(section_classifications)

    confidence_score = calc_confidence_score(
        section_classifications,
        {"tuition_and_fees": tuition_result, "application_deadlines": deadline_result, "admission_requirements": admission_result},
    )

    if confidence_score >= 75:
        confidence_label = "safe_to_present_as_official"
    elif confidence_score >= 60:
        confidence_label = "mostly_reliable"
    elif confidence_score >= 40:
        confidence_label = "use_with_caveat"
    else:
        confidence_label = "needs_review"

    summary = generate_summary(
        section_classifications, tuition_result, deadline_result, admission_result
    )

    def get_used_urls(urls, idx):
        return [url for url in urls if idx.get(url) and (PAGES_DIR / f"{idx[url]}.json").exists()]

    return {
        "college": college,
        "file": fname,
        "md_path": str(md_path),
        "tier": tier,
        "confidence_score": confidence_score,
        "confidence_label": confidence_label,
        "summary": summary,
        "sections": section_classifications,
        "section_details": {
            "tuition_and_fees": {
                "reason": tuition_result["reason"],
                "crawled_urls": get_used_urls(fdata.get("tuition_and_fees", []), url_index),
                "details": tuition_result.get("details", {}),
            },
            "application_deadlines": {
                "reason": deadline_result["reason"],
                "flag_type": deadline_result.get("flag_type", ""),
                "crawled_urls": get_used_urls(fdata.get("application_deadlines", []), url_index),
                "details": deadline_result.get("details", {}),
            },
            "admission_requirements": {
                "reason": admission_result["reason"],
                "crawled_urls": get_used_urls(fdata.get("admission_requirements", []), url_index),
                "details": admission_result.get("details", {}),
            },
        },
    }


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

def main():
    from concurrent.futures import ProcessPoolExecutor, as_completed

    for d in [HIGH_DIR, MODERATE_DIR, LOW_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    for d in [HIGH_DIR, MODERATE_DIR, LOW_DIR]:
        for f in d.glob("*.md"):
            f.unlink()

    with open(SECTION_URLS_FILE, encoding="utf-8") as f:
        section_urls = json.load(f)

    with open(INDEX_FILE, encoding="utf-8") as f:
        url_index = json.load(f)

    tasks = []
    for college, files in section_urls.items():
        for fname, fdata in files.items():
            tasks.append((college, fname, fdata, url_index, str(UG_MD_DIR)))

    print(f"Classifying {len(tasks)} UG files with 6 parallel workers...", flush=True)

    results = []
    tier_counts = {"high_confidence": 0, "moderate_confidence": 0, "low_confidence": 0}
    label_counts = {"safe_to_present_as_official": 0, "mostly_reliable": 0, "use_with_caveat": 0, "needs_review": 0}
    section_counts = {
        "tuition_and_fees": {"verified": 0, "partially_verified": 0, "flagged": 0, "no_data": 0},
        "application_deadlines": {"verified": 0, "partially_verified": 0, "flagged": 0, "no_data": 0},
        "admission_requirements": {"verified": 0, "partially_verified": 0, "flagged": 0, "no_data": 0},
    }

    with ProcessPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(classify_one_file, t): t for t in tasks}
        done = 0
        for future in as_completed(futures):
            result = future.result()
            if result is None:
                continue

            tier = result["tier"]
            tier_counts[tier] += 1
            label_counts[result["confidence_label"]] += 1

            for sec, cls in result["sections"].items():
                section_counts[sec][cls] += 1

            dest_dir = {"high_confidence": HIGH_DIR, "moderate_confidence": MODERATE_DIR, "low_confidence": LOW_DIR}[tier]
            dest_name = f"{result['college']}__{result['file']}"
            if len(str(dest_dir / dest_name)) > 250:
                dest_name = dest_name[:150] + ".md"
            shutil.copy2(result["md_path"], dest_dir / dest_name)

            del result["md_path"]
            results.append(result)

            done += 1
            if done % 50 == 0:
                print(f"  Processed {done} files...", flush=True)

    processed = len(results)
    if processed == 0:
        print("No files processed!")
        return

    report = {
        "total_processed": processed,
        "tier_counts": tier_counts,
        "confidence_label_counts": label_counts,
        "section_counts": section_counts,
        "results": results,
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n{'='*60}")
    print(f"UG CLASSIFICATION RESULTS")
    print(f"{'='*60}")
    print(f"Total processed: {processed}")
    print(f"\nTier distribution:")
    print(f"  High Confidence:    {tier_counts['high_confidence']}")
    print(f"  Moderate Confidence: {tier_counts['moderate_confidence']}")
    print(f"  Low Confidence:      {tier_counts['low_confidence']}")
    print(f"\nConfidence labels:")
    print(f"  safe_to_present_as_official (>=75): {label_counts['safe_to_present_as_official']} ({label_counts['safe_to_present_as_official']/processed*100:.1f}%)")
    print(f"  mostly_reliable (60-74):            {label_counts['mostly_reliable']} ({label_counts['mostly_reliable']/processed*100:.1f}%)")
    print(f"  use_with_caveat (40-59):            {label_counts['use_with_caveat']} ({label_counts['use_with_caveat']/processed*100:.1f}%)")
    print(f"  needs_review (<40):                 {label_counts['needs_review']} ({label_counts['needs_review']/processed*100:.1f}%)")
    print(f"\nPer-section breakdown (only where data existed):")
    for sec, counts in section_counts.items():
        with_data = counts['verified'] + counts['partially_verified'] + counts['flagged']
        if with_data > 0:
            print(f"  {sec} ({with_data} files with data):")
            print(f"    verified={counts['verified']}({counts['verified']/with_data*100:.1f}%)  partial={counts['partially_verified']}({counts['partially_verified']/with_data*100:.1f}%)  flagged={counts['flagged']}({counts['flagged']/with_data*100:.1f}%)")
        else:
            print(f"  {sec}: no data")
    print(f"\nFiles saved to: {OUTPUT_DIR}")
    print(f"Report: {REPORT_FILE}")


if __name__ == "__main__":
    main()
