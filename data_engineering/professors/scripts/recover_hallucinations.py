#!/usr/bin/env python3
"""
recover_hallucinations.py — second-pass URL recovery for caught hallucinations.

Reads existing grounding/*.json files where verification_status == 'all_failed'
(the URLs Gemini originally hallucinated), and tries to recover a real URL by:

  1. Extracting the groundingChunks Google Search returned during the original call
     (these are real URLs that Google indexed — we never used them).
  2. Following each vertexaisearch.cloud.google.com/grounding-api-redirect/...
     redirect to get the unwrapped source URL.
  3. Asking Gemini 2.5 Flash (with NO grounding tool — just text classification)
     to pick the index of the URL most likely to be the faculty directory.
  4. HEAD-verifying the chosen URL.

OUTPUTS — written under Professors_info/output_recovery/ to keep separate from
the main pipeline outputs. Never touches grounding/ or output/.

  output_recovery/recovery.csv             flat CSV of recovery results
  output_recovery/recovery.jsonl           same data as JSONL
  output_recovery/details/<slug>.json      per-row audit dump (chunks tried,
                                            classifier prompt, verification probe)
  logs/recovery_state.jsonl                resumable state log

USAGE
  python Professors_info/scripts/recover_hallucinations.py
      → recover all all_failed rows
  python Professors_info/scripts/recover_hallucinations.py --limit 50
      → cap at first N rows (for testing)
  python Professors_info/scripts/recover_hallucinations.py --skip-existing
      → resume; skip rows already in recovery_state.jsonl
  python Professors_info/scripts/recover_hallucinations.py --dry-run
      → print what would be processed, no API calls
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
#  PATHS & CONFIG
# ══════════════════════════════════════════════════════════════════════════════
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent

GROUNDING_DIR = PROJECT_ROOT / "grounding"
RECOVERY_DIR = PROJECT_ROOT / "output_recovery"
RECOVERY_DETAILS = RECOVERY_DIR / "details"
LOGS_DIR = PROJECT_ROOT / "logs"
for d in (RECOVERY_DIR, RECOVERY_DETAILS, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

RECOVERY_CSV = RECOVERY_DIR / "recovery.csv"
RECOVERY_UNIS = RECOVERY_DIR / "universities"
RECOVERY_UNIS.mkdir(parents=True, exist_ok=True)
RECOVERY_STATE = LOGS_DIR / "recovery_state.jsonl"

VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "ambitio-ds-v2")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
VERTEX_SA_KEY_PATH = os.getenv(
    "VERTEX_SA_KEY_PATH", str(REPO_ROOT / "dashboard" / "gcp-key.json")
)

WORKERS = int(os.getenv("RECOVERY_WORKERS", "8"))
REDIRECT_WORKERS = int(os.getenv("RECOVERY_REDIRECT_WORKERS", "20"))

log = logging.getLogger("recover")

# ══════════════════════════════════════════════════════════════════════════════
#  AUTH (mirrors grounding_runner._get_vertex_token)
# ══════════════════════════════════════════════════════════════════════════════
_TOKEN_CACHE = {"token": None, "expires_at": 0.0}
_TOKEN_LOCK = threading.Lock()


def _get_vertex_token() -> Optional[str]:
    with _TOKEN_LOCK:
        if _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expires_at"] - 60:
            return _TOKEN_CACHE["token"]

        # Prefer gcloud if available (covers user-account fallback)
        try:
            res = subprocess.run(
                ["gcloud", "auth", "application-default", "print-access-token"],
                capture_output=True, text=True, timeout=15,
            )
            if res.returncode == 0 and res.stdout.strip():
                tok = res.stdout.strip()
                _TOKEN_CACHE["token"] = tok
                _TOKEN_CACHE["expires_at"] = time.time() + 3000
                return tok
        except Exception:
            pass

        # Service-account JWT path
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
                "iat": now,
                "exp": now + 3500,
            }

            def b64(obj):
                return base64.urlsafe_b64encode(
                    json.dumps(obj, separators=(",", ":")).encode()
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
                        log.error("openssl signing failed: %s", sig.stderr.decode()[:200])
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
            _TOKEN_CACHE["token"] = tok
            _TOKEN_CACHE["expires_at"] = time.time() + tok_data.get("expires_in", 3000)
            return tok
        except Exception as e:
            log.error("JWT auth failed: %s", e)
            return None


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP HELPERS — redirect resolution + URL verification
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
    """Follow a vertexaisearch grounding-api-redirect URL to its real source URL.
    Returns the final landed URL, or None if expired / errored."""
    headers = {"User-Agent": _UA, "Accept": "*/*"}
    try:
        req = urllib.request.Request(redirect_url, headers=headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_CTX) as resp:
            final = resp.geturl()
            # If still a vertexaisearch URL, the redirect didn't unwrap (TTL expired or error)
            if "vertexaisearch.cloud.google.com" in final:
                return None
            return final
    except urllib.error.HTTPError as e:
        # Try GET if HEAD blocked
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
    """HEAD-then-GET probe for verification. Returns dict with status, ok, final_url."""
    headers = {"User-Agent": _UA, "Accept": "*/*"}

    def _do(method: str):
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
#  GEMINI CLASSIFIER (no grounding tool — pure text)
# ══════════════════════════════════════════════════════════════════════════════
SYSTEM = """You classify URLs. Given a list of candidate URLs (with their search-result \
titles) and a target university + department, return the SINGLE INDEX of the URL most \
likely to be the official faculty / academics directory page for that department.

Reply with a single integer only. Examples of valid responses: 0, 1, 2, 3, -1.
Return -1 if NONE of the candidates look like a faculty directory page (e.g. all are \
news articles, single-prof pages, third-party aggregators, or unrelated pages).
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
    """Call Gemini 2.5 Flash WITHOUT grounding to pick a URL index. Returns
    dict with: chosen_index, raw_text, error."""
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
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 32,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

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
        return {"chosen_index": None, "raw_text": "", "error": f"empty_response: {res.stderr[:200]}"}
    try:
        resp = json.loads(raw)
    except json.JSONDecodeError:
        return {"chosen_index": None, "raw_text": raw[:200], "error": "non_json_response"}
    if "error" in resp:
        return {"chosen_index": None, "raw_text": "", "error": f"api: {resp['error'].get('message','')[:200]}"}

    cand = (resp.get("candidates") or [{}])[0]
    parts = cand.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if "text" in p).strip()

    # Parse: first integer in the response (allow -1)
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
#  ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class RecoveryRow:
    grounding_file: Path
    university: str
    department: str
    original_model_url: Optional[str]
    chunks: list[dict] = field(default_factory=list)


def _load_one_grounding(gf: Path) -> Optional[RecoveryRow]:
    try:
        d = json.loads(gf.read_text(encoding="utf-8"))
    except Exception:
        return None
    ver = d.get("url_verification") or {}
    if ver.get("verification_status") != "all_failed":
        return None
    gm = d.get("grounding_metadata") or {}
    chunks = []
    for c in (gm.get("groundingChunks") or []):
        w = c.get("web") or {}
        uri = w.get("uri")
        if uri:
            chunks.append({"redirect_url": uri, "title": w.get("title", "")})
    return RecoveryRow(
        grounding_file=gf,
        university=d.get("university", ""),
        department=d.get("department", ""),
        original_model_url=(d.get("parsed_output") or {}).get("original_model_url"),
        chunks=chunks,
    )


def load_all_failed_rows() -> list[RecoveryRow]:
    """Walk grounding/ in parallel, return rows where verification_status == all_failed.
    Parallel I/O helps with OneDrive-synced folders where sequential reads stall."""
    files = sorted(GROUNDING_DIR.glob("*.json"))
    log.info("Scanning %d grounding files in parallel...", len(files))
    rows = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for r in pool.map(_load_one_grounding, files):
            if r is not None:
                rows.append(r)
    return rows


def slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_")[:60]


def detail_path(row: RecoveryRow) -> Path:
    return RECOVERY_DETAILS / f"{slug(row.university)}__{slug(row.department)}.json"


def load_done_pairs() -> set[tuple[str, str]]:
    done = set()
    if RECOVERY_STATE.exists():
        with open(RECOVERY_STATE, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done.add((r["university"], r["department"]))
                except Exception:
                    continue
    return done


def append_state(record: dict):
    with open(RECOVERY_STATE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


_state_lock = threading.Lock()


def recover_one(row: RecoveryRow) -> dict:
    """Process one all_failed row end-to-end. Returns a result dict."""
    started = time.time()
    result = {
        "university": row.university,
        "department": row.department,
        "original_model_url": row.original_model_url,
        "chunk_count": len(row.chunks),
        "resolved_chunk_count": 0,
        "gemini_chosen_index": None,
        "chosen_url": None,
        "verification_status": None,
        "verification_status_code": None,
        "final_url": None,
        "recovery_outcome": None,  # recovered | no_chunks | all_redirects_expired | gemini_no_pick | chosen_dead | error
        "duration_s": 0.0,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    audit = {**result, "chunks": [], "classify_raw": "", "classify_error": None}

    if not row.chunks:
        result["recovery_outcome"] = "no_chunks"
        audit["recovery_outcome"] = "no_chunks"
        detail_path(row).write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
        return result

    # 1) Resolve redirects in parallel
    resolved = []
    with ThreadPoolExecutor(max_workers=REDIRECT_WORKERS) as pool:
        futs = {pool.submit(resolve_redirect, c["redirect_url"]): c for c in row.chunks}
        for fut in as_completed(futs):
            chunk = futs[fut]
            real = fut.result()
            entry = {
                "redirect_url": chunk["redirect_url"],
                "title": chunk.get("title", ""),
                "real_url": real,
            }
            resolved.append(entry)

    audit["chunks"] = resolved
    valid = [r for r in resolved if r["real_url"]]
    result["resolved_chunk_count"] = len(valid)

    if not valid:
        result["recovery_outcome"] = "all_redirects_expired"
        audit["recovery_outcome"] = "all_redirects_expired"
        detail_path(row).write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
        return result

    # 2) Classify via Gemini
    cands = [{"url": v["real_url"], "title": v["title"]} for v in valid]
    cls = gemini_classify(row.university, row.department, cands)
    audit["classify_raw"] = cls.get("raw_text", "")
    audit["classify_error"] = cls.get("error")
    idx = cls.get("chosen_index")
    result["gemini_chosen_index"] = idx

    if idx is None:
        result["recovery_outcome"] = "classifier_error"
        audit["recovery_outcome"] = "classifier_error"
        detail_path(row).write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
        return result
    if idx == -1:
        result["recovery_outcome"] = "gemini_no_pick"
        audit["recovery_outcome"] = "gemini_no_pick"
        detail_path(row).write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
        return result

    chosen = cands[idx]["url"]
    result["chosen_url"] = chosen

    # 3) Verify chosen URL
    pr = probe_url(chosen)
    result["verification_status"] = "ok" if pr["ok"] else "dead"
    result["verification_status_code"] = pr["status"]
    if pr["ok"]:
        result["final_url"] = pr.get("final_url") or chosen
        result["recovery_outcome"] = "recovered"
    else:
        result["recovery_outcome"] = "chosen_dead"

    result["duration_s"] = round(time.time() - started, 2)
    audit.update(result)
    detail_path(row).write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def write_outputs(records: list[dict]):
    """Write a clean CSV with only recovered rows + minimal columns, plus
    per-university JSONs grouping recovered URLs by uni (mirrors output/ format).
    Full audit details stay in output_recovery/details/<uni>__<dept>.json."""
    recovered = [r for r in records if r.get("recovery_outcome") == "recovered"]

    # Minimal CSV — only recovered rows, only essential columns
    fields = ["university", "department", "recovery_outcome", "final_url"]
    with open(RECOVERY_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in recovered:
            w.writerow({k: r.get(k, "") for k in fields})

    # Per-uni JSONs (mirrors output/universities/<uni>.json shape)
    by_uni: dict[str, dict] = {}
    for r in recovered:
        uni = r["university"]
        if uni not in by_uni:
            by_uni[uni] = {"university": uni, "departments": {}}
        by_uni[uni]["departments"][r["department"]] = {
            "faculty_page_url": r["final_url"],
            "recovery_outcome": "recovered",
        }
    for uni, payload in by_uni.items():
        out = RECOVERY_UNIS / f"{slug(uni)[:80]}.json"
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    log.info("Wrote %d recovered rows to %s", len(recovered), RECOVERY_CSV.name)
    log.info("Wrote %d per-uni JSONs to %s/", len(by_uni), RECOVERY_UNIS.name)


def setup_logging(verbose: bool):
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
        datefmt="%H:%M:%S",
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, help="Cap rows processed (debug)")
    p.add_argument("--skip-existing", action="store_true",
                   help="Skip rows already in recovery_state.jsonl")
    p.add_argument("--dry-run", action="store_true",
                   help="Print plan, no API calls")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    setup_logging(args.verbose)

    rows = load_all_failed_rows()
    log.info("Found %d all_failed grounding files", len(rows))

    if args.skip_existing:
        done = load_done_pairs()
        before = len(rows)
        rows = [r for r in rows if (r.university, r.department) not in done]
        log.info("Skipped %d already-recovered rows; %d remaining", before - len(rows), len(rows))

    if args.limit:
        rows = rows[:args.limit]
        log.info("Capped at %d rows", len(rows))

    if args.dry_run:
        log.info("[DRY] Would process %d rows. Sample (first 5):", len(rows))
        for r in rows[:5]:
            print(f"  - {r.university} / {r.department}  chunks={len(r.chunks)}  "
                  f"original={r.original_model_url}")
        return

    if not rows:
        log.info("Nothing to do.")
        return

    # Auth check
    if not _get_vertex_token():
        log.error("Auth failed — check VERTEX_SA_KEY_PATH at %s", VERTEX_SA_KEY_PATH)
        sys.exit(1)
    log.info("Auth OK (project=%s, location=%s, model=%s)",
             VERTEX_PROJECT, VERTEX_LOCATION, GEMINI_MODEL)
    log.info("Workers: %d (Gemini), %d (redirect resolution per row)",
             WORKERS, REDIRECT_WORKERS)

    records = []
    started_all = time.time()
    outcomes = {}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(recover_one, r): r for r in rows}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                rec = fut.result()
            except Exception as e:
                row = futs[fut]
                log.warning("Worker crashed on %s/%s: %s", row.university, row.department, e)
                rec = {
                    "university": row.university, "department": row.department,
                    "recovery_outcome": "worker_crash", "final_url": None,
                    "original_model_url": row.original_model_url,
                }
            records.append(rec)
            with _state_lock:
                append_state(rec)
            outcomes[rec.get("recovery_outcome", "?")] = outcomes.get(rec.get("recovery_outcome", "?"), 0) + 1

            if i % 25 == 0 or i == len(rows):
                elapsed = (time.time() - started_all) / 60
                log.info("[progress] %d / %d  (%.1f min, outcomes=%s)",
                         i, len(rows), elapsed, dict(outcomes))

    write_outputs(records)

    elapsed_min = (time.time() - started_all) / 60
    log.info("=" * 60)
    log.info("RECOVERY COMPLETE — %d rows processed in %.1f min", len(records), elapsed_min)
    log.info("Outcomes:")
    for k, v in sorted(outcomes.items(), key=lambda x: -x[1]):
        log.info("  %-25s  %d", k, v)
    log.info("Outputs:")
    log.info("  %s  (clean: only recovered rows, minimal columns)", RECOVERY_CSV)
    log.info("  %s/  (per-uni JSONs — recovered URLs grouped)", RECOVERY_UNIS)
    log.info("  %s/  (per-row audit dumps)", RECOVERY_DETAILS)


if __name__ == "__main__":
    main()
