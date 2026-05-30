#!/usr/bin/env python3
"""
discover_programs.py — Discover summer school programs from university list
============================================================================

Takes universities_list.json (83 universities) and asks Gemini to discover
what pre-college summer programs each university offers for class 9-12 students.

For India universities: discovers ALL programs (in-person + online)
For International universities: discovers ONLY online/remote programs

Output: summer_programs.json — one entry per program with name + URLs

Usage:
  python discover_programs.py                  # all 83 universities
  python discover_programs.py --max 5          # first 5 only (test)
  python discover_programs.py --region India   # India only
  python discover_programs.py --region Online  # International online only
  python discover_programs.py --dry-run        # show what would run
"""

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from datetime import datetime

# ── Paths ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
VERTEX_SA_KEY_PATH = os.path.join(PROJECT_ROOT, "dashboard", "gcp-key.json")

UNIVERSITIES_FILE = os.path.join(SCRIPT_DIR, "universities_list.json")
# For testing: override with --config flag
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "summer_programs.json")

# ── Gemini config ──────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"
VERTEX_PROJECT = "ambitio-ds-v2"
VERTEX_LOCATION = "global"
_token_cache = {"token": None, "timestamp": 0}
_token_lock = threading.Lock()

BATCH_SIZE = 10  # universities per Gemini call
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ── Auth ───────────────────────────────────────────────────────────────────

def _get_vertex_token():
    with _token_lock:
        now = time.time()
        if _token_cache["token"] and (now - _token_cache["timestamp"]) < 3000:
            return _token_cache["token"]
    try:
        if not os.path.exists(VERTEX_SA_KEY_PATH):
            print(f"[ERROR] Key not found: {VERTEX_SA_KEY_PATH}")
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
        print(f"[ERROR] Auth failed: {e}")
    return None


def _call_gemini(prompt, max_tokens=12000):
    """Call Gemini and return parsed JSON or empty dict."""
    token = _get_vertex_token()
    if not token:
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
    http_code = int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else 0

    if http_code == 429:
        print("    [RATE LIMITED] Waiting 30s...")
        time.sleep(30)
        return _call_gemini(prompt, max_tokens)

    try:
        response = json.loads(body)
        text = ""
        for part in response.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            if "text" in part:
                text += part["text"]
        return json.loads(text.strip())
    except (json.JSONDecodeError, IndexError, KeyError):
        return {}


# ── URL verification ───────────────────────────────────────────────────────

def verify_url(url):
    """Check if URL is alive. Returns True/False."""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True
    except Exception:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read(1024)
                return True
        except Exception:
            return False


# ── Discovery prompts ──────────────────────────────────────────────────────

INDIA_PROMPT = """For each of the following INDIAN universities/institutions, list pre-college
summer programs they offer for HIGH SCHOOL students (class 8-12, age 14-18).

RULES:
1. List programs you have seen referenced in news articles, university announcements,
   or education websites. These are real programs that have run in 2024 or 2025.
2. If a university only has internships/fellowships for college students (B.Tech/M.Tech),
   return an empty array — those are NOT high school programs.
3. Do NOT fabricate program names. If unsure whether a HS program exists, return [].
4. For program_url: provide the specific program page URL if you know it.
   If you only know the university homepage, use null — do NOT put the homepage URL.
5. Many IITs (Kanpur, Kharagpur, Roorkee, Guwahati, etc.) do NOT have high school
   summer programs — they only have SURGE/fellowship for college students. Return []
   for these.

For each CONFIRMED program, provide:
- "program_name": exact official name (not made up)
- "program_url": specific program page URL, or null if unknown
- "mode": "In-person" or "Online" or "Hybrid"
- "duration": e.g. "2 weeks", "6 weeks", "5 days"
- "cost_estimate": e.g. "Free", "INR 49,500", or "Unknown"

Universities:
{university_list}

Return a JSON object. Use empty arrays [] for universities with no confirmed HS program.

Return ONLY the JSON object."""


ONLINE_PROMPT = """For each of the following INTERNATIONAL universities, list pre-college
summer programs they offer for HIGH SCHOOL students (age 14-18) that have an
ONLINE/VIRTUAL/REMOTE option.

RULES:
1. List programs you have seen referenced that have a confirmed online/virtual option.
2. Indian students cannot travel abroad — only programs doable from India matter.
3. If a university only offers residential/on-campus programs with NO online option,
   return an empty array [].
4. For program_url: provide the specific program page URL if you know it.
   If you only know the university homepage, use null — do NOT put the homepage URL.
5. Do NOT fabricate program names or URLs. If unsure, return [].
6. Many universities (Dartmouth, Georgetown, Georgia Tech, etc.) may NOT have
   online pre-college options — return [] for these.

For each CONFIRMED program with online option, provide:
- "program_name": exact official name
- "program_url": specific program page URL, or null if unknown
- "mode": "Online"
- "duration": e.g. "2 weeks", "7 weeks"
- "cost_estimate": e.g. "Free", "$4,500", "$6,100", or "Unknown"

Universities:
{university_list}

Return a JSON object. Use empty arrays [] for universities with no confirmed online program.

Return ONLY the JSON object."""


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Discover summer programs from university list")
    parser.add_argument("--max", type=int, default=0, help="Max universities to process (0=all)")
    parser.add_argument("--region", type=str, choices=["India", "Online", "all"], default="all",
                        help="Filter by region")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run")
    parser.add_argument("--config", type=str, default=UNIVERSITIES_FILE, help="University list JSON file")
    args = parser.parse_args()

    # Load university list
    with open(args.config, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Filter out metadata
    universities = {k: v for k, v in raw.items() if k != "_metadata"}

    # Filter by region
    if args.region != "all":
        universities = {k: v for k, v in universities.items() if v["region"] == args.region}

    if args.max > 0:
        universities = dict(list(universities.items())[:args.max])

    # Split into India and Online
    india_unis = {k: v for k, v in universities.items() if v["region"] == "India"}
    online_unis = {k: v for k, v in universities.items() if v["region"] == "Online"}

    print(f"Universities to process: {len(universities)}")
    print(f"  India: {len(india_unis)}")
    print(f"  Online (International): {len(online_unis)}")

    if args.dry_run:
        print("\nWould process:")
        for uid, u in universities.items():
            print(f"  {uid}: {u['name']} ({u['region']})")
        return

    all_programs = {}
    start_time = time.time()

    # ── Process India universities ──────────────────────────────────────
    if india_unis:
        india_list = list(india_unis.items())
        total_batches = (len(india_list) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n[India] Processing {len(india_unis)} universities in {total_batches} batches...")

        for i in range(0, len(india_list), BATCH_SIZE):
            batch = india_list[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            print(f"  Batch {batch_num}/{total_batches} ({len(batch)} universities)...")

            uni_text = "\n".join(
                f"- {uid}: {u['name']} ({u.get('category', '')}) — {u.get('known_url', 'no URL')}"
                for uid, u in batch
            )
            prompt = INDIA_PROMPT.replace("{university_list}", uni_text)
            result = _call_gemini(prompt)

            if result:
                for uid, programs in result.items():
                    if programs:
                        all_programs[uid] = {
                            "university": india_unis.get(uid, {}).get("name", uid),
                            "region": "India",
                            "programs": programs,
                        }
                        print(f"    {uid}: {len(programs)} programs found")
                    else:
                        print(f"    {uid}: no programs found")
            else:
                print(f"    [WARN] Gemini returned empty for batch {batch_num}")

            if batch_num < total_batches:
                time.sleep(2)

    # ── Process International Online universities ───────────────────────
    if online_unis:
        online_list = list(online_unis.items())
        total_batches = (len(online_list) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n[Online] Processing {len(online_unis)} universities in {total_batches} batches...")

        for i in range(0, len(online_list), BATCH_SIZE):
            batch = online_list[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            print(f"  Batch {batch_num}/{total_batches} ({len(batch)} universities)...")

            uni_text = "\n".join(
                f"- {uid}: {u['name']} ({u['country']}) — {u.get('known_url', 'no URL')}"
                for uid, u in batch
            )
            prompt = ONLINE_PROMPT.replace("{university_list}", uni_text)
            result = _call_gemini(prompt)

            if result:
                for uid, programs in result.items():
                    if programs:
                        all_programs[uid] = {
                            "university": online_unis.get(uid, {}).get("name", uid),
                            "region": "Online",
                            "programs": programs,
                        }
                        print(f"    {uid}: {len(programs)} programs found")
                    else:
                        print(f"    {uid}: no online programs found")
            else:
                print(f"    [WARN] Gemini returned empty for batch {batch_num}")

            if batch_num < total_batches:
                time.sleep(2)

    # ── Verify URLs + detect homepage-only URLs ──────────────────────
    print(f"\n[Verify] Checking program URLs...")
    total_programs = 0
    verified_programs = 0
    dead_urls = 0
    homepage_urls = 0
    removed_programs = []

    for uid, data in list(all_programs.items()):
        verified_list = []
        for prog in data.get("programs", []):
            total_programs += 1
            url = prog.get("program_url")

            # Check if URL is just the university homepage (not a program page)
            if url:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                path = parsed.path.rstrip("/")
                # Homepage detection: path is empty, just "/", or very short like "/en"
                if path in ("", "/", "/en", "/en/", "/index.html"):
                    homepage_urls += 1
                    print(f"    [HOMEPAGE — SKIPPED] {uid}: {url[:60]} (not a program page)")
                    removed_programs.append((uid, prog.get("program_name")))
                    continue

            if url and url.startswith("http"):
                alive = verify_url(url)
                prog["url_verified"] = alive
                if alive:
                    verified_programs += 1
                    verified_list.append(prog)
                else:
                    dead_urls += 1
                    print(f"    [DEAD URL] {uid}: {url[:60]}")
                    removed_programs.append((uid, prog.get("program_name")))
            elif url is None:
                # Gemini returned null URL — keep program but mark unverified
                prog["url_verified"] = False
                verified_list.append(prog)
                print(f"    [NO URL] {uid}: {prog.get('program_name', '?')} — kept but unverified")
            else:
                prog["url_verified"] = False

        # Update programs list with only verified ones
        data["programs"] = verified_list
        if not verified_list:
            del all_programs[uid]

    print(f"  Total programs: {total_programs}")
    print(f"  URLs verified: {verified_programs}")
    print(f"  Dead URLs: {dead_urls}")

    # ── Save output ────────────────────────────────────────────────────
    output = {
        "_metadata": {
            "generated": datetime.now().isoformat(),
            "total_universities": len(all_programs),
            "total_programs": total_programs,
            "verified_urls": verified_programs,
            "dead_urls": dead_urls,
            "india_programs": sum(
                len(d["programs"]) for d in all_programs.values() if d["region"] == "India"
            ),
            "online_programs": sum(
                len(d["programs"]) for d in all_programs.values() if d["region"] == "Online"
            ),
        },
        "universities": all_programs,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - start_time

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"SUMMER PROGRAM DISCOVERY SUMMARY")
    print(f"{'='*60}")
    print(f"Universities with programs: {len(all_programs)}/{len(universities)}")
    print(f"Total programs discovered:  {total_programs}")
    print(f"  India (in-person+online): {output['_metadata']['india_programs']}")
    print(f"  International (online):   {output['_metadata']['online_programs']}")
    print(f"URLs verified:              {verified_programs}/{total_programs}")
    print(f"Dead URLs removed:          {dead_urls}")
    print(f"Time: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"\nOutput: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
