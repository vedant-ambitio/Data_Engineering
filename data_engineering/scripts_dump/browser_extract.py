#!/usr/bin/env python3
"""
browser_extract.py — Generic data extraction using BrowserOS + Gemini Vertex AI
=================================================================================

Controls BrowserOS via MCP to visit URLs, get rendered page content,
then sends content to Gemini for structured field extraction.

Works for ANY use case — just change the input, output, and prompt.

USAGE:
  python browser_extract.py --config extract_config.json

  python browser_extract.py \
    --input-dir   Olympiad/olympiad_data/raw \
    --url-field   official_website \
    --id-field    olympiad_id \
    --output-dir  Olympiad/olympiad_data/registration_patch \
    --prompt-file prompts/registration_dates.txt \
    --max 5

  python browser_extract.py \
    --input-dir   Summer_Schools/program_data \
    --url-field   program_page_url \
    --id-field    program_id \
    --output-dir  Summer_Schools/extracted \
    --prompt-file prompts/summer_school_details.txt

OPTIONS:
  --config       Path to JSON config file (alternative to CLI args)
  --input-dir    Folder with input JSONs (each has a URL field)
  --url-field    JSON field name containing the URL to visit (default: official_website)
  --id-field     JSON field name for the record ID (default: olympiad_id)
  --output-dir   Where to save extracted JSONs
  --prompt-file  Path to prompt text file (describes what to extract)
  --prompt       Inline prompt string (alternative to --prompt-file)
  --max          Process at most N records
  --skip-existing  Skip if output file already exists
  --dry-run      Show what would run without executing
  --no-subpages  Don't crawl subpages, only visit the main URL
  --verbose      Print full page content and Gemini responses
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
import glob
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"
MAX_OUTPUT_TOKENS = 8000

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VERTEX_SA_KEY_PATH = os.path.join(SCRIPT_DIR, "dashboard", "gcp-key.json")

# BrowserOS MCP endpoint
BROWSEROS_MCP_URL = "http://127.0.0.1:9000/mcp"

# Token cache
_token_cache = {"token": None, "timestamp": 0}
_token_lock = threading.Lock()
TOKEN_REFRESH_INTERVAL = 3000


# ══════════════════════════════════════════════════════════════════════════════
#  MOJIBAKE CLEANER — fix UTF-8 chars mis-decoded as Latin-1
# ══════════════════════════════════════════════════════════════════════════════

_MOJIBAKE_MAP = [
    # Currency
    ("â‚¹", "₹"),      # â‚¹ -> ₹
    ("Â£", "£"),            # Â£ -> £
    ("â‚¬", "€"),      # â‚¬ -> €
    ("Â¥", "¥"),            # Â¥ -> ¥
    ("Â¹", "₹"),            # Â¹ -> ₹ (bare rupee fallback)
    # Quotes & dashes
    ("â€™", "’"),      # â€™ -> right single quote
    ("â€˜", "‘"),      # â€˜ -> left single quote
    ("â€œ", "“"),      # â€œ -> left double quote
    ("â€", "”"),      # â€ -> right double quote
    ("â€", "–"),      # â€“ -> en dash
    ("â€", "—"),      # â€” -> em dash
    ("â€¦", "…"),      # â€¦ -> ellipsis
    ("â€¢", "•"),      # â€¢ -> bullet
    # Accents
    ("Ã©", "é"),            # Ã© -> é
    ("Ã¨", "è"),            # Ã¨ -> è
    ("Ã ", "à"),            # Ã  -> à
    ("Ã®", "î"),            # Ã® -> î
    ("Ã§", "ç"),            # Ã§ -> ç
    ("Ã±", "ñ"),            # Ã± -> ñ
    ("Ã´", "ô"),            # Ã´ -> ô
    ("Ã»", "û"),            # Ã» -> û
    # Spaces
    ("Â ", " "),                 # Â  -> space
    # Double-encoded variants
    ("Ã‚£", "£"),      # Ã‚£ -> £
    ("Ã‚¹", "₹"),      # Ã‚¹ -> ₹
    # Fallback: strip stray Â (U+00C2)
    ("Â", ""),
]


def clean_mojibake_str(s):
    """Remove mojibake patterns from a string. Returns cleaned string."""
    if not isinstance(s, str):
        return s
    for bad, good in _MOJIBAKE_MAP:
        s = s.replace(bad, good)
    return s


def clean_mojibake_dict(d):
    """Recursively clean mojibake in all string values of a dict/list. Returns cleaned object."""
    if isinstance(d, dict):
        return {k: clean_mojibake_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [clean_mojibake_dict(x) for x in d]
    if isinstance(d, str):
        return clean_mojibake_str(d)
    return d


# ══════════════════════════════════════════════════════════════════════════════
#  VERTEX AI AUTH (same pattern as all our scrapers)
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
            return None
        with open(VERTEX_SA_KEY_PATH) as f:
            sa_key = json.load(f)
        now_ts = int(time.time())
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=")
        payload_data = {
            "iss": sa_key["client_email"],
            "scope": "https://www.googleapis.com/auth/cloud-platform",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now_ts, "exp": now_ts + 3600,
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
        print(f"[AUTH ERROR] {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI CALL (Vertex AI, structured JSON output)
# ══════════════════════════════════════════════════════════════════════════════

def gemini_extract(prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.1, attempt=0):
    token = _get_vertex_token()
    if not token:
        return {"error": "Failed to get token"}

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
             endpoint, "-d", f"@{tmp_path}"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        if attempt < 2:
            time.sleep(5 * (2 ** attempt))
            return gemini_extract(prompt, max_tokens, temperature, attempt + 1)
        return {"error": "Timeout"}
    finally:
        if tmp_path:
            try: os.unlink(tmp_path)
            except: pass

    output = result.stdout.strip()
    lines = output.rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else output
    http_code = int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else 0

    if http_code == 429:
        if attempt < 2:
            delay = 30 if attempt == 0 else 60
            print(f"    [RATE-LIMITED] {delay}s...")
            time.sleep(delay)
            return gemini_extract(prompt, max_tokens, temperature, attempt + 1)
        return {"error": "Rate limited"}

    if http_code >= 500:
        if attempt < 2:
            time.sleep(5 * (2 ** attempt))
            return gemini_extract(prompt, max_tokens, temperature, attempt + 1)
        return {"error": f"Server error {http_code}"}

    try:
        response = json.loads(body)
    except json.JSONDecodeError:
        return {"error": f"Bad JSON: {body[:200]}"}

    if "error" in response:
        return {"error": response["error"].get("message", str(response["error"]))}

    text = ""
    candidates = response.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            if "text" in part:
                text += part["text"]

    return {"text": text}


# ══════════════════════════════════════════════════════════════════════════════
#  BROWSEROS MCP CLIENT
# ══════════════════════════════════════════════════════════════════════════════

_mcp_call_id = 0

def mcp_call(method, params=None):
    """Call a BrowserOS MCP tool via HTTP POST. Returns result dict or None."""
    global _mcp_call_id
    _mcp_call_id += 1

    payload = {
        "jsonrpc": "2.0",
        "id": _mcp_call_id,
        "method": method,
        "params": params or {},
    }

    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", BROWSEROS_MCP_URL,
             "-H", "Content-Type: application/json",
             "-H", "Accept: application/json, text/event-stream",
             "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=60,
        )
        resp = json.loads(result.stdout)
        if "error" in resp:
            print(f"    [MCP ERROR] {resp['error']}")
            return None
        return resp.get("result", {})
    except Exception as e:
        print(f"    [MCP ERROR] {e}")
        return None


def mcp_tool(tool_name, arguments=None):
    """Call a specific BrowserOS tool."""
    return mcp_call("tools/call", {"name": tool_name, "arguments": arguments or {}})


def get_active_page_id():
    """Get the active BrowserOS page/tab ID."""
    result = mcp_tool("get_active_page")
    if result:
        sc = result.get("structuredContent", {})
        page = sc.get("page", {})
        return page.get("pageId", 1)
    return 1


def navigate(page_id, url, wait_time=8):
    """Navigate a BrowserOS tab to a URL. Waits for page to fully render."""
    print(f"    [BROWSER] Opening {url[:80]}...")
    result = mcp_tool("navigate_page", {"page": page_id, "url": url})
    if result:
        # Wait for page to fully render (JS frameworks need time)
        time.sleep(wait_time)
        # Poll until page is actually loaded (not isLoading)
        for _ in range(5):
            page_info = mcp_tool("get_active_page")
            if page_info:
                sc = page_info.get("structuredContent", {})
                page = sc.get("page", {})
                if not page.get("isLoading", True):
                    break
            time.sleep(2)
    return result


def is_404_page(content):
    """Detect if page is a 404 / Not Found page."""
    if not content:
        return True
    content_lower = content.lower()[:500]  # Check first 500 chars (title/header)
    indicators = [
        "404", "page not found", "not found", "page doesn't exist",
        "this page doesn't", "oops", "error 404", "we can't find",
    ]
    return any(ind in content_lower for ind in indicators)


def get_page_content(page_id):
    """Get rendered page content as markdown text."""
    result = mcp_tool("get_page_content", {"page": page_id})
    if result:
        content_parts = result.get("content", [])
        text = ""
        for part in content_parts:
            if part.get("type") == "text":
                text += part["text"] + "\n"
        return text.strip()
    return ""


def get_page_links(page_id):
    """Get all links on the current page."""
    result = mcp_tool("get_page_links", {"page": page_id})
    if result:
        content_parts = result.get("content", [])
        for part in content_parts:
            if part.get("type") == "text":
                return part["text"]
    return ""


# ══════════════════════════════════════════════════════════════════════════════
#  SUBPAGE DISCOVERY — find relevant subpages (dates, schedule, registration)
# ══════════════════════════════════════════════════════════════════════════════

SUBPAGE_KEYWORDS = [
    # Registration/application
    "apply", "register", "registration", "application", "submit", "admissions", "admission",
    # Fees/cost (summer schools, programs)
    "fees", "fee", "tuition", "cost", "pricing", "price", "investment",
    # Rules/eligibility
    "rules", "eligibility", "criteria", "requirements", "guidelines", "faq",
    # Program details
    "program", "programme", "curriculum", "subjects", "courses", "tracks",
    # Prizes/awards (competitions)
    "prizes", "prize", "awards", "rewards",
    # Judging (competitions)
    "judge", "judging", "evaluation",
    # Structure/format
    "structure", "format", "rounds", "stages", "timeline", "schedule",
    # Dates
    "date", "deadline", "important-date", "calendar",
    # About
    "about", "overview", "details",
    # Year markers
    "2026", "2027",
]

SUBPAGE_SKIP = [
    "login", "signup", "sign-up", "account", "password",
    "cart", "shop", "store", "buy",
    "gallery", "photo", "video", "image", "media",
    "contact", "privacy", "terms", "cookie", "sitemap",
    "feed", "rss", "xml", ".pdf", ".zip", ".doc",
    "javascript:", "mailto:", "#",
    "sponsor", "partner", "donate",
]


def find_relevant_subpages(links_text, base_url):
    """Parse links text and find relevant subpages."""
    from urllib.parse import urlparse

    base_domain = urlparse(base_url).netloc.lower().replace("www.", "")
    base_path = urlparse(base_url).path.rstrip("/")

    # Extract URLs from the links text
    urls = re.findall(r'https?://[^\s<>"\')\]]+', links_text)

    relevant = []
    seen_paths = {base_path}
    for url in urls:
        url_domain = urlparse(url).netloc.lower().replace("www.", "")
        if url_domain != base_domain:
            continue

        url_lower = url.lower()
        url_path = urlparse(url).path.rstrip("/")

        # Skip if same as base or already seen
        if url_path == base_path or url_path in seen_paths:
            continue
        # Skip if too short (e.g. just /en)
        if len(url_path) < 3:
            continue

        # Skip junk
        if any(skip in url_lower for skip in SUBPAGE_SKIP):
            continue

        # Score by keywords
        score = sum(1 for kw in SUBPAGE_KEYWORDS if kw in url_lower)
        if score > 0:
            relevant.append((url, score))
            seen_paths.add(url_path)

    # Sort by score, return top 3
    relevant.sort(key=lambda x: -x[1])
    return [url for url, _ in relevant[:3]]


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN EXTRACTION LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def extract_one(record_id, url, prompt_template, page_id, crawl_subpages=True, verbose=False, fallback_url=None):
    """
    Visit a URL in BrowserOS, get content, send to Gemini, return extracted data.
    Always crawls 2-3 subpages proactively for better coverage.
    If primary URL fails (404 or thin), falls back to fallback_url.
    """
    print(f"\n  {'='*60}")
    print(f"  {record_id}")
    print(f"  URL: {url}")
    print(f"  {'='*60}")

    # Step 1: Navigate to URL (8s wait for JS rendering)
    navigate(page_id, url)
    content = get_page_content(page_id)

    # Detect 404 / Not Found pages
    is_404 = is_404_page(content) if content else True
    if is_404:
        print(f"    [404] Primary URL appears to be Not Found or error page")

    # If primary URL fails (empty, 404, or thin), try fallback
    if fallback_url and (not content or len(content) < 200 or is_404):
        print(f"    [FALLBACK] Primary URL returned {len(content)} chars (404={is_404}). Trying fallback: {fallback_url}")
        navigate(page_id, fallback_url)
        content = get_page_content(page_id)
        url = fallback_url  # Update url for source_url field in prompt
        if is_404_page(content):
            print(f"    [404] Fallback URL also appears to be 404")

    if not content or len(content) < 100:
        print(f"    [WARN] Page content too short ({len(content)} chars)")

    if verbose and content:
        print(f"    [CONTENT] {content[:500]}...")

    # Step 2: ALWAYS crawl subpages proactively (Tier 1 improvement)
    subpage_content = ""
    if crawl_subpages:
        print(f"    [SUBPAGES] Discovering relevant subpages...")
        links_text = get_page_links(page_id)
        if links_text:
            subpages = find_relevant_subpages(links_text, url)
            print(f"    [SUBPAGES] Found {len(subpages)} relevant subpages")
            for sp_url in subpages:
                print(f"    [SUBPAGE] {sp_url[:80]}")
                navigate(page_id, sp_url, wait_time=5)  # shorter wait for subpages
                sp_content = get_page_content(page_id)
                if sp_content and not is_404_page(sp_content):
                    # Truncate subpage to 5000 chars to capture full sections (rules, criteria)
                    sp_trimmed = sp_content[:5000]
                    subpage_content += f"\n\n--- Subpage: {sp_url} ---\n{sp_trimmed}"

    # Combine all content
    all_content = content or ""
    if subpage_content:
        all_content += subpage_content

    if not all_content.strip():
        print(f"    [SKIP] No content extracted from any page")
        return {record_id: None, "_error": "No content"}

    # Truncate if too long (Gemini context limit)
    if len(all_content) > 30000:
        all_content = all_content[:30000] + "\n\n[... truncated ...]"

    print(f"    [CONTENT] {len(all_content)} chars total")

    # Step 3: Build prompt and send to Gemini
    full_prompt = prompt_template.replace("{RECORD_ID}", record_id)
    full_prompt = full_prompt.replace("{URL}", url)
    full_prompt = full_prompt.replace("{PAGE_CONTENT}", all_content)

    print(f"    [GEMINI] Extracting fields...")
    result = gemini_extract(full_prompt)

    if result.get("error"):
        print(f"    [GEMINI ERROR] {result['error']}")
        return {"_id": record_id, "_error": result["error"]}

    # Parse Gemini response
    try:
        extracted = json.loads(result["text"])
    except json.JSONDecodeError:
        print(f"    [PARSE ERROR] {result.get('text', '')[:200]}")
        return {"_id": record_id, "_error": "parse_error"}

    print(f"    [OK] Extracted: {json.dumps(extracted, ensure_ascii=False)[:200]}")
    return extracted


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_config(args):
    """Build config from CLI args or config file."""
    config = {}

    # If config file provided, load it
    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)

    # CLI args override config file
    if args.input_dir:
        config["input_dir"] = args.input_dir
    if args.url_field:
        config["url_field"] = args.url_field
    if args.fallback_url_field:
        config["fallback_url_field"] = args.fallback_url_field
    if args.id_field:
        config["id_field"] = args.id_field
    if args.output_dir:
        config["output_dir"] = args.output_dir
    if args.prompt_file:
        config["prompt_file"] = args.prompt_file
    if args.prompt:
        config["prompt"] = args.prompt
    if args.max:
        config["max"] = args.max

    # Defaults
    config.setdefault("url_field", "official_website")
    config.setdefault("fallback_url_field", None)
    config.setdefault("id_field", "olympiad_id")
    config.setdefault("crawl_subpages", not args.no_subpages)

    return config


def load_prompt(config):
    """Load prompt template from file or inline string."""
    if "prompt_file" in config:
        prompt_path = config["prompt_file"]
        if not os.path.isabs(prompt_path):
            prompt_path = os.path.join(SCRIPT_DIR, prompt_path)
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    elif "prompt" in config:
        return config["prompt"]
    else:
        print("[ERROR] No prompt provided. Use --prompt-file or --prompt")
        sys.exit(1)


def load_input_records(config):
    """Load all input JSONs from input_dir. Returns (id, url, fallback_url) tuples."""
    input_dir = config["input_dir"]
    if not os.path.isabs(input_dir):
        input_dir = os.path.join(SCRIPT_DIR, input_dir)

    url_field = config["url_field"]
    fallback_url_field = config.get("fallback_url_field")
    id_field = config["id_field"]

    records = []
    for f in sorted(glob.glob(os.path.join(input_dir, "*.json"))):
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        record_id = data.get(id_field, os.path.basename(f).replace(".json", ""))
        url = data.get(url_field)
        fallback_url = data.get(fallback_url_field) if fallback_url_field else None

        if url:
            records.append((record_id, url, fallback_url))
        elif fallback_url:
            # No primary URL, but fallback exists — use fallback as main
            records.append((record_id, fallback_url, None))
        else:
            print(f"  [SKIP] {record_id} — no {url_field} or {fallback_url_field} field")

    return records


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generic data extraction using BrowserOS + Gemini",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="Path to JSON config file")
    parser.add_argument("--input-dir", help="Folder with input JSONs")
    parser.add_argument("--url-field", help="JSON field with URL (default: official_website)")
    parser.add_argument("--fallback-url-field", help="Fallback URL field if primary fails (optional)")
    parser.add_argument("--id-field", help="JSON field with record ID (default: olympiad_id)")
    parser.add_argument("--output-dir", help="Where to save extracted JSONs")
    parser.add_argument("--prompt-file", help="Path to prompt template file")
    parser.add_argument("--prompt", help="Inline prompt string")
    parser.add_argument("--max", type=int, help="Process at most N records")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-subpages", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Load config
    config = load_config(args)

    if "input_dir" not in config or "output_dir" not in config:
        print("[ERROR] --input-dir and --output-dir are required")
        parser.print_help()
        sys.exit(1)

    # Create output dir
    output_dir = config["output_dir"]
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(SCRIPT_DIR, output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # Load prompt
    prompt_template = load_prompt(config)
    print(f"Prompt loaded ({len(prompt_template)} chars)")

    # Load input records
    records = load_input_records(config)
    print(f"Loaded {len(records)} records from {config['input_dir']}")

    if config.get("max"):
        records = records[:config["max"]]

    # Check BrowserOS is running
    print(f"\nChecking BrowserOS MCP at {BROWSEROS_MCP_URL}...")
    test = mcp_tool("get_active_page")
    if not test:
        print("[ERROR] Cannot connect to BrowserOS. Is it running?")
        print("        Make sure BrowserOS is open on your desktop.")
        sys.exit(1)
    print("BrowserOS connected!\n")

    # Get active page ID
    page_id = get_active_page_id()
    print(f"Using BrowserOS page/tab ID: {page_id}")

    # Process each record
    saved = 0
    for i, record_tuple in enumerate(records):
        # Support both (id, url) and (id, url, fallback_url) tuples
        if len(record_tuple) == 3:
            record_id, url, fallback_url = record_tuple
        else:
            record_id, url = record_tuple
            fallback_url = None

        # Skip existing
        if args.skip_existing:
            out_file = os.path.join(output_dir, f"{record_id}.json")
            if os.path.exists(out_file):
                print(f"  [{i+1}/{len(records)}] {record_id} — exists, skipping")
                continue

        print(f"\n  [{i+1}/{len(records)}]", end="")

        if args.dry_run:
            fb_str = f" (fallback: {fallback_url[:40]})" if fallback_url else ""
            print(f"  [DRY RUN] {record_id} -> {url[:60]}{fb_str}")
            continue

        result = extract_one(
            record_id, url, prompt_template, page_id,
            crawl_subpages=config.get("crawl_subpages", True),
            verbose=args.verbose,
            fallback_url=fallback_url,
        )

        # Save output
        if result:
            result = clean_mojibake_dict(result)
            out_file = os.path.join(output_dir, f"{record_id}.json")
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            saved += 1

        # Small delay between pages
        if i < len(records) - 1:
            time.sleep(2)

    print(f"\n{'='*60}")
    print(f"  DONE: {saved} records extracted")
    print(f"  Output: {output_dir}/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
