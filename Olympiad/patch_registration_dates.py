#!/usr/bin/env python3
"""
patch_registration_dates.py — Scrape official URLs + subpages, extract registration_close_date
=================================================================================================

For each of the 56 olympiads:
  1. Read official_website from raw JSON
  2. Fetch official URL + discover subpages (same crawl logic as olympiad_scraper)
  3. For JS-heavy sites (SOF, Silverzone, UC, CREST), use Puppeteer fallback
  4. Send scraped text to Gemini with a focused single-field prompt
  5. Save output as minimal JSON: {olympiad_id, registration_close_date}
     in olympiad_data/registration_patch/

Does NOT modify any existing files or scripts.

Usage:
  python patch_registration_dates.py                     # all 56
  python patch_registration_dates.py --olympiad IMO      # single
  python patch_registration_dates.py --max 5             # first 5
  python patch_registration_dates.py --skip-existing     # resume mode
  python patch_registration_dates.py --dry-run           # show what would run
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
MAX_OUTPUT_TOKENS = 2000  # small — only extracting one field

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
VERTEX_SA_KEY_PATH = os.path.join(PROJECT_ROOT, "dashboard", "gcp-key.json")
USE_VERTEX = True

# Input: existing raw JSONs
RAW_DIR = os.path.join(SCRIPT_DIR, "olympiad_data", "raw")

# Output: separate folder for registration date patches
PATCH_DIR = os.path.join(SCRIPT_DIR, "olympiad_data", "registration_patch")

# Fetch settings
HTTP_TIMEOUT = 30
MAX_TEXT_PER_SOURCE = 8000
MIN_USEFUL_TEXT = 2000
MAX_SUBPAGES = 4
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

JS_HEAVY_DOMAINS = ["sofworld.org", "silverzone.org", "unifiedcouncil.com",
                    "crestolympiads.com", "myark.in"]

# Token cache
_token_cache = {"token": None, "timestamp": 0}
_token_lock = threading.Lock()
TOKEN_REFRESH_INTERVAL = 3000


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH (same as olympiad_scraper.py)
# ══════════════════════════════════════════════════════════════════════════════

def _get_vertex_token():
    with _token_lock:
        now = time.time()
        if _token_cache["token"] and (now - _token_cache["timestamp"]) < TOKEN_REFRESH_INTERVAL:
            return _token_cache["token"]

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
#  GEMINI CALL (same as olympiad_scraper.py)
# ══════════════════════════════════════════════════════════════════════════════

def gemini_extract(prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.1, attempt=0):
    """Call Gemini, get JSON back."""
    token = _get_vertex_token()
    if not token:
        return {"text": "", "error": "Failed to get token"}
    endpoint = (
        f"https://aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}"
        f"/locations/{VERTEX_LOCATION}/publishers/google/models/{GEMINI_MODEL}:generateContent"
    )

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        }
    }

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            tmp.write(json.dumps(payload))
            tmp_path = tmp.name
        result = subprocess.run(
            ["curl", "-s", "-w", "\n%{http_code}", "-X", "POST",
             "-H", "Content-Type: application/json",
             "-H", f"Authorization: Bearer {token}",
             endpoint,
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
#  HTML FETCHING + CLEANING (same as olympiad_scraper.py)
# ══════════════════════════════════════════════════════════════════════════════

def _fix_mojibake(text):
    replacements = {
        "\u00e2\u0080\u0099": "'", "\u00e2\u0080\u0098": "'",
        "\u00e2\u0080\u009c": '"', "\u00e2\u0080\u009d": '"',
        "\u00e2\u0080\u0093": "-", "\u00e2\u0080\u0094": "--",
        "\u00e2\u0080\u00a6": "...", "\u00c2\u00a0": " ", "\u00c2": "",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def _extract_main_content(html):
    for tag in ["main", "article"]:
        m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', html, re.DOTALL | re.IGNORECASE)
        if m and len(m.group(1)) > 500:
            return m.group(1)
    for noise_tag in ["nav", "header", "footer", "aside"]:
        html = re.sub(rf'<{noise_tag}[^>]*>.*?</{noise_tag}>', '', html, flags=re.DOTALL | re.IGNORECASE)
    return html


def html_to_text(html):
    if not html:
        return ""
    import html as html_lib
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = _extract_main_content(html)
    html = re.sub(r'<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', html)
    text = html_lib.unescape(text)
    text = _fix_mojibake(text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()


def _fetch_raw_html(url):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept-Charset": "utf-8",
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


def fetch_url(url):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept-Charset": "utf-8",
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
                html = raw.decode(encoding, errors="replace")
            except (UnicodeDecodeError, LookupError):
                html = raw.decode("utf-8", errors="replace")
            return html_to_text(html)
    except Exception as e:
        print(f"    [HTTP ERROR] {url}: {e}")
        return ""


def _fetch_via_puppeteer(url):
    try:
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
            text = re.sub(r'[^\x20-\x7E\n\r\t]', ' ', text)
            return text
        return ""
    except Exception as e:
        print(f"    [PUPPETEER ERROR] {url}: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
#  SUBPAGE CRAWLER (same as olympiad_scraper.py)
# ══════════════════════════════════════════════════════════════════════════════

_SUBPAGE_HIGH_SCORE = [
    "registration", "register", "apply", "application",
    "dates", "timeline", "calendar", "deadline", "schedule",
    "2026", "2027",
]
_SUBPAGE_MEDIUM_SCORE = [
    "eligibility", "rules", "requirements",
    "about", "overview", "information", "exam",
]
_SUBPAGE_SKIP = [
    "login", "signup", "sign-up", "account", "password",
    "gallery", "photo", "video", "image", "media",
    "sponsor", "partner", "donate", "shop", "store",
    "contact", "privacy", "terms", "cookie", "sitemap",
    "feed", "rss", "xml", ".pdf", ".zip", ".doc",
    "javascript:", "mailto:", "#",
]


def _extract_internal_links(html, base_url):
    from urllib.parse import urlparse, urljoin
    base_parsed = urlparse(base_url)
    base_domain = base_parsed.netloc.lower().replace("www.", "")
    hrefs = re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    links = set()
    for href in hrefs:
        if any(href.lower().startswith(skip) for skip in ["#", "javascript:", "mailto:"]):
            continue
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        link_domain = parsed.netloc.lower().replace("www.", "")
        if link_domain == base_domain:
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean += f"?{parsed.query}"
            links.add(clean)
    return list(links)


def _score_subpage(url):
    url_lower = url.lower()
    score = 0
    for skip in _SUBPAGE_SKIP:
        if skip in url_lower:
            return -1
    for kw in _SUBPAGE_HIGH_SCORE:
        if kw in url_lower:
            score += 3
    for kw in _SUBPAGE_MEDIUM_SCORE:
        if kw in url_lower:
            score += 1
    return score


def crawl_subpages(official_url):
    html = _fetch_raw_html(official_url)
    if not html:
        return []
    all_links = _extract_internal_links(html, official_url)
    from urllib.parse import urlparse
    base_path = urlparse(official_url).path.rstrip("/")
    filtered = []
    for link in all_links:
        link_path = urlparse(link).path.rstrip("/")
        if link_path == base_path or link_path in ("", "/", "/en", "/en/"):
            continue
        filtered.append(link)
    if not filtered:
        return []
    scored = [(url, _score_subpage(url)) for url in filtered]
    scored = [(url, s) for url, s in scored if s > 0]
    scored.sort(key=lambda x: -x[1])
    return [url for url, score in scored[:MAX_SUBPAGES]]


# ══════════════════════════════════════════════════════════════════════════════
#  FETCH ALL SOURCES (official URL + subpages, with Puppeteer fallback)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_sources(urls):
    """Fetch all URLs. Returns list of (url, text, status)."""
    sources = []
    for url in urls:
        text = fetch_url(url)

        if len(text) < MIN_USEFUL_TEXT:
            is_js_heavy = any(d in url.lower() for d in JS_HEAVY_DOMAINS)
            if is_js_heavy or len(text) == 0:
                print(f"    [PUPPETEER FALLBACK] {url[:60]}... (HTTP got {len(text)} chars)")
                puppet_text = _fetch_via_puppeteer(url)
                if len(puppet_text) > len(text):
                    text = puppet_text
                    if text:
                        if len(text) > MAX_TEXT_PER_SOURCE:
                            text = text[:MAX_TEXT_PER_SOURCE] + "\n\n[... truncated ...]"
                        sources.append((url, text, "ok_puppeteer"))
                        continue

        if text:
            if len(text) > MAX_TEXT_PER_SOURCE:
                text = text[:MAX_TEXT_PER_SOURCE] + "\n\n[... truncated ...]"
            sources.append((url, text, "ok"))
        else:
            sources.append((url, "", "error"))
    return sources


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI PROMPT — focused on registration_close_date only
# ══════════════════════════════════════════════════════════════════════════════

def build_prompt(olympiad_id, olympiad_name, scraped_text):
    return f"""You are extracting ONE field for an olympiad competition.

Olympiad: {olympiad_name} (ID: {olympiad_id})

From the scraped text below, extract ONLY the registration_close_date.

RULES:
- registration_close_date = the last date students or schools can register/apply.
- Return date in ISO format: "YYYY-MM-DD" (e.g. "2026-09-15").
- If the page mentions a range like "August-September 2026", use the LAST day of the range: "2026-09-30".
- If it says "schools must register by [date]", that IS the registration_close_date.
- If the page says "registration opens in August" but gives no close date, return null.
- If registration is year-round / rolling / always open, return "rolling".
- If no registration date info is found at all, return null.
- Do NOT guess or fabricate dates. Only extract what is explicitly stated.

Return JSON:
{{
  "olympiad_id": "{olympiad_id}",
  "registration_close_date": "<date or null or rolling>",
  "source_text": "<the exact sentence or phrase from the text where you found the date>"
}}

--- SCRAPED TEXT ---
{scraped_text}
--- END ---
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def load_olympiads():
    """Load all 56 olympiad raw JSONs and return list of (id, name, official_url)."""
    import glob
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.json")))
    olympiads = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        oid = d.get("olympiad_id", os.path.basename(f).replace(".json", ""))
        name = d.get("activity_name", oid)
        url = d.get("official_website")
        if url:
            olympiads.append((oid, name, url))
    return olympiads


def process_one(olympiad_id, olympiad_name, official_url, dry_run=False):
    """Scrape + extract registration_close_date for one olympiad."""
    print(f"\n{'='*60}")
    print(f"  {olympiad_id} | {olympiad_name}")
    print(f"  URL: {official_url}")
    print(f"{'='*60}")

    if dry_run:
        print("  [DRY RUN] Would scrape and extract.")
        return None

    # Step 1: Discover subpages from official URL
    print(f"  Crawling subpages from {official_url}...")
    subpages = crawl_subpages(official_url)
    if subpages:
        print(f"  Found {len(subpages)} relevant subpages:")
        for sp in subpages:
            print(f"    -> {sp}")

    # Step 2: Fetch official URL + subpages
    all_urls = [official_url] + subpages
    print(f"  Fetching {len(all_urls)} URLs...")
    sources = fetch_all_sources(all_urls)

    # Combine all text
    combined_text = ""
    ok_count = 0
    for url, text, status in sources:
        if status in ("ok", "ok_puppeteer") and text:
            ok_count += 1
            combined_text += f"\n\n--- Source: {url} ---\n{text}"

    if not combined_text.strip():
        print(f"  [SKIP] No text fetched from any URL.")
        return {"olympiad_id": olympiad_id, "registration_close_date": None,
                "source_text": "No content fetched from official URL"}

    print(f"  Fetched {ok_count}/{len(all_urls)} URLs, {len(combined_text)} chars total")

    # Step 3: Send to Gemini
    prompt = build_prompt(olympiad_id, olympiad_name, combined_text)
    print(f"  Calling Gemini...")
    result = gemini_extract(prompt)

    if result.get("error"):
        print(f"  [GEMINI ERROR] {result['error']}")
        return {"olympiad_id": olympiad_id, "registration_close_date": None,
                "source_text": f"Gemini error: {result['error']}"}

    # Parse Gemini response
    try:
        data = json.loads(result["text"])
    except json.JSONDecodeError:
        print(f"  [PARSE ERROR] Could not parse Gemini response")
        return {"olympiad_id": olympiad_id, "registration_close_date": None,
                "source_text": f"Parse error: {result['text'][:200]}"}

    reg_date = data.get("registration_close_date")
    source_text = data.get("source_text", "")
    print(f"  RESULT: {reg_date}")
    if source_text:
        print(f"  SOURCE: {source_text[:100]}")

    return {
        "olympiad_id": olympiad_id,
        "registration_close_date": reg_date,
        "source_text": source_text,
    }


def main():
    parser = argparse.ArgumentParser(description="Patch registration_close_date for olympiads")
    parser.add_argument("--olympiad", help="Process single olympiad by ID")
    parser.add_argument("--max", type=int, help="Process at most N olympiads")
    parser.add_argument("--skip-existing", action="store_true", help="Skip if patch file exists")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run")
    args = parser.parse_args()

    # Create output dir
    os.makedirs(PATCH_DIR, exist_ok=True)

    # Load olympiads
    olympiads = load_olympiads()
    print(f"Loaded {len(olympiads)} olympiads from {RAW_DIR}")

    # Filter
    if args.olympiad:
        olympiads = [(oid, name, url) for oid, name, url in olympiads if oid == args.olympiad]
        if not olympiads:
            print(f"Olympiad '{args.olympiad}' not found.")
            return

    if args.max:
        olympiads = olympiads[:args.max]

    # Process
    results = []
    for i, (oid, name, url) in enumerate(olympiads):
        if args.skip_existing:
            patch_file = os.path.join(PATCH_DIR, f"{oid}.json")
            if os.path.exists(patch_file):
                print(f"  [{i+1}/{len(olympiads)}] {oid} — already exists, skipping")
                continue

        print(f"\n  [{i+1}/{len(olympiads)}]", end="")
        result = process_one(oid, name, url, dry_run=args.dry_run)

        if result and not args.dry_run:
            # Save patch JSON
            patch_file = os.path.join(PATCH_DIR, f"{oid}.json")
            with open(patch_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            results.append(result)

        # Small delay between Gemini calls
        if not args.dry_run and i < len(olympiads) - 1:
            time.sleep(2)

    # Summary
    if results:
        filled = sum(1 for r in results if r.get("registration_close_date") not in [None, "null", ""])
        print(f"\n{'='*60}")
        print(f"  DONE: {len(results)} processed, {filled} dates found")
        print(f"  Output: {PATCH_DIR}/")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
