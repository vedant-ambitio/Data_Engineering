"""
Unified 3-Tier Classification for PhD files.

Classifies each PhD .md file by verifying 3 sections against crawled official data:
  1. Tuition & Fees
  2. Application Deadlines
  3. Admission Requirements

Per-section classification: verified / partially_verified / flagged

File-level tier:
  Tier 1 (High Confidence):    All 3 sections verified
  Tier 2 (Moderate Confidence): 2 verified + 1 partial, OR 3 partial, OR 2 verified + 1 flagged
  Tier 3 (Low Confidence):     1 or 0 verified, 2+ flagged

Output:
  classification_results/phd/high_confidence/     ← Tier 1 .md files
  classification_results/phd/moderate_confidence/  ← Tier 2 .md files
  classification_results/phd/low_confidence/       ← Tier 3 .md files
  classification_results/phd/classification_report.json
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
PHD_MD_DIR = BASE_DIR / "phd_data" / "phd"
OFFICIAL_DIR = BASE_DIR / "official_urls"
SECTION_URLS_FILE = OFFICIAL_DIR / "phd_section_urls.json"
INDEX_FILE = OFFICIAL_DIR / "crawled_data" / "url_index.json"
PAGES_DIR = OFFICIAL_DIR / "crawled_data" / "pages"

OUTPUT_DIR = BASE_DIR / "classification_results" / "phd"
HIGH_DIR = OUTPUT_DIR / "high_confidence"
MODERATE_DIR = OUTPUT_DIR / "moderate_confidence"
LOW_DIR = OUTPUT_DIR / "low_confidence"
REPORT_FILE = OUTPUT_DIR / "classification_report.json"

TOLERANCE = 0.05  # 5% for amount matching
TODAY = date.today()


# ─────────────────────────────────────────────
#  Shared extraction helpers
# ─────────────────────────────────────────────

# Currency-amount regex (same as fees classifier)
CURRENCY_AMOUNT_RE = re.compile(
    r'(?:'
    r'(?P<sym>[£€$]|(?:US|S|A|C|HK|NZ)\$)\s*(?P<amt1>[\d,]+(?:\.\d{1,2})?)'
    r'|'
    r'(?P<code1>AUD|CAD|CHF|DKK|EUR|GBP|HKD|INR|JPY|KRW|MYR|NOK|NZD|SEK|SGD|THB|USD|ZAR)\s*(?P<amt2>[\d,]+(?:\.\d{1,2})?)'
    r'|'
    r'(?P<amt3>[\d,]+(?:\.\d{1,2})?)\s*(?P<code2>AUD|CAD|CHF|DKK|EUR|GBP|HKD|INR|JPY|KRW|MYR|NOK|NZD|SEK|SGD|THB|USD|ZAR)'
    r')',
    re.IGNORECASE
)

SYMBOL_TO_CURRENCY = {
    '$': 'USD', '£': 'GBP', '€': 'EUR',
    'US$': 'USD', 'S$': 'SGD', 'A$': 'AUD', 'C$': 'CAD',
    'HK$': 'HKD', 'NZ$': 'NZD',
}

TUITION_KEYWORDS_RE = re.compile(
    r'(?:tuition|fees?|cost|per\s+year|per\s+annum|per\s+semester|annual|yearly|doctoral|phd|program(?:me)?)',
    re.IGNORECASE
)

WAIVED_RE = re.compile(
    r'no tuition fees?|tuition (?:is|are) (?:typically )?(?:waived|covered)|fully funded|'
    r'fees? (?:are|is) (?:typically )?(?:waived|covered)|tuition[- ]free|'
    r'not (?:charged|paying) tuition|(?:full )?tuition (?:fee )?waiver|'
    r'does not charge tuition|no tuition',
    re.IGNORECASE
)

YEAR_RE = re.compile(
    r'(?:AY\s*)?(?:20\d{2})\s*[-/]\s*(?:20)?\d{2}|academic year\s*20\d{2}|20\d{2}\s*academic year',
    re.IGNORECASE
)

DOMESTIC_RE = re.compile(r'(?:home|domestic|in-state|EU/EEA|local)', re.IGNORECASE)
INTERNATIONAL_RE = re.compile(r'(?:overseas|international|out-of-state|non-EU|foreign)', re.IGNORECASE)

# Date patterns for deadlines
MONTH_NAMES = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

# "December 1, 2025" or "1 March 2026" or "January 8, 2026"
DATE_FULL_RE = re.compile(
    r'(?:(?P<m1>' + '|'.join(MONTH_NAMES.keys()) + r')\s+(?P<d1>\d{1,2})\s*,?\s*(?P<y1>20\d{2}))'
    r'|(?:(?P<d2>\d{1,2})\s+(?P<m2>' + '|'.join(MONTH_NAMES.keys()) + r')\s*,?\s*(?P<y2>20\d{2}))'
    r'|(?:(?P<y3>20\d{2})-(?P<m3>\d{2})-(?P<d3>\d{2}))',
    re.IGNORECASE
)

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

# Admission test score patterns
TOEFL_SCORE_RE = re.compile(r'TOEFL.*?(?:score\s+(?:of\s+)?)?(?:at\s+least\s+)?(\d{2,3}(?:\.\d)?)', re.IGNORECASE)
IELTS_SCORE_RE = re.compile(r'IELTS.*?(?:score\s+(?:of\s+)?)?(?:at\s+least\s+)?(\d\.\d)', re.IGNORECASE)
DET_SCORE_RE = re.compile(r'(?:Duolingo|DET).*?(?:score\s+(?:of\s+)?)?(\d{2,3})', re.IGNORECASE)
PTE_SCORE_RE = re.compile(r'PTE.*?(?:score\s+(?:of\s+)?)?(\d{2,3})', re.IGNORECASE)

GRE_REQUIRED_RE = re.compile(r'GRE.*?(?:is\s+)?required', re.IGNORECASE)
GRE_NOT_REQUIRED_RE = re.compile(r'GRE.*?(?:not\s+required|not\s+sought|no longer\s+require|not\s+needed|not\s+mandatory)', re.IGNORECASE)
GRE_OPTIONAL_RE = re.compile(r'GRE.*?optional|GRE.*?encouraged\s+but|GRE.*?not\s+required\s+but', re.IGNORECASE)

APP_FEE_RE = re.compile(r'application\s+fee', re.IGNORECASE)
NO_APP_FEE_RE = re.compile(r'no\s+application\s+fee|free\s+of\s+charge|application\s+is\s+free', re.IGNORECASE)

LOR_COUNT_RE = re.compile(
    r'(\d)\s*(?:letters?\s+of\s+recommendation|letters?\s+are\s+required|references?\s+(?:are\s+)?required|academic\s+referees?|letters?\s+required)',
    re.IGNORECASE
)
WORD_TO_NUM = {'two': 2, 'three': 3, 'four': 4, 'five': 5}
LOR_WORD_RE = re.compile(
    r'(two|three|four|five)\s+(?:letters?\s+of\s+recommendation|letters?|references?|referees?)',
    re.IGNORECASE
)


# ─────────────────────────────────────────────
#  Extraction functions
# ─────────────────────────────────────────────

def extract_section(content: str, heading: str) -> str:
    """Extract section text without citation blocks."""
    pattern = rf'^## {re.escape(heading)}\s*\n(.*?)(?=\n## [A-Z]|\Z)'
    matches = re.findall(pattern, content, re.DOTALL | re.MULTILINE)
    full = "\n".join(matches)
    return re.sub(r'<citation>.*?</citation>', '', full, flags=re.DOTALL).strip()


def parse_amount(amt_str: str) -> float:
    return float(amt_str.replace(',', ''))


def extract_currency_amounts(text: str) -> list[tuple[str, float]]:
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
            if amount > 10:
                pairs.append((currency, amount))
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


def extract_dates(text: str) -> list[tuple[int, int, int]]:
    """Extract (year, month, day) tuples from text."""
    dates = []
    for m in DATE_FULL_RE.finditer(text):
        try:
            if m.group('m1'):
                month = MONTH_NAMES.get(m.group('m1').lower())
                dates.append((int(m.group('y1')), month, int(m.group('d1'))))
            elif m.group('m2'):
                month = MONTH_NAMES.get(m.group('m2').lower())
                dates.append((int(m.group('y2')), month, int(m.group('d2'))))
            elif m.group('y3'):
                dates.append((int(m.group('y3')), int(m.group('m3')), int(m.group('d3'))))
        except (ValueError, TypeError):
            continue
    return dates


def extract_intakes(text: str) -> set[str]:
    return {m.strip().lower() for m in INTAKE_RE.findall(text)}


def extract_test_scores(text: str) -> dict:
    """Extract standardized test scores."""
    scores = {}
    for m in TOEFL_SCORE_RE.finditer(text):
        val = float(m.group(1))
        if 50 <= val <= 120 or 1 <= val <= 10:  # old scale or new scale
            scores.setdefault('toefl', []).append(val)
    for m in IELTS_SCORE_RE.finditer(text):
        val = float(m.group(1))
        if 4 <= val <= 9.5:
            scores.setdefault('ielts', []).append(val)
    for m in DET_SCORE_RE.finditer(text):
        val = float(m.group(1))
        if 80 <= val <= 165:
            scores.setdefault('det', []).append(val)
    for m in PTE_SCORE_RE.finditer(text):
        val = float(m.group(1))
        if 30 <= val <= 90:
            scores.setdefault('pte', []).append(val)
    return scores


def extract_gre_status(text: str) -> str:
    """Return 'required', 'not_required', 'optional', or 'unknown'."""
    if GRE_NOT_REQUIRED_RE.search(text):
        return 'not_required'
    if GRE_OPTIONAL_RE.search(text):
        return 'optional'
    if GRE_REQUIRED_RE.search(text):
        return 'required'
    return 'unknown'


def extract_lor_count(text: str) -> int:
    """Extract number of recommendation letters required."""
    m = LOR_COUNT_RE.search(text)
    if m:
        return int(m.group(1))
    m = LOR_WORD_RE.search(text)
    if m:
        return WORD_TO_NUM.get(m.group(1).lower(), 0)
    return 0


# ─────────────────────────────────────────────
#  Section classifiers
# ─────────────────────────────────────────────

def classify_tuition(md_content: str, crawled_texts: list[str]) -> dict:
    section = extract_section(md_content, "Tuition & Fees")
    if not section:
        return {"classification": "no_data", "reason": "no_tuition_section"}
    if not crawled_texts:
        return {"classification": "no_data", "reason": "no_crawled_data"}

    combined = "\n".join(crawled_texts)

    md_amounts = extract_currency_amounts(section)
    crawled_amounts = extract_amounts_near_keywords(combined, TUITION_KEYWORDS_RE)

    md_waived = bool(WAIVED_RE.search(section))
    crawled_waived = bool(WAIVED_RE.search(combined))

    md_years = extract_years(section)
    crawled_years = extract_years(combined)
    year_match = bool(md_years & crawled_years)

    md_dom, md_intl = bool(DOMESTIC_RE.search(section)), bool(INTERNATIONAL_RE.search(section))
    cr_dom, cr_intl = bool(DOMESTIC_RE.search(combined)), bool(INTERNATIONAL_RE.search(combined))
    distinction_match = (not (md_dom and md_intl)) or ((md_dom and md_intl) and (cr_dom and cr_intl))

    matched = []
    unmatched = []
    for (mc, ma) in md_amounts:
        found = any(mc == cc and amounts_match(ma, ca) for cc, ca in crawled_amounts)
        (matched if found else unmatched).append((mc, ma))

    details = {
        "md_amounts": len(md_amounts), "matched": len(matched), "unmatched": len(unmatched),
        "md_waived": md_waived, "crawled_waived": crawled_waived,
        "year_match": year_match, "distinction_match": distinction_match,
    }

    if not md_amounts:
        if md_waived and crawled_waived:
            return {"classification": "verified", "reason": "waived_language_match", "details": details}
        elif md_waived:
            return {"classification": "partially_verified", "reason": "md_waived_crawled_unclear", "details": details}
        return {"classification": "flagged", "reason": "no_amounts_no_waived", "details": details}

    total = len(md_amounts)
    if len(matched) == total:
        return {"classification": "verified", "reason": f"all_{total}_amounts_matched", "details": details}
    elif len(matched) > 0:
        return {"classification": "partially_verified", "reason": f"{len(matched)}_of_{total}_matched", "details": details}
    else:
        if md_waived and crawled_waived:
            return {"classification": "partially_verified", "reason": "amounts_mismatch_but_waived_match", "details": details}
        return {"classification": "flagged", "reason": f"0_of_{total}_matched", "details": details}


def classify_deadlines(md_content: str, crawled_texts: list[str]) -> dict:
    section = extract_section(md_content, "Application Deadlines")
    if not section:
        return {"classification": "no_data", "reason": "no_deadline_section"}
    if not crawled_texts:
        return {"classification": "no_data", "reason": "no_crawled_data"}

    combined = "\n".join(crawled_texts)

    # Heuristic 2: Rolling admissions
    md_rolling = bool(ROLLING_RE.search(section))
    crawled_rolling = bool(ROLLING_RE.search(combined))

    if md_rolling:
        if crawled_rolling:
            return {"classification": "verified", "reason": "rolling_match", "details": {"md_rolling": True, "crawled_rolling": True}}
        # Check if crawled has specific dates instead
        crawled_dates = extract_dates(combined)
        if crawled_dates:
            return {"classification": "flagged", "reason": "md_says_rolling_crawled_has_dates", "details": {"crawled_dates_count": len(crawled_dates)}}
        return {"classification": "partially_verified", "reason": "md_rolling_crawled_unclear", "details": {}}

    # Check "information not available"
    if re.search(r'information not available', section, re.IGNORECASE) and len(section) < 200:
        return {"classification": "partially_verified", "reason": "info_not_available", "details": {}}

    # Heuristic 1: Date matching
    md_dates = extract_dates(section)
    crawled_dates = extract_dates(combined)

    if not md_dates:
        # Heuristic 3: Intake term cross-check
        md_intakes = extract_intakes(section)
        crawled_intakes = extract_intakes(combined)
        if md_intakes & crawled_intakes:
            return {"classification": "partially_verified", "reason": "intake_terms_match", "details": {"common_intakes": list(md_intakes & crawled_intakes)}}
        return {"classification": "partially_verified", "reason": "no_dates_extractable", "details": {}}

    md_date_set = set(md_dates)
    crawled_date_set = set(crawled_dates)
    matched_dates = md_date_set & crawled_date_set

    # Heuristic 3: Intake terms as bonus
    md_intakes = extract_intakes(section)
    crawled_intakes = extract_intakes(combined)
    intake_match = bool(md_intakes & crawled_intakes)

    details = {
        "md_dates_count": len(md_date_set), "matched_dates": len(matched_dates),
        "intake_match": intake_match,
    }

    # Helper to safely create date objects
    def safe_date(y, m, d):
        try:
            return date(y, m, d) if y and m and d and 1 <= m <= 12 and 1 <= d <= 31 else None
        except ValueError:
            return None

    # Check if all md dates are in the past
    md_safe_dates = [sd for y, m, d in md_dates if (sd := safe_date(y, m, d)) is not None]
    all_past = all(sd < TODAY for sd in md_safe_dates) if md_safe_dates else False
    any_current = any(sd >= TODAY for sd in md_safe_dates) if md_safe_dates else False

    details["all_dates_past"] = all_past

    if len(matched_dates) > 0 and len(matched_dates) >= len(md_date_set) / 2:
        if any_current:
            # Dates match AND some are current/future — trustworthy now
            return {"classification": "verified", "reason": f"{len(matched_dates)}_of_{len(md_date_set)}_dates_matched_current", "details": details}
        else:
            # Dates match but ALL are past — correct but stale
            return {"classification": "partially_verified", "reason": f"{len(matched_dates)}_of_{len(md_date_set)}_dates_matched_but_all_past", "details": details}

    if len(matched_dates) > 0:
        return {"classification": "partially_verified", "reason": f"{len(matched_dates)}_of_{len(md_date_set)}_dates_matched", "details": details}

    # Zero match — all cases are flagged
    if all_past and crawled_dates:
        return {"classification": "flagged", "reason": "stale_dates_website_updated", "details": details}

    # Intake term match as weak fallback
    if intake_match:
        return {"classification": "partially_verified", "reason": "dates_mismatch_but_intakes_match", "details": details}

    return {"classification": "flagged", "reason": "no_dates_matched", "details": details}


def find_numbers_near_keyword(text: str, keyword_pattern: str, window: int = 200) -> list[float]:
    """Find all numbers within `window` chars of a keyword match."""
    numbers = []
    for km in re.finditer(keyword_pattern, text, re.IGNORECASE):
        start = max(0, km.start() - window)
        end = min(len(text), km.end() + window)
        chunk = text[start:end]
        # Extract numbers (integers and decimals)
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
    """
    Simple per-entity check:
    1. Is keyword in both md and crawled?
    2. If yes, do any numbers near the keyword in crawled match any md values?

    Returns: {status, md_value, crawled_value} where status is:
      matched / mismatched / not_in_crawled / not_applicable
    """
    md_has = bool(re.search(keyword, md_text, re.IGNORECASE))
    crawled_has = bool(re.search(keyword, crawled_text, re.IGNORECASE))

    if not md_has:
        return {"status": "not_applicable"}

    if not crawled_has:
        return {"status": "not_in_crawled", "md_values": md_values}

    if not md_values:
        return {"status": "keyword_only_no_values"}

    # Find numbers near keyword in crawled text
    crawled_numbers = find_numbers_near_keyword(crawled_text, keyword)

    # Filter by valid range if provided
    if valid_range and crawled_numbers:
        lo, hi = valid_range
        crawled_numbers = [n for n in crawled_numbers if lo <= n <= hi]

    # Check if ANY md value appears in crawled numbers (exact or ±5%)
    for mv in md_values:
        for cn in crawled_numbers:
            if amounts_match(mv, cn):
                return {"status": "matched", "md_value": mv, "crawled_value": cn}

    return {"status": "mismatched", "md_values": md_values, "crawled_numbers": crawled_numbers[:10]}


def check_status_entity(md_text: str, crawled_text: str, keyword: str,
                        positive_patterns: list[str], negative_patterns: list[str]) -> dict:
    """
    Check a status entity (required / not required / optional).
    Both sides must agree on the status.
    """
    md_has = bool(re.search(keyword, md_text, re.IGNORECASE))
    crawled_has = bool(re.search(keyword, crawled_text, re.IGNORECASE))

    if not md_has:
        return {"status": "not_applicable"}
    if not crawled_has:
        return {"status": "not_in_crawled"}

    def get_status(text):
        # Check negative first (more specific: "not required" before "required")
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
    else:
        return {"status": "mismatched", "md_status": md_status, "crawled_status": crawled_status}


def classify_admission(md_content: str, crawled_texts: list[str]) -> dict:
    section = extract_section(md_content, "Admission Requirements")
    if not section:
        return {"classification": "no_data", "reason": "no_admission_section"}
    if not crawled_texts:
        return {"classification": "no_data", "reason": "no_crawled_data"}

    combined = "\n".join(crawled_texts)

    # Check if crawled page has any admission-related content
    if not re.search(r'admission|requirement|apply|application|eligibility|toefl|ielts|gre|gpa|proficiency', combined, re.IGNORECASE):
        return {"classification": "partially_verified", "reason": "crawled_page_not_admission_related", "details": {}}

    # ── Per-entity extraction from .md ──
    md_toefl = [float(m.group(1)) for m in TOEFL_SCORE_RE.finditer(section)
                if 50 <= float(m.group(1)) <= 120 or 1 <= float(m.group(1)) <= 10]
    md_ielts = [float(m.group(1)) for m in IELTS_SCORE_RE.finditer(section)
                if 4 <= float(m.group(1)) <= 9.5]
    md_det = [float(m.group(1)) for m in DET_SCORE_RE.finditer(section)
              if 80 <= float(m.group(1)) <= 165]
    md_pte = [float(m.group(1)) for m in PTE_SCORE_RE.finditer(section)
              if 30 <= float(m.group(1)) <= 90]
    md_lor = extract_lor_count(section)
    md_app_fee = extract_amounts_near_keywords(section, APP_FEE_RE)

    # ── Check each entity ──
    entities = {}

    # Numeric entities: keyword + number near keyword
    entities["toefl"] = check_entity(section, combined, r'TOEFL', md_toefl, valid_range=(50, 120))
    entities["ielts"] = check_entity(section, combined, r'IELTS', md_ielts, valid_range=(4, 9.5))
    entities["duolingo"] = check_entity(section, combined, r'Duolingo|DET', md_det, valid_range=(80, 165))
    entities["pte"] = check_entity(section, combined, r'PTE', md_pte, valid_range=(30, 90))
    entities["cambridge"] = check_entity(
        section, combined, r'Cambridge|CAE|CPE|C1 Advanced|C2 Proficiency',
        [float(m.group(1)) for m in re.finditer(r'(?:Cambridge|CAE|CPE|C1 Advanced|C2 Proficiency).*?(\d{3})', section, re.IGNORECASE)
         if 170 <= float(m.group(1)) <= 210],
        valid_range=(170, 210)
    )

    # LOR count
    if md_lor > 0:
        crawled_lor = extract_lor_count(combined)
        if crawled_lor > 0:
            entities["lor_count"] = {
                "status": "matched" if md_lor == crawled_lor else "mismatched",
                "md_value": md_lor, "crawled_value": crawled_lor
            }
        else:
            entities["lor_count"] = {"status": "not_in_crawled", "md_value": md_lor}
    else:
        entities["lor_count"] = {"status": "not_applicable"}

    # Application fee
    if bool(NO_APP_FEE_RE.search(section)):
        if bool(NO_APP_FEE_RE.search(combined)):
            entities["app_fee"] = {"status": "matched", "value": "no_fee"}
        elif bool(re.search(r'application fee', combined, re.IGNORECASE)):
            entities["app_fee"] = {"status": "mismatched", "md_value": "no_fee", "crawled": "has_fee_info"}
        else:
            entities["app_fee"] = {"status": "not_in_crawled", "md_value": "no_fee"}
    elif md_app_fee:
        crawled_fee = extract_amounts_near_keywords(combined, APP_FEE_RE)
        fee_matched = any(
            mc == cc and amounts_match(ma, ca)
            for mc, ma in md_app_fee for cc, ca in crawled_fee
        )
        if fee_matched:
            entities["app_fee"] = {"status": "matched"}
        elif crawled_fee:
            entities["app_fee"] = {"status": "mismatched", "md_values": [(c, a) for c, a in md_app_fee]}
        else:
            entities["app_fee"] = {"status": "not_in_crawled"}
    else:
        entities["app_fee"] = {"status": "not_applicable"}

    # GRE — split into status + score (two separate entities)
    # GRE status: required / not required / optional
    entities["gre_status"] = check_status_entity(
        section, combined, r'GRE',
        positive_patterns=[r'GRE\s+(?:General\s+)?(?:(?:Test|scores?)\s+)?(?:is\s+|are\s+)?required(?!\s+document)',
                           r'(?:submit|provide|send).*GRE\s+scores?'],
        negative_patterns=[r'GRE.*not\s+required', r'GRE.*not\s+sought', r'GRE.*no longer\s+require',
                           r'GRE.*not\s+needed', r'GRE.*not\s+mandatory',
                           r'GRE.*optional', r'GRE.*encouraged\s+but',
                           r'does\s+not\s+require.*GRE', r'not\s+accept.*GRE', r'GRE.*waived',
                           r'without\s+GRE', r'GRE.*not\s+used', r'no\s+GRE']
    )
    # GRE score: numeric (260-340 combined, or V/Q 130-170 each)
    md_gre_scores = [float(m.group(1)) for m in re.finditer(r'GRE.*?(\d{3})', section, re.IGNORECASE)
                     if 130 <= float(m.group(1)) <= 340]
    entities["gre_score"] = check_entity(section, combined, r'GRE', md_gre_scores, valid_range=(130, 340))

    # GPA (numeric)
    md_gpa = [float(m.group(1)) for m in re.finditer(r'GPA.*?(\d\.\d+)', section, re.IGNORECASE)
              if 1.0 <= float(m.group(1)) <= 4.5]
    entities["gpa"] = check_entity(section, combined, r'GPA', md_gpa, valid_range=(1.0, 4.5))

    # Work experience (required / not required)
    entities["work_experience"] = check_status_entity(
        section, combined, r'work experience|professional experience',
        positive_patterns=[r'(?:work|professional)\s+experience.*required',
                           r'at least.*(?:year|month).*(?:work|professional)\s+experience',
                           r'must have.*(?:work|professional)\s+experience'],
        negative_patterns=[r'(?:work|professional)\s+experience.*not.*required',
                           r'not.*(?:require|need).*(?:work|professional)\s+experience',
                           r'no\s+(?:work|professional)\s+experience.*required']
    )

    # ── Roll up to classification ──
    matched = sum(1 for e in entities.values() if e["status"] == "matched")
    mismatched = sum(1 for e in entities.values() if e["status"] == "mismatched")
    not_in_crawled = sum(1 for e in entities.values() if e["status"] == "not_in_crawled")
    not_applicable = sum(1 for e in entities.values() if e["status"] == "not_applicable")
    verifiable = matched + mismatched  # entities where both sides had data

    details = {
        "entities": entities,
        "matched": matched,
        "mismatched": mismatched,
        "not_in_crawled": not_in_crawled,
        "not_applicable": not_applicable,
    }

    if verifiable == 0:
        return {"classification": "partially_verified", "reason": "no_verifiable_entities", "details": details}

    if mismatched == 0:
        return {"classification": "verified", "reason": f"all_{matched}_entities_matched", "details": details}

    if matched > mismatched:
        return {"classification": "partially_verified", "reason": f"{matched}_matched_{mismatched}_mismatched", "details": details}

    return {"classification": "flagged", "reason": f"{matched}_matched_{mismatched}_mismatched", "details": details}


# ─────────────────────────────────────────────
#  Tier assignment
# ─────────────────────────────────────────────

def assign_tier(sections: dict) -> str:
    """Assign file-level tier based on per-section classifications.
    'no_data' sections are neutral — they don't help or hurt the score.
    Only sections with actual crawled data contribute to the tier decision.
    """
    vals = [sections[s] for s in ['tuition_and_fees', 'application_deadlines', 'admission_requirements']]
    v_count = vals.count('verified')
    p_count = vals.count('partially_verified')
    f_count = vals.count('flagged')
    nd_count = vals.count('no_data')

    # If all 3 sections have no data, we can't classify
    if nd_count == 3:
        return "low_confidence"

    # Count only sections that have data
    data_sections = 3 - nd_count

    # Tier 1: All sections with data are verified
    if v_count == data_sections and data_sections >= 1:
        return "high_confidence"

    # Tier 3: 2+ flagged among sections with data
    if f_count >= 2:
        return "low_confidence"

    # Tier 2: Everything else
    return "moderate_confidence"


# ─────────────────────────────────────────────
#  Confidence score
# ─────────────────────────────────────────────

def calc_confidence_score(section_classifications: dict, section_results: dict) -> float:
    """Calculate a 0-100 confidence score based on per-section quality.
    Each section contributes up to 33.3 points.
    """
    total = 0.0

    for sec in ['tuition_and_fees', 'application_deadlines', 'admission_requirements']:
        cls = section_classifications[sec]
        det = section_results[sec]

        if cls == 'verified':
            total += 33.3

        elif cls == 'no_data':
            total += 16.5  # neutral half-credit

        elif cls == 'partially_verified':
            # Distinguish clean partial (0 mismatches) from dirty partial
            if sec == 'admission_requirements':
                ents = det.get("details", {}).get("entities", {})
                mismatched = sum(1 for e in ents.values() if e.get("status") == "mismatched")
                total += 25.0 if mismatched == 0 else 15.0
            elif sec == 'tuition_and_fees':
                d = det.get("details", {})
                matched = d.get("matched", 0)
                amt = d.get("md_amounts", 0)
                total += 25.0 if (amt > 0 and matched / amt >= 0.5) else 15.0
            else:  # deadlines
                reason = det.get("reason", "")
                total += 25.0 if "matched" in reason else 15.0

        elif cls == 'flagged':
            if sec == 'admission_requirements':
                ents = det.get("details", {}).get("entities", {})
                matched = sum(1 for e in ents.values() if e.get("status") == "matched")
                total += 8.0 if matched > 0 else 0
            else:
                total += 0

    return round(total, 1)


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

def load_crawled_content(urls: list[str], url_index: dict) -> list[str]:
    """Load crawled text for a list of URLs."""
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


def generate_summary(section_classifications: dict, tuition_result: dict,
                     deadline_result: dict, admission_result: dict) -> str:
    """Generate a plain-English summary of classification results."""
    parts = []

    # Tuition summary
    tc = section_classifications["tuition_and_fees"].upper()
    tr = tuition_result["reason"]
    td = tuition_result.get("details", {})
    if tc == "NO_DATA":
        parts.append("Tuition: NO DATA - no crawled data available.")
    elif tc == "VERIFIED":
        if "waived" in tr:
            parts.append("Tuition: VERIFIED - both .md and official page confirm tuition waived/funded.")
        else:
            n = td.get("matched", td.get("md_amounts", 0))
            parts.append(f"Tuition: VERIFIED - {tr}.")
    elif tc == "PARTIALLY_VERIFIED":
        parts.append(f"Tuition: PARTIAL - {tr}.")
    else:
        parts.append(f"Tuition: FLAGGED - {tr}.")

    # Deadline summary
    dc = section_classifications["application_deadlines"].upper()
    dr = deadline_result["reason"]
    dd = deadline_result.get("details", {})
    if dc == "NO_DATA":
        parts.append("Deadlines: NO DATA - no crawled data available.")
    elif dc == "VERIFIED":
        parts.append(f"Deadlines: VERIFIED - {dr}.")
    elif dc == "PARTIALLY_VERIFIED":
        if "past" in dr:
            parts.append(f"Deadlines: PARTIAL - dates matched but all are in the past (stale).")
        elif "intake" in dr:
            parts.append(f"Deadlines: PARTIAL - intake terms match but specific dates differ.")
        else:
            parts.append(f"Deadlines: PARTIAL - {dr}.")
    else:
        if "stale" in dr:
            parts.append("Deadlines: FLAGGED - all dates in .md are past, website has updated to newer dates.")
        else:
            parts.append(f"Deadlines: FLAGGED - {dr}.")

    # Admission summary — per-entity detail
    ac = section_classifications["admission_requirements"].upper()
    ad = admission_result.get("details", {})
    entities = ad.get("entities", {})
    if ac == "NO_DATA":
        parts.append("Admission: NO DATA - no crawled data available.")
    elif not entities:
        parts.append(f"Admission: {ac} - {admission_result['reason']}.")
    else:
        matched_list = []
        mismatched_list = []
        not_found_list = []

        for name, info in entities.items():
            status = info.get("status", "not_applicable")
            if status == "matched":
                mv = info.get("md_value", info.get("value", ""))
                cv = info.get("crawled_value", "")
                if mv and cv and mv != cv:
                    matched_list.append(f"{name} ({mv}~{cv})")
                elif mv:
                    matched_list.append(f"{name} ({mv})")
                else:
                    matched_list.append(name)
            elif status == "mismatched":
                mv = info.get("md_value", info.get("md_values", info.get("md_status", "")))
                cv = info.get("crawled_value", info.get("crawled_numbers", info.get("crawled_status", "")))
                mismatched_list.append(f"{name} (md={mv}, crawled={cv})")
            elif status == "not_in_crawled":
                mv = info.get("md_value", info.get("md_values", ""))
                not_found_list.append(f"{name}")

        detail_parts = []
        if matched_list:
            detail_parts.append(f"matched: {', '.join(matched_list)}")
        if mismatched_list:
            detail_parts.append(f"mismatched: {', '.join(mismatched_list)}")
        if not_found_list:
            detail_parts.append(f"not on page: {', '.join(not_found_list)}")

        parts.append(f"Admission: {ac} - {'; '.join(detail_parts)}.")

    return " ".join(parts)


def classify_one_file(args):
    """Classify a single file — used by multiprocessing pool."""
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

    # COMPLETE-ONLY MODE: skip files where any section has uncrawled URLs
    # A section is complete if ALL its URLs have been crawled (or it has no URLs)
    for sec in ['tuition_and_fees', 'application_deadlines', 'admission_requirements']:
        sec_urls = fdata.get(sec, [])
        if sec_urls:
            crawled_count = sum(1 for u in sec_urls if url_index.get(u) and (PAGES_DIR / f"{url_index[u]}.json").exists())
            if crawled_count < len(sec_urls):
                return None  # incomplete — skip

    tuition_result = classify_tuition(md_content, tuition_texts)
    deadline_result = classify_deadlines(md_content, deadline_texts)
    admission_result = classify_admission(md_content, admission_texts)

    section_classifications = {
        "tuition_and_fees": tuition_result["classification"],
        "application_deadlines": deadline_result["classification"],
        "admission_requirements": admission_result["classification"],
    }

    tier = assign_tier(section_classifications)

    # Calculate confidence score
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

    # Collect which crawled URLs were actually used per section
    def get_used_urls(urls, idx):
        used = []
        for url in urls:
            h = idx.get(url)
            if h and (PAGES_DIR / f"{h}.json").exists():
                used.append(url)
        return used

    tuition_urls_used = get_used_urls(fdata.get("tuition_and_fees", []), url_index)
    deadline_urls_used = get_used_urls(fdata.get("application_deadlines", []), url_index)
    admission_urls_used = get_used_urls(fdata.get("admission_requirements", []), url_index)

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
                "crawled_urls": tuition_urls_used,
                "details": tuition_result.get("details", {}),
            },
            "application_deadlines": {
                "reason": deadline_result["reason"],
                "crawled_urls": deadline_urls_used,
                "details": deadline_result.get("details", {}),
            },
            "admission_requirements": {
                "reason": admission_result["reason"],
                "crawled_urls": admission_urls_used,
                "details": admission_result.get("details", {}),
            },
        },
    }


def main():
    from concurrent.futures import ProcessPoolExecutor, as_completed

    for d in [HIGH_DIR, MODERATE_DIR, LOW_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # Clean previous results
    for d in [HIGH_DIR, MODERATE_DIR, LOW_DIR]:
        for f in d.glob("*.md"):
            f.unlink()

    with open(SECTION_URLS_FILE, encoding="utf-8") as f:
        section_urls = json.load(f)

    with open(INDEX_FILE, encoding="utf-8") as f:
        url_index = json.load(f)

    # Build task list
    tasks = []
    for college, files in section_urls.items():
        for fname, fdata in files.items():
            tasks.append((college, fname, fdata, url_index, str(PHD_MD_DIR)))

    print(f"Classifying {len(tasks)} files with 6 parallel workers...", flush=True)

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

            # Copy file to tier folder
            dest_dir = {"high_confidence": HIGH_DIR, "moderate_confidence": MODERATE_DIR, "low_confidence": LOW_DIR}[tier]
            dest_name = f"{result['college']}__{result['file']}"
            if len(str(dest_dir / dest_name)) > 250:
                dest_name = dest_name[:150] + ".md"
            shutil.copy2(result["md_path"], dest_dir / dest_name)

            # Remove md_path from result before storing in report
            del result["md_path"]
            results.append(result)

            done += 1
            if done % 500 == 0:
                print(f"  Processed {done} files...", flush=True)

    processed = len(results)

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
    print(f"CLASSIFICATION RESULTS")
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
