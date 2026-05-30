#!/usr/bin/env python3
"""
olympiad_scraper.py — Generic scraper for Activity Finder olympiads
====================================================================

Single script that loops over all olympiads in olympiads_urls.json and:
  1. Fetches all source URLs (HTTP first, Puppeteer fallback)
  2. Strips HTML to plain text with source citations
  3. Sends concatenated text to Gemini for structured extraction
  4. Saves output as JSON + markdown + QA sidecar

Reuses the auth/Gemini-call pattern from structured_data_extraction.py
but does NOT modify or import any existing files.

Usage:
  python olympiad_scraper.py                            # all olympiads
  python olympiad_scraper.py --olympiad IMO             # single olympiad
  python olympiad_scraper.py --max 5                    # first 5 only
  python olympiad_scraper.py --skip-existing            # resume mode
  python olympiad_scraper.py --dry-run                  # show what would run

Outputs (under olympiad_data/):
  olympiad_data/raw/IMO.json              structured JSON with citations
  olympiad_data/markdown/IMO.md           human-readable markdown
  olympiad_data/qa/IMO_qa.json            per-field source tracking
  olympiad_data/_summary_TIMESTAMP.json   batch run summary
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"
MAX_OUTPUT_TOKENS = 16000

# Resolve paths relative to this script's location, NOT current working directory.
# Script lives in: course_data/Olympiad/olympiad_scraper.py
# Service account key lives in: course_data/dashboard/gcp-key.json
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)  # parent of Olympiad/ → course_data/

# Service account key (in course_data/dashboard/)
VERTEX_SA_KEY_PATH = os.path.join(PROJECT_ROOT, "dashboard", "gcp-key.json")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
USE_VERTEX = True

# Paths inside Olympiad/ folder
OLYMPIAD_CONFIG = os.path.join(SCRIPT_DIR, "olympiads_urls.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "olympiad_data")
RAW_DIR = os.path.join(OUTPUT_DIR, "raw")
MD_DIR = os.path.join(OUTPUT_DIR, "markdown")
QA_DIR = os.path.join(OUTPUT_DIR, "qa")
SOURCES_DIR = os.path.join(OUTPUT_DIR, "sources")

# Per-source fetch settings
HTTP_TIMEOUT = 30
MAX_TEXT_PER_SOURCE = 8000   # cap each source to avoid prompt bloat
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Token cache
_token_cache = {"token": None, "timestamp": 0}
_token_lock = threading.Lock()
TOKEN_REFRESH_INTERVAL = 3000


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH (copied pattern from structured_data_extraction.py)
# ══════════════════════════════════════════════════════════════════════════════

def _get_vertex_token():
    with _token_lock:
        now = time.time()
        if _token_cache["token"] and (now - _token_cache["timestamp"]) < TOKEN_REFRESH_INTERVAL:
            return _token_cache["token"]

    # Try gcloud first
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            token = result.stdout.strip()
            with _token_lock:
                _token_cache["token"] = token
                _token_cache["timestamp"] = time.time()
            return token
    except Exception:
        pass

    # Fallback: service account JWT
    try:
        if not os.path.exists(VERTEX_SA_KEY_PATH):
            print(f"[ERROR] Service account key not found at {VERTEX_SA_KEY_PATH}")
            return None

        with open(VERTEX_SA_KEY_PATH) as f:
            sa_key = json.load(f)

        now_ts = int(time.time())
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=")
        payload_data = {
            "iss": sa_key["client_email"],
            "scope": "https://www.googleapis.com/auth/cloud-platform",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now_ts,
            "exp": now_ts + 3600,
        }
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=")
        signing_input = header + b"." + payload_b64

        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key = serialization.load_pem_private_key(sa_key["private_key"].encode(), password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=")
        jwt_token = (signing_input + b"." + sig_b64).decode()

        token_result = subprocess.run(
            ["curl", "-s", "-X", "POST", "https://oauth2.googleapis.com/token",
             "-d", f"grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion={jwt_token}"],
            capture_output=True, text=True, timeout=15,
        )
        token_resp = json.loads(token_result.stdout)
        token = token_resp.get("access_token")
        if token:
            with _token_lock:
                _token_cache["token"] = token
                _token_cache["timestamp"] = time.time()
            return token
    except Exception as e:
        print(f"[ERROR] Service account auth failed: {e}")

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI CALL
# ══════════════════════════════════════════════════════════════════════════════

def gemini_extract(prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.1, attempt=0):
    """Call Gemini with text-only prompt (no web search), get JSON back."""
    if USE_VERTEX:
        token = _get_vertex_token()
        if not token:
            return {"text": "", "error": "Failed to get token"}
        endpoint = (
            f"https://aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}"
            f"/locations/{VERTEX_LOCATION}/publishers/google/models/{GEMINI_MODEL}:generateContent"
        )
        auth_headers = ["-H", f"Authorization: Bearer {token}"]
    else:
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
            f"?key={GEMINI_API_KEY}"
        )
        auth_headers = []

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        }
    }

    # Write payload to temp file (Windows command line length workaround)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            tmp.write(json.dumps(payload))
            tmp_path = tmp.name
        result = subprocess.run(
            ["curl", "-s", "-w", "\n%{http_code}", "-X", "POST",
             "-H", "Content-Type: application/json",
             *auth_headers, endpoint,
             "-d", f"@{tmp_path}"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        if attempt < 2:
            time.sleep(5 * (2 ** attempt))
            return gemini_extract(prompt, max_tokens, temperature, attempt + 1)
        return {"text": "", "error": "Timeout"}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    output = result.stdout.strip()
    lines = output.rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else output
    http_code = int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else 0

    if http_code == 429:
        if attempt < 2:
            delay = 30 if attempt == 0 else 60
            print(f"    [RATE-LIMITED] Backing off {delay}s...")
            time.sleep(delay)
            return gemini_extract(prompt, max_tokens, temperature, attempt + 1)
        return {"text": "", "error": "Rate limited"}

    if http_code >= 500:
        if attempt < 2:
            time.sleep(5 * (2 ** attempt))
            return gemini_extract(prompt, max_tokens, temperature, attempt + 1)
        return {"text": "", "error": f"Server error {http_code}"}

    try:
        response = json.loads(body)
    except json.JSONDecodeError:
        return {"text": "", "error": f"Bad JSON response: {body[:200]}"}

    if "error" in response:
        err_msg = response["error"].get("message", str(response["error"]))
        return {"text": "", "error": err_msg}

    text = ""
    candidates = response.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            if "text" in part:
                text += part["text"]

    return {"text": text}


# ══════════════════════════════════════════════════════════════════════════════
#  SUBPAGE CRAWLER — discovers relevant subpages from official site homepages
# ══════════════════════════════════════════════════════════════════════════════

# Keywords that indicate a subpage has useful olympiad data
_SUBPAGE_HIGH_SCORE = [
    "registration", "register", "apply", "application",
    "eligibility", "rules", "requirements", "criteria",
    "syllabus", "curriculum", "topics", "content",
    "schedule", "dates", "timeline", "calendar", "deadline",
    "2026", "2027",
    "about", "overview", "information",
]
_SUBPAGE_MEDIUM_SCORE = [
    "results", "problems", "past-papers", "archive",
    "faq", "history", "structure", "format", "exam",
    "awards", "medals", "prizes",
]
_SUBPAGE_SKIP = [
    "login", "signup", "sign-up", "account", "password",
    "gallery", "photo", "video", "image", "media",
    "sponsor", "partner", "donate", "shop", "store",
    "contact", "privacy", "terms", "cookie", "sitemap",
    "feed", "rss", "xml", ".pdf", ".zip", ".doc",
    "javascript:", "mailto:", "#",
]

MAX_SUBPAGES = 4  # max subpages to crawl per official URL


def _fetch_raw_html(url):
    """Fetch URL and return raw HTML string (not stripped). Empty string on failure."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Charset": "utf-8",
        })
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
            encoding = resp.headers.get_content_charset()
            if not encoding:
                head = raw[:2048].decode("ascii", errors="replace").lower()
                m = re.search(r'charset=["\']?([\w-]+)', head)
                if m:
                    encoding = m.group(1)
            if not encoding:
                encoding = "utf-8"
            try:
                return raw.decode(encoding, errors="replace")
            except (UnicodeDecodeError, LookupError):
                return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_internal_links(html, base_url):
    """Extract all <a href> links that are on the same domain as base_url."""
    from urllib.parse import urlparse, urljoin

    base_parsed = urlparse(base_url)
    base_domain = base_parsed.netloc.lower().replace("www.", "")

    # Find all href values
    hrefs = re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.IGNORECASE)

    links = set()
    for href in hrefs:
        # Skip anchors, javascript, mailto
        if any(href.lower().startswith(skip) for skip in ["#", "javascript:", "mailto:"]):
            continue

        # Resolve relative URLs
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        link_domain = parsed.netloc.lower().replace("www.", "")

        # Only keep same-domain links
        if link_domain == base_domain:
            # Normalize — remove fragment, keep path+query
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean += f"?{parsed.query}"
            links.add(clean)

    return list(links)


def _score_subpage(url):
    """Score a subpage URL by keyword relevance. Higher = more useful."""
    url_lower = url.lower()
    score = 0

    # Skip junk pages
    for skip in _SUBPAGE_SKIP:
        if skip in url_lower:
            return -1

    # High-value keywords
    for kw in _SUBPAGE_HIGH_SCORE:
        if kw in url_lower:
            score += 3

    # Medium-value keywords
    for kw in _SUBPAGE_MEDIUM_SCORE:
        if kw in url_lower:
            score += 1

    return score


def crawl_subpages(official_url):
    """
    Fetch an official homepage, discover internal links, and return
    the top N most relevant subpage URLs.
    Returns: list of subpage URLs (not including the homepage itself)
    """
    html = _fetch_raw_html(official_url)
    if not html:
        return []

    # Extract all internal links
    all_links = _extract_internal_links(html, official_url)

    # Remove the homepage itself and very short paths (/, /en, /en/)
    from urllib.parse import urlparse
    base_path = urlparse(official_url).path.rstrip("/")
    filtered = []
    for link in all_links:
        link_path = urlparse(link).path.rstrip("/")
        # Skip if it's the same as homepage or too short
        if link_path == base_path or link_path in ("", "/", "/en", "/en/"):
            continue
        filtered.append(link)

    if not filtered:
        return []

    # Score and sort by relevance
    scored = [(url, _score_subpage(url)) for url in filtered]
    scored = [(url, s) for url, s in scored if s > 0]  # drop negative (skip) and zero (irrelevant)
    scored.sort(key=lambda x: -x[1])  # highest score first

    # Return top N
    top = [url for url, score in scored[:MAX_SUBPAGES]]
    return top


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP FETCHER + HTML CLEANER
# ══════════════════════════════════════════════════════════════════════════════

def fetch_url(url):
    """Fetch URL via HTTP. Returns plain text or empty string on failure."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Charset": "utf-8",
        })
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()

            # Try to detect encoding from HTTP header first, then meta tag, then default UTF-8
            encoding = resp.headers.get_content_charset()
            if not encoding:
                # Look for <meta charset="..."> in first 2KB
                head = raw[:2048].decode("ascii", errors="replace").lower()
                m = re.search(r'charset=["\']?([\w-]+)', head)
                if m:
                    encoding = m.group(1)
            if not encoding:
                encoding = "utf-8"

            try:
                html = raw.decode(encoding, errors="replace")
            except (UnicodeDecodeError, LookupError):
                html = raw.decode("utf-8", errors="replace")
            return html_to_text(html)
    except Exception as e:
        print(f"    [HTTP ERROR] {url}: {e}")
        return ""


def _fix_mojibake(text):
    """Fix common UTF-8 mis-decoded as Latin-1 patterns (e.g. â€™ → ')."""
    # Common mojibake patterns when UTF-8 is decoded as Latin-1
    replacements = {
        "â€™": "'",     # right single quotation mark
        "â€˜": "'",     # left single quotation mark
        "â€œ": '"',     # left double quotation mark
        "â€\x9d": '"',  # right double quotation mark
        "â€": '"',      # generic double quote
        "â€“": "-",     # en dash
        "â€”": "—",     # em dash
        "â€¦": "...",   # ellipsis
        "â†’": "→",     # right arrow
        "â†'": "→",     # right arrow variant
        "â†": "←",     # left arrow
        "â†'": "→",
        "â‰¥": "≥",     # >=
        "â‰¤": "≤",     # <=
        "Â ": " ",      # non-breaking space artifact
        "Â": "",        # stray Â
        "Ã©": "é",      # é
        "Ã¨": "è",      # è
        "Ã¢": "â",      # â
        "Ã¯": "ï",      # ï
        "Ã´": "ô",      # ô
        "Ã»": "û",      # û
        "Ã±": "ñ",      # ñ
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def _extract_main_content(html):
    """
    IMPROVEMENT 1: Extract main content area, stripping nav/header/footer/sidebar noise.
    Tries <main>, <article>, then falls back to largest content <div>.
    """
    # Try to find <main> or <article> tag first
    for tag in ["main", "article"]:
        m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', html, re.DOTALL | re.IGNORECASE)
        if m and len(m.group(1)) > 500:
            return m.group(1)

    # Remove obvious noise sections before falling back to full page
    for noise_tag in ["nav", "header", "footer", "aside"]:
        html = re.sub(rf'<{noise_tag}[^>]*>.*?</{noise_tag}>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Remove common noise patterns by class/id
    for noise_pattern in [
        r'<div[^>]*class="[^"]*(?:cookie|consent|popup|modal|sidebar|menu|nav|footer|header|ad-|advertisement|social-share|breadcrumb)[^"]*"[^>]*>.*?</div>',
        r'<div[^>]*id="[^"]*(?:cookie|consent|popup|modal|sidebar|menu|nav|footer|header|ad-|advertisement)[^"]*"[^>]*>.*?</div>',
    ]:
        html = re.sub(noise_pattern, '', html, flags=re.DOTALL | re.IGNORECASE)

    return html


def html_to_text(html):
    """Strip HTML tags and clean whitespace. Uses smart content extraction."""
    if not html:
        return ""
    import html as html_lib  # standard library

    # Remove script and style blocks first
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # IMPROVEMENT 1: Extract main content, strip nav/footer/sidebar
    html = _extract_main_content(html)

    # Replace common block tags with newlines
    html = re.sub(r'<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>', '\n', html, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r'<[^>]+>', ' ', html)
    # Decode all HTML entities (&amp; &nbsp; &#39; &rsquo; etc.)
    text = html_lib.unescape(text)
    # Fix mojibake from UTF-8 decoded as Latin-1
    text = _fix_mojibake(text)
    # Replace any remaining mojibake / non-printable artifacts
    text = text.replace("\u200b", "").replace("\ufeff", "")
    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()


# Minimum text length to consider a fetch "useful" — below this, the page is
# likely JS-rendered and needs Puppeteer.
# SOF pages return ~500-1500 chars of nav/header text via HTTP but the real
# content (dates, fees, eligibility) is JS-rendered and needs ~3000+ chars.
MIN_USEFUL_TEXT = 2000


def _fetch_via_puppeteer(url):
    """
    Fallback: fetch a JS-rendered page using Puppeteer MCP.
    Uses the puppeteer_navigate + puppeteer_evaluate tools from .mcp.json config.
    Returns plain text or empty string on failure.
    """
    try:
        # Use Puppeteer installed inside the MCP server package
        puppeteer_path = os.path.join(
            os.environ.get("APPDATA", ""),
            "npm", "node_modules", "@modelcontextprotocol", "server-puppeteer",
            "node_modules", "puppeteer"
        ).replace("\\", "/")

        node_script = f"""
        const puppeteer = require('{puppeteer_path}');
        (async () => {{
            const browser = await puppeteer.launch({{
                headless: true,
                executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
                args: ['--no-sandbox', '--disable-setuid-sandbox']
            }});
            const page = await browser.newPage();
            await page.setUserAgent('{USER_AGENT}');
            try {{
                await page.goto('{url}', {{waitUntil: 'networkidle2', timeout: 30000}});
                await new Promise(r => setTimeout(r, 3000));
                const text = await page.evaluate(() => document.body.innerText);
                console.log(text);
            }} catch(e) {{
                console.error(e.message);
            }}
            await browser.close();
        }})();
        """

        result = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True, timeout=60,
        )

        if result.returncode == 0 and result.stdout:
            text = result.stdout.decode("utf-8", errors="replace").strip()
            text = _fix_mojibake(text)
            # Remove non-printable chars that break Windows console
            text = re.sub(r'[^\x20-\x7E\n\r\t]', ' ', text)
            return text
        return ""
    except Exception as e:
        print(f"    [PUPPETEER ERROR] {url}: {e}")
        return ""


def fetch_all_sources(urls, max_per_source=MAX_TEXT_PER_SOURCE):
    """
    Fetch all URLs and return list of (url, text, status).
    For JS-rendered sites (thin HTTP response), falls back to Puppeteer.
    status = "ok" | "ok_puppeteer" | "empty" | "error"
    """
    sources = []
    for url in urls:
        text = fetch_url(url)

        # If HTTP fetch returned too little text, try Puppeteer
        if len(text) < MIN_USEFUL_TEXT:
            # Check if this looks like a JS-heavy site (SOF, Silverzone, etc.)
            js_heavy_domains = ["sofworld.org", "silverzone.org", "unifiedcouncil.com",
                                "crestolympiads.com", "myark.in"]
            is_js_heavy = any(d in url.lower() for d in js_heavy_domains)

            if is_js_heavy or len(text) == 0:
                print(f"    [PUPPETEER FALLBACK] {url[:60]}... (HTTP got {len(text)} chars)")
                puppet_text = _fetch_via_puppeteer(url)
                if len(puppet_text) > len(text):
                    text = puppet_text
                    if text:
                        if len(text) > max_per_source:
                            text = text[:max_per_source] + "\n\n[... truncated ...]"
                        sources.append((url, text, "ok_puppeteer"))
                        continue

        if text:
            if len(text) > max_per_source:
                text = text[:max_per_source] + "\n\n[... truncated ...]"
            sources.append((url, text, "ok"))
        else:
            sources.append((url, "", "error"))
    return sources


# ══════════════════════════════════════════════════════════════════════════════
#  EXTRACTION PROMPT
# ══════════════════════════════════════════════════════════════════════════════

EXTRACTION_PROMPT = """You are a precise data extraction system. Your job is to read the source texts below and extract structured JSON data about an Olympiad/academic competition for high school students.

## TODAY'S DATE: {today_date}
## OLYMPIAD: {olympiad_name} ({olympiad_id})

## CRITICAL RULES:
1. **ONLY extract information explicitly stated in the source texts.** Never guess or fabricate.
2. **If information is not present, use null.** Empty strings = null.
3. **For each field, track which source URL it came from** in the `_sources` mapping at the bottom. If a field is null, set its `_sources` value to null too — never put rule text or non-URLs there.
4. **SOURCE PRIORITY (IMPROVEMENT 4):** Each source is labeled with its priority tier:
   - **OFFICIAL — HIGHEST PRIORITY:** Trust this source above all others. If it states a value, use it even if aggregators say something different.
   - **REFERENCE — TRUSTED:** Use for background info, history, structure. Trust for facts like founding year, format, team size.
   - **AGGREGATOR — USE ONLY TO FILL GAPS:** Only use if OFFICIAL and REFERENCE sources don't have the field. NEVER override an OFFICIAL source with an aggregator value.
   When sources conflict on dates, costs, or eligibility: ALWAYS prefer the OFFICIAL source.
   When sources conflict on descriptions or structure: prefer the more detailed/specific version.
5. **Dates:** Use ISO format YYYY-MM-DD. If only month+day given, use the next upcoming cycle relative to today. IMPORTANT: All dates must be for the CURRENT or NEXT upcoming cycle (2026 or 2027). If you see a 2024 or 2025 date, it's from a past cycle — extrapolate to the next cycle (same month/day, next year).
6. **Multi-stage olympiads (RMO -> INMO -> IMO):** Set `parent_olympiad` to the SHORT CODE of the next stage only, not the full name. E.g. "IMO" not "International Mathematical Olympiad (IMO)".
7. **Subject must be one of:** Mathematics, Physics, Chemistry, Biology, Economics, Computer Science, History, Astronomy, Linguistics, Philosophy, Geography, General Knowledge, Other
8. **entry_route must be:** "Via School" (school registers, e.g. SOF NSO) OR "Independent" (student applies directly, e.g. IMO/INMO)
9. **level must be:** "School" OR "National" OR "International"
10. **Cost:** Report the STUDENT-FACING cost only — what an individual student pays to participate.
   - If there is NO individual registration fee for students → use "Free" (even if the country/school pays a delegation fee).
   - INTERNATIONAL olympiads (IMO, IPhO, IChO, IBO, IOI, IOAA) are FREE for students — the delegation fee is paid by the national society, NOT by students. Set cost="Free" and cost_chip="Free" for these.
   - INDIA NATIONAL olympiads (HBCSE chain: IOQM, RMO, INMO, NSE*, IN*) charge a small registration fee (typically INR 200-300). Extract the exact fee from the source. Do NOT default to "Free" for these — look for the fee amount.
   - SCHOOL-LEVEL olympiads (SOF, Silverzone, Unified Council, CREST) charge per student (e.g. INR 125, INR 170). Extract the exact fee from the source.
   - If cost is genuinely not mentioned in any source, set cost=null (not "Free").
11. **rounds field:** Count only the rounds of THIS specific olympiad, NOT the entire multi-stage chain. E.g. IMO itself has 2 rounds (2 exam days), not 6 (which is the IOQM->RMO->INMO->IMOTC->PDC->IMO chain). The chain goes in `structure_format` instead.
12. **structure_format:** Describe BOTH the exam format of this specific olympiad AND the broader selection chain if it's part of a multi-stage process.

## OUTPUT SCHEMA:

Return a single JSON object with these top-level keys:

```json
{{
  "olympiad_id": "{olympiad_id}",
  "activity_name": "string — full official name",
  "short_code": "string — abbreviation like IMO, INMO, NSO",
  "parent_olympiad": "string or null — short_code of next stage, e.g. RMO has parent INMO",
  "organizer": "string — organization that runs it (e.g. HBCSE, SOF, IMO Foundation)",
  "parent_organization": "string — top-level org (e.g. TIFR for HBCSE)",

  "subject": "Mathematics | Physics | Chemistry | Biology | Economics | Computer Science | History | Astronomy | Linguistics | Philosophy | Geography | General Knowledge | Other",
  "entry_route": "Via School | Independent",
  "level": "School | National | International",
  "modality": "Online | Offline | Hybrid | null",

  "eligibility_text": "string — full eligibility description",
  "age_limit": "string or null — e.g. 'Under 20 years on July 1, 2026'",
  "grade_levels": ["array of grade numbers, e.g. [9, 10, 11, 12]"],
  "countries_eligible": "string — e.g. 'India only', 'Worldwide', 'IMO member countries'",
  "team_size": "integer — 1 for individual, 2-6 for team competitions",

  "registration_open_date": "YYYY-MM-DD or null",
  "registration_close_date": "YYYY-MM-DD or null — THIS IS THE DEADLINE",
  "exam_date": "YYYY-MM-DD or null — when the actual exam happens",
  "result_date": "YYYY-MM-DD or null",

  "rounds": "integer or null — number of rounds (1, 2, 3, 4)",
  "structure_format": "string — describe the rounds, e.g. 'Round 1: RMO via school → Round 2: INMO national → Round 3: IMOTC selection camp'",
  "how_to_apply": "string — step-by-step or short instructions",

  "cost": "string — 'Free' or 'INR 125' or 'USD 50', etc.",
  "cost_chip": "Free | Paid",
  "outcome": "Medal | Certificate | Both | null",
  "certificate_type": "Participation | Merit | Achievement | null",
  "rewards_outcomes": "string — what winners get",

  "about_description": "string — 1-3 sentence overview suitable for a UI card",

  "registration_url": "string URL or null — direct link to apply/register",
  "official_website": "string URL or null — main olympiad homepage",
  "syllabus_url": "string URL or null",
  "past_papers_url": "string URL or null",

  "language": "string — e.g. 'English', 'English/Hindi'",
  "international_recognition": "string or null — e.g. 'IMO winners get college scholarships'",
  "num_participants_last_year": "integer or null — popularity indicator",

  "_sources": {{
    "field_name": "URL where this field came from",
    "...": "..."
  }},

  "_extraction_notes": {{
    "well_covered": ["list of field names that were filled with confidence"],
    "gaps": ["list of fields that were missing from sources"],
    "warnings": ["list of any conflicts or issues seen"]
  }}
}}
```

## SOURCE TEXTS (in priority order — Tier 1 first):

{sources_text}

---

Return ONLY the JSON object. No markdown fences, no explanation, no preamble."""


# ══════════════════════════════════════════════════════════════════════════════
#  MARKDOWN GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def to_markdown(data):
    """Convert structured JSON to a human-readable markdown with inline citations."""
    name = data.get("activity_name") or data.get("olympiad_id", "Unknown Olympiad")
    short = data.get("short_code") or ""
    title = f"{name}" + (f" ({short})" if short else "")
    sources = data.get("_sources") or {}

    def cite(*fields):
        """Build a citation block from one or more field source URLs (deduplicated)."""
        urls = []
        for field in fields:
            val = sources.get(field)
            if not val:
                continue
            # Source value may be a single URL or multiple comma/space separated
            # Split into individual URLs
            for u in re.split(r'[,;\s]+', str(val)):
                u = u.strip().rstrip('.,;')
                if u.startswith("http") and u not in urls:
                    urls.append(u)
        if not urls:
            return ""
        url_lines = "\n".join(f"- {u}" for u in urls)
        return f"\n\n<citation>\nstatus: extracted\nurls:\n{url_lines}\n</citation>\n"

    md = [f"# {title}\n"]

    # Overview
    if data.get("about_description"):
        md.append("## Overview")
        md.append(data["about_description"])
        md.append(cite("about_description"))

    # Quick facts table
    md.append("## Quick Facts")
    md.append("")
    facts = [
        ("Subject", data.get("subject")),
        ("Organizer", data.get("organizer")),
        ("Entry Route", data.get("entry_route")),
        ("Level", data.get("level")),
        ("Modality", data.get("modality")),
        ("Cost", data.get("cost")),
        ("Outcome", data.get("outcome")),
        ("Team Size", data.get("team_size")),
        ("Language", data.get("language")),
    ]
    for label, val in facts:
        if val:
            md.append(f"- **{label}:** {val}")
    md.append("")

    # Eligibility
    if data.get("eligibility_text"):
        md.append("## Eligibility")
        md.append(data["eligibility_text"])
        if data.get("age_limit"):
            md.append(f"\n- **Age limit:** {data['age_limit']}")
        if data.get("grade_levels"):
            md.append(f"- **Grade levels:** {', '.join(str(g) for g in data['grade_levels'])}")
        if data.get("countries_eligible"):
            md.append(f"- **Countries:** {data['countries_eligible']}")
        md.append(cite("eligibility_text"))

    # Dates
    md.append("## Important Dates")
    md.append("")
    dates = [
        ("Registration opens", data.get("registration_open_date")),
        ("Registration deadline", data.get("registration_close_date")),
        ("Exam date", data.get("exam_date")),
        ("Result date", data.get("result_date")),
    ]
    has_date = False
    for label, val in dates:
        if val:
            md.append(f"- **{label}:** {val}")
            has_date = True
    if not has_date:
        md.append("Information not available.")
    md.append(cite("registration_open_date", "registration_close_date", "exam_date", "result_date"))

    # Structure
    if data.get("structure_format") or data.get("rounds"):
        md.append("## Structure & Format")
        if data.get("rounds"):
            md.append(f"**Rounds:** {data['rounds']}")
        if data.get("structure_format"):
            md.append(data["structure_format"])
        md.append(cite("structure_format", "rounds"))

    # How to apply
    if data.get("how_to_apply"):
        md.append("## How to Apply")
        md.append(data["how_to_apply"])
        md.append(cite("how_to_apply"))

    # Rewards
    if data.get("rewards_outcomes"):
        md.append("## Rewards & Outcomes")
        md.append(data["rewards_outcomes"])
        if data.get("international_recognition"):
            md.append(f"\n{data['international_recognition']}")
        md.append(cite("rewards_outcomes", "international_recognition"))

    # Links
    md.append("## Links")
    links = [
        ("Official website", data.get("official_website")),
        ("Registration", data.get("registration_url")),
        ("Syllabus", data.get("syllabus_url")),
        ("Past papers", data.get("past_papers_url")),
    ]
    for label, url in links:
        if url:
            md.append(f"- [{label}]({url})")
    md.append("")

    # Multi-stage chain
    if data.get("parent_olympiad"):
        md.append(f"## Parent Olympiad\nNext stage: **{data['parent_olympiad']}**\n")

    return "\n".join(md)


# ══════════════════════════════════════════════════════════════════════════════
#  PER-OLYMPIAD PROCESSOR
# ══════════════════════════════════════════════════════════════════════════════

def process_olympiad(olympiad_id, config):
    """
    Process a single olympiad: fetch sources, send to Gemini, save outputs.
    Returns: dict with status and stats.
    """
    print(f"\n[{olympiad_id}] {config.get('name', '')}")

    # IMPROVEMENT 5: Collect URLs in priority order
    tier1_original = list(config.get("tier1_urls", []))
    tier2 = list(config.get("tier2_urls", []))
    tier3 = list(config.get("tier3_urls", []))

    # OPTION B: Auto-crawl subpages from Tier 1 official sites
    # Skip subpage crawling for JS-heavy sites — Puppeteer handles them as-is
    JS_HEAVY_DOMAINS = ["sofworld.org", "silverzone.org", "unifiedcouncil.com",
                        "crestolympiads.com", "myark.in"]
    tier1 = list(tier1_original)
    subpage_count = 0
    for official_url in tier1_original:
        # Skip subpage crawling for JS-heavy sites (avoids rate limiting)
        if any(d in official_url.lower() for d in JS_HEAVY_DOMAINS):
            continue
        subpages = crawl_subpages(official_url)
        for sp in subpages[:3]:  # max 3 subpages per official site
            if sp not in tier1:
                tier1.append(sp)
                subpage_count += 1
    if subpage_count > 0:
        print(f"  Discovered {subpage_count} subpages from official sites")

    # Cap tier1 (with subpages) at 6 to leave room for tier2+tier3
    MAX_TIER1 = 6
    if len(tier1) > MAX_TIER1:
        tier1 = tier1[:MAX_TIER1]

    # Always include all tier 2 (Wikipedia — usually 1 URL)
    # Always include at least 1 tier 3 (aggregator with structured data)
    urls = tier1 + tier2 + tier3[:2]  # guarantee tier3 gets at least 2 slots

    if not urls:
        print(f"  [SKIP] No URLs configured")
        return {"olympiad_id": olympiad_id, "status": "no_urls"}

    t3_used = min(len(tier3), 2)
    print(f"  Fetching {len(urls)} sources ({len(tier1)} tier1, {len(tier2)} tier2, {t3_used} tier3)...")
    sources = fetch_all_sources(urls)
    ok_count = sum(1 for _, _, s in sources if s in ("ok", "ok_puppeteer"))
    print(f"  Fetched {ok_count}/{len(urls)} sources successfully")

    if ok_count == 0:
        print(f"  [ERROR] All sources failed to fetch")
        return {"olympiad_id": olympiad_id, "status": "fetch_failed"}

    # IMPROVEMENT 2: Build sources_text with priority labels
    # Tier 1 = OFFICIAL (trust most), Tier 2 = REFERENCE, Tier 3 = AGGREGATOR (gap-fill only)
    tier1_count = len(config.get("tier1_urls", []))
    tier2_count = len(config.get("tier2_urls", []))
    sources_blocks = []
    source_idx = 0
    for i, (url, text, status) in enumerate(sources, 1):
        if status not in ("ok", "ok_puppeteer"):
            source_idx += 1
            continue
        # Determine tier label
        if source_idx < tier1_count:
            label = "OFFICIAL — HIGHEST PRIORITY"
        elif source_idx < tier1_count + tier2_count:
            label = "REFERENCE — TRUSTED"
        else:
            label = "AGGREGATOR — USE ONLY TO FILL GAPS"
        sources_blocks.append(f"[SOURCE {i} — {label}: {url}]\n{text}")
        source_idx += 1
    sources_text = "\n\n----------\n\n".join(sources_blocks)

    # Build the prompt
    today = datetime.now().strftime("%B %d, %Y")
    prompt = (EXTRACTION_PROMPT
              .replace("{olympiad_name}", config.get("name", olympiad_id))
              .replace("{olympiad_id}", olympiad_id)
              .replace("{today_date}", today)
              .replace("{sources_text}", sources_text))

    # Call Gemini
    print(f"  Extracting via Gemini...")
    start = time.time()
    result = gemini_extract(prompt)
    elapsed = time.time() - start

    if result.get("error"):
        print(f"  [ERROR] Gemini: {result['error']}")
        return {"olympiad_id": olympiad_id, "status": "gemini_error", "error": result["error"]}

    raw_text = result["text"].strip()

    # Parse JSON
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown fences
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw_text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                print(f"  [ERROR] Could not parse JSON")
                return {"olympiad_id": olympiad_id, "status": "parse_error"}
        else:
            print(f"  [ERROR] No JSON in response")
            return {"olympiad_id": olympiad_id, "status": "parse_error"}

    # ══════════════════════════════════════════════════════════════════════
    #  IMPROVEMENT 3: Post-extraction validation
    # ══════════════════════════════════════════════════════════════════════
    validation_warnings = []
    today_date = datetime.now().date()

    # Check dates are not in the past (except registration_open which can be past)
    for date_field in ["registration_close_date", "exam_date", "result_date"]:
        val = data.get(date_field)
        if val:
            try:
                d = datetime.strptime(val[:10], "%Y-%m-%d").date()
                if d < today_date:
                    validation_warnings.append(
                        f"{date_field}={val} is in the past. May be wrong year/cycle."
                    )
            except (ValueError, TypeError):
                validation_warnings.append(f"{date_field}={val} is not a valid date.")

    # Check age limit math
    age_val = data.get("age_limit") or ""
    birth_year_match = re.search(r'\b(19\d{2}|200\d|201\d)\b', age_val)
    if birth_year_match:
        birth_year = int(birth_year_match.group(1))
        implied_age = 2026 - birth_year
        if not (13 <= implied_age <= 25):
            validation_warnings.append(
                f"age_limit mentions year {birth_year} implying age {implied_age}. "
                f"Expected 13-25 for high school. May be calculation error."
            )

    # Check rounds is reasonable
    rounds_val = data.get("rounds")
    if rounds_val is not None:
        try:
            r = int(rounds_val)
            if r > 10:
                validation_warnings.append(f"rounds={r} seems too high. Expected 1-6.")
        except (ValueError, TypeError):
            pass

    # Check cost is reasonable
    cost_val = data.get("cost") or ""
    cost_match = re.search(r'(\d[\d,]*)', cost_val.replace(",", ""))
    if cost_match:
        try:
            cost_num = int(cost_match.group(1))
            if cost_num > 10000:
                validation_warnings.append(f"cost={cost_val} seems unusually high for an olympiad.")
        except ValueError:
            pass

    # Check required fields
    required_fields = ["activity_name", "subject", "entry_route", "level", "registration_close_date"]
    missing_required = [f for f in required_fields if not data.get(f)]

    # Add warnings to extraction notes
    if "_extraction_notes" not in data:
        data["_extraction_notes"] = {"well_covered": [], "gaps": [], "warnings": []}
    if validation_warnings:
        data["_extraction_notes"]["warnings"] = (
            data["_extraction_notes"].get("warnings", []) + validation_warnings
        )
        for w in validation_warnings:
            print(f"    [VALIDATION] {w}")

    # Save outputs
    raw_path = os.path.join(RAW_DIR, f"{olympiad_id}.json")
    md_path = os.path.join(MD_DIR, f"{olympiad_id}.md")
    qa_path = os.path.join(QA_DIR, f"{olympiad_id}_qa.json")
    sources_path = os.path.join(SOURCES_DIR, f"{olympiad_id}_sources.txt")

    # Separate QA metadata from clean UI data
    qa_metadata = {
        "_sources": data.pop("_sources", {}),
        "_extraction_notes": data.pop("_extraction_notes", {}),
    }

    # raw JSON = clean UI-ready data only (no _sources, no _extraction_notes)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    md_content = to_markdown(data)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # Save raw scraped sources (audit trail / debugging)
    with open(sources_path, "w", encoding="utf-8") as f:
        f.write(f"# RAW SCRAPED SOURCES — {olympiad_id}\n")
        f.write(f"# Olympiad: {config.get('name', olympiad_id)}\n")
        f.write(f"# Scraped at: {datetime.now().isoformat()}\n")
        f.write(f"# Total sources: {len(sources)} ({ok_count} successful)\n")
        f.write("=" * 80 + "\n\n")
        for i, (url, text, status) in enumerate(sources, 1):
            f.write(f"[SOURCE {i}] {url}\n")
            f.write(f"Status: {status}\n")
            f.write(f"Length: {len(text)} chars\n")
            f.write("-" * 80 + "\n")
            if text:
                f.write(text)
            else:
                f.write("(empty / failed to fetch)")
            f.write("\n\n" + "=" * 80 + "\n\n")

    # Build QA sidecar (all internal metadata goes HERE, not in raw JSON)
    qa = {
        "olympiad_id": olympiad_id,
        "name": config.get("name"),
        "fetch_summary": {
            "total_urls": len(urls),
            "successful": ok_count,
            "failed": len(urls) - ok_count,
            "url_status": [(u, s) for u, _, s in sources],
        },
        "field_sources": qa_metadata.get("_sources", {}),
        "extraction_notes": qa_metadata.get("_extraction_notes", {}),
        "validation_warnings": validation_warnings,
        "elapsed_seconds": round(elapsed, 1),
    }
    with open(qa_path, "w", encoding="utf-8") as f:
        json.dump(qa, f, indent=2, ensure_ascii=False)

    # Quality check
    well_covered = qa_metadata.get("_extraction_notes", {}).get("well_covered", [])

    print(f"  OK ({elapsed:.1f}s) — {len(well_covered)} fields filled, {len(missing_required)} required missing")

    return {
        "olympiad_id": olympiad_id,
        "status": "ok",
        "elapsed": elapsed,
        "fields_filled": len(well_covered),
        "missing_required": missing_required,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generic olympiad scraper using Gemini extraction")
    parser.add_argument("--config", type=str, default=OLYMPIAD_CONFIG, help="URL config file")
    parser.add_argument("--olympiad", type=str, help="Process single olympiad by ID (e.g. IMO)")
    parser.add_argument("--max", type=int, default=0, help="Max olympiads to process (0 = all)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip olympiads that already have output")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run, don't fetch/extract")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"[ERROR] Config file not found: {args.config}")
        print(f"Create it with format:")
        print('  {"IMO": {"name": "International Math Olympiad",')
        print('          "tier1_urls": ["https://imo-official.org/"],')
        print('          "tier2_urls": ["https://en.wikipedia.org/wiki/IMO"],')
        print('          "tier3_urls": []}, ...}')
        sys.exit(1)

    with open(args.config, "r", encoding="utf-8") as f:
        olympiads = json.load(f)

    print(f"Loaded {len(olympiads)} olympiads from {args.config}")

    # Filter
    if args.olympiad:
        if args.olympiad not in olympiads:
            print(f"[ERROR] {args.olympiad} not found in config")
            sys.exit(1)
        olympiads = {args.olympiad: olympiads[args.olympiad]}

    # Create output dirs
    for d in [OUTPUT_DIR, RAW_DIR, MD_DIR, QA_DIR, SOURCES_DIR]:
        os.makedirs(d, exist_ok=True)

    # Skip-existing filter
    items = list(olympiads.items())
    if args.skip_existing:
        before = len(items)
        items = [(k, v) for k, v in items if not os.path.exists(os.path.join(RAW_DIR, f"{k}.json"))]
        print(f"Skipping {before - len(items)} already-done olympiads")

    if args.max > 0:
        items = items[:args.max]

    if args.dry_run:
        print("\nWould process:")
        for k, v in items:
            url_count = sum(len(v.get(t, [])) for t in ["tier1_urls", "tier2_urls", "tier3_urls"])
            print(f"  {k} — {v.get('name', '')} ({url_count} URLs)")
        return

    print(f"\nProcessing {len(items)} olympiads...\n")
    start_time = time.time()
    results = []

    for olympiad_id, config in items:
        try:
            r = process_olympiad(olympiad_id, config)
        except Exception as e:
            print(f"  [EXCEPTION] {e}")
            r = {"olympiad_id": olympiad_id, "status": "exception", "error": str(e)}
        results.append(r)

    total_time = time.time() - start_time

    # Summary
    print(f"\n{'='*60}")
    print(f"OLYMPIAD SCRAPING SUMMARY")
    print(f"{'='*60}")
    ok = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] != "ok"]
    print(f"Total:    {len(results)}")
    print(f"Success:  {len(ok)}")
    print(f"Failed:   {len(failed)}")
    print(f"Time:     {total_time:.0f}s ({total_time/max(len(results),1):.1f}s avg)")

    if failed:
        print(f"\nFailed olympiads:")
        for r in failed:
            print(f"  {r['olympiad_id']}: {r['status']} {r.get('error','')}")

    # Save batch summary
    summary_path = os.path.join(OUTPUT_DIR, f"_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "total": len(results),
            "success": len(ok),
            "failed": len(failed),
            "total_time_seconds": round(total_time, 1),
            "results": results,
        }, f, indent=2)
    print(f"\nSummary: {summary_path}")
    print(f"Outputs: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
