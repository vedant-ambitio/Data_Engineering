#!/usr/bin/env python3
"""
Gemini-First Program Scraper
==============================
Uses Gemini Flash with Google Search grounding to generate comprehensive
program markdown files directly — replacing the browser-based Pass 1 + Pass 2.

Reads cleaned program URLs from INPUT_DIR, writes structured markdown to OUTPUT_DIR.

Usage:
    python run_gemini_scraper.py                          # Process all universities
    python run_gemini_scraper.py --max 5                  # Limit to 5 universities
    python run_gemini_scraper.py --university "MIT"        # Single university
    python run_gemini_scraper.py --dry-run                 # Show what would be processed
    python run_gemini_scraper.py --workers 10              # Set concurrency
    python run_gemini_scraper.py --skip-existing           # Skip already-scraped programs
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────

INPUT_DIR = "university_data/program_urls_cleaned"
OUTPUT_DIR = "university_data/program_info_gemini"
MAX_WORKERS = 10
MAX_OUTPUT_TOKENS = 40000
GEMINI_MODEL = "gemini-2.5-flash"

# Vertex AI config (higher rate limits than public API)
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"

# Fallback: public API key (used if --public-api flag is set)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyAuBwU029bR6gsp1mJhnOGgiMS_Z4vvP1o")
USE_VERTEX = True  # default to Vertex AI; set False via --public-api

# Retry config
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5        # seconds — base for exponential backoff
RATE_LIMIT_DELAY = 30        # seconds — pause on 429
RATE_LIMIT_LONG_DELAY = 60   # seconds — pause on repeated 429s

# Rate limiter: token bucket to stay under API quotas
_rate_lock = threading.Lock()
_last_request_time = 0
MIN_REQUEST_GAP = 0.05  # minimum seconds between any two requests (Vertex has high limits)

# Vertex AI service account key
VERTEX_SA_KEY_PATH = os.path.join(os.path.dirname(__file__), "..", "voice_agent", "vertex-ai-key.json")

# Token cache (thread-safe)
_token_cache = {"token": None, "timestamp": 0}
_token_lock = threading.Lock()
TOKEN_REFRESH_INTERVAL = 3000  # refresh token every ~50 min (expires at 60)


def _get_vertex_token():
    """Get an OAuth2 access token from the service account key file."""
    import hashlib
    import base64

    with _token_lock:
        now = time.time()
        if _token_cache["token"] and (now - _token_cache["timestamp"]) < TOKEN_REFRESH_INTERVAL:
            return _token_cache["token"]

    # Try gcloud first (if available)
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, timeout=15,
        )
        token = result.stdout.strip()
        if token and len(token) > 20:
            with _token_lock:
                _token_cache["token"] = token
                _token_cache["timestamp"] = time.time()
            return token
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fall back to service account key JWT exchange
    key_path = os.path.abspath(VERTEX_SA_KEY_PATH)
    if not os.path.exists(key_path):
        print(f"[ERROR] Service account key not found: {key_path}")
        print("[ERROR] Install gcloud or place vertex-ai-key.json. Or use --public-api")
        return None

    try:
        with open(key_path) as f:
            sa = json.load(f)

        # Build JWT
        import hmac
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=")
        iat = int(time.time())
        claims = {
            "iss": sa["client_email"],
            "scope": "https://www.googleapis.com/auth/cloud-platform",
            "aud": sa["token_uri"],
            "iat": iat,
            "exp": iat + 3600,
        }
        payload_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
        signing_input = header + b"." + payload_b64

        # Sign with RSA private key using openssl (no extra Python deps)
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", "/dev/stdin"],
            input=sa["private_key"].encode(),
            capture_output=True,
            timeout=10,
        )
        # Actually we need to pipe signing_input to sign — use a temp approach
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as kf:
            kf.write(sa["private_key"])
            key_tmp = kf.name
        try:
            proc = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", key_tmp],
                input=signing_input,
                capture_output=True,
                timeout=10,
            )
        finally:
            os.unlink(key_tmp)

        if proc.returncode != 0:
            print(f"[ERROR] openssl signing failed: {proc.stderr.decode()}")
            return None

        signature = base64.urlsafe_b64encode(proc.stdout).rstrip(b"=")
        jwt_token = (signing_input + b"." + signature).decode()

        # Exchange JWT for access token
        token_result = subprocess.run(
            [
                "curl", "-s", "-X", "POST", sa["token_uri"],
                "-H", "Content-Type: application/x-www-form-urlencoded",
                "-d", f"grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion={jwt_token}",
            ],
            capture_output=True, text=True, timeout=30,
        )
        token_resp = json.loads(token_result.stdout)
        token = token_resp.get("access_token")
        if not token:
            print(f"[ERROR] Token exchange failed: {token_result.stdout[:300]}")
            return None

        with _token_lock:
            _token_cache["token"] = token
            _token_cache["timestamp"] = time.time()
        return token

    except Exception as e:
        print(f"[ERROR] Service account auth failed: {e}")
        return None


# ── Gemini Web Search (with retry + rate limit handling) ─────────────────

def gemini_web_search(prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.3, attempt=0):
    """Query Gemini with Google Search grounding via Vertex AI (or public API fallback)."""
    global _last_request_time

    # Rate limiter: ensure minimum gap between requests
    with _rate_lock:
        now = time.time()
        wait = MIN_REQUEST_GAP - (now - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.time()

    # Build endpoint and auth headers based on backend
    if USE_VERTEX:
        token = _get_vertex_token()
        if not token:
            return {"text": "", "sources": [], "error": "Failed to get gcloud token"}
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
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {
                "thinkingBudget": 2048,
            },
        }
    }

    try:
        result = subprocess.run(
            [
                "curl", "-s", "-w", "\n%{http_code}", "-X", "POST",
                "-H", "Content-Type: application/json",
                *auth_headers,
                endpoint,
                "-d", json.dumps(payload),
            ],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            print(f"    [RETRY {attempt+1}/{MAX_RETRIES}] Timeout, retrying in {delay}s...")
            time.sleep(delay)
            return gemini_web_search(prompt, max_tokens, temperature, attempt + 1)
        return {"text": "", "sources": [], "error": "Timeout after retries"}

    # Split body and HTTP status code
    output = result.stdout.strip()
    lines = output.rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else output
    http_code = int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else 0

    # Handle rate limits (429)
    if http_code == 429:
        if attempt < MAX_RETRIES:
            delay = RATE_LIMIT_DELAY if attempt == 0 else RATE_LIMIT_LONG_DELAY
            print(f"    [RATE-LIMITED] 429 received, backing off {delay}s (attempt {attempt+1}/{MAX_RETRIES})...")
            time.sleep(delay)
            return gemini_web_search(prompt, max_tokens, temperature, attempt + 1)
        return {"text": "", "sources": [], "error": "Rate limited (429) after retries"}

    # Handle server errors (500, 502, 503)
    if http_code >= 500:
        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            print(f"    [RETRY {attempt+1}/{MAX_RETRIES}] Server error {http_code}, retrying in {delay}s...")
            time.sleep(delay)
            return gemini_web_search(prompt, max_tokens, temperature, attempt + 1)
        return {"text": "", "sources": [], "error": f"Server error {http_code} after retries"}

    # Parse JSON
    try:
        response = json.loads(body)
    except json.JSONDecodeError:
        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            print(f"    [RETRY {attempt+1}/{MAX_RETRIES}] Bad JSON, retrying in {delay}s...")
            time.sleep(delay)
            return gemini_web_search(prompt, max_tokens, temperature, attempt + 1)
        return {"text": "", "sources": [], "error": f"Bad response: {body[:300]}"}

    # Handle API-level errors
    if "error" in response:
        err_msg = response["error"].get("message", str(response["error"]))
        err_code = response["error"].get("code", 0)

        # Rate limit at API level
        if err_code == 429 or "RATE_LIMIT" in err_msg.upper() or "RESOURCE_EXHAUSTED" in err_msg.upper():
            if attempt < MAX_RETRIES:
                delay = RATE_LIMIT_DELAY if attempt == 0 else RATE_LIMIT_LONG_DELAY
                print(f"    [RATE-LIMITED] API 429, backing off {delay}s (attempt {attempt+1}/{MAX_RETRIES})...")
                time.sleep(delay)
                return gemini_web_search(prompt, max_tokens, temperature, attempt + 1)

        # Retriable server errors
        if err_code >= 500:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"    [RETRY {attempt+1}/{MAX_RETRIES}] API error {err_code}, retrying in {delay}s...")
                time.sleep(delay)
                return gemini_web_search(prompt, max_tokens, temperature, attempt + 1)

        return {"text": "", "sources": [], "error": err_msg}

    # Extract text
    text = ""
    candidates = response.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            if "text" in part:
                text += part["text"]

    # Check for empty response (thinking tokens exhausted output budget)
    if not text.strip() and attempt < MAX_RETRIES:
        delay = RETRY_BASE_DELAY * (2 ** attempt)
        print(f"    [RETRY {attempt+1}/{MAX_RETRIES}] Empty response, retrying in {delay}s...")
        time.sleep(delay)
        return gemini_web_search(prompt, max_tokens, temperature, attempt + 1)

    # Extract grounding metadata
    grounding = candidates[0].get("groundingMetadata", {}) if candidates else {}

    # Extract unique source URLs from grounding chunks
    sources = []
    chunk_urls = {}  # index -> url
    for i, chunk in enumerate(grounding.get("groundingChunks", [])):
        web = chunk.get("web", {})
        if web:
            url = web.get("uri", web.get("url", ""))
            title = web.get("title", "")
            sources.append(url)
            chunk_urls[i] = {"url": url, "title": title}

    # Extract grounding supports (segment -> source mapping)
    supports = []
    for support in grounding.get("groundingSupports", []):
        segment = support.get("segment", {})
        chunk_indices = support.get("groundingChunkIndices", [])
        seg_urls = []
        for idx in chunk_indices:
            if idx in chunk_urls:
                seg_urls.append(chunk_urls[idx]["url"])
        if seg_urls:
            supports.append({
                "text": segment.get("text", ""),
                "start": segment.get("startIndex", 0),
                "end": segment.get("endIndex", 0),
                "urls": seg_urls,
            })

    return {"text": text, "sources": sources, "supports": supports}


# ── Resolve Vertex Redirect URLs ─────────────────────────────────────────

_url_cache = {}
_url_cache_lock = threading.Lock()


def resolve_redirect(url):
    """Resolve vertexaisearch redirect URLs to actual URLs via HEAD request."""
    if "vertexaisearch.cloud.google.com/grounding-api-redirect" not in url:
        return url

    with _url_cache_lock:
        if url in _url_cache:
            return _url_cache[url]

    try:
        result = subprocess.run(
            ["curl", "-sI", "-o", "/dev/null", "-w", "%{redirect_url}", "--max-time", "5", url],
            capture_output=True, text=True, timeout=10,
        )
        resolved = result.stdout.strip()
        if resolved and resolved.startswith("http"):
            with _url_cache_lock:
                _url_cache[url] = resolved
            return resolved
    except Exception:
        pass

    return url


def resolve_all_urls(sources, supports):
    """Resolve all vertex redirect URLs in sources and supports."""
    # Collect all unique URLs to resolve
    all_urls = set(sources)
    for sup in supports:
        all_urls.update(sup.get("urls", []))

    # Resolve in parallel (fast HEAD requests)
    resolved_map = {}
    redirect_urls = [u for u in all_urls if "grounding-api-redirect" in u]

    if redirect_urls:
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(resolve_redirect, u): u for u in redirect_urls}
            for future in as_completed(futures):
                original = futures[future]
                try:
                    resolved_map[original] = future.result()
                except Exception:
                    resolved_map[original] = original

    # Apply resolved URLs
    new_sources = [resolved_map.get(s, s) for s in sources]
    new_supports = []
    for sup in supports:
        new_urls = [resolved_map.get(u, u) for u in sup.get("urls", [])]
        new_supports.append({**sup, "urls": new_urls})

    return new_sources, new_supports


# ── Prompt Builder ───────────────────────────────────────────────────────

def build_prompt_masters(university, country, program):
    """Build a comprehensive prompt that generates a full program markdown."""
    program_name = program["program_name"]
    degree = program.get("degree", "")
    url = program.get("url", "")
    tags = program.get("field_tags", [])
    delivery = program.get("delivery", "")
    extra_urls = program.get("extra_urls", [])

    today = datetime.now().strftime("%B %d, %Y")
    year = datetime.now().year
    month = datetime.now().month
    # Determine relevant upcoming intakes based on current date
    if month <= 3:
        deadline_hint = f"Fall {year}, Spring {year + 1}, and Fall {year + 1}"
    elif month <= 8:
        deadline_hint = f"Spring {year + 1} and Fall {year + 1}"
    else:
        deadline_hint = f"Spring {year + 1}, Fall {year + 1}, and Spring {year + 2}"

    return f"""You are a graduate program research expert. Produce a comprehensive markdown document about this program.

CRITICAL: You MUST use the google_search tool to look up EVERY piece of information. Do NOT rely on your training data or memory. Do NOT guess or assume any data points. For EACH section below, perform at least one dedicated search query. If you cannot find data via search, write "Information not available" — never fill in data from memory.

TODAY'S DATE: {today}

PROGRAM: {program_name}
DEGREE TYPE: {degree}
UNIVERSITY: {university}
COUNTRY: {country}
OFFICIAL URL: {url}
{f"ADDITIONAL KNOWN URLS (search these pages for data):" + chr(10) + chr(10).join(f"- {u}" for u in extra_urls) if extra_urls else ""}
FIELD TAGS: {', '.join(tags)}
DELIVERY: {delivery}

RESEARCH INSTRUCTIONS:
You MUST call the search tool MANY times — at least 8-10 separate searches. Do NOT stop after a few searches.{f" START by searching for the OFFICIAL URL and ADDITIONAL KNOWN URLS above — these are verified official pages." if extra_urls else ""} Perform SEPARATE dedicated searches for EACH of these:
1. "{university} {degree} {program_name}" — the program's main page
2. "{university} {degree} {program_name} admission requirements" — GPA, test scores, documents
3. "{university} {degree} {program_name} application deadlines {year}" — all rounds, priority, early, final
4. "{university} {degree} {program_name} tuition fees {year}" — per-credit, per-semester, total cost
5. "{university} graduate application requirements GRE GMAT waiver" — test waiver policies
6. "{university} {degree} {program_name} TOEFL IELTS English requirements" — all accepted tests and scores
7. "{university} {degree} {program_name} scholarships financial aid" — merit, need-based, assistantships
8. "{university} {degree} {program_name} curriculum courses" — course list, specializations
9. "{university} international students credential evaluation WES" — credential requirements
10. "{university} {degree} {program_name} apply now application portal" — direct application link
11. "{university} {degree} {program_name} faculty" — faculty page link
12. "{university} graduate school career outcomes employment" — placement data

SOURCE PRIORITY (CRITICAL):
You MUST prioritize sources in this order — use official sources whenever possible:
1. BEST: Official university websites (.edu, .ac.uk, .ac, university-owned domains) — e-catalogue, admissions pages, department pages
2. OK: Government/accreditation databases (nces.ed.gov, hesa.ac.uk)
3. AVOID: Third-party aggregators — NEVER use collegedunia, shiksha, yocket, topuniversities, leverage.edu, masterstudies.com, masterscompare, lilacbuds, bschools.org, rightpath, myunisearch, gyandhan, mentr-me, gmat.edu.sg, mim-essay
4. LAST RESORT: Reputable third-party sources (clearadmit, usnews, qs) — only if official source is truly unavailable

When you find data from a third-party site, ALWAYS do a follow-up search on the university's own website to verify. Prefer the official source.

STRICT RULES:
1. NEVER fabricate or guess data. If you cannot find something, write "Information not available".
2. Include specific numbers, dates, and requirements — not vague statements.
3. For tuition, specify the academic year, per credit/semester/year/total, and domestic vs international.
4. For deadlines: today is {today}. Search HARD for upcoming relevant intakes: {deadline_hint}. Include specific dates (month and day) for EVERY round — priority/early, regular, final, international. Note if rolling admissions.
5. DO NOT include URLs or citation blocks — source URLs are tracked automatically via search grounding. Focus all output tokens on DATA.
6. For test waivers: explicitly search whether GRE/GMAT waivers are available and under what conditions.
7. For credential evaluation: search what international credential evaluation services are accepted (WES, ECE, SpanTran, Scholaro, etc).

OUTPUT FORMAT:

The document MUST use EXACTLY these ## level headings in this order. These are FIXED and MANDATORY — never rename, reorder, skip, or merge them. You may freely add ### or #### subsections, bullet points, tables, bold fields, or any other markdown WITHIN each section. But the ## headings must appear exactly as shown.

# {{University Name}} - {{Full Program Name}}

## University Overview
University type (public/private), brief institutional context.

## Program Overview
What the program is, which school/department offers it, duration, credits, delivery mode, STEM designation, key highlights. Include all key facts as bold fields.

## Curriculum
Program structure, core courses (with course codes if available), elective areas, specializations, concentrations, thesis/capstone requirements. Organize with subsections as needed.

## Admission Requirements
This is the most important section — be thorough. Cover ALL of:
- Educational background and prerequisites
- GPA requirements (minimum and/or average of admitted students, state if recommendation vs hard cutoff)
- Standardized tests: GRE (required/optional/not required, scores), GMAT (same), EA (same)
- TEST WAIVERS: Search specifically — are GRE/GMAT waivers available? Under what conditions? (GPA threshold, work experience years, specific degrees, military service, etc.)
- English proficiency: TOEFL iBT (total + section mins), IELTS (overall + section mins), Duolingo DET, PTE Academic, Cambridge — list ALL accepted tests with scores
- English proficiency waiver conditions (degree from English-medium institution, citizenship, etc.)
- Credential evaluation for international students: required? Which services accepted? (WES ICAP, WES course-by-course, ECE, SpanTran, Scholaro, NACES members, etc.) Course-by-course or document-by-document?
- Required documents with specifics (transcript count, LOR count, SOP, CV, writing sample, portfolio, etc.)
- Work experience (required years, preferred, or not required)
- Application fee (exact amount, currency, fee waiver availability and conditions)

## Tuition & Fees
Tuition rates by academic year, broken down by international/domestic, per-credit and total program cost. Additional fees (student services, technology, health insurance, activity fees — each with amount). Estimated total cost of attendance if published.

## Application Deadlines
Search for EVERY round for: {deadline_hint}. Many programs have priority/early, round 1, round 2, round 3, regular, and final deadlines. Include:
- Each intake period separately (Fall, Spring, Summer)
- Every round with specific dates (Month Day, Year)
- International vs domestic deadlines if different
- Decision notification dates per round
- Deposit/enrollment confirmation deadlines
- Rolling admissions status
- Early decision/early action availability
- Only include future deadlines — skip anything before {today}

## Scholarships & Financial Aid
Merit-based scholarships (named, with amounts and eligibility), assistantships (TA/RA/GA with stipend and tuition details), fellowships, need-based aid, tuition waivers, employer sponsorship options, departmental awards.

## Career Outcomes
Employment rate (with timeframe), median/average salary, top employers, top industries, any placement statistics.

## Class Profile
Class size, international student %, average work experience, average GPA of admitted class, average GRE/GMAT scores, acceptance rate, gender ratio.

## Important Links
Provide these specific links (search for each):
- Program page URL
- Direct application portal / "Apply Now" link
- Faculty directory page for the department
- Department/school contact page

## Contact Information
Department/school name, admissions email, phone, physical address.

FINAL INSTRUCTIONS:
- Search BROADLY and DEEPLY — at minimum 8-10 separate search queries. Deadlines, test waivers, tuition, and credential evaluation each need their own dedicated search.
- Return ONLY the markdown content. No preamble, no explanation, no citation blocks.
- The ## headings above are FIXED. Never skip one. If no data, keep the heading and write "Information not available".
- Within each section, use whatever markdown structure (###, bullets, tables, bold) makes the data clearest."""


def build_prompt_phd(university, country, program):
    """Build a comprehensive prompt for PhD/doctoral program research."""
    program_name = program["program_name"]
    degree = program.get("degree", "")
    url = program.get("url", "")
    tags = program.get("field_tags", [])
    delivery = program.get("delivery", "")
    extra_urls = program.get("extra_urls", [])

    today = datetime.now().strftime("%B %d, %Y")
    year = datetime.now().year
    month = datetime.now().month
    # PhD deadlines are typically single annual deadline (Dec-Jan for Fall)
    if month <= 6:
        deadline_hint = f"Fall {year} and Fall {year + 1}"
    else:
        deadline_hint = f"Fall {year + 1}"

    return f"""You are a doctoral program research expert. Produce a comprehensive markdown document about this PhD/doctoral program.

CRITICAL: You MUST use the google_search tool to look up EVERY piece of information. Do NOT rely on your training data or memory. Do NOT guess or assume any data points. For EACH section below, perform at least one dedicated search query. If you cannot find data via search, write "Information not available" — never fill in data from memory.

TODAY'S DATE: {today}

PROGRAM: {program_name}
DEGREE TYPE: {degree}
UNIVERSITY: {university}
COUNTRY: {country}
OFFICIAL URL: {url}
{f"ADDITIONAL KNOWN URLS (search these pages for data):" + chr(10) + chr(10).join(f"- {u}" for u in extra_urls) if extra_urls else ""}
FIELD TAGS: {', '.join(tags)}
DELIVERY: {delivery}

RESEARCH INSTRUCTIONS:
You MUST call the search tool MANY times — at least 10-12 separate searches. Do NOT stop after a few searches.{f" START by searching for the OFFICIAL URL and ADDITIONAL KNOWN URLS above — these are verified official pages." if extra_urls else ""} Perform SEPARATE dedicated searches for EACH of these:
1. "{university} {degree} {program_name}" — the program's main page
2. "{university} {program_name} PhD faculty research areas labs" — faculty listing with research interests
3. "{university} {program_name} PhD admission requirements" — GPA, test scores, documents
4. "{university} {program_name} PhD application deadline {year}" — deadline dates
5. "{university} {program_name} PhD funding stipend tuition waiver" — funding packages
6. "{university} {program_name} PhD TOEFL IELTS GRE requirements" — test scores
7. "{university} {program_name} PhD qualifying exam candidacy requirements" — program milestones
8. "{university} {program_name} PhD research areas topics" — department research clusters
9. "{university} international students credential evaluation WES" — credential requirements
10. "{university} {program_name} PhD apply application portal" — direct application link
11. "{university} {program_name} PhD placement outcomes graduates" — where graduates end up
12. "{university} graduate school GRE waiver policy" — test waiver conditions

SOURCE PRIORITY (CRITICAL):
You MUST prioritize sources in this order — use official sources whenever possible:
1. BEST: Official university websites (.edu, .ac.uk, .ac, university-owned domains) — department pages, graduate school pages, faculty directories
2. OK: Government/accreditation databases (nces.ed.gov, hesa.ac.uk)
3. AVOID: Third-party aggregators — NEVER use collegedunia, shiksha, yocket, topuniversities, leverage.edu, masterstudies.com, masterscompare, lilacbuds, bschools.org, rightpath, myunisearch, gyandhan, mentr-me, gmat.edu.sg, mim-essay, phdportal.com
4. LAST RESORT: Reputable third-party sources (csrankings.org, usnews) — only if official source is truly unavailable

When you find data from a third-party site, ALWAYS do a follow-up search on the university's own website to verify. Prefer the official source.

STRICT RULES:
1. NEVER fabricate or guess data. If you cannot find something, write "Information not available".
2. Include specific numbers, dates, names, and requirements — not vague statements.
3. For faculty: include actual names, research areas, and lab names where available. This is the MOST IMPORTANT section for PhD applicants.
4. For funding: specify exact stipend amounts, whether tuition is waived, health insurance coverage, and duration of guaranteed funding.
5. For deadlines: today is {today}. Search for upcoming intakes: {deadline_hint}. PhD programs typically have ONE deadline per year — find the exact date.
6. DO NOT include URLs or citation blocks — source URLs are tracked automatically via search grounding. Focus all output tokens on DATA.
7. For test waivers: explicitly search whether GRE waivers are available and under what conditions.
8. For credential evaluation: search what services are accepted (WES, ECE, SpanTran, Scholaro, etc).

OUTPUT FORMAT:

The document MUST use EXACTLY these ## level headings in this order. These are FIXED and MANDATORY — never rename, reorder, skip, or merge them. You may freely add ### or #### subsections, bullet points, tables, bold fields, or any other markdown WITHIN each section. But the ## headings must appear exactly as shown.

# {{University Name}} - {{Full Program Name}}

## University Overview
University type (public/private), brief institutional context, research profile.

## Program Overview
What the program is, which school/department offers it, duration (typical and maximum), delivery mode, key highlights. Include:
- **Duration**: typical years to completion and maximum allowed
- **Credits Required**: if applicable (some PhD programs are purely research-based)
- **Delivery Mode**: Full-time / Part-time
- **STEM Designated**: Yes/No and CIP code if available (for US programs)

## Faculty & Research
THIS IS THE MOST IMPORTANT SECTION. Search deeply for this. Include:
- Named faculty members with their research areas/interests and lab names
- Organize by research theme/cluster if the department groups them that way
- Include lab URLs or group names where available
- Note which faculty are currently accepting PhD students if that information is available
- List at least 8-10 faculty members if the department has them
- Note any named/endowed chairs, distinguished professors, or notable researchers

## Program Structure
How the PhD program is organized from start to finish:
- Coursework requirements (number of courses/credits, any specific required courses)
- Qualifying/comprehensive exam (when, format, written/oral, retake policy)
- Candidacy advancement requirements and timeline
- Teaching requirements (mandatory TA semesters, hours per week)
- Dissertation/thesis requirements (committee size, proposal defense, final defense format)
- Typical milestones by year (Year 1: coursework, Year 2: quals, etc.)
- Advisor matching process (rotation system, pre-matched, choose after arrival, must contact faculty before applying?)

## Admission Requirements
Cover ALL of:
- Educational background: Bachelor's sufficient or Master's required/preferred? Field requirements.
- GPA requirements (minimum and/or average of admitted students)
- Standardized tests:
  - GRE General (required/optional/not required, scores if stated)
  - GRE Subject Test (required/recommended for specific fields like Math, Physics, CS?)
  - GRE/test waivers: available? conditions? (GPA threshold, experience, specific degrees)
- English proficiency: TOEFL iBT (total + section mins), IELTS (overall + section mins), Duolingo DET, PTE Academic, Cambridge — ALL accepted tests with scores
- English proficiency waiver conditions
- Credential evaluation for international students: required? Which services? (WES, ECE, SpanTran, Scholaro, NACES)
- Required documents with specifics:
  - Research statement/proposal (how long, what to include)
  - Letters of recommendation (exact count, academic vs professional preference)
  - CV/resume
  - Writing sample or publication list
  - Transcripts
  - Master's thesis (if applicable)
- Prior research experience (expected level)
- Work experience relevance
- Do applicants need to contact/identify a potential advisor before applying?
- Application fee (exact amount, fee waiver availability)

## Funding & Stipend
THIS IS CRITICAL FOR PhD — search thoroughly. Include:
- Is the program fully funded? For how many years?
- Annual stipend amount (exact figure, academic year)
- Tuition waiver/remission included?
- Health insurance coverage included?
- Summer funding availability
- Conference travel funding (annual budget per student)
- Funding sources (TA, RA, fellowship, combination)
- Named fellowships available to incoming students
- Any additional funding competitions students can apply for
- Funding for international vs domestic students (any differences?)

## Tuition & Fees
Brief since PhD is often funded — but include:
- Published tuition rates (in-state/out-of-state for US public, international/domestic)
- Whether tuition is typically waived for funded students
- Mandatory fees not covered by tuition waiver (student services, health, technology fees)
- Note the academic year for rates quoted

## Application Deadlines
PhD programs typically have ONE annual deadline. Search for: {deadline_hint}.
- Application deadline (exact date)
- Priority/early deadline if applicable
- International vs domestic deadline if different
- Decision notification timeline
- Offer acceptance/response deadline
- Rolling admissions status (rare for PhD but note if applicable)
- Only include future deadlines — skip anything before {today}

## Scholarships & Fellowships
Named fellowships and awards beyond the base funding package:
- University-wide doctoral fellowships (name, amount, eligibility)
- Department-specific awards
- External fellowships the program supports (NSF GRFP, NDSEG, Fulbright, Gates Cambridge, etc.)
- Diversity fellowships
- Any supplemental funding on top of base stipend

## Career Outcomes
Focus on PhD-specific outcomes:
- Academic placement rate (% who enter tenure-track positions, postdocs)
- Industry placement rate
- Notable alumni in academia (current professors at which institutions)
- Notable alumni in industry
- Median time to degree completion
- Completion/attrition rate if available

## Class Profile
- Cohort size (annual intake)
- International student percentage
- Acceptance rate
- Average GPA / GRE of admitted students
- Gender ratio

## Important Links
- **Program Page**: official program URL
- **Application Portal / Apply Now**: direct link to apply
- **Faculty Directory**: department faculty page URL — search specifically for this
- **Research Groups/Labs Page**: if the department has a dedicated research page
- **Department/School Page**: department URL

## Contact Information
Department/school name, graduate admissions email, graduate program coordinator name if available, phone, physical address.

FINAL INSTRUCTIONS:
- Search BROADLY and DEEPLY — at minimum 10-12 separate search queries.
- Faculty & Research and Funding & Stipend are the TWO MOST IMPORTANT sections — spend extra search effort on these.
- Return ONLY the markdown content. No preamble, no explanation, no citation blocks.
- The ## headings above are FIXED. Never skip one. If no data, keep the heading and write "Information not available".
- Within each section, use whatever markdown structure (###, bullets, tables, bold) makes the data clearest."""


# ── File Naming ──────────────────────────────────────────────────────────

def sanitize_filename(name):
    """Create a filesystem-safe filename."""
    name = re.sub(r'[/\\:*?"<>|]', '_', name)
    name = re.sub(r'[\s]+', '_', name)
    name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', name)
    name = re.sub(r'_+', '_', name)
    return name.strip('_')


def get_output_path(dataset, university, program):
    """Build output path: OUTPUT_DIR/dataset/University_Name/University_Program.md"""
    uni_dir = sanitize_filename(university)
    degree = program.get("degree", "")
    prog_name = program.get("program_name", "Unknown")

    if degree and degree != "?":
        filename = f"{sanitize_filename(university)}_{sanitize_filename(degree)}_{sanitize_filename(prog_name)}.md"
    else:
        filename = f"{sanitize_filename(university)}_{sanitize_filename(prog_name)}.md"

    return os.path.join(OUTPUT_DIR, dataset, uni_dir, filename)


# ── Process Single Program ───────────────────────────────────────────────

def process_program(university, country, program, dataset, index, total):
    """Scrape a single program using Gemini web search with auto-retry."""
    prog_name = program["program_name"]
    degree = program.get("degree", "")
    label = f"[{index}/{total}] {university} — {degree} {prog_name}"

    out_path = get_output_path(dataset, university, program)

    print(f"  START {label}")
    start = time.time()

    if dataset in ("phd", "phd_jobs"):
        prompt = build_prompt_phd(university, country, program)
    else:
        prompt = build_prompt_masters(university, country, program)
    result = gemini_web_search(prompt)

    elapsed = time.time() - start

    if result.get("error"):
        print(f"  ERROR {label} — {elapsed:.0f}s — {result['error']}")
        return {
            "university": university,
            "program": prog_name,
            "degree": degree,
            "status": "error",
            "error": str(result["error"]),
            "elapsed": round(elapsed, 1),
        }

    markdown = result["text"].strip()
    sources = result.get("sources", [])

    # Validate: must have at least a heading and some content
    if not markdown or len(markdown) < 200:
        print(f"  EMPTY {label} — {elapsed:.0f}s — response too short ({len(markdown)} chars)")
        return {
            "university": university,
            "program": prog_name,
            "degree": degree,
            "status": "empty",
            "response_length": len(markdown),
            "elapsed": round(elapsed, 1),
        }

    # Strip markdown code fences if Gemini wrapped the output
    markdown = re.sub(r'^```(?:markdown)?\s*\n', '', markdown)
    markdown = re.sub(r'\n```\s*$', '', markdown)

    # Build citation blocks from grounding supports
    supports = result.get("supports", [])

    # Resolve vertex redirect URLs to actual URLs
    sources, supports = resolve_all_urls(sources, supports)

    # Collect all unique official source URLs
    all_urls = set()
    for s in sources:
        if not _is_blocked_source(s):
            all_urls.add(s)
    for sup in supports:
        for u in sup.get("urls", []):
            if not _is_blocked_source(u):
                all_urls.add(u)

    # Map which sections each URL supports by matching text segments
    section_sources = {}  # section_header -> set of urls
    sections_split = re.split(r'^(## .+)$', markdown, flags=re.MULTILINE)
    section_ranges = []  # (header, start_char, end_char)
    pos = 0
    for i, part in enumerate(sections_split):
        end = pos + len(part)
        if part.startswith("## "):
            # This section's body is the next part
            body_end = end + len(sections_split[i + 1]) if i + 1 < len(sections_split) else end
            section_ranges.append((part.strip(), pos, body_end))
        pos = end

    for sup in supports:
        seg_start = sup.get("start", 0)
        seg_urls = [u for u in sup.get("urls", []) if not _is_blocked_source(u)]
        if not seg_urls:
            continue
        for header, s_start, s_end in section_ranges:
            if seg_start >= s_start and seg_start < s_end:
                if header not in section_sources:
                    section_sources[header] = set()
                section_sources[header].update(seg_urls)
                break

    # Inject citation blocks after each section
    enriched_parts = []
    i = 0
    while i < len(sections_split):
        part = sections_split[i]
        enriched_parts.append(part)

        if part.startswith("## ") and part.strip() in section_sources:
            header = part.strip()
            urls = section_sources[header]
            # Append body first, then citation
            if i + 1 < len(sections_split):
                i += 1
                body = sections_split[i]
                enriched_parts.append(body.rstrip())
                citation = "\n\n<citation>\nstatus: verified\nurls:\n"
                for u in sorted(urls):
                    citation += f"- {u}\n"
                citation += "</citation>\n"
                enriched_parts.append(citation)
            else:
                pass
        i += 1

    markdown = "".join(enriched_parts)

    # Append master source list at the end
    if all_urls:
        source_block = "\n---\n\n## Sources\n\n"
        for u in sorted(all_urls):
            source_block += f"- {u}\n"
        markdown += source_block

    # Count sections found
    section_headers = re.findall(r'^## (.+)$', markdown, re.MULTILINE)
    verified = len(re.findall(r'status:\s*verified', markdown))
    missing_count = markdown.lower().count("information not available")

    # Write output
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(markdown + "\n")

    print(
        f"  DONE {label} — {elapsed:.0f}s — "
        f"{len(section_headers)} sections, "
        f"{verified} cited / {missing_count} gaps, "
        f"{len(all_urls)} sources"
    )

    return {
        "university": university,
        "program": prog_name,
        "degree": degree,
        "status": "success",
        "output_path": out_path,
        "sections": len(section_headers),
        "verified": verified,
        "missing_fields": missing_count,
        "grounding_sources": len(all_urls),
        "elapsed": round(elapsed, 1),
    }


BLOCKED_DOMAINS = [
    'collegedunia', 'shiksha', 'yocket', 'studyabroad', 'hotcoursesabroad',
    'topuniversities.com', 'leverage.edu', 'masterstudies.com', 'findamasters',
    'studyin', 'educationnest', 'studylink', 'idp.com', 'keystone',
    'mastersportal', 'phdportal', 'bachelorsportal', 'scholarshipportal',
    'usnews.com', 'niche.com',
]


def _is_blocked_source(url):
    url_lower = url.lower()
    return any(b in url_lower for b in BLOCKED_DOMAINS)


# ── Load Programs from Cleaned URLs ─────────────────────────────────────

def load_programs(dataset, university_filter=None):
    """Load all programs from cleaned URL JSON files."""
    data_dir = os.path.join(INPUT_DIR, dataset)
    if not os.path.isdir(data_dir):
        print(f"[ERROR] Input directory not found: {data_dir}")
        sys.exit(1)

    all_programs = []

    for filename in sorted(os.listdir(data_dir)):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(data_dir, filename)
        try:
            with open(filepath) as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  [WARN] Skipping {filename}: {e}")
            continue

        university = data.get("university", filename.replace(".json", ""))
        country = data.get("country", "")

        if university_filter and university_filter.lower() not in university.lower():
            continue

        programs = data.get("programs", [])
        for prog in programs:
            if not prog.get("url"):
                continue
            all_programs.append({
                "university": university,
                "country": country,
                "program": prog,
                "dataset": dataset,
            })

    return all_programs


def load_programs_csv(csv_path, university_filter=None):
    """Load programs from a CSV file.

    Supports two schemas:
      Schema A (bulk CSVs): program_id, University Name, course_major_name,
          course_specialization_name, course_level, course_degree_name, qsRank,
          officialPageLink, officialLinks
      Schema B (query_result CSVs): ID, University Name, Course Major,
          Course Specialization, Course Level, Course Degree, Links, Program ID
    """
    all_programs = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            university = row.get("University Name", "").strip()
            if not university:
                continue
            if university_filter and university_filter.lower() not in university.lower():
                continue

            # Detect schema: Schema B has "Links" column, Schema A has "officialLinks"
            is_schema_b = "Links" in row or "Course Degree" in row

            url = row.get("officialPageLink", "").strip() if not is_schema_b else ""

            # Parse links — both schemas use space-separated quoted URLs in brackets
            extra_links = []
            raw_links = (row.get("Links", "") if is_schema_b else row.get("officialLinks", "")).strip()
            if raw_links and raw_links != "[]":
                raw_links = raw_links.strip("[]")
                extra_links = [u.strip().strip('"') for u in re.split(r'"\s+"', raw_links) if u.strip().strip('"')]
                extra_links = list(dict.fromkeys(u for u in extra_links if u and u != url))

            # Determine dataset from course_level
            level = (row.get("Course Level", "") if is_schema_b else row.get("course_level", "")).strip().lower()
            if level in ("phd", "doctorate"):
                dataset = "phd"
            else:
                dataset = "masters"

            if is_schema_b:
                major = row.get("Course Major", "").strip()
                specialization = row.get("Course Specialization", "").strip()
                degree_name = row.get("Course Degree", "").strip()
                program_id = row.get("Program ID", row.get("ID", "")).strip()
                qs_rank = ""
            else:
                major = row.get("course_major_name", "").strip()
                specialization = row.get("course_specialization_name", "").strip()
                degree_name = row.get("course_degree_name", "").strip()
                program_id = row.get("program_id", "").strip()
                qs_rank = row.get("qsRank", "").strip()

            # Build program name: prefer specialization, fall back to major
            program_name = specialization if specialization and specialization != major else major

            prog = {
                "program_name": program_name,
                "degree": degree_name,
                "url": url,
                "field_tags": [major] if major != program_name else [],
                "delivery": "",
                "extra_urls": extra_links,
                "program_id": program_id,
                "qs_rank": qs_rank,
            }

            all_programs.append({
                "university": university,
                "country": "",  # CSV doesn't have country — prompt will handle
                "program": prog,
                "dataset": dataset,
            })

    return all_programs


# ── Persistent Progress Tracking ─────────────────────────────────────────

PROGRESS_DIR = os.path.join(OUTPUT_DIR, "_progress")


def get_progress_path(run_id):
    """Path to the JSONL progress file for a run."""
    return os.path.join(PROGRESS_DIR, f"{run_id}.jsonl")


def load_completed_ids(run_id):
    """Load set of program_ids already completed in a previous run."""
    progress_path = get_progress_path(run_id)
    completed = set()
    if not os.path.exists(progress_path):
        return completed
    with open(progress_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("status") == "success":
                    completed.add(entry.get("program_id", ""))
            except json.JSONDecodeError:
                continue
    return completed


def append_progress(run_id, result):
    """Append a result to the JSONL progress file (crash-safe)."""
    os.makedirs(PROGRESS_DIR, exist_ok=True)
    progress_path = get_progress_path(run_id)
    with open(progress_path, "a") as f:
        f.write(json.dumps(result) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Gemini-First Program Scraper")
    parser.add_argument("--dataset", default="masters", help="Dataset (default: masters)")
    parser.add_argument("--csv", type=str, default=None,
                        help="Load from CSV file instead of JSON (masters_programs_filtered.csv or phd_programs_filtered.csv)")
    parser.add_argument("--max", type=int, default=None, help="Max programs to process")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Concurrent workers (default: 10)")
    parser.add_argument("--university", type=str, default=None, help="Filter by university name (substring match)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed")
    parser.add_argument("--skip-existing", action="store_true", help="Skip programs that already have output files")
    parser.add_argument("--resume", type=str, default=None,
                        help="Resume a previous run by run_id (skips already-completed program_ids)")
    parser.add_argument("--priority", type=str, default=None,
                        help="Filter by priority (p0, p1, p2). Comma-separated for multiple.")
    parser.add_argument("--model", type=str, default=None,
                        help="Override model (e.g. gemini-2.5-flash, gemini-2.5-pro)")
    parser.add_argument("--public-api", action="store_true",
                        help="Use public Gemini API (lower rate limits) instead of Vertex AI")
    args = parser.parse_args()

    # Allow runtime model override
    global GEMINI_MODEL, USE_VERTEX
    if args.model:
        GEMINI_MODEL = args.model
    if args.public_api:
        USE_VERTEX = False

    # Generate run_id for progress tracking
    run_id = args.resume or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    print("=== Gemini-First Program Scraper ===\n")
    backend = f"Vertex AI ({VERTEX_PROJECT})" if USE_VERTEX else "Public API (generativelanguage)"
    print(f"Config: model={GEMINI_MODEL}, backend={backend}, workers={args.workers}, retries={MAX_RETRIES}")
    print(f"Run ID: {run_id}")

    # ── Load programs from CSV or JSON ──────────────────────────────────
    if args.csv:
        all_programs = load_programs_csv(args.csv, args.university)
        print(f"Loaded from CSV: {args.csv}")
    else:
        all_programs = load_programs(args.dataset, args.university)
    print(f"Total programs found: {len(all_programs)}")

    # Filter by priority (JSON-only field)
    if args.priority:
        allowed = set(args.priority.split(","))
        all_programs = [p for p in all_programs if p["program"].get("priority", "") in allowed]
        print(f"After priority filter ({args.priority}): {len(all_programs)}")

    # Resume: skip already-completed program_ids
    if args.resume:
        completed_ids = load_completed_ids(run_id)
        if completed_ids:
            before = len(all_programs)
            all_programs = [p for p in all_programs if p["program"].get("program_id", "") not in completed_ids]
            print(f"Resume: skipping {before - len(all_programs)} already completed, {len(all_programs)} remaining")

    # Filter out existing output files
    if args.skip_existing:
        before = len(all_programs)
        all_programs = [
            p for p in all_programs
            if not os.path.exists(get_output_path(p["dataset"], p["university"], p["program"]))
        ]
        print(f"After skip-existing filter: {len(all_programs)} (skipped {before - len(all_programs)})")

    # Apply max limit
    if args.max:
        all_programs = all_programs[:args.max]

    print(f"Will process: {len(all_programs)} programs\n")

    if not all_programs:
        print("Nothing to process!")
        return

    # Dry run
    if args.dry_run:
        for p in all_programs:
            prog = p["program"]
            extra = prog.get("extra_urls", [])
            print(f"  {p['university']} — {prog.get('degree', '?')} {prog['program_name']}")
            print(f"    URL: {prog.get('url', 'N/A')} (+{len(extra)} extra)")
            print(f"    Output: {get_output_path(p['dataset'], p['university'], prog)}")
        print(f"\nTotal: {len(all_programs)} programs")
        return

    # Ensure output dir exists
    for ds in set(p["dataset"] for p in all_programs):
        os.makedirs(os.path.join(OUTPUT_DIR, ds), exist_ok=True)

    # ── Process with concurrent workers ──────────────────────────────────
    print(f"Starting {len(all_programs)} programs with {args.workers} workers...\n")
    results = []
    failed_items = []
    _progress_lock = threading.Lock()
    _success_count = [0]
    _error_count = [0]

    def handle_result(item, result):
        """Thread-safe result handling with progress tracking."""
        # Append progress to JSONL (crash-safe)
        result["program_id"] = item["program"].get("program_id", "")
        append_progress(run_id, result)

        with _progress_lock:
            results.append(result)
            if result.get("status") == "success":
                _success_count[0] += 1
            else:
                _error_count[0] += 1
                failed_items.append((item, result))

            # Print running totals every 50 programs
            done = _success_count[0] + _error_count[0]
            if done % 50 == 0 or done == len(all_programs):
                print(f"\n  ── Progress: {done}/{len(all_programs)} done "
                      f"({_success_count[0]} ok, {_error_count[0]} failed) ──\n")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for i, item in enumerate(all_programs, 1):
            future = executor.submit(
                process_program,
                item["university"],
                item["country"],
                item["program"],
                item["dataset"],
                i,
                len(all_programs),
            )
            futures[future] = item

        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
                handle_result(item, result)
            except Exception as e:
                prog = item["program"]
                print(f"  EXCEPTION {item['university']} — {prog['program_name']} — {e}")
                result = {
                    "university": item["university"],
                    "program": prog["program_name"],
                    "status": "exception",
                    "error": str(e),
                }
                handle_result(item, result)

    # ── Retry pass for failures ──────────────────────────────────────────
    if failed_items:
        print(f"\n{'─'*60}")
        print(f"RETRY PASS: {len(failed_items)} failed programs (reduced to {min(args.workers, 5)} workers)")
        print(f"{'─'*60}\n")
        time.sleep(10)  # cooldown before retry — let rate limits reset

        retry_results = []
        with ThreadPoolExecutor(max_workers=min(args.workers, 5)) as executor:
            futures = {}
            for item, prev_result in failed_items:
                future = executor.submit(
                    process_program,
                    item["university"],
                    item["country"],
                    item["program"],
                    item["dataset"],
                    0,
                    len(failed_items),
                )
                futures[future] = (item, prev_result)

            for future in as_completed(futures):
                item, prev_result = futures[future]
                try:
                    result = future.result()
                    result["program_id"] = item["program"].get("program_id", "")
                    retry_results.append(result)
                    append_progress(run_id, {**result, "retry": True})
                    if result.get("status") == "success":
                        for i, r in enumerate(results):
                            if (r.get("university") == result["university"]
                                    and r.get("program") == result["program"]):
                                results[i] = result
                                break
                        print(f"  RETRY-OK {result['university']} — {result['program']}")
                    else:
                        print(f"  RETRY-FAIL {result.get('university', '?')} — {result.get('status', '?')}")
                except Exception as e:
                    print(f"  RETRY-EXCEPTION {item['university']} — {e}")

        retry_success = sum(1 for r in retry_results if r.get("status") == "success")
        print(f"\n  Retry recovered: {retry_success}/{len(failed_items)}")

    # ── Summary ──────────────────────────────────────────────────────────
    success = sum(1 for r in results if r.get("status") == "success")
    errors = sum(1 for r in results if r.get("status") in ("error", "exception"))
    empty = sum(1 for r in results if r.get("status") == "empty")
    total_cited = sum(r.get("verified", 0) for r in results)
    total_gaps = sum(r.get("missing_fields", 0) for r in results)
    total_sources = sum(r.get("grounding_sources", 0) for r in results)
    avg_elapsed = sum(r.get("elapsed", 0) for r in results) / max(len(results), 1)

    print(f"\n{'='*60}")
    print(f"GEMINI SCRAPER COMPLETE")
    print(f"  Run ID: {run_id}")
    print(f"  Programs processed: {len(results)}")
    print(f"  Success: {success}")
    print(f"  Empty responses: {empty}")
    print(f"  Errors: {errors}")
    print(f"  Cited sections: {total_cited}, Data gaps: {total_gaps}, Total sources: {total_sources}")
    print(f"  Avg time per program: {avg_elapsed:.1f}s")
    print(f"  Progress file: {get_progress_path(run_id)}")

    # Write summary log
    log_path = os.path.join(OUTPUT_DIR, f"gemini_scraper_log_{run_id}.json")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "run_id": run_id,
            "csv_source": args.csv,
            "config": {
                "model": GEMINI_MODEL,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "workers": args.workers,
                "max_retries": MAX_RETRIES,
            },
            "summary": {
                "total": len(results),
                "success": success,
                "empty": empty,
                "errors": errors,
                "cited_sections": total_cited,
                "data_gaps": total_gaps,
                "total_sources": total_sources,
                "avg_elapsed_seconds": round(avg_elapsed, 1),
            },
            "failed_programs": [
                {"university": item["university"], "program": item["program"]["program_name"],
                 "program_id": item["program"].get("program_id", ""), "error": res.get("error", "")}
                for item, res in failed_items
            ],
        }, f, indent=2)
    print(f"  Log: {log_path}")


if __name__ == "__main__":
    main()