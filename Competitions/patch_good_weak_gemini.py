#!/usr/bin/env python3
"""
patch_good_weak_gemini.py — Use Gemini web search to fill 4 weak fields
========================================================================

For each of the 43 good competition files, asks Gemini (with Google Search
grounding) to find the 4 weak fields:
  - deadline
  - prizes_detail
  - prize_amount
  - judging_criteria

Unlike browser_extract.py, this does NOT scrape any page. Gemini searches
the web itself and returns the answer.

Input:  competition_data/input_good_patch/*.json (43 files)
Output: competition_data/patch_good_weak_gemini/*.json

Usage:
  python patch_good_weak_gemini.py
  python patch_good_weak_gemini.py --max 3
  python patch_good_weak_gemini.py --skip-existing
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

# ── Config ──────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"
MAX_OUTPUT_TOKENS = 8000

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
VERTEX_SA_KEY_PATH = os.path.join(PROJECT_ROOT, "dashboard", "gcp-key.json")

INPUT_DIR = os.path.join(SCRIPT_DIR, "competition_data", "input_good_patch")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "competition_data", "patch_good_weak_gemini")

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
#  GEMINI CALL (web-grounded, no JSON mode — Google Search tool conflicts with it)
# ══════════════════════════════════════════════════════════════════════════════

def gemini_search(prompt, attempt=0):
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
            "temperature": 0.1,
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
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
             endpoint, "-d", f"@{tmp_path}"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        if attempt < 2:
            time.sleep(5 * (2 ** attempt))
            return gemini_search(prompt, attempt + 1)
        return {"text": "", "error": "Timeout"}
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
            return gemini_search(prompt, attempt + 1)
        return {"text": "", "error": "Rate limited"}

    if http_code >= 500:
        if attempt < 2:
            time.sleep(5 * (2 ** attempt))
            return gemini_search(prompt, attempt + 1)
        return {"text": "", "error": f"Server error {http_code}"}

    try:
        response = json.loads(body)
    except json.JSONDecodeError:
        return {"text": "", "error": f"Bad JSON: {body[:200]}"}

    if "error" in response:
        return {"text": "", "error": response["error"].get("message", str(response["error"]))}

    text = ""
    candidates = response.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            if "text" in part:
                text += part["text"]
    return {"text": text}


# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT
# ══════════════════════════════════════════════════════════════════════════════

def build_prompt(competition_id, competition_name, official_url, source_url):
    return f"""Search the web thoroughly to find 4 details for this competition.

Competition ID: {competition_id}
Competition name: {competition_name}
Official URL: {official_url}
Source URL: {source_url}

SEARCH STRATEGY:
- Run MULTIPLE Google searches with different queries:
  1. "{competition_name} 2026 deadline"
  2. "{competition_name} 2026 prizes"
  3. "{competition_name} judging criteria"
  4. "{competition_name} 2026 registration last date"
- Check: official URL, news articles, aggregator sites (physicswallah, careers360,
  scholarshipsinindia, vikaspedia), student blogs, school blogs.
- If first search returns nothing, try alternate competition names
  (e.g. ANMC for "Aryabhatta National Maths Competition").

Find these 4 fields for the 2026-27 edition (current/upcoming):

1. deadline: Last date to register/submit. Format: "YYYY-MM-DD".
   Page may say "12th May, 2026" → convert to "2026-05-12".
   Page may say "Last Date of Registration: 12-05-2026" → "2026-05-12".
   PREFER 2026-27 dates. If only old dates found, return null.
   Search aggressively — deadlines are almost always mentioned somewhere.

2. prizes_detail: Full prize breakdown — all tiers, cash amounts, non-cash prizes
   (mentorship, internships, trophies, certificates, trips, scholarships,
   training, free courses). Write as a concise sentence or two.

3. prize_amount: Top prize summary as a string. Can be cash ("$10,000"), non-cash
   ("Apple AirPods + mentorship"), or mixed ("Rs 1 Lakh + internship").

4. judging_criteria: How submissions are evaluated. Could be:
   - Explicit criteria ("Creativity 25%, Innovation 25%, ...")
   - Exam stages ("Stage 1: MCQ test; Stage 2: Interview")
   - Selection basis ("judged on originality, technical quality, presentation")
   Extract whatever selection/evaluation info exists.

RULES:
- Only include information you found in search results. Do NOT fabricate dates.
- For deadline: always convert to YYYY-MM-DD format.
- If one field has data but others don't, still return what you found.
- Don't give up after one search — try 2-3 different query variations.
- Keep values concise — fields will be used in a student-facing UI.

Return your answer in this JSON format (inside ```json``` code block):

```json
{{
  "competition_id": "{competition_id}",
  "deadline": "<YYYY-MM-DD or null>",
  "prizes_detail": "<text or null>",
  "prize_amount": "<text or null>",
  "judging_criteria": "<text or null>",
  "sources": ["<URL1>", "<URL2>"]
}}
```
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Patch weak fields via Gemini web search")
    parser.add_argument("--max", type=int, help="Process at most N records")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.json")))
    if args.max:
        files = files[:args.max]

    print(f"Processing {len(files)} competitions via Gemini web search")
    print(f"Input:  {INPUT_DIR}")
    print(f"Output: {OUTPUT_DIR}\n")

    saved = 0
    for i, f in enumerate(files):
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        cid = data.get("competition_id", os.path.basename(f).replace(".json", ""))
        name = data.get("competition_name", cid)
        official_url = data.get("official_url", "")
        source_url = data.get("source_url", "")

        out_file = os.path.join(OUTPUT_DIR, f"{cid}.json")

        if args.skip_existing and os.path.exists(out_file):
            print(f"  [{i+1}/{len(files)}] {cid} — exists, skipping")
            continue

        print(f"\n  [{i+1}/{len(files)}] {cid}")
        print(f"    Name: {name}")

        if args.dry_run:
            print(f"    [DRY RUN] Would search for deadline, prizes, criteria")
            continue

        prompt = build_prompt(cid, name, official_url, source_url)
        result = gemini_search(prompt)

        if result.get("error"):
            print(f"    [ERROR] {result['error']}")
            continue

        raw_text = result["text"]

        # Extract JSON from response
        extracted = None
        json_match = re.search(r'```json\s*(.*?)\s*```', raw_text, re.DOTALL)
        if json_match:
            try:
                extracted = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Fallback: try incomplete json block
        if extracted is None:
            json_match = re.search(r'```json\s*(.*)', raw_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1).rstrip('`').strip()
                for fix in [json_str, json_str + "]", json_str + "}]", json_str + "\"}"]:
                    try:
                        extracted = json.loads(fix)
                        break
                    except json.JSONDecodeError:
                        continue

        if extracted is None:
            print(f"    [PARSE ERROR] {raw_text[:200]}")
            continue

        # Print summary
        print(f"    deadline:         {extracted.get('deadline')}")
        print(f"    prize_amount:     {str(extracted.get('prize_amount') or '')[:70]}")
        print(f"    judging_criteria: {str(extracted.get('judging_criteria') or '')[:70]}")

        # Save
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(extracted, fh, indent=2, ensure_ascii=False)
        saved += 1

        # Throttle to avoid rate limits
        if i < len(files) - 1:
            time.sleep(3)

    print(f"\n{'='*60}")
    print(f"  Done: {saved} records saved to {OUTPUT_DIR}/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
