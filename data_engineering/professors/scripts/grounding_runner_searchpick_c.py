#!/usr/bin/env python3
"""
grounding_runner_searchpick_c.py — Tier C faculty URL discovery, search-pick mode.

Differs from grounding_runner.py in ONE crucial way: the model NEVER generates
a URL. We only ever pick from the URLs Google Search returned as grounding
chunks. Per pair:

  1. Make a grounded Gemini call (Google Search runs; we get groundingChunks).
     The URL the model "emits" is IGNORED — we only want the chunks.
  2. Resolve each chunk's vertexaisearch redirect to its real source URL.
  3. Make a classifier call (NO grounding tool) to pick the index of the most
     relevant URL. The model can only respond with an integer (or -1).
  4. HEAD-verify the chosen URL.

OUTPUTS — structured to mirror Professors_info/output/ for easy merging later.

  output_tier_c/faculty_urls.csv
  output_tier_c/faculty_urls.jsonl
  output_tier_c/universities/<uni>.json
  grounding_tier_c/<uni>__<dept>.json    (raw per-pair audit dump)
  logs/state_tier_c.jsonl                 (resumable state log)

USAGE
  python Professors_info/scripts/grounding_runner_searchpick_c.py --pilot -v
      → first 5 Tier C unis (testing)
  python Professors_info/scripts/grounding_runner_searchpick_c.py --all -v
      → all 23 Tier C unis x 30 depts (690 pairs)
  python Professors_info/scripts/grounding_runner_searchpick_c.py --all --skip-existing -v
      → resume (skip pairs already in state_tier_c.jsonl)
  python Professors_info/scripts/grounding_runner_searchpick_c.py --dry-run --pilot
      → print prompts, no API calls
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import logging
import os
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
#  PATHS & CONFIG
# ══════════════════════════════════════════════════════════════════════════════
PROJECT_ROOT = Path(__file__).resolve().parent.parent       # .../Professors_info
REPO_ROOT = PROJECT_ROOT.parent                              # .../course_data

CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output_tier_c"
UNIS_DIR = OUTPUT_DIR / "universities"
GROUNDING_DIR = PROJECT_ROOT / "grounding_tier_c"
LOGS_DIR = PROJECT_ROOT / "logs"
for d in (OUTPUT_DIR, UNIS_DIR, GROUNDING_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

UNIVERSITIES_CSV = CONFIG_DIR / "universities_tier_C.csv"
DEPARTMENTS_CSV = CONFIG_DIR / "top_30_departments_enriched.csv"
STATE_FILE = LOGS_DIR / "state_tier_c.jsonl"
FLAT_CSV = OUTPUT_DIR / "faculty_urls.csv"
FLAT_JSONL = OUTPUT_DIR / "faculty_urls.jsonl"

VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "ambitio-ds-v2")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
VERTEX_SA_KEY_PATH = os.getenv(
    "VERTEX_SA_KEY_PATH", str(REPO_ROOT / "dashboard" / "gcp-key.json")
)

WORKERS = int(os.getenv("SEARCHPICK_WORKERS", "5"))
REDIRECT_WORKERS = int(os.getenv("SEARCHPICK_REDIRECT_WORKERS", "20"))
MIN_REQUEST_GAP_MS = int(os.getenv("SEARCHPICK_MIN_GAP_MS", "50"))

PILOT_FIRST_N_UNIS = 5    # --pilot processes first N Tier C unis (alphabetical)

log = logging.getLogger("searchpick_c")

# ══════════════════════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════════════════════
_TOKEN = {"token": None, "expires_at": 0.0}
_TOKEN_LOCK = threading.Lock()


def _get_vertex_token() -> Optional[str]:
    with _TOKEN_LOCK:
        if _TOKEN["token"] and time.time() < _TOKEN["expires_at"] - 60:
            return _TOKEN["token"]
        try:
            res = subprocess.run(
                ["gcloud", "auth", "application-default", "print-access-token"],
                capture_output=True, text=True, timeout=15,
            )
            if res.returncode == 0 and res.stdout.strip():
                tok = res.stdout.strip()
                _TOKEN["token"] = tok
                _TOKEN["expires_at"] = time.time() + 3000
                return tok
        except Exception:
            pass

        if not Path(VERTEX_SA_KEY_PATH).exists():
            log.error("Service account key not found: %s", VERTEX_SA_KEY_PATH)
            return None
        try:
            with open(VERTEX_SA_KEY_PATH) as f:
                key = json.load(f)
            now = int(time.time())
            header = {"alg": "RS256", "typ": "JWT"}
            payload = {
                "iss": key["client_email"],
                "scope": "https://www.googleapis.com/auth/cloud-platform",
                "aud": "https://oauth2.googleapis.com/token",
                "iat": now, "exp": now + 3500,
            }

            def b64(o):
                return base64.urlsafe_b64encode(
                    json.dumps(o, separators=(",", ":")).encode()
                ).rstrip(b"=").decode()

            unsigned = f"{b64(header)}.{b64(payload)}"
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".pem") as kf:
                kf.write(key["private_key"])
                kf_path = kf.name
            try:
                with tempfile.NamedTemporaryFile(mode="wb", delete=False) as df:
                    df.write(unsigned.encode())
                    df_path = df.name
                try:
                    sig = subprocess.run(
                        ["openssl", "dgst", "-sha256", "-sign", kf_path, df_path],
                        capture_output=True, timeout=10,
                    )
                    if sig.returncode != 0:
                        log.error("openssl sign failed: %s", sig.stderr.decode()[:200])
                        return None
                    signature = base64.urlsafe_b64encode(sig.stdout).rstrip(b"=").decode()
                finally:
                    os.unlink(df_path)
            finally:
                os.unlink(kf_path)

            jwt = f"{unsigned}.{signature}"
            data = f"grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion={jwt}"
            req = urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=data.encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                tok_data = json.loads(resp.read())
            tok = tok_data["access_token"]
            _TOKEN["token"] = tok
            _TOKEN["expires_at"] = time.time() + tok_data.get("expires_in", 3000)
            return tok
        except Exception as e:
            log.error("JWT auth failed: %s", e)
            return None


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP HELPERS
# ══════════════════════════════════════════════════════════════════════════════
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)
_TIMEOUT = 12
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def resolve_redirect(redirect_url: str) -> Optional[str]:
    """Follow a vertexaisearch grounding-api-redirect URL to its real source."""
    headers = {"User-Agent": _UA, "Accept": "*/*"}
    try:
        req = urllib.request.Request(redirect_url, headers=headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_CTX) as resp:
            final = resp.geturl()
            if "vertexaisearch.cloud.google.com" in final:
                return None
            return final
    except urllib.error.HTTPError as e:
        if e.code in (403, 405, 501):
            try:
                req = urllib.request.Request(redirect_url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_CTX) as resp:
                    final = resp.geturl()
                    if "vertexaisearch.cloud.google.com" in final:
                        return None
                    return final
            except Exception:
                return None
        return None
    except Exception:
        return None


def probe_url(url: str) -> dict:
    headers = {"User-Agent": _UA, "Accept": "*/*"}

    def _do(method):
        req = urllib.request.Request(url, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_CTX) as resp:
            return resp.status, resp.geturl()

    try:
        status, final = _do("HEAD")
        if status in (405, 501) or status >= 400:
            status, final = _do("GET")
    except urllib.error.HTTPError as e:
        if e.code in (403, 405, 501):
            try:
                status, final = _do("GET")
            except urllib.error.HTTPError as e2:
                return {"status": e2.code, "final_url": url, "ok": False}
            except Exception as e2:
                return {"status": 0, "final_url": "", "ok": False, "error": str(e2)}
        else:
            return {"status": e.code, "final_url": url, "ok": False}
    except Exception as e:
        return {"status": 0, "final_url": "", "ok": False, "error": str(e)}

    ok = 200 <= status < 300
    if ok and final and final != url:
        from urllib.parse import urlparse
        if urlparse(final).path in ("", "/"):
            ok = False
    return {"status": status, "final_url": final, "ok": ok}


# ══════════════════════════════════════════════════════════════════════════════
#  RATE LIMITING
# ══════════════════════════════════════════════════════════════════════════════
_rate_lock = threading.Lock()
_last_request = 0.0


def _rate_limit_wait():
    global _last_request
    with _rate_lock:
        now = time.time()
        elapsed = (now - _last_request) * 1000
        if elapsed < MIN_REQUEST_GAP_MS:
            time.sleep((MIN_REQUEST_GAP_MS - elapsed) / 1000.0)
        _last_request = time.time()


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 1: GROUNDED DISCOVERY (we IGNORE the URL the model emits)
# ══════════════════════════════════════════════════════════════════════════════
DISCOVERY_SYSTEM = """You are a research assistant searching Google for a university faculty page.

Your job is to call Google Search 1-3 times with relevant queries to find the \
faculty / academics directory page for a specific university department.

Use queries like: "<university> <department> faculty directory", \
"<university> <department> staff", "<university> <department> people page".

After searching, briefly note what you found. We will pick the best URL ourselves \
from the search results — you do NOT need to return a single URL.

Just confirm you searched and briefly summarise the results in 1-2 sentences."""


def build_discovery_prompt(uni: str, domain: str, dept: str, aliases: str) -> str:
    aliases_clean = "; ".join([a.strip() for a in aliases.split(";") if a.strip()])
    return f"""Find Google Search results for the faculty directory page of:

University: {uni}
Official website: {domain}
Department: {dept}
Also called: {aliases_clean}

Search 2-3 times to surface the best candidate URLs. Briefly summarise findings."""


def gemini_grounded_discovery(uni: str, domain: str, dept: str, aliases: str,
                              max_retries: int = 3) -> dict:
    """Make a grounded call to trigger Google Search. Returns dict with
    grounding_metadata (the chunks we actually care about), text_raw, usage, error."""
    token = _get_vertex_token()
    if not token:
        return {"error": "auth_failed"}

    url = (
        f"https://aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}"
        f"/locations/{VERTEX_LOCATION}/publishers/google/models/{GEMINI_MODEL}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": build_discovery_prompt(uni, domain, dept, aliases)}]}],
        "systemInstruction": {"parts": [{"text": DISCOVERY_SYSTEM}]},
        "tools": [{"googleSearch": {}}],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 512,
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
                capture_output=True, text=True, timeout=120,
            )
        finally:
            os.unlink(body_path)

        raw = res.stdout
        if not raw:
            log.warning("[discovery] empty response (attempt %d): %s", attempt + 1, res.stderr[:200])
            time.sleep(2 + attempt * 3)
            continue
        try:
            resp = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("[discovery] non-JSON response: %s", raw[:200])
            time.sleep(2)
            continue

        if "error" in resp:
            err = resp["error"]
            code = err.get("code", 0)
            msg = err.get("message", "")
            if code in (429, 503) and attempt < max_retries - 1:
                log.warning("[discovery] %d %s — retrying", code, msg[:100])
                time.sleep(10 + attempt * 20)
                continue
            return {"error": f"api_error_{code}: {msg[:300]}"}

        candidates = resp.get("candidates", [])
        if not candidates:
            return {"error": "no_candidates", "raw": resp}
        cand = candidates[0]
        parts = cand.get("content", {}).get("parts", [])
        text_raw = "".join(p.get("text", "") for p in parts if "text" in p)
        gm = cand.get("groundingMetadata", {}) or {}
        usage = resp.get("usageMetadata", {})

        return {
            "text_raw": text_raw,
            "grounding_metadata": gm,
            "usage": usage,
            "error": None,
        }

    return {"error": "max_retries_exceeded"}


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 3: CLASSIFIER (no grounding — pure multiple-choice picker)
# ══════════════════════════════════════════════════════════════════════════════
CLASSIFY_SYSTEM = """You classify URLs. Given a list of candidate URLs (with their search-result \
titles) and a target university + department, return the SINGLE INDEX of the URL most \
likely to be the official faculty / academics directory page for that department.

Reply with a single integer only. Examples of valid responses: 0, 1, 2, 3, -1.
Return -1 if NONE of the candidates look like a faculty directory page (e.g. all are \
news articles, single-prof pages, third-party aggregators, or unrelated pages).
Prefer URLs that:
  - are on the university's official domain (or a faculty-school subdomain)
  - point to a list of multiple faculty members (not a single profile)
  - have words like 'faculty', 'people', 'staff', 'academics', 'personnel',
    'personen', 'medewerkers', 'personale', 'personal', 'membres'
No prose, no explanation, just the integer."""


def build_classify_prompt(uni: str, dept: str, candidates: list[dict]) -> str:
    lines = [
        f"University: {uni}",
        f"Department: {dept}",
        "",
        "Candidate URLs (from Google Search results):",
    ]
    for i, c in enumerate(candidates):
        title = c.get("title") or "(no title)"
        lines.append(f"  [{i}] {c['url']}  (title: {title})")
    lines += [
        "",
        f"Return the index (0..{len(candidates) - 1}) of the URL most likely to "
        f"be the faculty/academics directory page for this department.",
        "Return -1 if none of these look like a faculty directory.",
        "Reply with just the integer.",
    ]
    return "\n".join(lines)


def gemini_classify(uni: str, dept: str, candidates: list[dict]) -> dict:
    if not candidates:
        return {"chosen_index": -1, "raw_text": "", "error": "no_candidates"}

    token = _get_vertex_token()
    if not token:
        return {"chosen_index": None, "raw_text": "", "error": "auth_failed"}

    url = (
        f"https://aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}"
        f"/locations/{VERTEX_LOCATION}/publishers/google/models/{GEMINI_MODEL}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": build_classify_prompt(uni, dept, candidates)}]}],
        "systemInstruction": {"parts": [{"text": CLASSIFY_SYSTEM}]},
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 32,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

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
            capture_output=True, text=True, timeout=60,
        )
    finally:
        os.unlink(body_path)

    raw = res.stdout
    if not raw:
        return {"chosen_index": None, "raw_text": "", "error": f"empty: {res.stderr[:200]}"}
    try:
        resp = json.loads(raw)
    except json.JSONDecodeError:
        return {"chosen_index": None, "raw_text": raw[:200], "error": "non_json"}
    if "error" in resp:
        return {"chosen_index": None, "raw_text": "", "error": f"api: {resp['error'].get('message','')[:200]}"}

    cand = (resp.get("candidates") or [{}])[0]
    parts = cand.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if "text" in p).strip()
    m = re.search(r"-?\d+", text)
    if not m:
        return {"chosen_index": None, "raw_text": text, "error": "no_int_in_response"}
    idx = int(m.group(0))
    if idx == -1:
        return {"chosen_index": -1, "raw_text": text, "error": None}
    if 0 <= idx < len(candidates):
        return {"chosen_index": idx, "raw_text": text, "error": None}
    return {"chosen_index": None, "raw_text": text, "error": f"index_out_of_range:{idx}"}


# ══════════════════════════════════════════════════════════════════════════════
#  ORCHESTRATION — process one pair
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class Pair:
    uni_name: str
    uni_domain: str
    uni_country: str
    dept_name: str
    dept_aliases: str


def slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_")[:60]


def grounding_path(p: Pair) -> Path:
    return GROUNDING_DIR / f"{slug(p.uni_name)}__{slug(p.dept_name)}.json"


def uni_json_path(uni: str) -> Path:
    return UNIS_DIR / f"{slug(uni)[:80]}.json"


def _save_grounding(pair: Pair, payload: dict):
    grounding_path(pair).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def process_pair(pair: Pair, dry_run: bool = False) -> dict:
    started = time.time()
    state_rec = {
        "university": pair.uni_name,
        "department": pair.dept_name,
        "country": pair.uni_country,
    }

    if dry_run:
        log.info("[DRY] %s || %s", pair.uni_name[:30], pair.dept_name)
        return {**state_rec, "status": "dry_run"}

    # Pass 1: grounded discovery
    api = gemini_grounded_discovery(pair.uni_name, pair.uni_domain,
                                    pair.dept_name, pair.dept_aliases)
    duration_grounding = round(time.time() - started, 2)

    if api.get("error"):
        log.warning("[ERR] %s || %s :: %s",
                    pair.uni_name[:30], pair.dept_name, api["error"])
        _save_grounding(pair, {
            "university": pair.uni_name, "department": pair.dept_name,
            "stage": "grounding", "error": api["error"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        return {**state_rec, "status": "error", "error": api["error"],
                "duration_s": duration_grounding}

    gm = api.get("grounding_metadata") or {}
    chunks_raw = gm.get("groundingChunks") or []
    chunk_records = []
    for c in chunks_raw:
        w = c.get("web") or {}
        if w.get("uri"):
            chunk_records.append({"redirect_url": w["uri"], "title": w.get("title", "")})

    # Persist what we got (even on no-chunks) for audit
    base_payload = {
        "university": pair.uni_name,
        "university_domain": pair.uni_domain,
        "department": pair.dept_name,
        "model": GEMINI_MODEL,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "discovery_text_raw": api.get("text_raw", ""),
        "discovery_usage": api.get("usage", {}),
        "grounding_metadata": gm,
        "chunks": chunk_records,
    }

    if not chunk_records:
        _save_grounding(pair, {**base_payload, "outcome": "no_chunks",
                               "faculty_page_url": None, "verification_status": "no_chunks"})
        log.info("[NO-CHUNKS] %-30s || %-25s",
                 pair.uni_name[:30], pair.dept_name[:25])
        return {**state_rec, "status": "ok", "verification_status": "no_chunks",
                "faculty_page_url": None, "confidence": "not_found",
                "chunk_count": 0, "resolved_chunk_count": 0,
                "duration_s": round(time.time() - started, 2)}

    # Pass 2: resolve redirects
    resolved = []
    with ThreadPoolExecutor(max_workers=REDIRECT_WORKERS) as pool:
        futs = {pool.submit(resolve_redirect, c["redirect_url"]): c for c in chunk_records}
        for fut in as_completed(futs):
            c = futs[fut]
            real = fut.result()
            resolved.append({**c, "real_url": real})

    valid = [r for r in resolved if r["real_url"]]
    base_payload["resolved_chunks"] = resolved

    if not valid:
        _save_grounding(pair, {**base_payload, "outcome": "all_redirects_expired",
                               "faculty_page_url": None,
                               "verification_status": "all_redirects_expired"})
        log.info("[EXPIRED] %-30s || %-25s",
                 pair.uni_name[:30], pair.dept_name[:25])
        return {**state_rec, "status": "ok",
                "verification_status": "all_redirects_expired",
                "faculty_page_url": None, "confidence": "low",
                "chunk_count": len(chunk_records),
                "resolved_chunk_count": 0,
                "duration_s": round(time.time() - started, 2)}

    # Pass 3: classifier
    cands = [{"url": v["real_url"], "title": v["title"]} for v in valid]
    cls = gemini_classify(pair.uni_name, pair.dept_name, cands)
    idx = cls.get("chosen_index")
    base_payload["classify_raw"] = cls.get("raw_text", "")
    base_payload["classify_error"] = cls.get("error")
    base_payload["gemini_chosen_index"] = idx

    if idx is None:
        _save_grounding(pair, {**base_payload, "outcome": "classifier_error",
                               "faculty_page_url": None,
                               "verification_status": "classifier_error"})
        log.warning("[CLS-ERR] %s || %s :: %s",
                    pair.uni_name[:30], pair.dept_name, cls.get("error"))
        return {**state_rec, "status": "ok",
                "verification_status": "classifier_error",
                "faculty_page_url": None, "confidence": "low",
                "chunk_count": len(chunk_records),
                "resolved_chunk_count": len(valid),
                "duration_s": round(time.time() - started, 2)}

    if idx == -1:
        _save_grounding(pair, {**base_payload, "outcome": "no_match",
                               "faculty_page_url": None,
                               "verification_status": "no_match"})
        log.info("[NO-MATCH] %-30s || %-25s",
                 pair.uni_name[:30], pair.dept_name[:25])
        return {**state_rec, "status": "ok",
                "verification_status": "no_match",
                "faculty_page_url": None, "confidence": "not_found",
                "chunk_count": len(chunk_records),
                "resolved_chunk_count": len(valid),
                "gemini_chosen_index": -1,
                "duration_s": round(time.time() - started, 2)}

    chosen = cands[idx]["url"]
    base_payload["chosen_url"] = chosen

    # Pass 4: HEAD verification
    pr = probe_url(chosen)
    base_payload["verification_probe"] = pr

    if pr["ok"]:
        final_url = pr.get("final_url") or chosen
        _save_grounding(pair, {**base_payload, "outcome": "ok",
                               "faculty_page_url": final_url,
                               "verification_status": "ok"})
        dur = round(time.time() - started, 2)
        log.info("[OK] %-30s || %-25s -> %s (%.1fs)",
                 pair.uni_name[:30], pair.dept_name[:25], final_url[:60], dur)
        return {**state_rec, "status": "ok",
                "verification_status": "ok",
                "faculty_page_url": final_url, "confidence": "high",
                "chosen_url": chosen,
                "chunk_count": len(chunk_records),
                "resolved_chunk_count": len(valid),
                "gemini_chosen_index": idx,
                "duration_s": dur}

    _save_grounding(pair, {**base_payload, "outcome": "chosen_dead",
                           "faculty_page_url": None,
                           "verification_status": "chosen_dead"})
    log.info("[CHOSEN-DEAD] %s || %s", pair.uni_name[:30], pair.dept_name)
    return {**state_rec, "status": "ok",
            "verification_status": "chosen_dead",
            "faculty_page_url": None, "confidence": "low",
            "chosen_url": chosen,
            "chunk_count": len(chunk_records),
            "resolved_chunk_count": len(valid),
            "gemini_chosen_index": idx,
            "duration_s": round(time.time() - started, 2)}


# ══════════════════════════════════════════════════════════════════════════════
#  STATE / RESUME
# ══════════════════════════════════════════════════════════════════════════════
def load_done_pairs() -> set[tuple[str, str]]:
    done = set()
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("status") in ("error", "crash"):
                        continue
                    done.add((r["university"], r["department"]))
                except Exception:
                    continue
    return done


_state_lock = threading.Lock()


def append_state(rec: dict):
    with _state_lock:
        with open(STATE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ══════════════════════════════════════════════════════════════════════════════
#  AGGREGATION → output_tier_c/{faculty_urls.csv,jsonl,universities/}
# ══════════════════════════════════════════════════════════════════════════════
def rebuild_outputs():
    per_uni: dict[str, dict] = {}
    for gf in sorted(GROUNDING_DIR.glob("*.json")):
        try:
            d = json.loads(gf.read_text(encoding="utf-8"))
        except Exception:
            continue
        uni = d.get("university")
        if not uni:
            continue
        if uni not in per_uni:
            per_uni[uni] = {
                "university": uni,
                "university_domain": d.get("university_domain"),
                "departments": {},
            }
        per_uni[uni]["departments"][d["department"]] = {
            "faculty_page_url": d.get("faculty_page_url"),
            "verification_status": d.get("verification_status"),
            "chosen_url": d.get("chosen_url"),
            "gemini_chosen_index": d.get("gemini_chosen_index"),
            "outcome": d.get("outcome"),
            "grounding_file": gf.name,
            "timestamp": d.get("timestamp"),
        }

    for uni, rec in per_uni.items():
        uni_json_path(uni).write_text(
            json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    fields = [
        "university", "department", "faculty_page_url",
        "verification_status", "outcome",
        "chosen_url", "gemini_chosen_index",
        "grounding_file", "timestamp",
    ]
    with open(FLAT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for uni, rec in sorted(per_uni.items()):
            for dept, d in rec["departments"].items():
                w.writerow({
                    "university": uni,
                    "department": dept,
                    "faculty_page_url": d.get("faculty_page_url") or "",
                    "verification_status": d.get("verification_status") or "",
                    "outcome": d.get("outcome") or "",
                    "chosen_url": d.get("chosen_url") or "",
                    "gemini_chosen_index": "" if d.get("gemini_chosen_index") is None else d["gemini_chosen_index"],
                    "grounding_file": d.get("grounding_file", ""),
                    "timestamp": d.get("timestamp", ""),
                })
    with open(FLAT_JSONL, "w", encoding="utf-8") as f:
        for uni, rec in sorted(per_uni.items()):
            for dept, d in rec["departments"].items():
                f.write(json.dumps({"university": uni, "department": dept, **d},
                                   ensure_ascii=False) + "\n")
    log.info("Aggregated %d unis into %s + %s + universities/",
             len(per_uni), FLAT_CSV.name, FLAT_JSONL.name)


# ══════════════════════════════════════════════════════════════════════════════
#  LOAD INPUTS
# ══════════════════════════════════════════════════════════════════════════════
def load_universities(pilot: bool = False) -> list[dict]:
    with open(UNIVERSITIES_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if pilot:
        rows = rows[:PILOT_FIRST_N_UNIS]
    return rows


def load_departments() -> list[dict]:
    with open(DEPARTMENTS_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


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
    p = argparse.ArgumentParser(description="Tier C faculty URL discovery (search-pick mode)")
    p.add_argument("--pilot", action="store_true", help=f"Run only first {PILOT_FIRST_N_UNIS} Tier C unis (testing)")
    p.add_argument("--all", action="store_true", help="Run all 23 Tier C unis x 30 depts")
    p.add_argument("--university", help="Run one specific university")
    p.add_argument("--dept", help="Run one specific department")
    p.add_argument("--limit", type=int, help="Cap total pairs (debug)")
    p.add_argument("--skip-existing", action="store_true",
                   help="Skip pairs already in state_tier_c.jsonl")
    p.add_argument("--dry-run", action="store_true", help="Print plan, no API calls")
    p.add_argument("--rebuild-outputs", action="store_true",
                   help="Re-aggregate from grounding_tier_c/, no API calls")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    setup_logging(args.verbose)

    if args.rebuild_outputs:
        rebuild_outputs()
        return

    if not UNIVERSITIES_CSV.exists():
        log.error("Tier C CSV not found: %s", UNIVERSITIES_CSV)
        sys.exit(1)

    universities = load_universities(pilot=args.pilot)
    if args.university:
        universities = [u for u in universities if u["university_name"] == args.university]
    if not universities:
        log.error("No universities matched")
        sys.exit(1)

    depts = load_departments()
    if args.dept:
        depts = [d for d in depts if d["department_name"] == args.dept]

    pairs = []
    for u in universities:
        for d in depts:
            pairs.append(Pair(
                uni_name=u["university_name"],
                uni_domain=u.get("official_website", ""),
                uni_country=u.get("country", ""),
                dept_name=d["department_name"],
                dept_aliases=d.get("common_aliases", ""),
            ))

    if args.skip_existing:
        done = load_done_pairs()
        before = len(pairs)
        pairs = [p for p in pairs if (p.uni_name, p.dept_name) not in done
                 and not grounding_path(p).exists()]
        log.info("Skipped %d already-done pairs; %d remaining.", before - len(pairs), len(pairs))

    if args.limit:
        pairs = pairs[:args.limit]

    log.info("Plan: %d unis x %d depts = %d pairs. mode=%s, workers=%d, model=%s",
             len(universities), len(depts), len(pairs),
             "pilot" if args.pilot else ("single" if args.university or args.dept else "all"),
             WORKERS, GEMINI_MODEL)

    if args.dry_run:
        for pr in pairs[:5]:
            log.info("[DRY] %s || %s", pr.uni_name, pr.dept_name)
        return

    if not pairs:
        log.info("Nothing to do.")
        return

    if not _get_vertex_token():
        log.error("Auth failed — check key at %s", VERTEX_SA_KEY_PATH)
        sys.exit(1)
    log.info("Auth OK (project=%s, location=%s)", VERTEX_PROJECT, VERTEX_LOCATION)

    started_all = time.time()
    counts = {"ok": 0, "no_chunks": 0, "no_match": 0, "all_redirects_expired": 0,
              "chosen_dead": 0, "classifier_error": 0, "error": 0}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(process_pair, pr, args.dry_run): pr for pr in pairs}
        for i, fut in enumerate(as_completed(futs), 1):
            pr = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:
                log.error("[CRASH] %s || %s :: %s", pr.uni_name, pr.dept_name, e)
                rec = {"university": pr.uni_name, "department": pr.dept_name,
                       "status": "crash", "error": str(e)}
            append_state(rec)

            if rec.get("status") == "error":
                counts["error"] += 1
            else:
                vs = rec.get("verification_status", "?")
                if vs == "ok":
                    counts["ok"] += 1
                elif vs in counts:
                    counts[vs] += 1

            if i % 25 == 0 or i == len(pairs):
                elapsed = (time.time() - started_all) / 60
                log.info("[progress] %d / %d (%.1f min, ok=%d, no_chunks=%d, no_match=%d, expired=%d, dead=%d, err=%d)",
                         i, len(pairs), elapsed,
                         counts["ok"], counts["no_chunks"], counts["no_match"],
                         counts["all_redirects_expired"], counts["chosen_dead"],
                         counts["error"])

    rebuild_outputs()
    elapsed_min = (time.time() - started_all) / 60
    log.info("=" * 60)
    log.info("RUN COMPLETE — %d pairs in %.1f min", len(pairs), elapsed_min)
    for k, v in counts.items():
        log.info("  %-25s  %d", k, v)
    log.info("Outputs:")
    log.info("  %s", FLAT_CSV)
    log.info("  %s", FLAT_JSONL)
    log.info("  %s/  (per-uni JSONs)", UNIS_DIR)
    log.info("  %s/  (per-pair audit dumps)", GROUNDING_DIR)


if __name__ == "__main__":
    main()
