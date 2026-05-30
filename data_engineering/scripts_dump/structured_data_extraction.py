#!/usr/bin/env python3
"""
Structured Data Extraction Test
================================
Reads existing scraped markdown files and uses Gemini to extract structured JSON
matching the new schema (cost_of_attendance, course_structure_data, deadline tags, etc.)

NO web search — purely transforms existing markdown into structured data.
Strict anti-hallucination: only extract what's present, null for missing.

Usage:
    python test_structured_extraction.py                    # 15 random programs
    python test_structured_extraction.py --max 5            # 5 random programs
    python test_structured_extraction.py --file path.md     # Single file
    python test_structured_extraction.py --workers 5        # Concurrent workers
"""

import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"
MAX_OUTPUT_TOKENS = 26000  # structured JSON is smaller than full markdown
MAX_WORKERS = 60  # Vertex AI handles this well; no web search = lighter calls

INPUT_DIR = "classification_results/ug"
OUTPUT_DIR = "university_data/structured_extraction_ug"

# Vertex AI service account key
VERTEX_SA_KEY_PATH = os.path.join(os.path.dirname(__file__), "dashboard", "gcp-key.json")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

USE_VERTEX = True

# Rate limiting
_rate_lock = threading.Lock()
_last_request_time = 0
MIN_REQUEST_GAP = 0.05  # no web search = faster, Vertex handles high concurrency

# Token cache
_token_cache = {"token": None, "timestamp": 0}
_token_lock = threading.Lock()
TOKEN_REFRESH_INTERVAL = 3000


# ── Auth (reused from run_gemini_scraper.py) ────────────────────────────────

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
        import hashlib
        import base64

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


# ── Gemini call (NO web search) ─────────────────────────────────────────────

def gemini_extract(prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.1, attempt=0):
    """Call Gemini WITHOUT web search — pure text-to-JSON extraction."""
    global _last_request_time

    with _rate_lock:
        now = time.time()
        wait = MIN_REQUEST_GAP - (now - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.time()

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
        # NO tools — no web search
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        }
    }

    # Write payload to temp file to avoid Windows command line length limit
    import tempfile
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
        # Fallback: extract the "text" field via regex from raw Vertex response
        import re
        text_match = re.search(r'"text"\s*:\s*"(.*)', body, re.DOTALL)
        if text_match:
            raw = text_match.group(1)
            # Find the JSON object inside the text value (unescape \n etc.)
            try:
                # The text field contains escaped JSON — unescape it
                unescaped = raw.encode().decode('unicode_escape')
                # Find the outermost { ... }
                brace_start = unescaped.index('{')
                depth = 0
                for i in range(brace_start, len(unescaped)):
                    if unescaped[i] == '{': depth += 1
                    elif unescaped[i] == '}': depth -= 1
                    if depth == 0:
                        return {"text": unescaped[brace_start:i+1]}
            except Exception:
                pass
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


# ── Extraction Prompt (level-aware) ────────────────────────────────────────

# ── SHARED RULES (all levels) ──────────────────────────────────────────────

_SHARED_RULES_HEADER = """You are a precise data extraction system. Your job is to read the markdown document below and extract structured JSON data from it.

## TODAY'S DATE: {today_date}
## PROGRAM LEVEL: {course_level}

## CRITICAL RULES:
1. **ONLY extract information explicitly stated in the document** — with specific exceptions listed in Rule 12.
2. **If information is not present, use null.** Never guess, infer, or fabricate.
3. **If the document says "Information not available", that field must be null.**
4. **Preserve original currencies and amounts exactly as stated.**
5. **For arrays, return empty [] if no items are found — never fabricate entries.**"""

_SHARED_RULE_7 = """
7. **NUMERIC RANGES:** If the document gives a range (e.g. "$19,904 to $28,074"), capture BOTH values. Use `per_year_min` and `per_year_max` for cost items. Do not silently drop one end of the range."""

_SHARED_RULE_8 = """
8. **INTAKE NAMING for Southern Hemisphere (Australia, NZ, South America):**
   - "Session 1" / "Semester 1" (Feb-Mar start) = use intake `"spring"` (their academic year start)
   - "Session 2" / "Semester 2" (Jul-Aug start) = use intake `"fall"`
   - "Mid-year intake" = use intake `"fall"`
   - When in doubt for Australian universities, Feb/Mar = `"spring"`, Jul/Aug = `"fall"`"""

_SHARED_RULE_9_DEADLINES = """
9. **DEADLINES — IMPORTANT:**
   - Extract ALL deadline dates mentioned in the document, even if labeled "for reference" or "for comparison".
   - If a deadline has month/day but no year, or mentions a past year (e.g. 2024, 2025), **extrapolate to the next upcoming cycle** relative to today's date. For example, if the document says "Fall Admission: February 14" with no year, use "2027-02-14" if today is March 2026 (since Feb 2026 has passed).
   - **RELATIVE DEADLINES:** If the document says something like "apply at least X months in advance" or "X months before enrollment", CALCULATE the actual date by subtracting from the known intake/enrollment date.
   - **DO NOT create deadline entries with null deadline_date** unless it's a rolling admission. If you can't determine any date (neither explicit nor calculable), skip that entry entirely.
   - **ONE entry per unique deadline.** Do not split the same deadline into separate domestic/international entries unless the document gives DIFFERENT dates for each. If one date applies to all applicants, use one entry with tags `["domestic", "international"]`. If international has a different (earlier) date, create a separate entry tagged `["international"]`.
   - **ROLLING ADMISSIONS:** If the document says admissions are rolling or continuous, create a single deadline entry with `tags: ["rolling"]`, `deadline_date: null`.
   - Do NOT skip deadlines just because the document says future-cycle-specific dates are "not available" — extract whatever dates ARE mentioned and extrapolate forward.
   - If ANY deadlines were extrapolated or calculated, set the top-level `"deadline_note"` field explaining what was done.
   - **DEDUPLICATION:** After extrapolation, check that no two deadline entries have the same intake + deadline_date + tags combination. If extrapolating a past date forward would create a duplicate of an already-present future entry, drop the extrapolated one.
   - **CONTEXT IN deadline_note:** Also include in `deadline_note` any useful context from the source that doesn't fit in the structured fields: application open dates, time specificity, program start dates, visa/immigration notes."""

_SHARED_RULE_10_FEE = """
10. **APPLICATION FEE:** If domestic and international fees differ, use the structure: `"application_fee_domestic": number, "application_fee_international": number`. If same for all, use `"application_fee": number`."""

_SHARED_RULE_13_COST = """
13. **COST — INTERNATIONAL STUDENT FOCUS:** Ambitio's primary audience is international students. Follow these rules:
   - `tuition_per_year`: ALWAYS fill this. If only domestic/international split exists, use the INTERNATIONAL tuition figure here. If a range exists, use the higher end (international is typically higher).
   - `tuition_domestic` / `tuition_international`: Fill when the document gives separate domestic vs international/out-of-state figures.
   - `total_credits`: Extract the numeric value from credits_required. If the program says "30 credits" or "90 ECTS" or "180 points", set total_credits to 30, 90, or 180 respectively. This must NOT be null when credits_required has a number.
   - `tuition_per_credit`: If the document gives per-credit cost, extract it. If it gives total tuition and total credits, you may CALCULATE per_credit = tuition / credits.
   - `total_program_cost`: Should make sense — e.g. for a 4-year program at $60,000/yr, total ~ $240,000. Prefer the university's own figure over third-party. If only third-party, include it but note the source in `notes`.
   - `cost_of_living_per_year`: If the document states it, use that. If NOT stated, you SHOULD estimate it from your knowledge of the city/country's cost of living for a student. Use a reasonable mid-range estimate. Mark the `notes` field to indicate "Cost of living estimated for [city]" when you do this.
   - `overall_cost_per_year`: ALWAYS calculate this as: tuition_per_year (international) + cost_of_living_per_year. If you have both values, compute the sum. Never leave this null when tuition and living cost are both available.
   - `health_insurance_fee`: If stated in document use it. If not stated, you MAY estimate mandatory student health insurance for international students if you know the country requires it.
   - Do NOT leave `tuition_per_year` null when you have international tuition data — copy it there."""

_SHARED_RULE_15_COPYWRITING = """
15. **COPYWRITING & FRONTEND QUALITY — ALL TEXT FIELDS:** Every text field in this JSON may be shown directly to prospective students on a website. Write accordingly:

   **TONE:** Confident, helpful, concise. Write as if advising a friend who's researching programs. Never sound like a data dump or extraction log.

   **NEVER SAY:**
   - "Information not available", "Not explicitly stated", "Not mentioned in the document"
   - "Extrapolated from...", "relative to today's date", "original dates had passed"
   - "Not required (university performs internal evaluation)" — just omit or say something useful
   - "Based on third-party source", "as official figures were not available"
   - Any meta-commentary about the extraction process or source quality

   **INSTEAD:**
   - If a field has nothing useful to say → use `null`, don't write filler text
   - `cost_of_attendance.notes`: Focus on what helps students plan. "Tuition covers teaching and lab access. Payment plans available." = good. "Figures derived from third-party source as official data was not available" = bad.

   **DEADLINE_NOTE — must be user-friendly:**
   - Write as helpful context for applicants, not an extraction log
   - GOOD: "Program starts September 2026. Applications typically open in August. Dates shown are based on the most recent admission cycle."
   - If deadlines were projected forward, say: "Dates shown are based on the most recent admission cycle and may be updated."

   **DEADLINE ORDERING in the deadlines array:**
   - Group by intake (fall → spring → summer)
   - Within each intake: application deadlines first, then administrative/visa deadlines last
   - Label consistently: "Application Deadline", "Priority Deadline", "Scholarship Deadline"

   **CAREER OUTCOMES description:** Write 1-2 sentences about career prospects. Include specifics when available.

   **OVERVIEW_DESCRIPTION:** Should flow as a compelling program summary a student would want to read. Lead with what makes the program distinctive.

   **POINTERS / WHY_STUDY_POINTS:** Each should be a punchy, standalone selling point. Not repetitive. Not generic filler."""


# ── MASTERS-SPECIFIC RULES ─────────────────────────────────────────────────

_MASTERS_RULE_6 = """
6. **CONDITIONAL vs REQUIRED:** If something is "may be required", "required if X", or "recommended", do NOT flatten to "Yes" or true. Use these values:
   - For gre_required/gmat_required: use "Conditional" and explain in gre_waiver_conditions/gmat_waiver_conditions
   - For work_experience_required: use false if conditional, and explain in work_experience_details
   - For booleans: use false if it's only sometimes needed, and capture the condition in the description field"""

_MASTERS_RULE_11 = """
11. **ENTRY REQUIREMENTS — MAP TO ENUM VALUES:** When extracting required application documents, map them to these predefined values:
   - MASTER_SOP — Statement of Purpose, SOP, Personal Statement
   - ACADEMIC_LOR — Academic Letters of Recommendation, Academic References
   - PROFESSIONAL_LOR — Professional Letters of Recommendation, Professional References
   - GENERAL_LOR — Letters of Recommendation (when type not specified)
   - LOR_DOCUMENT — Generic reference letters
   - RESUME — Resume, CV, Curriculum Vitae
   - TRANSCRIPT_DOCUMENT — Transcripts, Academic Records
   - ENGLISH_PROFICIENCY_TEST — auto-add if english_tests[] is non-empty
   - DIVERSITY_STATEMENT — Diversity Statement, Diversity Essay
   - COVER_LETTER — Cover Letter, Motivation Letter
   - ESSAY_QUESTION — Supplemental Essays, Short Answer Questions
   - ESSAY_PROMPT — Essay Prompts
   - PERSONAL_HISTORY_STATEMENT — Personal History Statement
   - GOAL_STATEMENT — Goal Statement, Research Statement, Research Proposal
   - WES_ECE_EVALUATION — WES Evaluation, Credential Evaluation, ECE Evaluation
   - CURRICULUM_VITAE — CV (when distinct from Resume)
   - MEDIUM_OF_INSTRUCTION — Medium of Instruction Certificate
   Each entry_requirement should have: value (from the enum), count (default 1, e.g. 2 for "two letters of recommendation"), and detail (optional context)."""

_MASTERS_RULE_12 = """
12. **KNOWLEDGE AUGMENTATION — ALLOWED FIELDS ONLY:** For the following fields, if the source document is empty/sparse but you have confident, widely-known knowledge, you MAY fill them. Mark every such value with `"source": "inferred"`. For all other fields, extract ONLY from the source document and use `"source": "stated"`.
   Allowed for knowledge fill:
   - `why_study_points[]` — program/university selling points
   - `reasons_to_consider[]` — university-level location, campus, city, reputation benefits
   - `career_outcomes.description` — general career narrative for the field
   - `career_outcomes.job_roles[]` — typical career paths for this degree/field. **You MUST provide at least 3-5 inferred job roles when the source has none.**
   - `career_outcomes.top_recruiters[]` — well-known employers for this program's graduates. Provide 3-5 inferred recruiters when confident.
   - `overview_description` — you may ENRICH (not replace) the source text with additional context
   - `is_stem` — STEM designation based on CIP code knowledge. **You SHOULD fill this.**
   - `pointers[]` — program-specific selling points
   - `duration_months` — if not stated, infer from your knowledge (e.g. most full-time UK Master's are 12 months, most US Master's are 18-24 months). **You SHOULD fill this.**
   - `department` — if not stated, infer the department/school. **You SHOULD fill this.**
   - `credits_required` — if not stated, infer from your knowledge. **You MAY fill this** when confident.
   - `delivery_mode` — if not stated, infer (most traditional Master's programs are "On-campus"). **You SHOULD fill this.**
   NEVER fill from knowledge: dates, fees, scores, rates, percentages, URLs, contact info, scholarship details, faculty, course_structure_data."""

_MASTERS_RULE_14 = """
14. **SCHOLARSHIPS & FUNDING:** Extract ALL financial support mentioned in the document, including:
   - Named scholarships
   - Graduate Assistantships (GA, RA, TA positions) with stipend amounts
   - Fellowships
   - Tuition waivers or fee reductions
   - Merit-based aid
   If the document mentions assistantships with stipend ranges (e.g. "$3,800-$5,300/semester"), create a scholarship entry for it."""


# ── UG-SPECIFIC RULES ─────────────────────────────────────────────────────

_UG_RULE_6 = """
6. **CONDITIONAL vs REQUIRED (Undergraduate):**
   - For sat_required/act_required: If the school is "test-optional", use "Optional". If "test-blind" or "test-free", use "No". If required, use "Yes". Explain the policy in `test_optional_policy`.
   - For `superscoring`: If the university superscores the SAT or ACT, set to true and describe in `superscoring_details`.
   - For `early_decision_binding`: If Early Decision is binding, set to true. Early Action is NOT binding.
   - If something is "recommended but not required", do NOT flatten to "Yes" — use "Optional" or "Recommended" and explain."""

_UG_RULE_11 = """
11. **ENTRY REQUIREMENTS — MAP TO ENUM VALUES (Undergraduate):** When extracting required application documents, map them to these predefined values:
   - COMMON_APP_ESSAY — Common Application Personal Essay
   - SUPPLEMENTAL_ESSAY — University-specific supplemental essays, "Why us" essays
   - SCHOOL_REPORT — School Report / School Profile from high school counselor
   - COUNSELOR_REC — Counselor Recommendation / Guidance Counselor Letter
   - ACADEMIC_LOR — Teacher Letters of Recommendation, Academic References
   - GENERAL_LOR — Letters of Recommendation (when type not specified)
   - TRANSCRIPT_DOCUMENT — High School Transcripts, Academic Records
   - ENGLISH_PROFICIENCY_TEST — auto-add if english_tests[] is non-empty
   - RESUME — Resume, CV, Activities List (if distinct from Common App activities)
   - ACTIVITIES_LIST — Extracurricular activities list (Common App activities section)
   - PORTFOLIO — Art Portfolio, Music Audition, Writing Samples
   - INTERVIEW — Admissions Interview (if tracked as a document/requirement)
   - MID_YEAR_REPORT — Mid-Year School Report
   - FINAL_REPORT — Final School Report after graduation
   - DIVERSITY_STATEMENT — Diversity Statement, Diversity Essay
   - WES_ECE_EVALUATION — WES Evaluation, Credential Evaluation (for international students)
   - MEDIUM_OF_INSTRUCTION — Medium of Instruction Certificate
   - CSS_PROFILE — CSS Profile for financial aid
   - FAFSA — FAFSA application for financial aid
   Each entry_requirement should have: value (from the enum), count (default 1, e.g. 2 for "two teacher recommendations"), and detail (optional context)."""

_UG_RULE_12 = """
12. **KNOWLEDGE AUGMENTATION — ALLOWED FIELDS ONLY:** For the following fields, if the source document is empty/sparse but you have confident, widely-known knowledge, you MAY fill them. Mark every such value with `"source": "inferred"`. For all other fields, extract ONLY from the source document and use `"source": "stated"`.
   Allowed for knowledge fill:
   - `why_study_points[]` — program/university selling points
   - `reasons_to_consider[]` — university-level location, campus, city, reputation benefits
   - `career_outcomes.description` — general career narrative for the field
   - `career_outcomes.job_roles[]` — typical career paths for this degree/field. **You MUST provide at least 3-5 inferred job roles when the source has none** — e.g. a BS Computer Science should have "Software Engineer", "Data Scientist", etc.
   - `career_outcomes.top_recruiters[]` — well-known employers for this program's graduates. Provide 3-5 inferred recruiters when confident.
   - `overview_description` — you may ENRICH (not replace) the source text with additional context
   - `is_stem` — STEM designation based on CIP code knowledge. **You SHOULD fill this.**
   - `pointers[]` — program-specific selling points
   - `duration_months` — if not stated, infer from your knowledge (most US Bachelor's are 48 months / 4 years, UK Bachelor's are 36 months / 3 years). **You SHOULD fill this.**
   - `department` — if not stated, infer the department/school/college. **You SHOULD fill this.**
   - `credits_required` — if not stated, infer (most US Bachelor's are 120-128 credits). **You MAY fill this** when confident.
   - `delivery_mode` — if not stated, infer (most traditional Bachelor's programs are "On-campus"). **You SHOULD fill this.**
   NEVER fill from knowledge: dates, fees, scores, rates, percentages, URLs, contact info, scholarship details, faculty, course_structure_data."""

_UG_RULE_14 = """
14. **SCHOLARSHIPS & FUNDING (Undergraduate):** Extract ALL financial support mentioned in the document, including:
   - Named merit scholarships (e.g. "Presidential Scholarship", "Dean's Award")
   - Need-based grants and aid
   - Athletic scholarships
   - International student scholarships
   - Tuition waivers or fee reductions
   - State/federal grant programs mentioned
   - Work-study programs
   If the document mentions scholarship ranges (e.g. "$5,000-$25,000/year based on merit"), create an entry with amount as the midpoint and note the range in eligibility."""


# ── SCHEMA SECTIONS (level-specific) ──────────────────────────────────────

_SHARED_SCHEMA_TOP = """
## OUTPUT SCHEMA:

Return a single JSON object with these top-level keys:

```json
{{
  "program_name": "string — full program name as stated",
  "university_name": "string",
  "department": "string or null","""

_MASTERS_SCHEMA_IDENTITY = """
  "degree_type": "string — e.g. MS, MA, MBA, MEng, MSc, MFA, MPH, MPA",
  "duration_months": "integer or null",
  "credits_required": "string or null — e.g. '30 credits', '90 ECTS', '200 points'",
  "delivery_mode": "On-campus | Online | Hybrid | Blended | null",
  "is_stem": "boolean or null",
  "program_type_detail": "string or null — 'Coursework', 'Thesis', 'Research', 'Mixed'","""

_UG_SCHEMA_IDENTITY = """
  "degree_type": "string — e.g. BS, BA, BEng, BTech, BSc, BFA, BBA",
  "duration_months": "integer or null — typically 48 for US, 36 for UK",
  "credits_required": "string or null — e.g. '120 credits', '128 credits', '360 UCAS points'",
  "delivery_mode": "On-campus | Online | Hybrid | Blended | null",
  "is_stem": "boolean or null",
  "program_type_detail": "string or null — 'Major', 'Minor', 'Honors', 'Pre-Professional', 'Joint/Dual'","""

_SHARED_SCHEMA_OVERVIEW = """
  "overview_description": "string — program overview, or null",
  "pointers": ["list of program-specific selling points — source: stated or inferred"],

  "deadline_note": "string or null — user-facing note with helpful context.",
  "deadlines": [
    {{
      "intake": "fall | spring | winter | summer",
      "round": "string — e.g. 'Early Decision', 'Regular Decision', 'Round 1'",
      "deadline_date": "string ISO date or null — e.g. '2027-02-14'",
      "decision_date": "string ISO date or null",
      "tags": ["list of applicable tags from: international, domestic, eu, non_eu, early_bird, early_decision, early_action, priority, regular, final, rolling"],
      "label": "string or null — free text like 'Non-European Students', 'Binding'"
    }}
  ],

  "cost_of_attendance": {{
    "currency": "string — USD, GBP, EUR, CHF, AUD, etc.",
    "tuition_per_year": "number or null",
    "tuition_per_year_min": "number or null — only if document gives a range",
    "tuition_per_year_max": "number or null — only if document gives a range",
    "tuition_domestic": "number or null — domestic/in-state tuition per year",
    "tuition_international": "number or null — international/out-of-state tuition per year",
    "tuition_per_credit": "number or null",
    "total_credits": "integer or null — total credits in program",
    "total_program_cost": "number or null",
    "application_fee": "number or null — if same for all",
    "application_fee_domestic": "number or null — if different for domestic",
    "application_fee_international": "number or null — if different for international",
    "cost_of_living_per_year": "number or null",
    "overall_cost_per_year": "number or null — total annual cost including tuition + living",
    "health_insurance_fee": "number or null",
    "notes": "string or null"
  }},"""

_MASTERS_SCHEMA_ADMISSION = """
  "admission_requirements": {{
    "min_gpa": "string or null — e.g. '3.0/4.0'",
    "gre_required": "Yes | No | Optional | Conditional | Waived | Unknown",
    "gre_waiver_conditions": "string or null",
    "gmat_required": "Yes | No | Optional | Conditional | Unknown",
    "english_tests": [
      {{"test": "TOEFL | IELTS | Duolingo | PTE | Cambridge", "min_score": "string or null", "subscore_details": "string or null", "is_required": "Yes | No | Conditional"}}
    ],
    "english_waiver_conditions": "string or null",
    "entry_requirements": [
      {{"value": "MASTER_SOP | ACADEMIC_LOR | PROFESSIONAL_LOR | GENERAL_LOR | LOR_DOCUMENT | RESUME | TRANSCRIPT_DOCUMENT | ENGLISH_PROFICIENCY_TEST | DIVERSITY_STATEMENT | COVER_LETTER | ESSAY_QUESTION | ESSAY_PROMPT | PERSONAL_HISTORY_STATEMENT | GOAL_STATEMENT | WES_ECE_EVALUATION | CURRICULUM_VITAE | MEDIUM_OF_INSTRUCTION", "count": "integer — default 1", "detail": "string or null"}}
    ],
    "work_experience_required": "boolean or null",
    "work_experience_months": "integer or null",
    "work_experience_details": "string or null",
    "credential_evaluation": "string or null — e.g. 'WES required'",
    "prerequisites": "string or null",
    "note": "string or null"
  }},

  "eligibility_criteria": [
    {{
      "type": "BACHELOR_DEGREE | MINIMUM_GPA | WORK_EXPERIENCE | YEARS_OF_EDUCATION",
      "criteria": "object — type-specific: BACHELOR_DEGREE: {{'majors': ['field1', 'field2']}}, MINIMUM_GPA: {{'gpa': '3.0', 'scale': '4.0'}}, WORK_EXPERIENCE: {{'months': 24}}, YEARS_OF_EDUCATION: {{'years': 16}}",
      "details": "string or null"
    }}
  ],"""

_UG_SCHEMA_ADMISSION = """
  "admission_requirements": {{
    "min_gpa": "string or null — e.g. '3.5/4.0 unweighted' or '90/100'",
    "sat_required": "Yes | No | Optional | Test-blind | Unknown",
    "sat_score_range": "string or null — e.g. '1400-1550' (middle 50%)",
    "act_required": "Yes | No | Optional | Test-blind | Unknown",
    "act_score_range": "string or null — e.g. '32-35' (middle 50%)",
    "test_optional_policy": "string or null — e.g. 'Test-optional through Fall 2027 admits'",
    "superscoring": "boolean or null — whether SAT/ACT superscored",
    "superscoring_details": "string or null — e.g. 'SAT superscored across sittings, ACT single sitting only'",
    "english_tests": [
      {{"test": "TOEFL | IELTS | Duolingo | PTE | Cambridge", "min_score": "string or null", "subscore_details": "string or null", "is_required": "Yes | No | Conditional"}}
    ],
    "english_waiver_conditions": "string or null",
    "entry_requirements": [
      {{"value": "COMMON_APP_ESSAY | SUPPLEMENTAL_ESSAY | SCHOOL_REPORT | COUNSELOR_REC | ACADEMIC_LOR | GENERAL_LOR | TRANSCRIPT_DOCUMENT | ENGLISH_PROFICIENCY_TEST | RESUME | ACTIVITIES_LIST | PORTFOLIO | INTERVIEW | MID_YEAR_REPORT | FINAL_REPORT | DIVERSITY_STATEMENT | WES_ECE_EVALUATION | MEDIUM_OF_INSTRUCTION | CSS_PROFILE | FAFSA", "count": "integer — default 1", "detail": "string or null"}}
    ],
    "credential_evaluation": "string or null — e.g. 'WES required for international transcripts'",
    "prerequisites": "string or null — e.g. 'AP/IB recommended: Calculus, Physics, Chemistry'",
    "recommended_courses": "string or null — e.g. '4 years English, 3 years Math, 2 years Lab Science'",
    "early_decision_binding": "boolean or null — whether ED is binding",
    "common_app_accepted": "boolean or null",
    "coalition_app_accepted": "boolean or null",
    "application_platforms": ["list of accepted platforms: Common App, Coalition, QuestBridge, UC Application, ApplyTexas, etc."],
    "note": "string or null"
  }},

  "eligibility_criteria": [
    {{
      "type": "HIGH_SCHOOL_DIPLOMA | MINIMUM_GPA | CLASS_RANK | AP_IB_COURSES | YEARS_OF_EDUCATION",
      "criteria": "object — type-specific: HIGH_SCHOOL_DIPLOMA: {{'equivalent': ['IB Diploma', 'A-Levels', 'CBSE 12th']}}, MINIMUM_GPA: {{'gpa': '3.5', 'scale': '4.0', 'type': 'unweighted'}}, CLASS_RANK: {{'percentile': 'Top 10%'}}, AP_IB_COURSES: {{'recommended': ['Calculus', 'Physics']}}, YEARS_OF_EDUCATION: {{'years': 12}}",
      "details": "string or null"
    }}
  ],"""

_SHARED_SCHEMA_STRUCTURE = """
  "course_structure_data": {{
    "credit_system": {{"type": "string — ECTS/credits/units/points", "total": "number or null", "label": "string"}},
    "thesis_option": {{
      "available": "boolean",
      "credits": "number or null",
      "description": "string or null"
    }},
    "components": [
      {{
        "name": "string — e.g. 'Core Courses', 'Electives', 'Capstone', 'General Education'",
        "type": "required | elective | capstone | thesis | research | general_education",
        "credit_count": "number or null",
        "description": "string or null",
        "courses": [
          {{"code": "string or null", "name": "string", "credits": "string or null"}}
        ]
      }}
    ],
    "program_structure_notes": ["optional bullet points"]
  }},

  "scholarships": [
    {{
      "name": "string",
      "amount": "number or null",
      "currency": "string or null",
      "deadline": "string or null",
      "eligibility": "string or null"
    }}
  ],

  "career_outcomes": {{
    "description": "string or null — source: stated or inferred",
    "avg_salary": "number or null",
    "median_salary": "number or null",
    "salary_currency": "string or null",
    "graduation_rate": "number or null — as percentage",
    "job_placement_rate": "number or null — as percentage",
    "job_roles": [{{"name": "string", "source": "stated | inferred"}}],
    "top_recruiters": [{{"name": "string", "source": "stated | inferred"}}]
  }},"""

_MASTERS_SCHEMA_CLASS_PROFILE = """
  "class_profile": {{
    "class_size": "number or null",
    "acceptance_rate": "number or null — as percentage",
    "international_percentage": "number or null",
    "avg_gpa": "string or null",
    "avg_gre": "string or null",
    "avg_work_experience_years": "number or null",
    "gender_ratio": "string or null — e.g. '46% women'",
    "total_enrollment": "number or null",
    "avg_age": "number or null"
  }},"""

_UG_SCHEMA_CLASS_PROFILE = """
  "class_profile": {{
    "class_size": "number or null — freshman class / cohort size",
    "acceptance_rate": "number or null — as percentage",
    "international_percentage": "number or null",
    "avg_gpa_unweighted": "string or null — e.g. '3.8/4.0'",
    "avg_gpa_weighted": "string or null — e.g. '4.2/5.0'",
    "avg_sat": "string or null — e.g. '1480' or '1400-1550'",
    "avg_act": "string or null — e.g. '33' or '32-35'",
    "gender_ratio": "string or null — e.g. '52% women'",
    "total_enrollment": "number or null — total undergrad enrollment",
    "student_faculty_ratio": "string or null — e.g. '6:1'"
  }},"""

_SHARED_SCHEMA_FOOTER = """
  "faculty": [
    {{
      "name": "string",
      "title": "string or null",
      "research_areas": ["string"],
      "profile_url": "string or null"
    }}
  ],

  "contact_info": {{
    "department_email": "string or null",
    "admissions_email": "string or null",
    "phone": "string or null",
    "address": "string or null"
  }},

  "important_links": {{
    "program_page": "string URL or null",
    "application_portal": "string URL or null",
    "faculty_directory": "string URL or null"
  }},

  "why_study_points": [{{"text": "string", "source": "stated | inferred"}}],
  "reasons_to_consider": [{{"text": "string", "source": "stated | inferred"}}],

  "extraction_confidence": {{
    "well_covered": ["list of section names that had good data"],
    "gaps": ["list of section names where data was missing or sparse"]
  }}
}}
```

## DOCUMENT TO EXTRACT FROM:

{markdown_content}

---

Return ONLY the JSON object. No markdown fences, no explanation, no preamble."""


# ── PROMPT BUILDER ─────────────────────────────────────────────────────────

def build_extraction_prompt(course_level="masters"):
    """
    Assemble the extraction prompt from shared + level-specific sections.
    course_level: "masters" | "ug" | "phd"
    """
    if course_level == "ug":
        rule_6 = _UG_RULE_6
        rule_11 = _UG_RULE_11
        rule_12 = _UG_RULE_12
        rule_14 = _UG_RULE_14
        schema_identity = _UG_SCHEMA_IDENTITY
        schema_admission = _UG_SCHEMA_ADMISSION
        schema_class_profile = _UG_SCHEMA_CLASS_PROFILE
    else:
        # masters and phd share the same prompt (phd is close enough to masters)
        rule_6 = _MASTERS_RULE_6
        rule_11 = _MASTERS_RULE_11
        rule_12 = _MASTERS_RULE_12
        rule_14 = _MASTERS_RULE_14
        schema_identity = _MASTERS_SCHEMA_IDENTITY
        schema_admission = _MASTERS_SCHEMA_ADMISSION
        schema_class_profile = _MASTERS_SCHEMA_CLASS_PROFILE

    prompt = (
        _SHARED_RULES_HEADER
        + rule_6
        + _SHARED_RULE_7
        + _SHARED_RULE_8
        + _SHARED_RULE_9_DEADLINES
        + _SHARED_RULE_10_FEE
        + rule_11
        + rule_12
        + _SHARED_RULE_13_COST
        + rule_14
        + _SHARED_RULE_15_COPYWRITING
        + _SHARED_SCHEMA_TOP
        + schema_identity
        + _SHARED_SCHEMA_OVERVIEW
        + schema_admission
        + _SHARED_SCHEMA_STRUCTURE
        + schema_class_profile
        + _SHARED_SCHEMA_FOOTER
    )
    return prompt


# Build UG extraction prompt
EXTRACTION_PROMPT = build_extraction_prompt("ug")


# ── File discovery ──────────────────────────────────────────────────────────

def find_markdown_files(input_dir, max_count=15):
    """Find all .md files in the input directory, return random sample."""
    all_files = []
    for root, dirs, files in os.walk(input_dir):
        for f in files:
            if f.endswith(".md"):
                all_files.append(os.path.join(root, f))

    if not all_files:
        print(f"[ERROR] No .md files found in {input_dir}")
        sys.exit(1)

    # Sample randomly
    count = min(max_count, len(all_files))
    sampled = random.sample(all_files, count)
    print(f"Found {len(all_files)} total files, selected {count} for testing")
    return sampled


# ── Process single file ────────────────────────────────────────────────────

def process_file(filepath, output_dir):
    """Read markdown file, send to Gemini, save structured JSON."""
    filename = os.path.basename(filepath)
    rel_path = os.path.relpath(filepath, INPUT_DIR)
    print(f"  Processing: {rel_path}")

    # Read markdown
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Strip citation blocks to reduce noise (keep the content, remove citation XML)
    import re
    content_clean = re.sub(r'<citation>.*?</citation>', '', content, flags=re.DOTALL)

    # Truncate if too long (keep first ~25k chars to leave room for prompt)
    if len(content_clean) > 25000:
        content_clean = content_clean[:25000] + "\n\n[... truncated ...]"

    # Build prompt
    today = datetime.now().strftime("%B %d, %Y")  # e.g. "March 24, 2026"
    prompt = EXTRACTION_PROMPT.replace("{markdown_content}", content_clean).replace("{today_date}", today).replace("{course_level}", "UG")

    # Call Gemini
    start = time.time()
    result = gemini_extract(prompt)
    elapsed = time.time() - start

    if result.get("error"):
        print(f"    ERROR: {result['error']}")
        return {"file": rel_path, "status": "error", "error": result["error"], "elapsed": elapsed}

    raw_text = result["text"].strip()

    # Parse JSON response
    try:
        # Try direct parse first
        structured = json.loads(raw_text)
    except json.JSONDecodeError:
        # Try extracting JSON from markdown fences
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw_text, re.DOTALL)
        if json_match:
            try:
                structured = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                print(f"    ERROR: Could not parse JSON from response")
                return {"file": rel_path, "status": "parse_error", "raw": raw_text[:500], "elapsed": elapsed}
        else:
            print(f"    ERROR: No valid JSON in response")
            return {"file": rel_path, "status": "parse_error", "raw": raw_text[:500], "elapsed": elapsed}

    # Save output
    out_filename = filename.replace(".md", ".json")
    out_path = os.path.join(output_dir, out_filename)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(structured, f, indent=2, ensure_ascii=False)

    # Handle case where Gemini returns a list instead of dict
    if isinstance(structured, list):
        structured = structured[0] if structured else {}

    # Quick quality stats
    confidence = structured.get("extraction_confidence", {})
    well_covered = confidence.get("well_covered", [])
    gaps = confidence.get("gaps", [])
    n_deadlines = len(structured.get("deadlines") or [])
    n_entry_reqs = len((structured.get("admission_requirements") or {}).get("entry_requirements") or [])
    n_courses = sum(
        len(comp.get("courses") or [])
        for comp in ((structured.get("course_structure_data") or {}).get("components") or [])
    )
    n_scholarships = len(structured.get("scholarships") or [])
    n_job_roles = len((structured.get("career_outcomes") or {}).get("job_roles") or [])
    n_recruiters = len((structured.get("career_outcomes") or {}).get("top_recruiters") or [])
    n_why_points = len(structured.get("why_study_points") or [])
    n_reasons = len(structured.get("reasons_to_consider") or [])
    n_eligibility = len(structured.get("eligibility_criteria") or [])
    delivery = structured.get("delivery_mode")
    deadline_note = structured.get("deadline_note")

    # Count inferred vs stated
    inferred_roles = sum(1 for r in ((structured.get("career_outcomes") or {}).get("job_roles") or []) if isinstance(r, dict) and r.get("source") == "inferred")
    inferred_recruiters = sum(1 for r in ((structured.get("career_outcomes") or {}).get("top_recruiters") or []) if isinstance(r, dict) and r.get("source") == "inferred")

    print(f"    OK ({elapsed:.1f}s) — deadlines:{n_deadlines} entry_reqs:{n_entry_reqs} courses:{n_courses} eligibility:{n_eligibility}")
    print(f"    Career: roles:{n_job_roles}({inferred_roles} inferred) recruiters:{n_recruiters}({inferred_recruiters} inferred)")
    print(f"    Why: points:{n_why_points} reasons:{n_reasons} | delivery:{delivery}")
    if deadline_note:
        print(f"    Deadline note: {deadline_note[:80]}...")
    print(f"    Covered: {', '.join(well_covered[:5])}")
    if gaps:
        print(f"    Gaps: {', '.join(gaps)}")

    return {
        "file": rel_path,
        "status": "ok",
        "elapsed": elapsed,
        "stats": {
            "deadlines": n_deadlines,
            "entry_reqs": n_entry_reqs,
            "courses": n_courses,
            "scholarships": n_scholarships,
            "eligibility": n_eligibility,
            "job_roles": n_job_roles,
            "inferred_roles": inferred_roles,
            "recruiters": n_recruiters,
            "inferred_recruiters": inferred_recruiters,
            "why_points": n_why_points,
            "reasons_to_consider": n_reasons,
            "delivery_mode": delivery,
            "well_covered": well_covered,
            "gaps": gaps,
        }
    }


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Structured data extraction from UG scraped markdown")
    parser.add_argument("--max", type=int, default=15, help="Number of random programs to test (0 = all)")
    parser.add_argument("--file", type=str, help="Process a single file instead of random sample")
    parser.add_argument("--file-list", type=str, help="Process files listed in a text file (one path per line)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Concurrent workers")
    parser.add_argument("--public-api", action="store_true", help="Use public Gemini API instead of Vertex")
    parser.add_argument("--input-dir", type=str, default=INPUT_DIR, help="Input directory")
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR, help="Output directory")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--skip-existing", action="store_true", help="Skip files that already have JSON output (for resume)")
    parser.add_argument("--all", action="store_true", help="Process ALL files (full run)")
    args = parser.parse_args()

    global USE_VERTEX
    if args.public_api:
        USE_VERTEX = False

    input_dir = args.input_dir
    output_dir = args.output_dir

    print(f"Course level: UG")
    print(f"Input dir:    {input_dir}")
    print(f"Output dir:   {output_dir}")

    random.seed(args.seed)

    # Create output dir
    os.makedirs(output_dir, exist_ok=True)

    # Find files
    if args.file_list:
        with open(args.file_list) as fl:
            files = [line.strip() for line in fl if line.strip()]
        print(f"Processing {len(files)} files from {args.file_list}")
    elif args.file:
        files = [args.file]
        print(f"Processing single file: {args.file}")
    elif args.all:
        files = find_markdown_files(input_dir, max_count=999999)
    else:
        files = find_markdown_files(input_dir, args.max)

    # Skip existing (crash-resume support)
    if args.skip_existing:
        before = len(files)
        files = [
            f for f in files
            if not os.path.exists(
                os.path.join(output_dir, os.path.basename(f).replace(".md", ".json"))
            )
        ]
        skipped = before - len(files)
        if skipped:
            print(f"Skipping {skipped} already-processed files, {len(files)} remaining")

    if not files:
        print("Nothing to process.")
        return

    print(f"\nStarting extraction with {args.workers} workers...\n")
    start_time = time.time()

    # Progress tracking
    progress_file = os.path.join(output_dir, f"_progress_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
    progress_lock = threading.Lock()
    completed_count = [0]

    def process_and_track(filepath):
        r = process_file(filepath, output_dir)
        # Append to progress file (crash-safe)
        with progress_lock:
            completed_count[0] += 1
            with open(progress_file, "a") as pf:
                pf.write(json.dumps({"file": r["file"], "status": r["status"]}) + "\n")
            if completed_count[0] % 25 == 0:
                elapsed = time.time() - start_time
                rate = completed_count[0] / elapsed
                remaining = (len(files) - completed_count[0]) / rate if rate > 0 else 0
                print(f"\n  >>> Progress: {completed_count[0]}/{len(files)} ({rate:.1f}/s, ~{remaining/60:.0f}m remaining)\n")
        return r

    results = []
    if args.workers == 1 or len(files) == 1:
        for f in files:
            r = process_and_track(f)
            results.append(r)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(process_and_track, f): f for f in files}
            for future in as_completed(futures):
                results.append(future.result())

    total_time = time.time() - start_time

    # Summary
    print(f"\n{'='*60}")
    print(f"EXTRACTION SUMMARY")
    print(f"{'='*60}")

    ok = [r for r in results if r["status"] == "ok"]
    errors = [r for r in results if r["status"] != "ok"]

    print(f"Total: {len(results)} | Success: {len(ok)} | Errors: {len(errors)}")
    print(f"Time: {total_time:.1f}s ({total_time/len(results):.1f}s avg)")

    if ok:
        avg_deadlines = sum(r["stats"]["deadlines"] for r in ok) / len(ok)
        avg_entry_reqs = sum(r["stats"]["entry_reqs"] for r in ok) / len(ok)
        avg_courses = sum(r["stats"]["courses"] for r in ok) / len(ok)
        avg_scholarships = sum(r["stats"]["scholarships"] for r in ok) / len(ok)
        avg_eligibility = sum(r["stats"]["eligibility"] for r in ok) / len(ok)
        avg_roles = sum(r["stats"]["job_roles"] for r in ok) / len(ok)
        avg_recruiters = sum(r["stats"]["recruiters"] for r in ok) / len(ok)
        total_inferred_roles = sum(r["stats"]["inferred_roles"] for r in ok)
        total_inferred_recruiters = sum(r["stats"]["inferred_recruiters"] for r in ok)
        avg_why = sum(r["stats"]["why_points"] for r in ok) / len(ok)
        avg_reasons = sum(r["stats"]["reasons_to_consider"] for r in ok) / len(ok)

        print(f"\nAverage per program:")
        print(f"  Deadlines:       {avg_deadlines:.1f}")
        print(f"  Entry Reqs:      {avg_entry_reqs:.1f}")
        print(f"  Eligibility:     {avg_eligibility:.1f}")
        print(f"  Courses:         {avg_courses:.1f}")
        print(f"  Scholarships:    {avg_scholarships:.1f}")
        print(f"  Job Roles:       {avg_roles:.1f} ({total_inferred_roles} total inferred)")
        print(f"  Recruiters:      {avg_recruiters:.1f} ({total_inferred_recruiters} total inferred)")
        print(f"  Why Points:      {avg_why:.1f}")
        print(f"  Reasons (uni):   {avg_reasons:.1f}")

        # Delivery mode breakdown
        delivery_modes = {}
        for r in ok:
            dm = r["stats"].get("delivery_mode") or "null"
            delivery_modes[dm] = delivery_modes.get(dm, 0) + 1
        print(f"\nDelivery modes: {delivery_modes}")

        # Aggregate gap analysis
        all_gaps = {}
        all_covered = {}
        for r in ok:
            for g in r["stats"]["gaps"]:
                all_gaps[g] = all_gaps.get(g, 0) + 1
            for c in r["stats"]["well_covered"]:
                all_covered[c] = all_covered.get(c, 0) + 1

        if all_covered:
            print(f"\nMost covered sections:")
            for section, count in sorted(all_covered.items(), key=lambda x: -x[1])[:8]:
                print(f"  {section}: {count}/{len(ok)} programs")

        if all_gaps:
            print(f"\nMost common gaps:")
            for section, count in sorted(all_gaps.items(), key=lambda x: -x[1])[:8]:
                print(f"  {section}: {count}/{len(ok)} programs")

    if errors:
        print(f"\nErrors:")
        for r in errors:
            print(f"  {r['file']}: {r.get('error', r.get('status'))}")

    # Save summary
    summary_path = os.path.join(output_dir, f"_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(summary_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "total": len(results),
            "success": len(ok),
            "errors": len(errors),
            "total_time": total_time,
            "results": results,
        }, f, indent=2)
    print(f"\nSummary saved to: {summary_path}")
    print(f"JSON outputs saved to: {output_dir}/")


if __name__ == "__main__":
    main()