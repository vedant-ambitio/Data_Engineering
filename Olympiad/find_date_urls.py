#!/usr/bin/env python3
"""
find_date_urls.py — Use Gemini (web-grounded) to find official date/schedule pages
===================================================================================

For each of the 36 olympiads missing registration_close_date:
  1. Ask Gemini with web search: "What is the official page URL where
     [olympiad] publishes exam dates and registration deadlines?"
  2. Save discovered URLs to olympiad_data/date_urls.json

This gives us the RIGHT URLs to scrape (not homepages, but actual date pages).
Then patch_registration_dates.py can use these URLs instead.

Usage:
  python find_date_urls.py                    # all 36 missing
  python find_date_urls.py --max 5            # first 5 only
  python find_date_urls.py --dry-run          # show what would run
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
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"
MAX_OUTPUT_TOKENS = 4000

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
VERTEX_SA_KEY_PATH = os.path.join(PROJECT_ROOT, "dashboard", "gcp-key.json")

RAW_DIR = os.path.join(SCRIPT_DIR, "olympiad_data", "raw")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "olympiad_data", "date_urls.json")

# Token cache
_token_cache = {"token": None, "timestamp": 0}
_token_lock = threading.Lock()
TOKEN_REFRESH_INTERVAL = 3000


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH
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
            print(f"[ERROR] SA key not found at {VERTEX_SA_KEY_PATH}")
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
        print(f"[ERROR] SA auth failed: {e}")

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI CALL (with web search / grounding enabled)
# ══════════════════════════════════════════════════════════════════════════════

def gemini_call(prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.1, attempt=0):
    """Call Gemini with Google Search grounding enabled."""
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
        },
        "tools": [{"google_search": {}}],
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
            return gemini_call(prompt, max_tokens, temperature, attempt + 1)
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
            return gemini_call(prompt, max_tokens, temperature, attempt + 1)
        return {"text": "", "error": "Rate limited"}

    if http_code >= 500:
        if attempt < 2:
            time.sleep(5 * (2 ** attempt))
            return gemini_call(prompt, max_tokens, temperature, attempt + 1)
        return {"text": "", "error": f"Server error {http_code}"}

    try:
        response = json.loads(body)
    except json.JSONDecodeError:
        return {"text": "", "error": f"Bad JSON: {body[:200]}"}

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
#  BATCH PROMPT — group olympiads by org to reduce API calls
# ══════════════════════════════════════════════════════════════════════════════

# Group olympiads by organization so we ask one question per org
ORG_GROUPS = {
    "SOF": {
        "ids": ["SOF_ICSO", "SOF_IEO", "SOF_IGKO", "SOF_IMO", "SOF_ISSO", "SOF_NCO", "SOF_NSO"],
        "org_name": "Science Olympiad Foundation (SOF)",
        "known_domain": "sofworld.org",
    },
    "Silverzone": {
        "ids": ["SZ_SKGKO", "SZ_iIO", "SZ_iOEL", "SZ_iOM", "SZ_iOS"],
        "org_name": "SilverZone Foundation",
        "known_domain": "silverzone.org",
    },
    "UC": {
        "ids": ["UC_UCO", "UC_UIEO", "UC_UIMO"],
        "org_name": "Unified Council",
        "known_domain": "unifiedcouncil.com",
    },
    "CREST": {
        "ids": ["CREST_CCO", "CREST_CEO", "CREST_CMO", "CREST_CSO"],
        "org_name": "CREST Olympiads",
        "known_domain": "crestolympiads.com",
    },
    "HBCSE": {
        "ids": ["HBCSE_NSO", "NSEA", "NSEB", "NSEC", "NSEP", "RMO"],
        "org_name": "HBCSE (Homi Bhabha Centre for Science Education)",
        "known_domain": "olympiads.hbcse.tifr.res.in",
    },
    "INOI": {
        "ids": ["INOI"],
        "org_name": "IARCS (Indian Association for Research in Computing Science)",
        "known_domain": "iarcs.org.in",
    },
    "IChO": {
        "ids": ["IChO"],
        "org_name": "International Chemistry Olympiad",
        "known_domain": "icho-official.org",
    },
    "IPhO": {
        "ids": ["IPhO"],
        "org_name": "International Physics Olympiad",
        "known_domain": "ipho2026.com",
    },
    "INTL_OTHERS": {
        "ids": ["IEO_INTL", "IEarthSO", "IGeO", "IHO", "IJSO", "IOAA", "IOL", "IPO"],
        "org_name": "International Science Olympiads (IEO, IESO, IGeO, IHO, IJSO, IOAA, IOL, IPO)",
        "known_domain": "various",
    },
}


def build_batch_prompt(org_name, known_domain, olympiad_ids):
    """Build a Gemini prompt to find date/schedule URLs for an org group."""
    ids_str = ", ".join(olympiad_ids)
    return f"""Find the official webpage URL where {org_name} publishes exam dates,
exam schedule, and registration deadlines for their olympiad competitions.

Known official domain: {known_domain}
Olympiad IDs we need dates for: {ids_str}

RULES:
- Find the SPECIFIC page that has exam dates and registration deadlines.
  NOT the homepage. Look for pages like /dates, /schedule, /exam-dates,
  /olympiadsdates, /important-dates, /exam-schedule, etc.
- If there is ONE page with dates for ALL their olympiads, return that single URL.
- If each olympiad has its own dates page, return one URL per olympiad.
- Only return URLs from the official domain ({known_domain}).
- If the org has no dates page (e.g. international olympiads where students
  qualify via national stage and there is no direct registration), return null.

Return JSON:
{{
  "org": "{org_name}",
  "date_page_urls": [
    {{
      "url": "<specific URL with dates>",
      "covers_olympiad_ids": [<list of olympiad IDs this page covers>],
      "page_description": "<what this page contains>"
    }}
  ],
  "notes": "<any relevant notes, e.g. 'dates not announced yet' or 'no direct registration'>"
}}
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Find official date/schedule page URLs")
    parser.add_argument("--max", type=int, help="Process at most N org groups")
    parser.add_argument("--dry-run", action="store_true", help="Show prompts without calling Gemini")
    args = parser.parse_args()

    groups = list(ORG_GROUPS.items())
    if args.max:
        groups = groups[:args.max]

    print(f"Finding date page URLs for {len(groups)} org groups ({sum(len(g['ids']) for _, g in groups)} olympiads)")

    all_results = {}

    for i, (org_key, org_info) in enumerate(groups):
        org_name = org_info["org_name"]
        known_domain = org_info["known_domain"]
        ids = org_info["ids"]

        print(f"\n[{i+1}/{len(groups)}] {org_key} ({len(ids)} olympiads) — {known_domain}")

        prompt = build_batch_prompt(org_name, known_domain, ids)

        if args.dry_run:
            print(f"  [DRY RUN] Would ask Gemini for: {org_name}")
            print(f"  IDs: {ids}")
            continue

        result = gemini_call(prompt)

        if result.get("error"):
            print(f"  [ERROR] {result['error']}")
            all_results[org_key] = {"error": result["error"], "ids": ids}
            continue

        raw_text = result["text"]
        # Try to extract JSON from response (may be wrapped in ```json ... ```)
        json_match = re.search(r'```json\s*(.*?)\s*```', raw_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try raw JSON parse
            json_str = raw_text.strip()

        try:
            data = json.loads(json_str)
            all_results[org_key] = data
            urls = data.get("date_page_urls", [])
            notes = data.get("notes", "")
            if urls:
                for entry in urls:
                    url = entry.get("url", "null")
                    covers = entry.get("covers_olympiad_ids", [])
                    desc = entry.get("page_description", "")
                    print(f"  FOUND: {url}")
                    print(f"    Covers: {covers}")
                    print(f"    Desc: {desc}")
            else:
                print(f"  No date URLs found.")
            if notes:
                print(f"  Notes: {notes}")
        except json.JSONDecodeError:
            # If JSON parse fails, just store the raw text — we'll extract URLs manually
            print(f"  [RAW RESPONSE] {raw_text[:300]}")
            # Try to extract any URLs from the text
            found_urls = re.findall(r'https?://[^\s<>"\']+', raw_text)
            all_results[org_key] = {"raw_response": raw_text, "extracted_urls": found_urls, "ids": ids}
            if found_urls:
                print(f"  Extracted URLs: {found_urls}")

        # Delay between calls
        if i < len(groups) - 1:
            time.sleep(3)

    # Save all results
    if not args.dry_run:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)
        print(f"\nSaved to {OUTPUT_FILE}")

    print(f"\nDone: {len(groups)} org groups processed.")


if __name__ == "__main__":
    main()
