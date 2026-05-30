#!/usr/bin/env python3
"""
grounding_runner.py — discover faculty directory URLs using Gemini 2.5 Flash
                      with Google Search grounding on Vertex AI.

For each (university, department) pair from the two input CSVs, ask
Gemini + Google Search to find the official faculty directory page on
the university's own domain. Save structured JSON per university + flat
master CSV + raw grounding metadata per search.

Architecture:
  - Reuses the same auth pattern as browser_extract.py / run_gemini_scraper.py
    (service-account key at dashboard/gcp-key.json, project ambitio-ds-v2,
    location global).
  - Calls Vertex AI's Gemini 2.5 Flash with `google_search` grounding tool.
  - Parses the JSON response (prompt asks Claude ... I mean Gemini to return
    JSON directly) + attaches grounding_metadata for audit.
  - Resumable via state.jsonl — re-runs skip already-completed pairs.
  - Skips clearly N/A pairs based on `not_applicable_matrix` (optional
    pre-filter, empty by default — Gemini decides).

Usage:
    python Professors_info/scripts/grounding_runner.py --pilot
        → runs only the 5 pilot universities
    python Professors_info/scripts/grounding_runner.py --all
        → runs all 350 universities × 30 departments
    python Professors_info/scripts/grounding_runner.py --university "MIT" --dept "Computer Science"
        → runs one specific pair (for debugging)
    python Professors_info/scripts/grounding_runner.py --all --skip-existing
        → resume — skip pairs already in state.jsonl or grounding/<file>.json
    python Professors_info/scripts/grounding_runner.py --dry-run --pilot
        → template prompts + print, no API call, no cost

Env vars:
    VERTEX_SA_KEY_PATH      default: course_data/dashboard/gcp-key.json
    VERTEX_PROJECT          default: ambitio-ds-v2
    VERTEX_LOCATION         default: global
    GEMINI_MODEL            default: gemini-2.5-flash
    GROUNDING_WORKERS       default: 5 (concurrent API calls)
    GROUNDING_MIN_GAP_MS    default: 50 (minimum ms between requests per worker)
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG & CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════
PROJECT_ROOT = Path(__file__).resolve().parent.parent   # .../Professors_info
REPO_ROOT = PROJECT_ROOT.parent                          # .../course_data

CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
UNIS_DIR = OUTPUT_DIR / "universities"
GROUNDING_DIR = PROJECT_ROOT / "grounding"
LOGS_DIR = PROJECT_ROOT / "logs"
for d in (OUTPUT_DIR, UNIS_DIR, GROUNDING_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

UNIVERSITIES_CSV = CONFIG_DIR / "universities_top_450.csv"
DEPARTMENTS_CSV = CONFIG_DIR / "top_30_departments_enriched.csv"
STATE_FILE = LOGS_DIR / "state.jsonl"
FLAT_CSV = OUTPUT_DIR / "faculty_urls.csv"
FLAT_JSONL = OUTPUT_DIR / "faculty_urls.jsonl"

VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "ambitio-ds-v2")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
VERTEX_SA_KEY_PATH = os.getenv(
    "VERTEX_SA_KEY_PATH",
    str(REPO_ROOT / "dashboard" / "gcp-key.json"),
)

WORKERS = int(os.getenv("GROUNDING_WORKERS", "5"))
MIN_REQUEST_GAP_MS = int(os.getenv("GROUNDING_MIN_GAP_MS", "50"))

PILOT_UNIVERSITIES = {
    "Massachusetts Institute of Technology",
    "University of Oxford",
    "National University of Singapore",
    "London School of Economics",
    "Indian Institute of Technology Delhi",
}

# Second pilot — different 5 unis for cross-region generalization test.
# Chosen to cover varied URL structures & bilingual sites.
PILOT_V2_UNIVERSITIES = {
    "Stanford University",           # US, large tech/engineering, distinct from MIT
    "University of Cambridge",       # UK, collegiate — distinct from Oxford
    "ETH Zurich",                    # Switzerland, bilingual EN/DE
    "University of Tokyo",           # Japan, bilingual EN/JP
    "Tsinghua University",           # China, bilingual EN/ZH
}

# Known (uni, dept) pre-filters — skip obviously non-applicable pairs to save API calls.
# Kept conservative — only include combos that are universally absent.
# Gemini will still return "not_found" if we miss an edge case; this is just cost savings.
KNOWN_NOT_APPLICABLE: dict[str, set[str]] = {
    "Indian Institute of Technology Delhi": {"Art & Design", "Law", "Medicine"},
    "Indian Institute of Technology Bombay": {"Art & Design", "Law", "Medicine"},
    "Indian Institute of Technology Madras": {"Art & Design", "Law", "Medicine"},
    "California Institute of Technology": {"Law", "Medicine", "Art & Design"},
    "London School of Economics": {
        "Computer Science", "Mechanical Engineering", "Electrical Engineering",
        "Medicine", "Physics", "Chemistry", "Biology", "Civil Engineering",
        "Chemical Engineering", "Materials Science & Engineering", "Architecture",
        "Art & Design",
    },
}

log = logging.getLogger("grounding")

# ══════════════════════════════════════════════════════════════════════════════
#  VERTEX AI AUTH (reuses the pattern from browser_extract.py / run_gemini_scraper.py)
# ══════════════════════════════════════════════════════════════════════════════
_token_cache = {"token": None, "timestamp": 0.0}
_token_lock = threading.Lock()
TOKEN_REFRESH_INTERVAL = 3000  # refresh every 50 min (expires at 60)


def _get_vertex_token() -> Optional[str]:
    """OAuth2 access token: try gcloud first, fall back to service-account JWT."""
    with _token_lock:
        now = time.time()
        if _token_cache["token"] and (now - _token_cache["timestamp"]) < TOKEN_REFRESH_INTERVAL:
            return _token_cache["token"]

    # 1. gcloud if installed
    try:
        res = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, timeout=15,
        )
        t = res.stdout.strip()
        if t and len(t) > 20:
            with _token_lock:
                _token_cache.update({"token": t, "timestamp": time.time()})
            return t
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 2. service account key JWT exchange (uses openssl + curl — no extra deps)
    key_path = os.path.abspath(VERTEX_SA_KEY_PATH)
    if not os.path.exists(key_path):
        log.error("Service account key not found: %s", key_path)
        return None

    try:
        with open(key_path) as f:
            sa = json.load(f)

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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as kf:
            kf.write(sa["private_key"])
            key_tmp = kf.name
        try:
            proc = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", key_tmp],
                input=signing_input, capture_output=True, timeout=10,
            )
        finally:
            os.unlink(key_tmp)

        if proc.returncode != 0:
            log.error("openssl signing failed: %s", proc.stderr.decode()[:500])
            return None

        signature = base64.urlsafe_b64encode(proc.stdout).rstrip(b"=")
        jwt_token = (signing_input + b"." + signature).decode()

        token_resp = subprocess.run(
            ["curl", "-s", "-X", "POST", sa["token_uri"],
             "-H", "Content-Type: application/x-www-form-urlencoded",
             "-d", f"grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion={jwt_token}"],
            capture_output=True, text=True, timeout=30,
        )
        parsed = json.loads(token_resp.stdout)
        t = parsed.get("access_token")
        if not t:
            log.error("Token exchange failed: %s", token_resp.stdout[:300])
            return None
        with _token_lock:
            _token_cache.update({"token": t, "timestamp": time.time()})
        return t
    except Exception as e:
        log.exception("Token fetch failed: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT BUILDING
# ══════════════════════════════════════════════════════════════════════════════
SYSTEM_INSTRUCTIONS = """You are a university website URL finder.

Your ONLY job is to return the EXACT URL of the main faculty / people directory \
page for a specific academic department at a specific university, using Google \
Search to ground your answer.

STRICT REQUIREMENTS:
1. The URL MUST be hosted on the university's OFFICIAL domain (or an official \
   subdomain like eecs.mit.edu, polisci.mit.edu, cs.ox.ac.uk, lse.ac.uk).
2. The URL MUST point to a page that LISTS the department's faculty/academics \
   (professors, lecturers). It must NOT be:
     - a news article
     - a single professor's individual profile
     - the university homepage
     - a Wikipedia article
     - any third-party aggregator (shiksha, collegedunia, topuniversities, etc.)
3. The URL must be current and working — prefer pages that explicitly say \
   "Faculty", "People", "Academics", "Staff", "Our Team", or equivalent.
4. If the department does NOT exist at this university (e.g., MIT has no Law \
   school; LSE has no Engineering), return confidence = "not_found" with \
   faculty_page_url = null.
5. Apply HIGH confidence only when you are certain the URL lists the \
   department's faculty. If you're unsure between two pages, pick the most \
   authoritative one and mark confidence = "medium".

ANTI-HALLUCINATION RULES — READ CAREFULLY:
6. You MUST NOT construct, guess, invent, or infer URLs. Only return URLs \
   that you have SEEN verbatim in a Google Search result during this search. \
   Do not modify, extend, shorten, or combine URLs from different sources.
7. Do NOT generalize URL patterns across paths. If search results show \
   "/people", do not append "/faculty" to produce "/people/faculty". If you \
   saw "/role/faculty-ee/", do not emit "/people/faculty-ee/". Common failure \
   modes to avoid: appending /faculty to a /people path; swapping /role/ for \
   /people/; inventing /departments/<name>/faculty/ slugs on business-school \
   sites. Return the URL exactly as it appeared in a search result.
8. An honest confidence="not_found" is BETTER than a fabricated URL. If no \
   search result directly shows the department faculty directory page, return \
   not_found rather than guessing. We prefer a miss over a 404.
9. For alternate_urls, only include URLs you also saw in search results. \
   Never invent them.
10. Before emitting your JSON, ask yourself: "Did this exact URL appear in my \
    grounded search results?" If not, do not output it.

Respond with VALID JSON ONLY — no prose, no markdown fences, no explanation \
outside the JSON. Shape:
{
  "faculty_page_url": "https://..." OR null,
  "confidence": "high" | "medium" | "low" | "not_found",
  "reasoning_note": "one sentence — why this URL was chosen, or why not_found",
  "alternate_urls": ["https://...", ...]    // up to 2 backup candidates, all from search results
}
"""


def build_user_prompt(uni_name: str, uni_domain: str, dept_name: str, dept_aliases: str) -> str:
    aliases_clean = "; ".join([a.strip() for a in dept_aliases.split(";") if a.strip()])
    return f"""University: {uni_name}
Official website: {uni_domain}

Department: {dept_name}
This department is also sometimes called: {aliases_clean}

Find the MAIN faculty directory page for this department at this university. \
It should be on the {uni_domain} domain (or a subdomain of it) and list the \
department's professors. Use Google Search to verify.

Return JSON only (no prose)."""


# ══════════════════════════════════════════════════════════════════════════════
#  VERTEX AI CALL (HTTP + grounding tool)
# ══════════════════════════════════════════════════════════════════════════════
_rate_lock = threading.Lock()
_last_request_time = 0.0


def _rate_limit_wait():
    """Simple token-bucket: ensure MIN_REQUEST_GAP_MS between requests globally."""
    global _last_request_time
    with _rate_lock:
        now = time.time()
        elapsed_ms = (now - _last_request_time) * 1000
        if elapsed_ms < MIN_REQUEST_GAP_MS:
            time.sleep((MIN_REQUEST_GAP_MS - elapsed_ms) / 1000.0)
        _last_request_time = time.time()


def gemini_grounded_call(user_prompt: str, max_retries: int = 3) -> dict:
    """
    Call Gemini 2.5 Flash on Vertex AI with Google Search grounding.
    Returns dict with: text, parsed_output, grounding_metadata, usage, error.
    """
    token = _get_vertex_token()
    if not token:
        return {"error": "auth_failed"}

    url = (
        f"https://aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}"
        f"/locations/{VERTEX_LOCATION}/publishers/google/models/{GEMINI_MODEL}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTIONS}]},
        "tools": [{"googleSearch": {}}],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    for attempt in range(max_retries):
        _rate_limit_wait()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as bf:
            json.dump(body, bf)
            body_path = bf.name
        try:
            res = subprocess.run(
                ["curl", "-sS", "-X", "POST", url,
                 "-H", f"Authorization: Bearer {token}",
                 "-H", "Content-Type: application/json",
                 "-d", f"@{body_path}"],
                capture_output=True, text=True, timeout=90,
            )
        finally:
            os.unlink(body_path)

        raw = res.stdout
        if not raw:
            log.warning("[API] empty response (attempt %d): stderr=%s", attempt + 1, res.stderr[:200])
            time.sleep(2 + attempt * 3)
            continue

        try:
            resp = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("[API] non-JSON response (attempt %d): %s", attempt + 1, raw[:300])
            time.sleep(2)
            continue

        if "error" in resp:
            err = resp["error"]
            code = err.get("code", 0)
            msg = err.get("message", "")
            if code in (429, 503) and attempt < max_retries - 1:
                log.warning("[API] %d %s — retrying", code, msg[:100])
                time.sleep(10 + attempt * 20)
                continue
            return {"error": f"api_error_{code}: {msg[:300]}"}

        # Extract text + grounding metadata
        candidates = resp.get("candidates", [])
        if not candidates:
            return {"error": "no_candidates", "raw": resp}
        cand = candidates[0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        grounding_metadata = cand.get("groundingMetadata", {}) or {}
        usage = resp.get("usageMetadata", {})

        # Try to parse the JSON the model returned
        parsed_output = None
        parse_error = None
        try:
            parsed_output = json.loads(text)
        except json.JSONDecodeError as e:
            # Sometimes Gemini wraps in markdown fences despite responseMimeType
            stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
            try:
                parsed_output = json.loads(stripped)
            except json.JSONDecodeError:
                parse_error = str(e)

        return {
            "text": text,
            "parsed_output": parsed_output,
            "parse_error": parse_error,
            "grounding_metadata": grounding_metadata,
            "usage": usage,
        }

    return {"error": "max_retries_exceeded"}


# ══════════════════════════════════════════════════════════════════════════════
#  URL VERIFICATION (post-hoc HEAD check with alternate-URL fallback)
# ══════════════════════════════════════════════════════════════════════════════
import ssl as _ssl
import urllib.request as _urlreq
import urllib.error as _urlerr

_VERIFY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)
_VERIFY_TIMEOUT = 12
_VERIFY_CTX = _ssl.create_default_context()
_VERIFY_CTX.check_hostname = False
_VERIFY_CTX.verify_mode = _ssl.CERT_NONE


def _probe_url(url: str) -> dict:
    """HEAD with GET fallback. Returns dict with status, final_url, ok (bool)."""
    headers = {"User-Agent": _VERIFY_UA, "Accept": "*/*"}

    def _do(method: str) -> tuple[int, str]:
        req = _urlreq.Request(url, headers=headers, method=method)
        with _urlreq.urlopen(req, timeout=_VERIFY_TIMEOUT, context=_VERIFY_CTX) as resp:
            return resp.status, resp.geturl()

    try:
        status, final = _do("HEAD")
        if status in (405, 501) or status >= 400:
            status, final = _do("GET")
    except _urlerr.HTTPError as e:
        # Some sites 403/405 on HEAD but accept GET
        if e.code in (403, 405, 501):
            try:
                status, final = _do("GET")
            except _urlerr.HTTPError as e2:
                return {"status": e2.code, "final_url": url, "ok": False, "error": ""}
            except Exception as e2:
                return {"status": 0, "final_url": "", "ok": False, "error": f"{type(e2).__name__}: {e2}"}
        else:
            return {"status": e.code, "final_url": url, "ok": False, "error": ""}
    except Exception as e:
        return {"status": 0, "final_url": "", "ok": False, "error": f"{type(e).__name__}: {e}"}

    ok = 200 <= status < 300
    # Redirect-to-homepage check: if final URL path is / or empty, it's a useless redirect
    if ok and final and final != url:
        from urllib.parse import urlparse
        pu = urlparse(final)
        if pu.path in ("", "/"):
            ok = False
    return {"status": status, "final_url": final, "ok": ok, "error": ""}


def verify_url_with_fallback(primary: Optional[str], alternates: list[str]) -> dict:
    """
    Try primary URL first; if dead, try each alternate in order. Returns:
      {
        "verified_url": <working URL or None>,
        "verification_status": "ok" | "ok_via_alternate" | "all_failed",
        "probe_results": [ {url, status, ok, final_url, error}, ... ],
      }
    """
    tried: list[dict] = []

    def _try(url: str, is_primary: bool) -> Optional[dict]:
        if not url or not url.strip():
            return None
        r = _probe_url(url.strip())
        r["url"] = url.strip()
        r["is_primary"] = is_primary
        tried.append(r)
        return r if r["ok"] else None

    hit = _try(primary, True) if primary else None
    if hit:
        return {"verified_url": hit["final_url"] or hit["url"],
                "verification_status": "ok",
                "probe_results": tried}

    for alt in (alternates or []):
        hit = _try(alt, False)
        if hit:
            return {"verified_url": hit["final_url"] or hit["url"],
                    "verification_status": "ok_via_alternate",
                    "probe_results": tried}

    return {"verified_url": None,
            "verification_status": "all_failed",
            "probe_results": tried}


# ══════════════════════════════════════════════════════════════════════════════
#  PAIR PROCESSING
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class Pair:
    uni_name: str
    uni_domain: str
    uni_country: str
    uni_qs_rank: str
    dept_rank: int
    dept_name: str
    dept_aliases: str
    dept_domain: str


def load_universities(pilot_only: bool = False, pilot_v2: bool = False,
                      tier: str | None = None) -> list[dict]:
    """Load universities. If tier is set ('A'|'B'|'C'), read from the per-tier CSV.
    Otherwise read from UNIVERSITIES_CSV and optionally filter by pilot set."""
    if tier:
        tier_csv = CONFIG_DIR / f"universities_tier_{tier}.csv"
        if not tier_csv.exists():
            raise FileNotFoundError(
                f"Tier CSV not found: {tier_csv}. "
                f"Run scripts/split_by_tier.py first."
            )
        with open(tier_csv, encoding="utf-8") as f:
            return list(csv.DictReader(f))

    rows = []
    allowed = None
    if pilot_only:
        allowed = PILOT_UNIVERSITIES
    elif pilot_v2:
        allowed = PILOT_V2_UNIVERSITIES
    with open(UNIVERSITIES_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if allowed is not None and row["university_name"] not in allowed:
                continue
            rows.append(row)
    return rows


def load_departments() -> list[dict]:
    with open(DEPARTMENTS_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def is_pre_filtered(uni_name: str, dept_name: str) -> bool:
    na = KNOWN_NOT_APPLICABLE.get(uni_name, set())
    return dept_name in na


def load_done_pairs() -> set[tuple[str, str]]:
    """Read state.jsonl to find (uni, dept) pairs already processed successfully.
    Error/crash records AND ok records with null parsed output (e.g. truncated
    JSON from token-budget exhaustion) are NOT counted as done — they retry."""
    done = set()
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    status = rec.get("status")
                    if status in ("error", "crash"):
                        continue
                    # 'ok' with no url and no confidence → truncated/empty; retry
                    if status == "ok" and not rec.get("faculty_page_url") and not rec.get("confidence"):
                        continue
                    done.add((rec["university"], rec["department"]))
                except Exception:
                    continue
    return done


def append_state(record: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def grounding_file_path(uni_name: str, dept_name: str) -> Path:
    """Path for the raw per-search grounding dump."""
    def _slug(s: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_")[:60]
    return GROUNDING_DIR / f"{_slug(uni_name)}__{_slug(dept_name)}.json"


def uni_json_path(uni_name: str) -> Path:
    def _slug(s: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_")[:80]
    return UNIS_DIR / f"{_slug(uni_name)}.json"


def process_pair(pair: Pair, dry_run: bool = False) -> dict:
    """Process ONE (uni, dept) pair. Returns a state record dict."""
    started = time.time()
    prompt = build_user_prompt(pair.uni_name, pair.uni_domain, pair.dept_name, pair.dept_aliases)

    if dry_run:
        log.info("[DRY] %s || %s", pair.uni_name[:30], pair.dept_name)
        print("=" * 80)
        print(f"UNI: {pair.uni_name}  |  DEPT: {pair.dept_name}")
        print("=" * 80)
        print("SYSTEM:", SYSTEM_INSTRUCTIONS[:400], "...")
        print("USER:", prompt)
        return {"university": pair.uni_name, "department": pair.dept_name, "status": "dry_run"}

    # Pre-filter: skip obviously N/A combos
    if is_pre_filtered(pair.uni_name, pair.dept_name):
        log.info("[SKIP-NA] %s || %s (pre-filtered)", pair.uni_name[:30], pair.dept_name)
        result = {
            "faculty_page_url": None,
            "confidence": "not_applicable",
            "reasoning_note": "Pre-filtered — this department does not exist at this university per KNOWN_NOT_APPLICABLE.",
            "alternate_urls": [],
        }
        _save_grounding(pair, prompt, {"parsed_output": result, "pre_filtered": True})
        return {
            "university": pair.uni_name, "department": pair.dept_name,
            "status": "not_applicable_prefilter",
            "faculty_page_url": None, "confidence": "not_applicable",
            "duration_s": 0, "tokens_in": 0, "tokens_out": 0,
        }

    # Real API call
    api = gemini_grounded_call(prompt)
    dur = round(time.time() - started, 2)

    if api.get("error"):
        log.warning("[ERR] %s || %s: %s", pair.uni_name[:30], pair.dept_name, api["error"])
        _save_grounding(pair, prompt, api)
        return {
            "university": pair.uni_name, "department": pair.dept_name,
            "status": "error", "error": api["error"], "duration_s": dur,
        }

    parsed = api.get("parsed_output") or {}
    usage = api.get("usage", {})

    # ── URL verification: HEAD-probe the primary URL, fall back to alternates ──
    model_primary = parsed.get("faculty_page_url")
    model_alts = parsed.get("alternate_urls") or []
    confidence = parsed.get("confidence")

    verification = None
    if confidence and confidence != "not_found" and model_primary:
        verification = verify_url_with_fallback(model_primary, model_alts)
        # Attach to grounding file for audit
        api = dict(api)  # shallow copy so we don't mutate caller's dict
        api["url_verification"] = verification

        verified_url = verification["verified_url"]
        vstatus = verification["verification_status"]

        # Rewrite parsed_output so downstream aggregation uses the verified URL
        if vstatus == "ok":
            pass  # primary was fine — nothing to change
        elif vstatus == "ok_via_alternate":
            # Promote the working alternate to primary, keep original as audit
            new_alts = [u for u in ([model_primary] + model_alts) if u != verified_url]
            parsed = dict(parsed)
            parsed["faculty_page_url"] = verified_url
            parsed["alternate_urls"] = new_alts[:2]
            parsed["original_model_url"] = model_primary
            api["parsed_output"] = parsed
        elif vstatus == "all_failed":
            # All candidates dead — downgrade confidence + null out URL for safety
            parsed = dict(parsed)
            parsed["original_model_url"] = model_primary
            parsed["faculty_page_url"] = None
            parsed["confidence"] = "low"
            parsed["reasoning_note"] = (
                f"All candidate URLs returned 4xx/5xx or connection errors. "
                f"Model originally proposed: {model_primary}. "
                f"Tried: {[r['url'] for r in verification['probe_results']]}. "
                + (parsed.get("reasoning_note") or "")
            )[:500]
            api["parsed_output"] = parsed
            confidence = "low"

    _save_grounding(pair, prompt, api)

    final_url = (parsed.get("faculty_page_url") or "—")[:60]
    vtag = ""
    if verification:
        vtag = f" [verify:{verification['verification_status']}]"
    log.info("[OK] %-30s || %-25s -> %s (%s)%s %.1fs",
             pair.uni_name[:30], pair.dept_name[:25],
             final_url, confidence or "?", vtag, dur)

    return {
        "university": pair.uni_name,
        "department": pair.dept_name,
        "status": "ok",
        "faculty_page_url": parsed.get("faculty_page_url"),
        "confidence": parsed.get("confidence"),
        "reasoning_note": parsed.get("reasoning_note"),
        "alternate_urls": parsed.get("alternate_urls", []),
        "original_model_url": parsed.get("original_model_url"),
        "verification_status": verification["verification_status"] if verification else "not_checked",
        "duration_s": dur,
        "tokens_in": usage.get("promptTokenCount", 0),
        "tokens_out": usage.get("candidatesTokenCount", 0),
        "grounding_chunk_count": len(api.get("grounding_metadata", {}).get("groundingChunks", [])),
    }


def _save_grounding(pair: Pair, prompt: str, api_result: dict):
    """Write full per-search JSON to grounding/ folder for audit."""
    payload = {
        "university": pair.uni_name,
        "university_domain": pair.uni_domain,
        "department": pair.dept_name,
        "prompt_user": prompt,
        "model": GEMINI_MODEL,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "parsed_output": api_result.get("parsed_output"),
        "parse_error": api_result.get("parse_error"),
        "text_raw": api_result.get("text"),
        "usage": api_result.get("usage", {}),
        "grounding_metadata": api_result.get("grounding_metadata", {}),
        "url_verification": api_result.get("url_verification"),
        "error": api_result.get("error"),
        "pre_filtered": api_result.get("pre_filtered", False),
    }
    path = grounding_file_path(pair.uni_name, pair.dept_name)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
#  AGGREGATION — build per-uni JSON + master flat CSV after (or during) the run
# ══════════════════════════════════════════════════════════════════════════════
def rebuild_outputs_from_grounding():
    """
    Walk grounding/*.json, re-aggregate into per-uni JSONs + master CSV/JSONL.
    Idempotent — safe to run anytime.
    """
    per_uni: dict[str, dict] = {}
    for gf in sorted(GROUNDING_DIR.glob("*.json")):
        try:
            data = json.loads(gf.read_text(encoding="utf-8"))
        except Exception:
            continue
        uni = data.get("university")
        if not uni:
            continue
        if uni not in per_uni:
            per_uni[uni] = {
                "university": uni,
                "university_domain": data.get("university_domain"),
                "departments": {},
            }
        parsed = data.get("parsed_output") or {}
        ver = data.get("url_verification") or {}
        per_uni[uni]["departments"][data["department"]] = {
            "faculty_page_url": parsed.get("faculty_page_url"),
            "confidence": parsed.get("confidence"),
            "reasoning_note": parsed.get("reasoning_note"),
            "alternate_urls": parsed.get("alternate_urls", []),
            "original_model_url": parsed.get("original_model_url"),
            "verification_status": ver.get("verification_status"),
            "grounding_file": gf.name,
            "timestamp": data.get("timestamp"),
            "pre_filtered": data.get("pre_filtered", False),
        }

    # Per-uni JSONs
    for uni, rec in per_uni.items():
        uni_json_path(uni).write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")

    # Flat outputs
    fieldnames = ["university", "department", "faculty_page_url", "confidence",
                  "verification_status", "original_model_url",
                  "reasoning_note", "alternate_url_1", "alternate_url_2",
                  "pre_filtered", "grounding_file", "timestamp"]
    with open(FLAT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for uni, rec in sorted(per_uni.items()):
            for dept, d in rec["departments"].items():
                alts = d.get("alternate_urls") or []
                w.writerow({
                    "university": uni,
                    "department": dept,
                    "faculty_page_url": d.get("faculty_page_url") or "",
                    "confidence": d.get("confidence") or "",
                    "verification_status": d.get("verification_status") or "",
                    "original_model_url": d.get("original_model_url") or "",
                    "reasoning_note": (d.get("reasoning_note") or "")[:200],
                    "alternate_url_1": alts[0] if len(alts) > 0 else "",
                    "alternate_url_2": alts[1] if len(alts) > 1 else "",
                    "pre_filtered": d.get("pre_filtered", False),
                    "grounding_file": d.get("grounding_file", ""),
                    "timestamp": d.get("timestamp", ""),
                })
    with open(FLAT_JSONL, "w", encoding="utf-8") as f:
        for uni, rec in sorted(per_uni.items()):
            for dept, d in rec["departments"].items():
                f.write(json.dumps({"university": uni, "department": dept, **d}, ensure_ascii=False) + "\n")

    log.info("Aggregated %d unis into %s + %s", len(per_uni), FLAT_CSV.name, FLAT_JSONL.name)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def setup_logging(verbose: bool):
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
        datefmt="%H:%M:%S",
    )


def main():
    parser = argparse.ArgumentParser(description="Vertex AI + Gemini 2.5 Flash grounding runner for faculty URL discovery")
    parser.add_argument("--pilot", action="store_true", help="Run only pilot-1 universities (MIT, Oxford, NUS, LSE, IIT Delhi)")
    parser.add_argument("--pilot2", action="store_true", help="Run only pilot-2 universities (Stanford, Cambridge, ETH Zurich, U Tokyo, Tsinghua)")
    parser.add_argument("--tier", choices=["A", "B", "C"], help="Run only one language tier (A=English, B=European bilingual, C=East Asia/non-Latin). Reads universities_tier_{X}.csv.")
    parser.add_argument("--all", action="store_true", help="Run all 450 universities × 30 departments")
    parser.add_argument("--university", help="Run one specific university (exact name match)")
    parser.add_argument("--dept", help="Run one specific department (exact name match)")
    parser.add_argument("--limit", type=int, help="Cap total pairs (debug)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip pairs already in state.jsonl or grounding/")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts, do NOT call the API")
    parser.add_argument("--rebuild-outputs", action="store_true", help="Re-aggregate existing grounding/ JSONs into per-uni JSONs + flat CSV (no API calls)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)

    # Optional: just re-aggregate outputs from existing grounding files
    if args.rebuild_outputs:
        rebuild_outputs_from_grounding()
        return

    # Validate inputs
    if not UNIVERSITIES_CSV.exists():
        log.error("Universities CSV not found: %s", UNIVERSITIES_CSV)
        sys.exit(1)
    if not DEPARTMENTS_CSV.exists():
        log.error("Departments CSV not found: %s", DEPARTMENTS_CSV)
        sys.exit(1)

    universities = load_universities(pilot_only=args.pilot, pilot_v2=args.pilot2, tier=args.tier)
    departments = load_departments()

    if args.university:
        universities = [u for u in universities if u["university_name"] == args.university]
    if args.dept:
        departments = [d for d in departments if d["department_name"] == args.dept]

    if not universities:
        log.error("No universities matched.")
        sys.exit(1)
    if not departments:
        log.error("No departments matched.")
        sys.exit(1)

    # Build all pairs
    pairs: list[Pair] = []
    for u in universities:
        for d in departments:
            pairs.append(Pair(
                uni_name=u["university_name"],
                uni_domain=u.get("official_website", "").rstrip("/"),
                uni_country=u.get("country", ""),
                uni_qs_rank=u.get("qs_rank_2025", ""),
                dept_rank=int(d["rank"]),
                dept_name=d["department_name"],
                dept_aliases=d.get("common_aliases", ""),
                dept_domain=d.get("domain", ""),
            ))

    # Skip existing
    if args.skip_existing:
        done = load_done_pairs()
        # also skip by existence of grounding file — but only if it represents a
        # successful or pre-filtered result (errored files should be retried)
        def already(p: Pair) -> bool:
            if (p.uni_name, p.dept_name) in done:
                return True
            gp = grounding_file_path(p.uni_name, p.dept_name)
            if not gp.exists():
                return False
            try:
                with open(gp, encoding="utf-8") as fh:
                    rec = json.load(fh)
                if rec.get("error"):
                    return False
                if rec.get("pre_filtered"):
                    return True
                # truncated or empty parse → retry
                return rec.get("parsed_output") is not None
            except Exception:
                return False
        before = len(pairs)
        pairs = [p for p in pairs if not already(p)]
        log.info("Skipped %d already-done pairs; %d remaining.", before - len(pairs), len(pairs))

    if args.limit:
        pairs = pairs[: args.limit]

    log.info(
        "Plan: %d unis × %d depts = %d pairs. mode=%s, workers=%d, model=%s, dry_run=%s",
        len(universities), len(departments), len(pairs),
        "pilot" if args.pilot else ("single" if args.university or args.dept else "all"),
        WORKERS, GEMINI_MODEL, args.dry_run,
    )

    if not pairs:
        log.info("Nothing to do.")
        rebuild_outputs_from_grounding()
        return

    # Quick auth sanity check BEFORE launching workers
    if not args.dry_run:
        tok = _get_vertex_token()
        if not tok:
            log.error("Cannot obtain Vertex AI access token. Check VERTEX_SA_KEY_PATH and gcloud auth.")
            sys.exit(1)
        log.info("Auth OK (project=%s, location=%s)", VERTEX_PROJECT, VERTEX_LOCATION)

    # Process in parallel
    completed = 0
    errors = 0
    started_wall = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(process_pair, p, args.dry_run): p for p in pairs}
        for fut in as_completed(futs):
            try:
                rec = fut.result()
            except Exception as e:
                p = futs[fut]
                log.exception("[CRASH] %s || %s: %s", p.uni_name, p.dept_name, e)
                rec = {"university": p.uni_name, "department": p.dept_name, "status": "crash", "error": str(e)}
            if not args.dry_run:
                append_state(rec)
            completed += 1
            if rec.get("status") in ("error", "crash"):
                errors += 1
            if completed % 20 == 0:
                log.info("[progress] %d / %d done, %d errors, %.1f min elapsed",
                         completed, len(pairs), errors, (time.time() - started_wall) / 60)

    log.info("Run complete. %d pairs processed, %d errors, %.1f min total.",
             completed, errors, (time.time() - started_wall) / 60)

    # Aggregate outputs
    if not args.dry_run:
        rebuild_outputs_from_grounding()
        log.info("Outputs written:\n  %s\n  %s\n  %s/",
                 FLAT_CSV, FLAT_JSONL, UNIS_DIR)


if __name__ == "__main__":
    main()
