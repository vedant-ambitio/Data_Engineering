#!/usr/bin/env python3
"""
patch_avg_weak_gemini.py — Use Gemini 3 Flash web search to fill 5 weak fields
==============================================================================

For each of the 30 AVG-tier volunteering files (deep research 50-79% fill),
asks Gemini 3 Flash (with Google Search grounding) to find these 5 fields:

  - mode             (Online / Offline / Hybrid)
  - eligibility_text (who can volunteer — age, citizenship, skills, location)
  - minimum_hours    (commitment in hours — "2 hrs/week", "20 hrs total", etc.)
  - commitment_type  (one-time / recurring / full-time / part-time / weekend)
  - responsibilities (what volunteers actually do day-to-day)

Cloned from Competitions/patch_avg_weak_gemini.py with adaptations:
  - Targets 5 volunteering-specific fields (was 6 competition fields)
  - Reads from Volunteering/volunteering_data/extracted/avg/ (30 files)
  - Writes to Volunteering/volunteering_data/patch_avg_weak_gemini3/ (fresh)
  - Uses opportunity_id, program_name, organization_name, application_url

Input:  volunteering_data/extracted/avg/*.json (30 files)
Output: volunteering_data/patch_avg_weak_gemini3/*.json

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

GEMINI_MODEL = "gemini-3-flash-preview"
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"
MAX_OUTPUT_TOKENS = 16000


# ══════════════════════════════════════════════════════════════════════════════
#  MOJIBAKE CLEANUP
# ══════════════════════════════════════════════════════════════════════════════
_MOJIBAKE_PAIRS = [
    ("â‚¹", "₹"),     # ₹ Indian rupee
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
    ("â€™", "'"),
    ("â€œ", '"'),
    ("â€\x9d", '"'),
    ("â€”", "—"),
    ("â€“", "–"),
    ("â€¦", "…"),
]


def clean_mojibake(s):
    if not isinstance(s, str):
        return s
    for bad, good in _MOJIBAKE_PAIRS:
        if bad in s:
            s = s.replace(bad, good)
    return s


def clean_mojibake_dict(d):
    if isinstance(d, dict):
        return {k: clean_mojibake_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [clean_mojibake_dict(v) for v in d]
    if isinstance(d, str):
        return clean_mojibake(d)
    return d


# ══════════════════════════════════════════════════════════════════════════════
#  ROBUST JSON EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_json_robust(text):
    if not text:
        return None
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
    if not s:
        return None
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
    for cut in range(len(s) - 1, 0, -1):
        c = s[cut]
        if c in ',}\n':
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

INPUT_DIR = os.path.join(SCRIPT_DIR, "volunteering_data", "extracted", "avg")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "volunteering_data", "patch_avg_weak_gemini3")

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
#  GEMINI CALL
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

def build_prompt(opportunity_id, program_name, organization_name,
                 application_url, cause_area):
    return f"""Search the web thoroughly to find 5 details for this volunteering opportunity.

Opportunity ID: {opportunity_id}
Program name: {program_name}
Organization: {organization_name or 'unknown'}
Cause area: {cause_area or 'unknown'}
Application URL: {application_url}

SEARCH STRATEGY:
- ALWAYS check the application URL above FIRST. Prefer information from the
  organization's official site (and its sub-pages like /volunteer, /get-involved,
  /opportunities, /faq, /how-it-works, /apply) over aggregator sites. If the
  official site has the info, use it even if aggregators say something different.
- Run MULTIPLE Google searches with different queries:
  1. "{organization_name or ''} volunteer mode online offline"
  2. "{organization_name or ''} {program_name} eligibility age requirements"
  3. "{organization_name or ''} {program_name} hours commitment time"
  4. "{organization_name or ''} {program_name} volunteer responsibilities"
  5. "{organization_name or ''} volunteer one-time recurring weekly"
- Check: official URL, the organization's own site, news articles, aggregator
  sites (volunteermatch, idealist, justgiving, ivolunteer, connectfor, ngobox,
  betterindia, the-better-india), social-media announcement posts.
- If first search returns nothing, try alternate phrasings (NGO acronyms,
  full names, "{organization_name or ''} how to volunteer").

═══════════════════════════════════════════════════════════════════════════
CRITICAL GUARDRAIL — DO NOT MIX CONTENT ACROSS PROGRAMS
═══════════════════════════════════════════════════════════════════════════

Aggregator sites (volunteermatch, idealist, ivolunteer, connectfor, ngobox,
lawctopus, internshala, etc.) host MANY different programs from MANY different
organizations on the SAME domain.

⚠️ If your search results land on an aggregator page:
  1. FIRST verify: does this page primarily describe **{organization_name}**'s
     program "{program_name}"?
  2. If the aggregator describes MULTIPLE different programs, you MUST
     extract content ONLY from the section that matches **{organization_name}**
     and "{program_name}".
  3. NEVER pull responsibilities, eligibility, or hours from a program run
     by a DIFFERENT organization, even if it's mentioned on the same page.

✅ Good check: before writing each field, ask yourself "is this fact
   specifically about {organization_name}'s {program_name}, or about a
   different program?"

✅ When in doubt, prefer null over content from the wrong organization.

Find these 5 fields for the CURRENT/UPCOMING opportunity:

1. mode: How volunteering is performed. One of:
   - "Online" (fully remote — virtual mentoring, online content creation, etc.)
   - "Offline" (in-person at a location — animal shelter, school, NGO office)
   - "Hybrid" (mix of online + offline activities)
   Pick the SINGLE most accurate option for this specific program.

2. eligibility_text: Who is allowed to volunteer. Cover ALL relevant constraints:
   - Age requirement (e.g., "Open to anyone 18+", "Students aged 13-17")
   - Citizenship / location (e.g., "Indian citizens only", "Open globally")
   - Skills / background (e.g., "Teaching experience preferred", "Comfortable
     with English")
   - Time commitment minimums (e.g., "Must commit minimum 2 months")
   - Any other restrictions mentioned
   Write 2-4 sentences. If completely unspecified, return null.

3. minimum_hours: The minimum time commitment expected. Examples:
   - "2 hours per week"
   - "4 hours per session, weekly"
   - "20 hours total over the program"
   - "Full-day (8+ hours) on event day"
   - "Flexible — no minimum"
   Be specific about the cadence (per week / per month / total / per session).

4. commitment_type: How the volunteering is structured. One of:
   - "One-time" (single event or session)
   - "Recurring" (regular sessions over weeks/months)
   - "Full-time" (intensive, 30+ hrs/week for a fixed period)
   - "Part-time" (weekday afternoons, ~10-20 hrs/week)
   - "Weekend" (Saturday/Sunday only)
   - "Flexible" (volunteer chooses their own schedule)
   Pick the SINGLE most accurate option.

5. responsibilities: Day-to-day tasks the volunteer performs. Examples:
   - "Tutor underprivileged children in math and English for 2 hours per
     session, prepare lesson plans, and submit weekly progress reports."
   - "Help walk shelter dogs, assist with feeding, clean kennels, and
     support adoption events on weekends."
   - "Create awareness content for social media, design posters for
     campaigns, and manage the organization's online community."
   Aim for 1-3 sentences capturing the main work.

RULES:
- Only include information you found in search results. Do NOT fabricate.
- For mode: pick ONE — Online, Offline, or Hybrid.
- For commitment_type: pick ONE from the list above.
- If a field has no info, return null (or empty string for text).
- Don't give up after one search — try 3-5 different query variations.
- Keep values concise — fields will be used in a student-facing UI.

Return your answer in this JSON format (inside ```json``` code block):

```json
{{
  "opportunity_id": "{opportunity_id}",
  "mode": "<Online | Offline | Hybrid | null>",
  "eligibility_text": "<text or null>",
  "minimum_hours": "<text or null>",
  "commitment_type": "<One-time | Recurring | Full-time | Part-time | Weekend | Flexible | null>",
  "responsibilities": "<text or null>",
  "sources": ["<URL1>", "<URL2>", "<URL3>"]
}}
```
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def _first_truthy(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return ""


def main():
    parser = argparse.ArgumentParser(description="Patch AVG-tier volunteering weak fields via Gemini 3 Flash web search")
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

    print(f"Processing {len(files)} AVG-tier volunteering opportunities via Gemini 3 Flash")
    print(f"Model:  {GEMINI_MODEL}")
    print(f"Input:  {INPUT_DIR}")
    print(f"Output: {OUTPUT_DIR}\n")

    saved = 0
    for i, f in enumerate(files):
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        oid = data.get("opportunity_id", os.path.basename(f).replace(".json", ""))
        program_name = _first_truthy(data, "program_name", "title", "name") or oid
        org_name = data.get("organization_name", "")
        application_url = data.get("application_url", "")
        cause_area = data.get("cause_area", "")

        out_file = os.path.join(OUTPUT_DIR, f"{oid}.json")

        if args.skip_existing and os.path.exists(out_file):
            print(f"  [{i+1}/{len(files)}] {oid} — exists, skipping")
            continue

        print(f"\n  [{i+1}/{len(files)}] {oid}")
        print(f"    Program:      {program_name}")
        print(f"    Organization: {org_name}")

        if args.dry_run:
            print(f"    [DRY RUN] Would search for: mode, eligibility_text, "
                  f"minimum_hours, commitment_type, responsibilities")
            continue

        prompt = build_prompt(oid, program_name, org_name, application_url, cause_area)
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

        # Defensive mojibake cleanup
        extracted = clean_mojibake_dict(extracted)

        # Print summary of what got filled
        print(f"    mode:             {extracted.get('mode')}")
        print(f"    eligibility_text: {str(extracted.get('eligibility_text') or '')[:60]}")
        print(f"    minimum_hours:    {extracted.get('minimum_hours')}")
        print(f"    commitment_type:  {extracted.get('commitment_type')}")
        print(f"    responsibilities: {str(extracted.get('responsibilities') or '')[:60]}")

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
