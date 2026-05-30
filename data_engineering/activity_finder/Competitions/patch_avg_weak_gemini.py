#!/usr/bin/env python3
"""
patch_avg_weak_gemini.py — Use Gemini 3.0 web search to fill 6 weak fields
==========================================================================

For each of the 31 AVG-tier competition files (deep research 50-79% fill),
asks Gemini 3.0 Pro (with Google Search grounding) to find these 6 fields:

  - deadline           (last date to register/submit)
  - team_size          (solo / 1-4 / pair / etc.)
  - how_to_apply       (registration steps)
  - prizes_detail      (full prize breakdown — cash AND non-cash)
  - prize_amount       (top prize summary — amount OR non-cash items / opportunities)
  - submission_format  (what to submit — PDF, code, video, essay, etc.)

Unlike browser_extract.py, this does NOT scrape any page. Gemini searches the
web itself and returns structured answers.

Differences from patch_good_weak_gemini.py:
  - Uses Gemini 3 Pro (was 2.5 Flash) — better reasoning & search synthesis
  - Targets 6 fields (was 4) — adds team_size, how_to_apply, submission_format
  - prize_amount accepts non-cash items / opportunities, not just dollar values
  - Reads from extracted/avg/ (was input_good_patch/)
  - Writes to patch_avg_weak_gemini3/ (fresh folder)

Input:  competition_data/extracted/avg/*.json (31 files)
Output: competition_data/patch_avg_weak_gemini3/*.json

Usage:
  python patch_avg_weak_gemini.py
  python patch_avg_weak_gemini.py --max 3
  python patch_avg_weak_gemini.py --skip-existing
  python patch_avg_weak_gemini.py --dry-run
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

GEMINI_MODEL = "gemini-3-flash-preview"   # Gemini 3 Flash (was gemini-2.5-flash)
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"
MAX_OUTPUT_TOKENS = 16000   # Bumped from 8000 — Gemini 3 Flash sometimes truncates the JSON
                            # output mid-string when search results are long.


# ══════════════════════════════════════════════════════════════════════════════
#  MOJIBAKE CLEANUP — fixes â‚¹ -> ₹ etc. when subprocess output gets mis-decoded
# ══════════════════════════════════════════════════════════════════════════════
_MOJIBAKE_PAIRS = [
    ("â‚¹", "₹"),     # ₹ Indian rupee (most common — UTF-8 ₹ read as cp1252)
    ("â\x82¬", "€"),       # € Euro
    ("Â£", "£"),
    ("Â¥", "¥"),
    ("Â°", "°"),
    ("Ã©", "é"),
    ("Ã¨", "è"),
    ("Ã¢", "â"),
    ("Ã§", "ç"),
    ("Ã¶", "ö"),
    ("Ã¼", "ü"),
    ("Ã¤", "ä"),
    ("Ã±", "ñ"),
    ("â€™", "'"),   # right single quote
    ("â€œ", '"'),   # left double quote
    ("â€\x9d", '"'),     # right double quote
    ("â€”", "—"),   # em dash
    ("â€“", "–"),   # en dash
    ("â€¦", "…"),        # ellipsis
]


def clean_mojibake(s):
    """Replace common UTF-8-as-cp1252 mojibake sequences with the correct char."""
    if not isinstance(s, str):
        return s
    for bad, good in _MOJIBAKE_PAIRS:
        if bad in s:
            s = s.replace(bad, good)
    return s


def clean_mojibake_dict(d):
    """Apply clean_mojibake recursively to all string values in a dict/list."""
    if isinstance(d, dict):
        return {k: clean_mojibake_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [clean_mojibake_dict(v) for v in d]
    if isinstance(d, str):
        return clean_mojibake(d)
    return d


# ══════════════════════════════════════════════════════════════════════════════
#  ROBUST JSON EXTRACTION — handles ```json``` blocks, truncated output, plain JSON
# ══════════════════════════════════════════════════════════════════════════════

def extract_json_robust(text):
    """Try multiple strategies to extract a JSON object from Gemini's response.

    Strategies (in order):
      1. Closed ```json ... ``` block — direct parse
      2. Open ```json ... (truncated) — try parse, then repair
      3. Plain text containing { ... } — find largest balanced braces
      4. Repair truncated JSON by walking back to the last comma and closing braces

    Returns dict on success, None on total failure.
    """
    if not text:
        return None

    # --- Strategy 1: closed ```json ... ``` block ---
    m = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        repaired = _repair_truncated_json(candidate)
        if repaired is not None:
            return repaired

    # --- Strategy 2: open ```json ... (no closing fence) ---
    m = re.search(r'```json\s*(.*)', text, re.DOTALL)
    if m:
        candidate = m.group(1).rstrip('`').strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        repaired = _repair_truncated_json(candidate)
        if repaired is not None:
            return repaired

    # --- Strategy 3: plain { ... } in the text ---
    first = text.find('{')
    last = text.rfind('}')
    if first >= 0 and last > first:
        candidate = text[first:last + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        repaired = _repair_truncated_json(candidate)
        if repaired is not None:
            return repaired

    return None


def _repair_truncated_json(s):
    """Try to coerce a truncated/malformed JSON string into a valid object.

    Approach:
      1. Try simple suffix completions (close strings, brackets).
      2. If that fails, walk back from the end until we find a clean break
         (a comma or closing brace) and try closing from there.
    """
    if not s:
        return None

    # Quick suffix attempts — common truncation patterns
    suffixes = [
        '', '"', '"}', '"]', '}', ']',
        '"}}', '"]}', '"]}}', '}]}',
        '\n}', '\n]', '\n]}',
    ]
    for suffix in suffixes:
        try:
            return json.loads(s + suffix)
        except json.JSONDecodeError:
            continue

    # Walk back from the end to find a position where truncation looks clean
    for cut in range(len(s) - 1, 0, -1):
        c = s[cut]
        if c in ',}\n':
            # Cut here, drop trailing comma if any, then try closing
            candidate = s[:cut].rstrip().rstrip(',')
            for closer in ['}', '}}', '}]}', '"}']:
                try:
                    return json.loads(candidate + closer)
                except json.JSONDecodeError:
                    continue

    return None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
VERTEX_SA_KEY_PATH = os.path.join(PROJECT_ROOT, "dashboard", "gcp-key.json")

INPUT_DIR = os.path.join(SCRIPT_DIR, "competition_data", "extracted", "avg")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "competition_data", "patch_avg_weak_gemini3")

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
            encoding="utf-8", errors="replace",
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
            encoding="utf-8", errors="replace",
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
            capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace",
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

    if result.stdout is None:
        # Subprocess crashed during decode (rare on Windows when codec mismatches)
        if attempt < 2:
            time.sleep(5 * (2 ** attempt))
            return gemini_search(prompt, attempt + 1)
        return {"text": "", "error": "subprocess returned no stdout"}

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

def build_prompt(competition_id, competition_name, official_url, source_url,
                 organizer, mode):
    return f"""Search the web thoroughly to find 6 details for this competition.

Competition ID: {competition_id}
Competition name: {competition_name}
Organizer: {organizer or 'unknown'}
Mode: {mode or 'unknown'}
Official URL: {official_url}
Source URL: {source_url}

SEARCH STRATEGY:
- ALWAYS check the official URL above FIRST. Prefer information from the
  official site (and its sub-pages like /about, /rules, /prizes, /faq,
  /how-to-apply) over aggregator sites. If the official site has the info,
  use it even if aggregators say something different.
- Run MULTIPLE Google searches with different queries:
  1. "{competition_name} 2026 deadline registration"
  2. "{competition_name} 2026 prizes rewards"
  3. "{competition_name} how to apply submission"
  4. "{competition_name} team size members"
  5. "{competition_name} 2026 last date"
- Check: official URL, news articles, aggregator sites (physicswallah, careers360,
  scholarshipsinindia, vikaspedia, unstop, devpost), student blogs, school blogs,
  edtech portals, social-media announcement posts.
- If first search returns nothing, try alternate competition names
  (acronyms, abbreviations, or with the organizer name appended).

Find these 6 fields for the 2026-27 edition (current/upcoming):

1. deadline: Last date to register/submit. Format: "YYYY-MM-DD".
   Page may say "12th May, 2026" -> "2026-05-12".
   Page may say "Last Date of Registration: 12-05-2026" -> "2026-05-12".
   PREFER 2026-27 dates. If only old dates found, return null.
   Search aggressively — deadlines are almost always mentioned somewhere.

2. team_size: How many students per team. Examples:
   - "Solo / Individual" (1 person)
   - "1-4 members"
   - "Teams of 2-5"
   - "Pair (2 students)"
   - "Up to 6 members"
   Keep it concise. If unspecified, return null.

3. how_to_apply: Step-by-step registration process. Examples:
   - "Visit the official website, click 'Register', fill the form, pay the
     entry fee (if any), and submit before the deadline."
   - "Register through your school coordinator. Schools must be registered
     with the organizer first."
   - "Sign up on Devpost, join the hackathon, form a team, and submit project."
   Keep it 1-3 sentences.

4. prizes_detail: Full prize breakdown — ALL tiers, INCLUDING non-cash prizes:
   - Cash amounts at each rank (1st, 2nd, 3rd, runners-up, etc.)
   - Non-cash: mentorship, internships, trophies, certificates, trips,
     scholarships, training, free courses, hardware, software credits,
     publication opportunities, conference travel, university admission boost.
   Write as a comprehensive sentence or two. DO NOT skip non-cash prizes.

5. prize_amount: Top prize summary as a SINGLE STRING. The string should
   reflect WHAT the winner gets — even if it's not cash:
   - "$10,000 cash" (cash only)
   - "Apple AirPods + 6-month mentorship" (non-cash)
   - "Rs 1 Lakh + summer internship at IIT" (mixed)
   - "Trophy + certificate + invited talk at conference" (no cash, only opportunity)
   - "Trip to NASA HQ + scholarship" (opportunities, not money)
   IMPORTANT: If the competition has NO cash prize but offers opportunities
   (internship, conference, mentorship, trip, free course), STILL fill this
   field with those non-cash rewards. Don't return null just because there's
   no money.

6. submission_format: What participants must submit. Examples:
   - "5-minute video pitch + GitHub repo link + 1-page abstract"
   - "Original essay (1500-2000 words) in PDF format"
   - "Working prototype + technical report"
   - "Online quiz + final project"
   - "Painting / artwork uploaded as JPG (max 5MB)"
   Be specific about file types, length, and components if mentioned.

RULES:
- Only include information you found in search results. Do NOT fabricate.
- For deadline: always convert to YYYY-MM-DD format.
- If one field has data but others don't, still return what you found
  (use null for missing fields).
- Don't give up after one search — try 3-5 different query variations.
- Keep values concise — fields will be used in a student-facing UI.
- prize_amount should NEVER be null if there's ANY reward (even non-cash).

Return your answer in this JSON format (inside ```json``` code block):

```json
{{
  "competition_id": "{competition_id}",
  "deadline": "<YYYY-MM-DD or null>",
  "team_size": "<text or null>",
  "how_to_apply": "<text or null>",
  "prizes_detail": "<text or null>",
  "prize_amount": "<text or null>",
  "submission_format": "<text or null>",
  "sources": ["<URL1>", "<URL2>", "<URL3>"]
}}
```
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def _first_truthy(d, *keys):
    """Return the first key's value in d that is non-empty/non-null."""
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return ""


def main():
    parser = argparse.ArgumentParser(description="Patch AVG-tier weak fields via Gemini 3.0 web search")
    parser.add_argument("--max", type=int, help="Process at most N records")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip records whose output JSON already exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be searched, no API calls")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.json")))
    if args.max:
        files = files[:args.max]

    print(f"Processing {len(files)} AVG-tier competitions via Gemini 3.0 web search")
    print(f"Model:  {GEMINI_MODEL}")
    print(f"Input:  {INPUT_DIR}")
    print(f"Output: {OUTPUT_DIR}\n")

    saved = 0
    for i, f in enumerate(files):
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        cid = data.get("competition_id", os.path.basename(f).replace(".json", ""))
        # AVG files use activity_name + official_website; some older formats use
        # competition_name + official_url. Support both.
        name = _first_truthy(data, "activity_name", "competition_name") or cid
        official_url = _first_truthy(data, "official_website", "official_url")
        source_url = data.get("source_url", "")
        organizer = data.get("organizer", "")
        mode = data.get("mode", "")

        out_file = os.path.join(OUTPUT_DIR, f"{cid}.json")

        if args.skip_existing and os.path.exists(out_file):
            print(f"  [{i+1}/{len(files)}] {cid} — exists, skipping")
            continue

        print(f"\n  [{i+1}/{len(files)}] {cid}")
        print(f"    Name: {name}")

        if args.dry_run:
            print(f"    [DRY RUN] Would search for: deadline, team_size, "
                  f"how_to_apply, prizes_detail, prize_amount, submission_format")
            continue

        prompt = build_prompt(cid, name, official_url, source_url, organizer, mode)
        result = gemini_search(prompt)

        if result.get("error"):
            print(f"    [ERROR] {result['error']}")
            continue

        raw_text = result["text"]

        # Extract JSON from response — multiple strategies for robustness
        extracted = extract_json_robust(raw_text)

        if extracted is None:
            print(f"    [PARSE ERROR] {raw_text[:200]}")
            continue

        # Defensive mojibake cleanup on every string field — protects against
        # cases where Gemini itself echoes mojibake'd text from a misencoded
        # source page, even though our subprocess decode is now UTF-8.
        extracted = clean_mojibake_dict(extracted)

        # Print summary of what got filled
        print(f"    deadline:          {extracted.get('deadline')}")
        print(f"    team_size:         {str(extracted.get('team_size') or '')[:60]}")
        print(f"    how_to_apply:      {str(extracted.get('how_to_apply') or '')[:60]}")
        print(f"    prize_amount:      {str(extracted.get('prize_amount') or '')[:60]}")
        print(f"    prizes_detail:     {str(extracted.get('prizes_detail') or '')[:60]}")
        print(f"    submission_format: {str(extracted.get('submission_format') or '')[:60]}")

        # Save
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(extracted, fh, indent=2, ensure_ascii=False)
        saved += 1

        # Throttle to avoid rate limits — Gemini 3.0 may need slightly more
        # breathing room than 2.5 Flash; tune via --max during testing.
        if i < len(files) - 1:
            time.sleep(3)

    print(f"\n{'='*60}")
    print(f"  Done: {saved} records saved to {OUTPUT_DIR}/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
