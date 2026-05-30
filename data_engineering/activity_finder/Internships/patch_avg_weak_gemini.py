#!/usr/bin/env python3
"""
patch_avg_weak_gemini.py — Use Gemini 3 Flash web search to fill 4 weak fields
==============================================================================

For each of the 17 AVG-tier internship files (deep research 50-79% fill),
asks Gemini 3 Flash (with Google Search grounding) to find these 4 fields:

  - posted_date       (when was the internship listing posted)
  - duration          (how long the internship runs — 3 months, summer, etc.)
  - responsibilities  (day-to-day tasks the intern will do)
  - skills_required   (list of skills needed — Python, writing, research, etc.)

Unlike browser_extract.py, this does NOT scrape any page. Gemini searches the
web itself and returns structured answers.

Cloned from Competitions/patch_avg_weak_gemini.py with these adaptations:
  - Targets 4 internship-specific fields (was 6 competition fields)
  - Reads from Internships/internship_data/extracted/avg/ (17 files)
  - Writes to Internships/internship_data/patch_avg_weak_gemini3/ (fresh folder)
  - Uses internship_id, internship_title, company_name, application_url
  - skills_required is a LIST output, not a string

Input:  internship_data/extracted/avg/*.json (17 files)
Output: internship_data/patch_avg_weak_gemini3/*.json

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
#  MOJIBAKE CLEANUP — fixes â‚¹ -> ₹ etc. when subprocess output gets mis-decoded
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
    ("â€™", "'"),   # right single quote
    ("â€œ", '"'),   # left double quote
    ("â€\x9d", '"'),     # right double quote
    ("â€”", "—"),   # em dash
    ("â€“", "–"),   # en dash
    ("â€¦", "…"),        # ellipsis
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
#  ROBUST JSON EXTRACTION — handles ```json``` blocks, truncated output, plain JSON
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

INPUT_DIR = os.path.join(SCRIPT_DIR, "internship_data", "extracted", "avg")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "internship_data", "patch_avg_weak_gemini3")

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
#  GEMINI CALL (web-grounded)
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

def build_prompt(internship_id, internship_title, company_name, application_url,
                 domain, mode):
    return f"""Search the web thoroughly to find 4 details for this internship listing.

Internship ID: {internship_id}
Internship title: {internship_title}
Company: {company_name or 'unknown'}
Domain: {domain or 'unknown'}
Mode: {mode or 'unknown'}
Application URL: {application_url}

SEARCH STRATEGY:
- ALWAYS check the application URL above FIRST. Prefer information from the
  official company / internship listing page (and its sub-pages like /about,
  /careers, /internships, /jobs, /opportunities) over aggregator sites. If
  the official site has the info, use it even if aggregators say something
  different.
- Run MULTIPLE Google searches with different queries:
  1. "{internship_title} {company_name or ''} duration"
  2. "{internship_title} {company_name or ''} responsibilities"
  3. "{internship_title} {company_name or ''} skills required"
  4. "{internship_title} {company_name or ''} posted date apply"
  5. "{company_name or ''} internship 2026 application open"
- Check: official URL, the company's careers page, news articles, aggregator
  sites (internshala, linkedin, indeed, naukri, glassdoor, weekday, devpost,
  unstop, lawctopus), GitHub repos (for tech internships), and announcement
  posts on social media (Twitter/X, LinkedIn).
- If first search returns nothing, try alternate phrasings (acronyms,
  abbreviations, year variations, "{company_name or ''} internship 2026").

═══════════════════════════════════════════════════════════════════════════
CRITICAL GUARDRAIL — DO NOT MIX CONTENT ACROSS PROGRAMS
═══════════════════════════════════════════════════════════════════════════

Aggregator sites (lawctopus.com, internshala.com, contest360.in, careers360,
unstop, etc.) host MANY different programs from MANY different organizations
on the SAME domain. They also OFTEN list their own paid courses or training
programs alongside opportunities they're announcing for OTHER organizations.

⚠️ If your search results land on an aggregator page:
  1. FIRST verify: does this page primarily describe **{company_name}**'s
     internship "{internship_title}", or is it primarily about a different
     organization's program?
  2. If the aggregator describes MULTIPLE different programs (e.g. one
     paragraph about CRY's YCC internship AND another paragraph about
     Lawctopus's own legal training course), you MUST extract content ONLY
     from the section that matches **{company_name}** and "{internship_title}".
  3. NEVER pull responsibilities, duration, or skills from a program run by
     a DIFFERENT organization, even if it's mentioned on the same page.
  4. NEVER cite an aggregator's own training program as if it were the
     internship listing — they're separate things.

✅ Good check: before writing each field, ask yourself "is this fact
   specifically about {company_name}'s {internship_title}, or about a
   different program?"

✅ When in doubt, prefer null over content from the wrong organization.
   A null field is recoverable; wrong-program content is silently incorrect.

Find these 4 fields for the CURRENT/UPCOMING listing:

1. posted_date: When was the internship LISTING posted/announced (NOT the
   internship start date). Format: "YYYY-MM-DD".
   - Look for "Posted on March 15, 2026" -> "2026-03-15"
   - "Listed 2 weeks ago" on the page (search the snapshot date and back-calc)
   - Job board pages usually show this prominently
   - PREFER 2026 dates. If only old dates found, return null.

2. duration: How long the internship runs. Examples:
   - "3 months"
   - "6 weeks (Summer 2026)"
   - "12 weeks (Jun-Aug 2026)"
   - "Flexible, 2-6 months"
   - "Ongoing / Open-ended"
   Keep concise. Mention start/end dates if known.

3. responsibilities: Day-to-day tasks the intern will perform. Examples:
   - "Conduct user research, analyze customer feedback, build prototypes
     for new features, and present findings to the product team."
   - "Write articles for the magazine, fact-check stories, attend editorial
     meetings, and assist senior reporters with research."
   - "Develop and maintain backend services in Python, write unit tests,
     participate in code reviews, and ship features to production."
   Aim for 1-3 sentences capturing the main work.

4. skills_required: A LIST of specific skills the intern needs. Examples:
   ["Python", "SQL", "Data analysis", "Pandas", "Machine learning"]
   ["Excellent writing", "Research", "Editing", "Attention to detail"]
   ["Figma", "User research", "Wireframing", "Adobe Creative Suite"]
   - Include both hard skills (tools, languages) and soft skills (communication,
     research) when mentioned.
   - If the listing mentions "preferred but not required" skills, include them.
   - Return at least 3-7 skills if you can find them.

RULES:
- Only include information you found in search results. Do NOT fabricate.
- For posted_date: always convert to YYYY-MM-DD format.
- skills_required MUST be a list of strings, NOT a single string.
- If one field has data but others don't, still return what you found
  (use null for missing fields, or empty list [] for skills if none found).
- Don't give up after one search — try 3-5 different query variations.
- Keep values concise — fields will be used in a student-facing UI.

Return your answer in this JSON format (inside ```json``` code block):

```json
{{
  "internship_id": "{internship_id}",
  "posted_date": "<YYYY-MM-DD or null>",
  "duration": "<text or null>",
  "responsibilities": "<text or null>",
  "skills_required": ["skill1", "skill2", "skill3"],
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
    parser = argparse.ArgumentParser(description="Patch AVG-tier internship weak fields via Gemini 3 Flash web search")
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

    print(f"Processing {len(files)} AVG-tier internships via Gemini 3 Flash web search")
    print(f"Model:  {GEMINI_MODEL}")
    print(f"Input:  {INPUT_DIR}")
    print(f"Output: {OUTPUT_DIR}\n")

    saved = 0
    for i, f in enumerate(files):
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        iid = data.get("internship_id", os.path.basename(f).replace(".json", ""))
        title = _first_truthy(data, "internship_title", "title") or iid
        company = data.get("company_name", "")
        application_url = data.get("application_url", "")
        domain = data.get("domain", "")
        mode = data.get("mode", "")

        out_file = os.path.join(OUTPUT_DIR, f"{iid}.json")

        if args.skip_existing and os.path.exists(out_file):
            print(f"  [{i+1}/{len(files)}] {iid} — exists, skipping")
            continue

        print(f"\n  [{i+1}/{len(files)}] {iid}")
        print(f"    Title:   {title}")
        print(f"    Company: {company}")

        if args.dry_run:
            print(f"    [DRY RUN] Would search for: posted_date, duration, "
                  f"responsibilities, skills_required")
            continue

        prompt = build_prompt(iid, title, company, application_url, domain, mode)
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
        skills = extracted.get('skills_required') or []
        skills_preview = ", ".join(skills[:5]) if isinstance(skills, list) else str(skills)
        print(f"    posted_date:      {extracted.get('posted_date')}")
        print(f"    duration:         {str(extracted.get('duration') or '')[:60]}")
        print(f"    responsibilities: {str(extracted.get('responsibilities') or '')[:60]}")
        print(f"    skills_required:  [{skills_preview}{'...' if isinstance(skills, list) and len(skills) > 5 else ''}]")

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
