#!/usr/bin/env python3
"""
build_olympiad_urls.py — Auto-discover and validate URLs for all 62 olympiads
==============================================================================

For each olympiad:
  1. Generate template URLs from known site patterns (Allen, PW, Wikipedia, HBCSE, SOF)
  2. Ask Gemini for official website URLs (it knows most olympiad sites)
  3. HTTP verify every URL (HEAD request — keep 200 OK, drop 404/timeout)
  4. Categorize into Tier 1 (official) / Tier 2 (reference) / Tier 3 (aggregator)
  5. Save final olympiads_urls.json

Does NOT modify olympiad_scraper.py. Only creates/overwrites olympiads_urls.json.

Usage:
  python build_olympiad_urls.py                  # discover all 62
  python build_olympiad_urls.py --skip-verify    # skip HTTP verification (faster)
  python build_olympiad_urls.py --max 5          # first 5 only (testing)
"""

import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ── Paths ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
VERTEX_SA_KEY_PATH = os.path.join(PROJECT_ROOT, "dashboard", "gcp-key.json")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "olympiads_urls.json")

# ── Gemini config ──────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"
_token_cache = {"token": None, "timestamp": 0}
_token_lock = threading.Lock()

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# ══════════════════════════════════════════════════════════════════════════════
#  ALL 62 OLYMPIADS — master list
# ══════════════════════════════════════════════════════════════════════════════

OLYMPIADS = [
    # === INTERNATIONAL (17) ===
    {"id": "IMO", "name": "International Mathematical Olympiad", "subject": "Mathematics", "level": "International", "entry_route": "Independent",
     "known_official": ["https://www.imo-official.org/", "https://www.imo2026.com/"],
     "wikipedia": "International_Mathematical_Olympiad"},

    {"id": "IPhO", "name": "International Physics Olympiad", "subject": "Physics", "level": "International", "entry_route": "Independent",
     "known_official": ["https://www.ipho-new.org/"],
     "wikipedia": "International_Physics_Olympiad"},

    {"id": "IChO", "name": "International Chemistry Olympiad", "subject": "Chemistry", "level": "International", "entry_route": "Independent",
     "known_official": ["https://icho-official.org/"],
     "wikipedia": "International_Chemistry_Olympiad"},

    {"id": "IBO", "name": "International Biology Olympiad", "subject": "Biology", "level": "International", "entry_route": "Independent",
     "known_official": ["https://www.ibo-info.org/"],
     "wikipedia": "International_Biology_Olympiad"},

    {"id": "IOI", "name": "International Olympiad in Informatics", "subject": "Computer Science", "level": "International", "entry_route": "Independent",
     "known_official": ["https://ioinformatics.org/", "https://ioi2026.uz/"],
     "wikipedia": "International_Olympiad_in_Informatics"},

    {"id": "IOAA", "name": "International Olympiad on Astronomy and Astrophysics", "subject": "Astronomy", "level": "International", "entry_route": "Independent",
     "known_official": [],
     "wikipedia": "International_Olympiad_on_Astronomy_and_Astrophysics"},

    {"id": "IJSO", "name": "International Junior Science Olympiad", "subject": "Science", "level": "International", "entry_route": "Independent",
     "known_official": ["https://www.ijso-official.org/"],
     "wikipedia": "International_Junior_Science_Olympiad"},

    {"id": "IJMO", "name": "International Junior Math Olympiad", "subject": "Mathematics", "level": "International", "entry_route": "Independent",
     "known_official": [],
     "wikipedia": None},

    {"id": "IEO_INTL", "name": "International Economics Olympiad", "subject": "Economics", "level": "International", "entry_route": "Independent",
     "known_official": ["https://ieo-official.org/", "https://2026.ieo-official.org/"],
     "wikipedia": "International_Economics_Olympiad"},

    {"id": "IOAI", "name": "International Olympiad on Artificial Intelligence", "subject": "Computer Science", "level": "International", "entry_route": "Independent",
     "known_official": ["https://ioai-official.org/"],
     "wikipedia": "International_Olympiad_in_Artificial_Intelligence"},

    {"id": "IOL", "name": "International Linguistics Olympiad", "subject": "Linguistics", "level": "International", "entry_route": "Independent",
     "known_official": ["https://ioling.org/"],
     "wikipedia": "International_Linguistics_Olympiad"},

    {"id": "IGeO", "name": "International Geography Olympiad", "subject": "Geography", "level": "International", "entry_route": "Independent",
     "known_official": ["https://www.geoolympiad.org/"],
     "wikipedia": "International_Geography_Olympiad"},

    {"id": "IPO", "name": "International Philosophy Olympiad", "subject": "Philosophy", "level": "International", "entry_route": "Independent",
     "known_official": ["https://www.philosophy-olympiad.org/"],
     "wikipedia": "International_Philosophy_Olympiad"},

    {"id": "IHO", "name": "International History Olympiad", "subject": "History", "level": "International", "entry_route": "Independent",
     "known_official": ["https://www.historyolympiad.com/"],
     "wikipedia": None},

    {"id": "IEarthSO", "name": "International Earth Science Olympiad", "subject": "Earth Science", "level": "International", "entry_route": "Independent",
     "known_official": ["https://www.ieso-info.org/"],
     "wikipedia": "International_Earth_Science_Olympiad"},

    {"id": "IAAO", "name": "International Astronomy Olympiad", "subject": "Astronomy", "level": "International", "entry_route": "Independent",
     "known_official": ["http://www.issp.ac.ru/iao/"],
     "wikipedia": "International_Astronomy_Olympiad"},

    {"id": "ICAS", "name": "International Competitions and Assessments for Schools", "subject": "Science", "level": "International", "entry_route": "Via School",
     "known_official": ["https://www.icasassessments.com/"],
     "wikipedia": None},

    # === INDIA NATIONAL (15) ===
    {"id": "IOQM", "name": "Indian Olympiad Qualifier in Mathematics", "subject": "Mathematics", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/mathematical-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "RMO", "name": "Regional Mathematical Olympiad", "subject": "Mathematics", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/mathematical-olympiad-2025-2026/"],
     "wikipedia": "Regional_Mathematical_Olympiad", "hbcse": True},

    {"id": "INMO", "name": "Indian National Mathematical Olympiad", "subject": "Mathematics", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/mathematical-olympiad-2025-2026/", "https://secure.hbcse.tifr.res.in/ino/"],
     "wikipedia": "Indian_National_Mathematical_Olympiad", "hbcse": True},

    {"id": "NSEP", "name": "National Standard Examination in Physics", "subject": "Physics", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/science-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "INPhO", "name": "Indian National Physics Olympiad", "subject": "Physics", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/science-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "NSEC", "name": "National Standard Examination in Chemistry", "subject": "Chemistry", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/science-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "INChO", "name": "Indian National Chemistry Olympiad", "subject": "Chemistry", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/science-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "NSEB", "name": "National Standard Examination in Biology", "subject": "Biology", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/science-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "INBO", "name": "Indian National Biology Olympiad", "subject": "Biology", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/science-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "NSEA", "name": "National Standard Examination in Astronomy", "subject": "Astronomy", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/science-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "INAO", "name": "Indian National Astronomy Olympiad", "subject": "Astronomy", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/science-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "NSEJS", "name": "National Standard Examination in Junior Science", "subject": "Science", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/science-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "INJSO", "name": "Indian National Junior Science Olympiad", "subject": "Science", "level": "National", "entry_route": "Independent",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/science-olympiad-2025-2026/"],
     "wikipedia": None, "hbcse": True},

    {"id": "PaIO", "name": "Panini Linguistics Olympiad", "subject": "Linguistics", "level": "National", "entry_route": "Independent",
     "known_official": ["https://ltrc.iiit.ac.in/nlpmt/plo/"],
     "wikipedia": "Panini_Linguistics_Olympiad"},

    {"id": "IAPT", "name": "Indian Association of Physics Teachers National Exam", "subject": "Physics", "level": "National", "entry_route": "Independent",
     "known_official": ["https://www.iapt.org.in/"],
     "wikipedia": None},

    # === SCHOOL LEVEL — SOF (8) ===
    {"id": "SOF_NSO", "name": "National Science Olympiad (SOF)", "subject": "Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://sofworld.org/nso"],
     "wikipedia": None, "sof": True},

    {"id": "SOF_IMO", "name": "International Mathematics Olympiad (SOF)", "subject": "Mathematics", "level": "School", "entry_route": "Via School",
     "known_official": ["https://sofworld.org/imo"],
     "wikipedia": None, "sof": True},

    {"id": "SOF_IEO", "name": "International English Olympiad (SOF)", "subject": "English", "level": "School", "entry_route": "Via School",
     "known_official": ["https://sofworld.org/ieo"],
     "wikipedia": None, "sof": True},

    {"id": "SOF_NCO", "name": "National Cyber Olympiad (SOF)", "subject": "Computer Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://sofworld.org/nco"],
     "wikipedia": None, "sof": True},

    {"id": "SOF_ICO", "name": "International Commerce Olympiad (SOF)", "subject": "Economics", "level": "School", "entry_route": "Via School",
     "known_official": ["https://sofworld.org/ico"],
     "wikipedia": None, "sof": True},

    {"id": "SOF_ICSO", "name": "International Computer Science Olympiad (SOF)", "subject": "Computer Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://sofworld.org/icso"],
     "wikipedia": None, "sof": True},

    {"id": "SOF_IGKO", "name": "International General Knowledge Olympiad (SOF)", "subject": "General Knowledge", "level": "School", "entry_route": "Via School",
     "known_official": ["https://sofworld.org/igko"],
     "wikipedia": None, "sof": True},

    {"id": "SOF_ISSO", "name": "International Social Studies Olympiad (SOF)", "subject": "History", "level": "School", "entry_route": "Via School",
     "known_official": ["https://sofworld.org/isso"],
     "wikipedia": None, "sof": True},

    # === SCHOOL LEVEL — Silverzone (5) ===
    {"id": "SZ_iOM", "name": "Silverzone International Olympiad of Mathematics", "subject": "Mathematics", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.silverzone.org/"],
     "wikipedia": None, "silverzone": True},

    {"id": "SZ_iOS", "name": "Silverzone International Olympiad of Science", "subject": "Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.silverzone.org/"],
     "wikipedia": None, "silverzone": True},

    {"id": "SZ_iIO", "name": "Silverzone International Informatics Olympiad", "subject": "Computer Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.silverzone.org/"],
     "wikipedia": None, "silverzone": True},

    {"id": "SZ_iOEL", "name": "Silverzone International Olympiad of English Language", "subject": "English", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.silverzone.org/"],
     "wikipedia": None, "silverzone": True},

    {"id": "SZ_SKGKO", "name": "Silverzone Smart Kid General Knowledge Olympiad", "subject": "General Knowledge", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.silverzone.org/"],
     "wikipedia": None, "silverzone": True},

    # === SCHOOL LEVEL — Unified Council (4) ===
    {"id": "UC_NSTSE", "name": "National Science Talent Search Examination", "subject": "Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.unifiedcouncil.com/"],
     "wikipedia": None, "unified": True},

    {"id": "UC_UCO", "name": "Unified Cyber Olympiad", "subject": "Computer Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.unifiedcouncil.com/"],
     "wikipedia": None, "unified": True},

    {"id": "UC_UIEO", "name": "Unified International English Olympiad", "subject": "English", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.unifiedcouncil.com/"],
     "wikipedia": None, "unified": True},

    {"id": "UC_UIMO", "name": "Unified International Mathematics Olympiad", "subject": "Mathematics", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.unifiedcouncil.com/"],
     "wikipedia": None, "unified": True},

    # === SCHOOL LEVEL — AMTI (3) ===
    {"id": "NMTC_JR", "name": "National Math Talent Contest Junior", "subject": "Mathematics", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.mtai.org.in/"],
     "wikipedia": None},

    {"id": "NMTC_INTER", "name": "National Math Talent Contest Inter", "subject": "Mathematics", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.mtai.org.in/"],
     "wikipedia": None},

    {"id": "NMTC_SR", "name": "National Math Talent Contest Senior", "subject": "Mathematics", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.mtai.org.in/"],
     "wikipedia": None},

    # === SCHOOL LEVEL — CREST (4) ===
    {"id": "CREST_CMO", "name": "CREST Mathematics Olympiad", "subject": "Mathematics", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.crestolympiads.com/crest-mathematics-olympiad-cmo"],
     "wikipedia": None},

    {"id": "CREST_CSO", "name": "CREST Science Olympiad", "subject": "Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.crestolympiads.com/crest-science-olympiad-cso"],
     "wikipedia": None},

    {"id": "CREST_CEO", "name": "CREST English Olympiad", "subject": "English", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.crestolympiads.com/crest-english-olympiad-ceo"],
     "wikipedia": None},

    {"id": "CREST_CCO", "name": "CREST Cyber Olympiad", "subject": "Computer Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.crestolympiads.com/crest-cyber-olympiad-cco"],
     "wikipedia": None},

    # === SCHOOL LEVEL — Board-adjacent (2) ===
    {"id": "CBSE_SC", "name": "CBSE Science Challenge", "subject": "Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.cbse.gov.in/"],
     "wikipedia": None},

    {"id": "CBSE_HC", "name": "CBSE Heritage Challenge", "subject": "History", "level": "School", "entry_route": "Via School",
     "known_official": ["https://www.cbse.gov.in/"],
     "wikipedia": None},

    # === SCHOOL LEVEL — AI/Tech (3) ===
    {"id": "INOI", "name": "Indian National Olympiad in Informatics", "subject": "Computer Science", "level": "National", "entry_route": "Independent",
     "known_official": ["https://www.iarcs.org.in/inoi/"],
     "wikipedia": "Indian_National_Olympiad_in_Informatics"},

    {"id": "GOOGLE_CCE", "name": "Google Code Challenge for Education", "subject": "Computer Science", "level": "School", "entry_route": "Independent",
     "known_official": [],
     "wikipedia": None},

    {"id": "AI4ALL", "name": "AI for All Student Challenge", "subject": "Computer Science", "level": "School", "entry_route": "Independent",
     "known_official": [],
     "wikipedia": None},

    # === Other (1) ===
    {"id": "HBCSE_NSO", "name": "HBCSE Science Olympiad", "subject": "Science", "level": "School", "entry_route": "Via School",
     "known_official": ["https://olympiads.hbcse.tifr.res.in/"],
     "wikipedia": None, "hbcse": True},
]


# ══════════════════════════════════════════════════════════════════════════════
#  TEMPLATE URL GENERATORS
# ══════════════════════════════════════════════════════════════════════════════

def slugify(name):
    """Convert name to URL slug."""
    s = name.lower()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s]+', '-', s)
    s = re.sub(r'-+', '-', s)
    return s.strip('-')


def generate_template_urls(olympiad):
    """Generate predictable URLs from known site patterns."""
    oid = olympiad["id"]
    name = olympiad["name"]
    slug = slugify(name)

    urls = {
        "tier1": list(olympiad.get("known_official", [])),  # copy
        "tier2": [],
        "tier3": [],
    }

    # Wikipedia (Tier 2)
    wp = olympiad.get("wikipedia")
    if wp:
        urls["tier2"].append(f"https://en.wikipedia.org/wiki/{wp}")

    # Allen.in (Tier 3) — try multiple slug patterns
    # Allen uses: allen.in/olympiad/international-mathematics-olympiad (not "mathematical")
    urls["tier3"].append(f"https://allen.in/olympiad/{slug}")
    # Also try with common word swaps
    alt_slug = slug.replace("mathematical", "mathematics").replace("in-informatics", "informatics")
    if alt_slug != slug:
        urls["tier3"].append(f"https://allen.in/olympiad/{alt_slug}")

    # PW Live (Tier 3) — try both full slug and short form
    # PW uses: pw.live/olympiad/exams/imo, pw.live/olympiad/exams/inmo
    oid_lower = oid.lower().replace("_", "-")
    urls["tier3"].append(f"https://www.pw.live/olympiad/exams/{oid_lower}")
    urls["tier3"].append(f"https://www.pw.live/olympiad/exams/{slug}")

    # Aakash (Tier 3) — for India national olympiads
    if olympiad.get("hbcse") or olympiad["level"] == "National":
        urls["tier3"].append(f"https://www.aakash.ac.in/olympiad/{slug}")
        urls["tier3"].append(f"https://www.aakash.ac.in/olympiad/{oid_lower}-{slug.split('-')[-1]}")

    # Vedantu (Tier 3)
    urls["tier3"].append(f"https://www.vedantu.com/olympiad/{slug}")
    urls["tier3"].append(f"https://www.vedantu.com/olympiad/{oid_lower}-exam")

    return urls


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI URL DISCOVERY (for olympiads with no known_official URLs)
# ══════════════════════════════════════════════════════════════════════════════

def _get_vertex_token():
    with _token_lock:
        now = time.time()
        if _token_cache["token"] and (now - _token_cache["timestamp"]) < 3000:
            return _token_cache["token"]
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
        token = json.loads(token_result.stdout).get("access_token")
        if token:
            with _token_lock:
                _token_cache["token"] = token
                _token_cache["timestamp"] = time.time()
            return token
    except Exception as e:
        print(f"  [AUTH ERROR] {e}")
    return None


def _call_gemini(prompt, max_tokens=8000):
    """Generic Gemini call. Returns parsed JSON or empty dict on failure."""
    token = _get_vertex_token()
    if not token:
        print("  [WARN] Could not get Gemini token")
        return {}

    endpoint = (
        f"https://aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}"
        f"/locations/{VERTEX_LOCATION}/publishers/google/models/{GEMINI_MODEL}:generateContent"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens,
                             "responseMimeType": "application/json"}
    }

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            tmp.write(json.dumps(payload))
            tmp_path = tmp.name
        result = subprocess.run(
            ["curl", "-s", "-w", "\n%{http_code}", "-X", "POST",
             "-H", "Content-Type: application/json",
             "-H", f"Authorization: Bearer {token}", endpoint,
             "-d", f"@{tmp_path}"],
            capture_output=True, text=True, timeout=120,
        )
    except Exception:
        return {}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    output = result.stdout.strip()
    lines = output.rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else output
    try:
        response = json.loads(body)
        text = ""
        for part in response.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            if "text" in part:
                text += part["text"]
        return json.loads(text.strip())
    except (json.JSONDecodeError, IndexError, KeyError):
        return {}


def ask_gemini_for_all_urls(olympiads_list):
    """
    Ask Gemini for ALL URLs (official + Wikipedia + aggregators) for all olympiads.
    Returns: {olympiad_id: {"official": [...], "wikipedia": "...", "aggregators": [...]}}
    """
    if not olympiads_list:
        return {}

    items = "\n".join(f"- {o['id']}: {o['name']} (Level: {o['level']}, Subject: {o['subject']})"
                      for o in olympiads_list)

    prompt = f"""For each of the following olympiads/competitions, provide URLs for scraping their data.

IMPORTANT DISTINCTIONS:
- "International Mathematical Olympiad" (IMO, imo-official.org) is DIFFERENT from "SOF International Mathematics Olympiad" (SOF IMO, sofworld.org/imo). Do NOT confuse them.
- "International English Olympiad (SOF)" is a school-level exam by SOF. It is NOT the same as any international-level English competition.
- For SOF olympiads, the official URL is sofworld.org/nso, sofworld.org/imo, etc. Allen/PW pages for SOF exams may have different slugs.
- For HBCSE olympiads (IOQM, RMO, INMO, NSE*, IN*), the official URL is olympiads.hbcse.tifr.res.in

For each olympiad provide:
1. "official" — 1-3 official website URLs (the .org/.com run by the olympiad body itself, plus any 2026-specific event page)
2. "wikipedia" — the Wikipedia article URL (null if no article exists)
3. "aggregators" — 1-3 URLs from Indian education sites that have a DEDICATED page about THIS SPECIFIC olympiad. Must be the correct page — not a generic olympiad listing page.
   Known working aggregator domains and their URL patterns:
   - allen.in (NOT allen.ac.in): e.g. https://allen.in/olympiad/international-physics-olympiad
   - www.pw.live: e.g. https://www.pw.live/olympiad/exams/imo
   - www.aakash.ac.in: e.g. https://www.aakash.ac.in/olympiad/inmo-indian-national-mathematical-olympiad
   - www.vedantu.com: e.g. https://www.vedantu.com/olympiad/inmo-exam
   Use ONLY these domains for aggregators. Do NOT use allen.ac.in (that's a different site).

Rules:
- Only include URLs you are CONFIDENT are correct.
- If unsure whether a page exists, use null or empty array.
- For aggregator URLs, the page must be specifically about THIS olympiad, not a general olympiad overview page.
- Return null for wikipedia if no Wikipedia article exists for this olympiad.

Olympiads:
{items}

Return a JSON object where keys are olympiad IDs:
{{
  "IMO": {{
    "official": ["https://www.imo-official.org/"],
    "wikipedia": "https://en.wikipedia.org/wiki/International_Mathematical_Olympiad",
    "aggregators": ["https://www.pw.live/olympiad/exams/imo"]
  }},
  ...
}}

Return ONLY the JSON object."""

    print(f"  Asking Gemini for URLs for {len(olympiads_list)} olympiads...")
    result = _call_gemini(prompt, max_tokens=12000)
    if result:
        print(f"  Gemini returned URLs for {len(result)} olympiads")
    else:
        print(f"  [WARN] Gemini returned empty result")
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  URL VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

def verify_url(url):
    """HTTP HEAD request to check if URL is alive. Returns (url, status_code, ok).
    Treats 403 as OK — many official sites block bots but work in browsers."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return (url, resp.status, True)
    except urllib.error.HTTPError as e:
        # 403 Forbidden = site exists but blocks bots. Treat as alive.
        # 404 Not Found = genuinely dead. Treat as dead.
        if e.code in (403, 401, 405, 406):
            return (url, e.code, True)  # site exists, just blocking us
        return (url, e.code, e.code < 400)
    except Exception:
        # Some sites block HEAD — try GET with small read
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read(1024)  # read just 1KB
                return (url, resp.status, True)
        except urllib.error.HTTPError as e:
            if e.code in (403, 401, 405, 406):
                return (url, e.code, True)  # site exists, just blocking us
            return (url, e.code, False)
        except Exception:
            return (url, 0, False)


def verify_all_urls(all_urls, max_workers=20):
    """Verify a list of URLs in parallel. Returns dict of {url: (status, ok)}."""
    results = {}
    unique_urls = list(set(all_urls))
    print(f"  Verifying {len(unique_urls)} unique URLs ({max_workers} workers)...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(verify_url, url): url for url in unique_urls}
        done = 0
        for future in as_completed(futures):
            url, status, ok = future.result()
            results[url] = (status, ok)
            done += 1
            if done % 50 == 0:
                print(f"    Verified {done}/{len(unique_urls)}...")

    ok_count = sum(1 for _, ok in results.values() if ok)
    print(f"  Verification done: {ok_count}/{len(unique_urls)} URLs alive")
    return results


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build olympiads_urls.json with auto-discovery")
    parser.add_argument("--max", type=int, default=0, help="Max olympiads to process (0=all)")
    parser.add_argument("--skip-verify", action="store_true", help="Skip HTTP URL verification")
    parser.add_argument("--skip-gemini", action="store_true", help="Skip Gemini URL discovery")
    args = parser.parse_args()

    olympiads = OLYMPIADS
    if args.max > 0:
        olympiads = olympiads[:args.max]

    print(f"Building URLs for {len(olympiads)} olympiads...")

    # Step 1: Start with hardcoded official URLs
    print(f"\n[Step 1] Loading hardcoded official URLs...")
    url_data = {}
    for o in olympiads:
        url_data[o["id"]] = {
            "name": o["name"],
            "subject": o["subject"],
            "level": o["level"],
            "entry_route": o["entry_route"],
            "tier1_urls": list(o.get("known_official", [])),  # copy
            "tier2_urls": [],
            "tier3_urls": [],
        }
        # Add Wikipedia from hardcoded article name
        wp = o.get("wikipedia")
        if wp:
            url_data[o["id"]]["tier2_urls"].append(f"https://en.wikipedia.org/wiki/{wp}")

    hardcoded_count = sum(len(d["tier1_urls"]) for d in url_data.values())
    wiki_count = sum(len(d["tier2_urls"]) for d in url_data.values())
    print(f"  Hardcoded: {hardcoded_count} official URLs + {wiki_count} Wikipedia URLs")

    # Step 2: Ask Gemini for ALL URLs (official + wikipedia + aggregators)
    # Split into batches of 15 to avoid prompt size limits
    BATCH_SIZE = 15
    if not args.skip_gemini:
        print(f"\n[Step 2] Asking Gemini for all URLs (batches of {BATCH_SIZE})...")
        gemini_result = {}
        for i in range(0, len(olympiads), BATCH_SIZE):
            batch = olympiads[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (len(olympiads) + BATCH_SIZE - 1) // BATCH_SIZE
            print(f"  Batch {batch_num}/{total_batches} ({len(batch)} olympiads)...")
            batch_result = ask_gemini_for_all_urls(batch)
            gemini_result.update(batch_result)
            if batch_num < total_batches:
                time.sleep(2)  # brief pause between batches
        print(f"  Total: Gemini returned URLs for {len(gemini_result)} olympiads")

        # Step 3: Merge Gemini suggestions with hardcoded URLs
        print(f"\n[Step 3] Merging hardcoded + Gemini URLs...")
        added = {"official": 0, "wikipedia": 0, "aggregators": 0}

        for o in olympiads:
            oid = o["id"]
            gemini_data = gemini_result.get(oid, {})

            # Merge official URLs (Tier 1)
            for url in (gemini_data.get("official") or []):
                if url and url not in url_data[oid]["tier1_urls"]:
                    url_data[oid]["tier1_urls"].append(url)
                    added["official"] += 1

            # Merge Wikipedia (Tier 2)
            wp_url = gemini_data.get("wikipedia")
            if wp_url and wp_url not in url_data[oid]["tier2_urls"]:
                url_data[oid]["tier2_urls"].append(wp_url)
                added["wikipedia"] += 1

            # Merge aggregators (Tier 3)
            for url in (gemini_data.get("aggregators") or []):
                if url and url not in url_data[oid]["tier3_urls"]:
                    url_data[oid]["tier3_urls"].append(url)
                    added["aggregators"] += 1

        print(f"  Added from Gemini: +{added['official']} official, +{added['wikipedia']} Wikipedia, +{added['aggregators']} aggregators")
    else:
        print(f"\n[Step 2-3] Skipped (--skip-gemini). Using hardcoded + template URLs only.")
        # Fallback: use template generation for Tier 3 if Gemini skipped
        for o in olympiads:
            oid = o["id"]
            template = generate_template_urls(o)
            url_data[oid]["tier3_urls"] = template["tier3"]

    # Step 3: Verify all URLs
    if not args.skip_verify:
        all_urls = []
        for oid, data in url_data.items():
            all_urls.extend(data["tier1_urls"])
            all_urls.extend(data["tier2_urls"])
            all_urls.extend(data["tier3_urls"])

        print(f"\n[Step 3] Verifying URLs...")
        verification = verify_all_urls(all_urls)

        # Remove dead URLs
        removed = 0
        for oid, data in url_data.items():
            for tier_key in ["tier1_urls", "tier2_urls", "tier3_urls"]:
                original = data[tier_key]
                alive = [u for u in original if verification.get(u, (0, False))[1]]
                dead = [u for u in original if not verification.get(u, (0, False))[1]]
                data[tier_key] = alive
                removed += len(dead)
                for d in dead:
                    status = verification.get(d, (0, False))[0]
                    print(f"    [REMOVED] {oid} {tier_key}: {d} (HTTP {status})")

        print(f"  Removed {removed} dead URLs")

        # Step 3b: Ask Gemini for replacement URLs where Tier 1 is now empty
        if not args.skip_gemini:
            lost_tier1 = []
            for o in olympiads:
                oid = o["id"]
                if oid in url_data and len(url_data[oid]["tier1_urls"]) == 0:
                    lost_tier1.append(o)

            if lost_tier1:
                print(f"\n[Step 3b] {len(lost_tier1)} olympiads lost ALL Tier 1 URLs after verification.")
                print(f"  Asking Gemini for replacement official URLs...")

                # Build a targeted prompt with the dead URLs for context
                items = []
                for o in lost_tier1:
                    oid = o["id"]
                    # Collect the dead tier1 URLs for context
                    dead_urls = [u for u in o.get("known_official", [])
                                 if not verification.get(u, (0, False))[1]]
                    items.append({
                        "id": oid,
                        "name": o["name"],
                        "dead_urls": dead_urls,
                    })

                items_text = "\n".join(
                    f"- {it['id']}: {it['name']}"
                    + (f" (dead URLs: {', '.join(it['dead_urls'])})" if it['dead_urls'] else "")
                    for it in items
                )

                fallback_prompt = f"""The following olympiads/competitions had their official website URLs fail HTTP verification (site may have moved, changed domain, or be temporarily down).

For each, provide the CURRENT working official website URL (2025-2026 cycle).
Return a JSON object where keys are olympiad IDs and values are arrays of 1-2 working URLs.
If you're not confident about the current URL, return an empty array.
Do NOT guess — only provide URLs you are confident are correct and currently active.

Olympiads needing replacement URLs:
{items_text}

Return ONLY the JSON object."""

                token = _get_vertex_token()
                if token:
                    endpoint = (
                        f"https://aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}"
                        f"/locations/{VERTEX_LOCATION}/publishers/google/models/{GEMINI_MODEL}:generateContent"
                    )
                    payload = {
                        "contents": [{"role": "user", "parts": [{"text": fallback_prompt}]}],
                        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4000,
                                             "responseMimeType": "application/json"}
                    }
                    tmp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
                            tmp.write(json.dumps(payload))
                            tmp_path = tmp.name
                        result = subprocess.run(
                            ["curl", "-s", "-w", "\n%{http_code}", "-X", "POST",
                             "-H", "Content-Type: application/json",
                             "-H", f"Authorization: Bearer {token}", endpoint,
                             "-d", f"@{tmp_path}"],
                            capture_output=True, text=True, timeout=60,
                        )
                        output = result.stdout.strip()
                        lines = output.rsplit("\n", 1)
                        body = lines[0] if len(lines) > 1 else output
                        response = json.loads(body)
                        text = ""
                        for part in response.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                            if "text" in part:
                                text += part["text"]
                        fallback_urls = json.loads(text.strip())

                        # Verify the fallback URLs too
                        all_fallback = []
                        for urls in fallback_urls.values():
                            all_fallback.extend(urls)
                        if all_fallback:
                            print(f"  Verifying {len(all_fallback)} Gemini fallback URLs...")
                            fb_verification = verify_all_urls(all_fallback, max_workers=10)

                            added = 0
                            for oid, urls in fallback_urls.items():
                                if oid in url_data:
                                    for u in urls:
                                        if u and fb_verification.get(u, (0, False))[1]:
                                            if u not in url_data[oid]["tier1_urls"]:
                                                url_data[oid]["tier1_urls"].append(u)
                                                added += 1
                                                print(f"    [RECOVERED] {oid}: {u}")
                                        else:
                                            status = fb_verification.get(u, (0, False))[0]
                                            print(f"    [STILL DEAD] {oid}: {u} (HTTP {status})")
                            print(f"  Recovered {added} URLs via Gemini fallback")

                    except Exception as e:
                        print(f"  [ERROR] Gemini fallback failed: {e}")
                    finally:
                        if tmp_path:
                            try:
                                os.unlink(tmp_path)
                            except Exception:
                                pass
            else:
                print(f"\n[Step 3b] All olympiads still have Tier 1 URLs. No fallback needed.")

    else:
        print(f"\n[Step 3] Skipped (--skip-verify)")

    # Step 4: Save olympiads_urls.json
    print(f"\n[Step 4] Saving {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(url_data, f, indent=2, ensure_ascii=False)

    # Summary
    print(f"\n{'='*60}")
    print(f"URL DISCOVERY SUMMARY")
    print(f"{'='*60}")

    total_urls = 0
    no_tier1 = []
    for oid, data in url_data.items():
        t1 = len(data["tier1_urls"])
        t2 = len(data["tier2_urls"])
        t3 = len(data["tier3_urls"])
        total = t1 + t2 + t3
        total_urls += total
        if t1 == 0:
            no_tier1.append(oid)

    print(f"Total olympiads:     {len(url_data)}")
    print(f"Total URLs:          {total_urls}")
    print(f"Avg URLs/olympiad:   {total_urls/len(url_data):.1f}")
    print(f"No Tier 1 (official): {len(no_tier1)}")
    if no_tier1:
        print(f"  Missing official:  {', '.join(no_tier1)}")

    print(f"\nOutput: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
