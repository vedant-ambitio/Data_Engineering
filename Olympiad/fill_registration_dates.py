#!/usr/bin/env python3
"""
fill_registration_dates.py — Use Gemini web search to directly find registration dates
========================================================================================

For each org group of olympiads missing registration_close_date:
  1. Ask Gemini (web-grounded): "Search the official site and find the
     registration close date and exam dates for these olympiads in 2026-27"
  2. Gemini searches the web, reads the pages, returns dates
  3. Save results to olympiad_data/registration_patch/{olympiad_id}.json

No scraping needed — Gemini does the web search itself.

Usage:
  python fill_registration_dates.py                  # all missing
  python fill_registration_dates.py --max 3          # first 3 org groups
  python fill_registration_dates.py --dry-run        # preview prompts
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

# ── Config ──────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"
MAX_OUTPUT_TOKENS = 8000

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
VERTEX_SA_KEY_PATH = os.path.join(PROJECT_ROOT, "dashboard", "gcp-key.json")

RAW_DIR = os.path.join(SCRIPT_DIR, "olympiad_data", "raw")
PATCH_DIR = os.path.join(SCRIPT_DIR, "olympiad_data", "registration_patch")

# Token cache
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
        print(f"[ERROR] Auth failed: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI CALL (web-grounded, no JSON mode)
# ══════════════════════════════════════════════════════════════════════════════

def gemini_search(prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.1, attempt=0):
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
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
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
            return gemini_search(prompt, max_tokens, temperature, attempt + 1)
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
            return gemini_search(prompt, max_tokens, temperature, attempt + 1)
        return {"text": "", "error": "Rate limited"}

    if http_code >= 500:
        if attempt < 2:
            time.sleep(5 * (2 ** attempt))
            return gemini_search(prompt, max_tokens, temperature, attempt + 1)
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
#  ORG GROUPS — batch by organization
# ══════════════════════════════════════════════════════════════════════════════

ORG_GROUPS = {
    "SOF": {
        "ids": ["SOF_ICSO", "SOF_IEO", "SOF_IGKO", "SOF_IMO", "SOF_ISSO", "SOF_NCO", "SOF_NSO"],
        "org_name": "Science Olympiad Foundation (SOF)",
        "site": "sofworld.org",
    },
    "Silverzone": {
        "ids": ["SZ_SKGKO", "SZ_iIO", "SZ_iOEL", "SZ_iOM", "SZ_iOS"],
        "org_name": "SilverZone Foundation",
        "site": "silverzone.org",
    },
    "UC": {
        "ids": ["UC_UCO", "UC_UIEO", "UC_UIMO"],
        "org_name": "Unified Council",
        "site": "unifiedcouncil.com",
    },
    "CREST": {
        "ids": ["CREST_CCO", "CREST_CEO", "CREST_CMO", "CREST_CSO"],
        "org_name": "CREST Olympiads",
        "site": "crestolympiads.com",
    },
    "HBCSE": {
        "ids": ["HBCSE_NSO", "NSEA", "NSEB", "NSEC", "NSEP", "RMO"],
        "org_name": "HBCSE / IAPT (National Standard Examinations + RMO)",
        "site": "olympiads.hbcse.tifr.res.in and iapt.org.in",
    },
    "INOI": {
        "ids": ["INOI"],
        "org_name": "IARCS Indian National Olympiad in Informatics",
        "site": "iarcs.org.in",
    },
    "INTL_SCIENCE": {
        "ids": ["IChO", "IPhO", "IEO_INTL", "IEarthSO", "IGeO", "IHO", "IJSO", "IOAA", "IOL", "IPO"],
        "org_name": "International Science Olympiads",
        "site": "various official sites",
    },
}

# Olympiad ID → full name mapping (for the prompt)
OLYMPIAD_NAMES = {}


def load_olympiad_names():
    """Load activity_name from raw JSONs."""
    import glob
    for f in sorted(glob.glob(os.path.join(RAW_DIR, "*.json"))):
        with open(f, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        oid = d.get("olympiad_id", os.path.basename(f).replace(".json", ""))
        OLYMPIAD_NAMES[oid] = d.get("activity_name", oid)


# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT
# ══════════════════════════════════════════════════════════════════════════════

def build_prompt(org_name, site, olympiad_entries):
    """
    olympiad_entries: list of (id, name) tuples
    """
    entries_str = "\n".join(f"  - {oid}: {name}" for oid, name in olympiad_entries)

    return f"""Search the web and find the registration close date (last date to register)
for each of these olympiad competitions for the 2026-27 academic year.

Organization: {org_name}
Official site: {site}

Olympiads:
{entries_str}

INSTRUCTIONS:
- Search the official website ({site}) and any reliable education sites.
- For each olympiad, find the LAST DATE schools/students can register.
- If the site says \"forms must reach 30 days before exam date\", show exactly that (e.g., \"30 days before exam date\") or calculate the date if the exam date is known.
- If registration is year-round / rolling / always open, say \"rolling\".
- If the dates are genuinely not announced yet for 2026-27, say \"not_announced\".
- For international olympiads where Indian students qualify via national stage
  (no direct registration), say \"via_national_qualifier\".

Return your answer EXACTLY in this JSON format (inside ```json``` block):

```json
[
  {{
    \"olympiad_id\": \"<id>\",
    \"registration_close_date\": \"<YYYY-MM-DD or 30 days before exam date or rolling or not_announced or via_national_qualifier>\",
    \"source\": \"<where you found this info>\"
  }}
]
```
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Fill registration dates via Gemini web search")
    parser.add_argument("--max", type=int, help="Process at most N org groups")
    parser.add_argument("--groups", type=str, help="Comma-separated list of org keys to process (e.g. SOF,Silverzone)")
    parser.add_argument("--dry-run", action="store_true", help="Show prompts only")
    args = parser.parse_args()

    os.makedirs(PATCH_DIR, exist_ok=True)
    load_olympiad_names()

    if args.groups:
        filter_keys = [k.strip() for k in args.groups.split(",")]
        groups = [(k, v) for k, v in ORG_GROUPS.items() if k in filter_keys]
    else:
        groups = list(ORG_GROUPS.items())

    if args.max:
        groups = groups[:args.max]

    total_ids = sum(len(g["ids"]) for _, g in groups)
    print(f"Searching dates for {len(groups)} org groups ({total_ids} olympiads)\n")

    all_saved = 0

    for i, (org_key, org_info) in enumerate(groups):
        ids = org_info["ids"]
        org_name = org_info["org_name"]
        site = org_info["site"]
        entries = [(oid, OLYMPIAD_NAMES.get(oid, oid)) for oid in ids]

        print(f"[{i+1}/{len(groups)}] {org_key} ({len(ids)} olympiads) - {site}")

        prompt = build_prompt(org_name, site, entries)

        if args.dry_run:
            print(f"  [DRY RUN] Would search for: {[e[0] for e in entries]}")
            continue

        result = gemini_search(prompt)

        if result.get("error"):
            print(f"  [ERROR] {result['error']}")
            continue

        raw_text = result["text"]

        # Extract JSON from response — try multiple patterns
        data = None

        # Pattern 1: ```json ... ```
        json_match = re.search(r'```json\s*(.*?)\s*```', raw_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Pattern 2: ```json ... (no closing ```, truncated)
        if data is None:
            json_match = re.search(r'```json\s*(.*)', raw_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1).rstrip('`').strip()
                # Try to fix truncated JSON — close any open arrays/objects
                for fix in [json_str, json_str + "]", json_str + "}]", json_str + "\"}]", json_str + " \"source\": \"not found\"}]"]:
                    try:
                        data = json.loads(fix)
                        break
                    except json.JSONDecodeError:
                        continue

        # Pattern 3: raw JSON
        if data is None:
            try:
                data = json.loads(raw_text.strip())
            except json.JSONDecodeError:
                pass

        if data is None:
            print(f"  [PARSE ERROR] Could not extract JSON")
            print(f"  Raw: {raw_text[:300]}")
            continue

        if not isinstance(data, list):
            data = [data]

        # Save each olympiad result
        for entry in data:
            oid = entry.get("olympiad_id", "unknown")
            reg_date = entry.get("registration_close_date")
            source = entry.get("source", "")

            patch_file = os.path.join(PATCH_DIR, f"{oid}.json")
            with open(patch_file, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2, ensure_ascii=False)

            status = reg_date if reg_date else "null"
            print(f"  {oid:<15} reg_close: {status:<40}")
            all_saved += 1

        # Delay between Gemini calls
        if i < len(groups) - 1:
            time.sleep(3)

    print(f"\nDone: {all_saved} olympiad patches saved to {PATCH_DIR}/")


if __name__ == "__main__":
    main()
