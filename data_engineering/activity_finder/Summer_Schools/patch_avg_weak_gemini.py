#!/usr/bin/env python3
"""
patch_avg_weak_gemini.py — Use Gemini 3 Flash web search to fill 9 weak fields
==============================================================================

For each of the 20 AVG-tier summer-school files (deep research 50-79% fill),
asks Gemini 3 Flash (with Google Search grounding) to find these 9 fields:

  - funding_status         (Self-funded / Scholarship-based / Free / Mixed)
  - duration               (1 week / 4 weeks / Summer 2026 etc.)
  - how_to_apply           (steps to apply)
  - curriculum             (what students study — courses, projects)
  - college_credit         (Yes / No / Optional / Transferable)
  - deadline               (application deadline, YYYY-MM-DD)
  - program_dates          (when the program runs — Jun 15 - Jul 12, 2026)
  - subjects_offered       (LIST of subjects/tracks)
  - residential_or_online  (Residential / Online / Hybrid / Day program)

Cloned from Competitions/patch_avg_weak_gemini.py with adaptations for
summer-school programs.

Input:  summer_school_data/extracted/avg/*.json (20 files)
Output: summer_school_data/patch_avg_weak_gemini3/*.json

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
    ("â‚¹", "₹"),
    ("â\x82¬", "€"),
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

INPUT_DIR = os.path.join(SCRIPT_DIR, "summer_school_data", "extracted", "avg")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "summer_school_data", "patch_avg_weak_gemini3")

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

def build_prompt(program_id, program_name, institution_name,
                 application_url, mode, location_tag):
    return f"""Search the web thoroughly to find 9 details for this summer school / pre-college program.

Program ID: {program_id}
Program name: {program_name}
Institution: {institution_name or 'unknown'}
Mode: {mode or 'unknown'}
Location: {location_tag or 'unknown'}
Application URL: {application_url}

SEARCH STRATEGY:
- ALWAYS check the application URL above FIRST. Prefer information from the
  institution's official site (and its sub-pages like /about, /apply, /admissions,
  /tuition, /faq, /dates, /curriculum, /financial-aid, /scholarships) over
  aggregator sites. If the official site has the info, use it even if
  aggregators say something different.
- Run MULTIPLE Google searches with different queries:
  1. "{program_name} 2026 dates application deadline"
  2. "{program_name} {institution_name or ''} curriculum subjects courses"
  3. "{program_name} cost tuition fee scholarship financial aid"
  4. "{program_name} duration weeks residential online"
  5. "{program_name} college credit university credit transferable"
  6. "{institution_name or ''} pre-college summer program 2026"
- Check: official URL, the institution's pre-college / summer programs page,
  news articles, aggregator sites (crimson education, summerprograms,
  thesummerinstitute, idealist, prepscholar, collegevine), high-school
  counseling blogs, social media posts.
- If first search returns nothing, try alternate phrasings (acronyms,
  abbreviations, year variations).

═══════════════════════════════════════════════════════════════════════════
CRITICAL GUARDRAIL — DO NOT MIX CONTENT ACROSS PROGRAMS
═══════════════════════════════════════════════════════════════════════════

Many institutions offer MULTIPLE different summer programs (e.g., Columbia
has CSPA Journalism, Pre-College Liberal Arts, Summer Immersion, etc.). And
aggregator sites bundle many programs from many institutions on the same page.

⚠️ Before extracting any field:
  1. Verify the page describes **{institution_name}**'s "{program_name}"
     specifically — NOT a different program at the same institution, and NOT
     a program at a different institution.
  2. If you find content for a SIMILAR but DIFFERENT program (e.g., the
     residential vs online version, or a different track), do NOT mix details.
  3. NEVER pull dates, tuition, or curriculum from one program into another.

✅ Good check: does this fact match {program_name} specifically?
✅ When in doubt, prefer null over content from the wrong program.

Find these 9 fields for the 2026 (current/upcoming) edition:

1. funding_status: How students pay. One of:
   - "Self-funded" (full tuition, students pay)
   - "Scholarship-based" (need-based / merit-based aid available, sometimes free)
   - "Free" (no cost — fully sponsored, e.g., MITES, Summer Springboard scholarships)
   - "Mixed" (tuition with available scholarships)
   Pick ONE based on the dominant funding model.

2. duration: How long the program runs. Examples:
   - "1 week"
   - "2 weeks (per session)"
   - "3 weeks"
   - "6 weeks"
   - "Summer (Jun-Aug)"
   - "Self-paced (4-8 weeks)"
   Be concise.

3. how_to_apply: Step-by-step application process. Examples:
   - "Submit online application via the Common App (or institution portal)
     including transcript, 2 recommendations, and personal essay. Application
     fee is $XX, fee waivers available."
   - "Fill out the inquiry form on the website, complete a video interview,
     and submit a portfolio of 5 writing samples."
   Aim for 1-3 sentences.

4. curriculum: What students study. Examples:
   - "Students take 2 college-level courses (3 credits each) chosen from
     20+ subjects including Computer Science, Literature, Economics, and Biology."
   - "Hands-on STEM projects in robotics, programming, and engineering design.
     Daily labs and weekend field trips to research labs."
   - "Workshops on reporting, writing, photography, and editorial leadership.
     Students produce a final publication portfolio."
   Aim for 2-3 sentences capturing the academic content.

5. college_credit: Whether students earn transferable college credit. One of:
   - "Yes" (formal college credit awarded)
   - "No" (purely enrichment, no credit)
   - "Optional" (credit available for an extra fee or specific track)
   - "Transferable" (credit awarded and accepted by other universities)
   Pick the most accurate option. Most pre-college programs give a transcript
   but not always transferable credit.

6. deadline: Application deadline. Format: "YYYY-MM-DD".
   - "March 15, 2026" -> "2026-03-15"
   - For rolling admissions, return "Rolling"
   - If 2026 deadline not announced, return null.

7. program_dates: When the program runs (NOT the application deadline).
   Examples:
   - "June 15 - July 12, 2026"
   - "July 1 - 14 OR July 15 - 28, 2026 (two sessions)"
   - "Summer 2026 (multiple session dates)"
   Keep it to 1-2 sentences if there are multiple sessions.

8. subjects_offered: A LIST of subjects/tracks/courses available. Examples:
   ["Computer Science", "Engineering", "Mathematics", "Biology"]
   ["Journalism", "Reporting", "Writing", "Editorial Leadership"]
   ["Creative Writing", "Poetry", "Fiction", "Memoir"]
   - Include all major tracks/subjects mentioned.
   - Return at least 3-8 subjects if available.

9. residential_or_online: How students participate. One of:
   - "Residential" (students live on campus)
   - "Online" (fully virtual)
   - "Hybrid" (some online + some in-person)
   - "Day program" (commute daily, no overnight)
   - "Residential and Online" (program offers BOTH options as separate tracks)

RULES:
- Only include information you found in search results. Do NOT fabricate.
- For deadline: convert to YYYY-MM-DD format (or "Rolling" or null).
- subjects_offered MUST be a LIST of strings, NOT a single string.
- If a field has no info, return null (or empty list [] for subjects).
- Don't give up after one search — try 3-5 different query variations.
- Keep values concise — fields will be used in a student-facing UI.

Return your answer in this JSON format (inside ```json``` code block):

```json
{{
  "program_id": "{program_id}",
  "funding_status": "<Self-funded | Scholarship-based | Free | Mixed | null>",
  "duration": "<text or null>",
  "how_to_apply": "<text or null>",
  "curriculum": "<text or null>",
  "college_credit": "<Yes | No | Optional | Transferable | null>",
  "deadline": "<YYYY-MM-DD | Rolling | null>",
  "program_dates": "<text or null>",
  "subjects_offered": ["subject1", "subject2", "subject3"],
  "residential_or_online": "<Residential | Online | Hybrid | Day program | Residential and Online | null>",
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
    parser = argparse.ArgumentParser(description="Patch AVG-tier summer school weak fields via Gemini 3 Flash web search")
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

    print(f"Processing {len(files)} AVG-tier summer schools via Gemini 3 Flash")
    print(f"Model:  {GEMINI_MODEL}")
    print(f"Input:  {INPUT_DIR}")
    print(f"Output: {OUTPUT_DIR}\n")

    saved = 0
    for i, f in enumerate(files):
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        pid = data.get("program_id", os.path.basename(f).replace(".json", ""))
        program_name = _first_truthy(data, "program_name", "title", "name") or pid
        institution_name = data.get("institution_name", "")
        application_url = data.get("application_url", "")
        mode = data.get("mode", "")
        location_tag = data.get("location_tag", "")

        out_file = os.path.join(OUTPUT_DIR, f"{pid}.json")

        if args.skip_existing and os.path.exists(out_file):
            print(f"  [{i+1}/{len(files)}] {pid} — exists, skipping")
            continue

        print(f"\n  [{i+1}/{len(files)}] {pid}")
        print(f"    Program:     {program_name}")
        print(f"    Institution: {institution_name}")

        if args.dry_run:
            print(f"    [DRY RUN] Would search for: funding_status, duration, "
                  f"how_to_apply, curriculum, college_credit, deadline, "
                  f"program_dates, subjects_offered, residential_or_online")
            continue

        prompt = build_prompt(pid, program_name, institution_name,
                              application_url, mode, location_tag)
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
        subjects = extracted.get('subjects_offered') or []
        subjects_preview = ", ".join(subjects[:5]) if isinstance(subjects, list) else str(subjects)
        print(f"    funding_status:        {extracted.get('funding_status')}")
        print(f"    duration:              {extracted.get('duration')}")
        print(f"    college_credit:        {extracted.get('college_credit')}")
        print(f"    deadline:              {extracted.get('deadline')}")
        print(f"    program_dates:         {str(extracted.get('program_dates') or '')[:60]}")
        print(f"    residential_or_online: {extracted.get('residential_or_online')}")
        print(f"    subjects_offered:      [{subjects_preview}{'...' if isinstance(subjects, list) and len(subjects) > 5 else ''}]")

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
