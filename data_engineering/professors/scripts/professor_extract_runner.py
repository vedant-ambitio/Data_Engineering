#!/usr/bin/env python3
"""
professor_extract_runner.py — Stage 2 runner: extract structured professor info
from each verified faculty directory URL discovered in Stage 1.

Architecture (cloned from depth_enhance/depth_enhance_runner.py):
- **Claude Opus 4.6 (primary) / Haiku 4.5 (fallback)** on AWS Bedrock,
  region ap-south-1, via the Anthropic Messages API on /invoke.
- **web_fetch tool**: client-side, backed by **Amazon Bedrock AgentCore Browser**
  (managed Chromium in AWS) driven via **Playwright over CDP**. ONE AgentCore
  browser session per (uni, dept) pair, reused across all profile/personal
  fetches inside that directory.
- **NO web_search**: depth_enhance learned that mixing web_search with our
  custom web_fetch tool is rejected by /invoke. We rely on the directory URL
  + alternate_urls already provided by Stage 1.

Input format — JSONL (one row per (uni, dept) pair). Required fields:
  university                : string
  department                : string
  grounding_chunks          : list of {"url": "...", "title": "..."} — Google Search
                              results for "<uni> <dept> faculty". May be empty.
                              Claude picks the most relevant URL itself; if none
                              works, it can navigate from a homepage chunk;
                              if nothing is usable, the pair is logged as skipped.
Optional fields:
  pair_id                   : string (auto-derived from uni+dept slug if missing)
  country, tier, source     : strings (carried through to output)

Built by `consolidate_faculty_urls.py` (Phase 1 — not in this batch).

Authentication:
- **Bedrock Runtime (Claude)**: AWS_BEARER_TOKEN_BEDROCK env var.
- **AgentCore Browser**: standard AWS credentials. On EC2, IAM role is fine;
  for local dev, AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY.

Usage (from course_data/ folder):

  # Dry-run (template prompt + print first 5k chars, no API calls):
  python Professors_info/scripts/professor_extract_runner.py \
      --config Professors_info/scripts/professor_extract_config.json \
      --pair "Stanford University|Computer Science" --dry-run -v

  # One pair, real run:
  python Professors_info/scripts/professor_extract_runner.py \
      --config Professors_info/scripts/professor_extract_config.json \
      --pair "Stanford University|Computer Science" -v

  # Pilot (first N pairs from input):
  python Professors_info/scripts/professor_extract_runner.py \
      --config Professors_info/scripts/professor_extract_config.json \
      --all --skip-existing --limit 25 -v

  # Resume / full batch:
  python Professors_info/scripts/professor_extract_runner.py \
      --config Professors_info/scripts/professor_extract_config.json \
      --all --skip-existing -v

Environment overrides (same names as depth_enhance to keep operational habits):
  AWS_BEARER_TOKEN_BEDROCK      required (Claude calls), unless --dry-run
  AWS_REGION                    default ap-south-1
  AGENTCORE_REGION              default = AWS_REGION
  DEPTH_PRIMARY_MODEL           default global.anthropic.claude-opus-4-6-v1
  DEPTH_FALLBACK_MODEL          default global.anthropic.claude-haiku-4-5-20251001-v1:0
  DEPTH_MAX_TOOL_ITERATIONS     default 30
  DEPTH_MAX_OUTPUT_TOKENS       default 16000
  DEPTH_WEBFETCH_TIMEOUT_S      default 30
  DEPTH_WEBFETCH_MAX_CHARS      default 15000
  DEPTH_BEDROCK_READ_TIMEOUT    default 600
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── core AWS deps ──
try:
    import boto3
    from botocore.exceptions import ClientError
    from botocore.config import Config as BotoConfig
except ImportError:
    boto3 = None
    ClientError = Exception
    BotoConfig = None

# ── AgentCore Browser + Playwright ──
try:
    from bedrock_agentcore.tools.browser_client import browser_session  # type: ignore
    from playwright.sync_api import sync_playwright  # type: ignore
    HAS_AGENTCORE = True
except ImportError:
    browser_session = None
    sync_playwright = None
    HAS_AGENTCORE = False

# ── HTML cleaning deps ──
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
try:
    import html2text
except ImportError:
    html2text = None

PRIMARY_MODEL = os.getenv("DEPTH_PRIMARY_MODEL", "global.anthropic.claude-opus-4-6-v1")
# Default fallback = primary, i.e. on a ThrottlingException the loop sleeps
# 2s and retries the same model. If that also fails the pair is marked
# `status: error` in state.jsonl and the runner moves on; rerun with
# `--skip-existing` to retry failures on a fresh invocation.
# To re-enable a true model downgrade on throttle, set
# DEPTH_FALLBACK_MODEL=global.anthropic.claude-sonnet-4-6-v1 (or another id).
FALLBACK_MODEL = os.getenv("DEPTH_FALLBACK_MODEL", PRIMARY_MODEL)
BEDROCK_REGION = os.getenv("AWS_REGION", "ap-south-1")
AGENTCORE_REGION = os.getenv("AGENTCORE_REGION", BEDROCK_REGION)
MAX_TOOL_ITERATIONS = int(os.getenv("DEPTH_MAX_TOOL_ITERATIONS", "30"))
MAX_OUTPUT_TOKENS = int(os.getenv("DEPTH_MAX_OUTPUT_TOKENS", "16000"))
WEBFETCH_TIMEOUT_MS = int(os.getenv("DEPTH_WEBFETCH_TIMEOUT_S", "30")) * 1000
WEBFETCH_MAX_CHARS = int(os.getenv("DEPTH_WEBFETCH_MAX_CHARS", "15000"))

# Pair-level retry: how many times to re-attempt a pair on top-level failure
# (AgentCore session-open hit account quota, curl timeout, Bedrock throttle
# beyond the inner-loop's single retry, etc.). Each retry starts fresh —
# new AgentCore session, fresh tool-use loop. Backoff is exponential:
# attempt 1 waits BASE, attempt 2 waits BASE*3, attempt 3 waits BASE*9.
# With BASE=5s that's 5s / 15s / 45s before retries 1 / 2 / 3.
MAX_PAIR_RETRIES = int(os.getenv("DEPTH_MAX_PAIR_RETRIES", "3"))
PAIR_RETRY_BASE_SLEEP_SEC = float(os.getenv("DEPTH_PAIR_RETRY_BASE_SLEEP", "5"))

log = logging.getLogger("prof_extract")

# ══════════════════════════════════════════════════════════════════════════════
#  MOJIBAKE CLEANER (kept from depth_enhance)
# ══════════════════════════════════════════════════════════════════════════════
_MOJIBAKE_PAIRS_HEX = [
    ("c2a3", "£"), ("c2a5", "¥"), ("c2a0", " "),
    ("c3a9", "é"), ("c3a8", "è"), ("c3a2", "â"),
    ("c3a7", "ç"), ("c3b6", "ö"), ("c3bc", "ü"),
    ("c3a4", "ä"), ("c3b1", "ñ"),
]
_MOJIBAKE_MAP = [(bytes.fromhex(h).decode("latin-1"), good) for h, good in _MOJIBAKE_PAIRS_HEX]


def clean_mojibake(s: str) -> str:
    if not isinstance(s, str):
        return s
    for bad, good in _MOJIBAKE_MAP:
        if bad in s:
            s = s.replace(bad, good)
    return s


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL SPECS — only client-side web_fetch (web_search disabled — see depth_enhance)
# ══════════════════════════════════════════════════════════════════════════════
TOOL_SPECS = [
    {
        "name": "web_fetch",
        "description": (
            "Fetch an HTTP(S) URL via AWS-managed Chromium (Amazon Bedrock "
            "AgentCore Browser) and return the cleaned visible text content. "
            "Use this for the faculty directory page, every individual professor "
            "profile page, and (if enabled) each professor's personal website. "
            "Handles JavaScript rendering and anti-bot walls."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full HTTP(S) URL"},
            },
            "required": ["url"],
        },
    },
]


# ══════════════════════════════════════════════════════════════════════════════
#  HTML CLEANING (used after AgentCore page.content())
# ══════════════════════════════════════════════════════════════════════════════
def clean_html_to_markdown(html: str) -> str:
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            for t in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "aside"]):
                t.decompose()
            html = str(soup)
        except Exception as e:
            log.debug("[CLEAN] BS4 parse failed, falling back: %s", e)

    if html2text is not None:
        h = html2text.HTML2Text()
        h.ignore_images = True
        h.ignore_links = False
        h.body_width = 0
        text = h.handle(html)
    else:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)

    text = clean_mojibake(text.strip())
    if len(text) > WEBFETCH_MAX_CHARS:
        text = text[:WEBFETCH_MAX_CHARS] + f"\n\n[... truncated at {WEBFETCH_MAX_CHARS} chars ...]"
    return text


# ══════════════════════════════════════════════════════════════════════════════
#  AGENTCORE BROWSER — session + fetch helpers
# ══════════════════════════════════════════════════════════════════════════════
@contextmanager
def agentcore_page(region: Optional[str] = None):
    """Open ONE AgentCore Browser session and yield a Playwright Page object.
    All web_fetch calls for the current pair share this session.
    `region` overrides AGENTCORE_REGION when provided (for multi-region pools)."""
    if not HAS_AGENTCORE:
        raise RuntimeError(
            "bedrock_agentcore or playwright not installed. Run:\n"
            "  pip install bedrock-agentcore playwright\n"
            "  playwright install chromium"
        )
    region = region or AGENTCORE_REGION
    log.info("[AGENTCORE] opening browser session in %s ...", region)
    with browser_session(region) as agent_client:
        ws_url, headers = agent_client.generate_ws_headers()
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(ws_url, headers=headers)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_navigation_timeout(WEBFETCH_TIMEOUT_MS)
            try:
                yield page
            finally:
                try:
                    browser.close()
                except Exception:
                    pass


def fetch_via_agentcore(page, url: str, fetch_cache: Optional[dict] = None) -> str:
    """Navigate the shared AgentCore page to `url` and return cleaned content.
    If fetch_cache is provided, the cleaned text is stored as
    fetch_cache[url] = cleaned_text (raw, NOT prefixed by 'Fetched ...' header).
    The runner uses this cache after the loop to write per-prof personal-site
    markdown files, so we never have to re-fetch the same content."""
    log.info("  [FETCH] %s", url)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=WEBFETCH_TIMEOUT_MS)
    except Exception as e:
        log.warning("  [FETCH-ERR] goto failed: %s", e)
        return f"ERROR navigating to {url}: {e}"
    try:
        html = page.content()
    except Exception as e:
        log.warning("  [FETCH-ERR] page.content() failed: %s", e)
        return f"ERROR reading content from {url}: {e}"
    text = clean_html_to_markdown(html)
    if fetch_cache is not None:
        fetch_cache[url] = text
    return f"Fetched {url} ({len(text)} chars):\n\n{text}"


# ══════════════════════════════════════════════════════════════════════════════
#  BEDROCK CLIENT + TOOL-USE LOOP
# ══════════════════════════════════════════════════════════════════════════════
BEDROCK_READ_TIMEOUT = int(os.getenv("DEPTH_BEDROCK_READ_TIMEOUT", "600"))


def bedrock_runtime_client(region: Optional[str] = None):
    if boto3 is None:
        raise RuntimeError("boto3 not installed; run: pip install boto3")
    region = region or BEDROCK_REGION
    cfg = BotoConfig(
        read_timeout=BEDROCK_READ_TIMEOUT,
        connect_timeout=30,
        retries={"max_attempts": 3, "mode": "standard"},
    ) if BotoConfig else None
    return boto3.client("bedrock-runtime", region_name=region, config=cfg)


def _strip_cache_control(block):
    """Return a copy of a content block with cache_control removed."""
    if isinstance(block, dict) and "cache_control" in block:
        return {k: v for k, v in block.items() if k != "cache_control"}
    return block


def _annotate_messages_for_caching(messages: list) -> list:
    """Add an `ephemeral` cache_control breakpoint to the LAST content block of
    the LAST message. Anthropic caches everything before the breakpoint; the
    next call's identical prefix is read at 10% of normal input cost.

    We strip ALL existing cache_control markers from every message first, then
    add ONE fresh marker at the new last position. Why: the runner appends to
    `messages` across turns, and on each new invocation we want ONE cache
    breakpoint at the latest position (not accumulated from prior turns —
    Anthropic caps cache_control markers at 4 per request).

    Returns a NEW list; the caller's `messages` is unchanged."""
    if not messages:
        return messages

    # Pass 1: strip cache_control from ALL blocks in ALL messages (defensive —
    # the runner doesn't add cache_control anywhere else, but bare-string
    # contents from the first user message can carry it through if the same
    # messages list is annotated repeatedly).
    cleaned: list = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            cleaned.append({
                "role": msg["role"],
                "content": [_strip_cache_control(b) for b in content],
            })
        else:
            # str content or anything else — pass through
            cleaned.append(msg)

    # Pass 2: add ONE cache_control marker at the very end.
    last = cleaned[-1]
    content = last.get("content")
    if isinstance(content, str):
        # Bare-string content (first user message). Promote to block list.
        cleaned[-1] = {
            "role": last["role"],
            "content": [{
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }],
        }
    elif isinstance(content, list) and content:
        new_blocks = list(content[:-1])
        last_block = content[-1]
        if isinstance(last_block, dict):
            last_block = {**last_block, "cache_control": {"type": "ephemeral"}}
        new_blocks.append(last_block)
        cleaned[-1] = {"role": last["role"], "content": new_blocks}
    return cleaned


def invoke_claude(client, model_id: str, messages: list, tools: list,
                  max_tokens: int = MAX_OUTPUT_TOKENS) -> dict:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "tools": tools,
        "messages": _annotate_messages_for_caching(messages),
    }
    resp = client.invoke_model(
        modelId=model_id,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())


def tool_use_loop(client, primary_model: str, fallback_model: str,
                  page, initial_user_text: str, verbose: bool = False) -> dict:
    """Drive the Bedrock Messages API tool-use loop. Returns
    {text, iterations, tokens_in, tokens_out, model_used, fetch_cache}.
    fetch_cache is dict[url -> cleaned_text] of EVERY URL fetched during the
    loop — used afterwards by the runner to write per-prof personal-site
    markdown files without round-tripping the content through the model."""
    messages: list = [{"role": "user", "content": [{"type": "text", "text": initial_user_text}]}]
    iterations = 0
    model_id = primary_model
    using_fallback = False
    total_in = 0
    total_out = 0
    total_cache_read = 0     # input_tokens served from cache (Anthropic billing field)
    total_cache_write = 0    # input_tokens written to cache (Anthropic billing field)
    accumulated_text: list = []
    fetch_cache: dict = {}

    while iterations < MAX_TOOL_ITERATIONS:
        iterations += 1
        try:
            resp = invoke_claude(client, model_id, messages, TOOL_SPECS)
        except ClientError as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if code in ("ThrottlingException", "ServiceUnavailableException",
                        "InternalServerException", "ModelStreamErrorException"):
                if not using_fallback:
                    log.warning("[MODEL] '%s' on primary, falling back to %s", code, fallback_model)
                    model_id = fallback_model
                    using_fallback = True
                    time.sleep(2)
                    continue
            raise

        usage = resp.get("usage", {})
        total_in += usage.get("input_tokens", 0)
        total_out += usage.get("output_tokens", 0)
        total_cache_read += usage.get("cache_read_input_tokens", 0)
        total_cache_write += usage.get("cache_creation_input_tokens", 0)

        content_blocks = resp.get("content", [])
        stop_reason = resp.get("stop_reason")

        messages.append({"role": "assistant", "content": content_blocks})

        turn_text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
        if turn_text:
            accumulated_text.append(turn_text)

        if verbose:
            types = [b.get("type", "?") for b in content_blocks]
            log.debug("[MODEL] stop=%s blocks=%s text_len=%d", stop_reason, types, len(turn_text))

        if stop_reason == "end_turn":
            final_text = "\n".join(t for t in accumulated_text if t)
            return {
                "text": final_text,
                "iterations": iterations,
                "tokens_in": total_in,
                "tokens_out": total_out,
                "cache_read_tokens": total_cache_read,
                "cache_write_tokens": total_cache_write,
                "model_used": model_id,
                "fetch_cache": fetch_cache,
            }

        if stop_reason == "tool_use":
            tool_results = []
            client_tool_calls = [b for b in content_blocks if b.get("type") == "tool_use"]
            if not client_tool_calls:
                log.warning("[MODEL] stop_reason=tool_use but no client tool_use; breaking")
                break
            for b in client_tool_calls:
                tname = b.get("name", "")
                targs = b.get("input", {}) or {}
                tid = b.get("id", "")
                if tname == "web_fetch":
                    result = fetch_via_agentcore(page, targs.get("url", ""), fetch_cache)
                else:
                    result = f"ERROR: unknown client tool '{tname}'"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": result[:50000],
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        if stop_reason in ("max_tokens", "pause_turn"):
            log.warning("[MODEL] stop_reason=%s, asking model to continue the JSON", stop_reason)
            messages.append({"role": "user", "content": (
                "Continue emitting the JSON exactly where you left off. "
                "Do NOT repeat any content already written. Do NOT add any commentary, "
                "preamble, or meta-explanation. Just continue the JSON body so the entire "
                "concatenated output (across turns) is one valid JSON object."
            )})
            continue

        log.warning("[MODEL] unexpected stop_reason: %s; breaking", stop_reason)
        break

    raise RuntimeError(f"Tool loop did not converge in {MAX_TOOL_ITERATIONS} iterations")


# ══════════════════════════════════════════════════════════════════════════════
#  INPUT (JSONL) + OUTPUT-FILE LOOKUP
# ══════════════════════════════════════════════════════════════════════════════
def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")[:80]


def derive_pair_id(uni: str, dept: str) -> str:
    return f"{slug(uni)}__{slug(dept)}"


def load_rows(jsonl_path: Path):
    """Yield row dicts from input JSONL. Skips blank lines and decode errors."""
    with open(jsonl_path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                log.warning("[LOAD] skipping malformed line %d: %s", ln, e)


def output_paths(output_dir: Path, uni: str, dept: str) -> tuple[Path, Path]:
    """Return (uni_dir, dept_json_path)."""
    uni_dir = output_dir / "universities" / slug(uni)
    return uni_dir, uni_dir / f"{slug(dept)}.json"


def personal_sites_dir(uni_dir: Path, dept: str) -> Path:
    """Per-dept folder for professor personal-website markdown files."""
    return uni_dir / f"{slug(dept)}__personal_websites"


def _normalize_url_for_match(u: str) -> str:
    """Normalize a URL for cache lookup — handles trailing slashes, lowercase host."""
    if not u:
        return ""
    u = u.strip()
    # Strip trailing slash variants but keep the rest as-is
    if u.endswith("/"):
        u_alt = u[:-1]
    else:
        u_alt = u + "/"
    return u, u_alt  # type: ignore  # caller treats as tuple


def write_personal_site_files(parsed: dict, fetch_cache: dict,
                              uni_dir: Path, dept: str,
                              max_chars: int) -> int:
    """Walk the parsed JSON's professors[]; for each prof with a
    personal_website_url that we actually fetched, write the cleaned text to
    a markdown file, and add a personal_website_content_file field (relative
    to uni_dir's parent so paths are stable). Returns count of files written."""
    profs = parsed.get("professors") or []
    if not profs:
        return 0

    sites_dir = personal_sites_dir(uni_dir, dept)
    n_written = 0
    name_seen: dict[str, int] = {}

    for prof in profs:
        url = prof.get("personal_website_url")
        if not url or not isinstance(url, str):
            prof["personal_website_content_file"] = None
            continue

        # Try the URL as-emitted, then with/without trailing slash, in case
        # AgentCore canonicalised on the way in.
        candidates = [url]
        if url.endswith("/"):
            candidates.append(url[:-1])
        else:
            candidates.append(url + "/")
        content = None
        for c in candidates:
            if c in fetch_cache:
                content = fetch_cache[c]
                break

        if content is None:
            # The model said personal_website_url=X but never actually
            # web_fetched X (or fetched a redirected URL). No content captured.
            prof["personal_website_content_file"] = None
            continue

        # Build a stable filename from the prof's name; disambiguate dupes.
        prof_slug = slug(prof.get("name", "") or "professor") or "professor"
        seen = name_seen.get(prof_slug, 0)
        name_seen[prof_slug] = seen + 1
        fname = f"{prof_slug}.md" if seen == 0 else f"{prof_slug}_{seen+1}.md"

        sites_dir.mkdir(parents=True, exist_ok=True)
        body = content
        if max_chars and len(body) > max_chars:
            body = body[:max_chars] + f"\n\n[... runner-side cap at {max_chars} chars ...]"

        md_path = sites_dir / fname
        # Light header so the .md file is self-describing without parsing the JSON
        header = (
            f"# {prof.get('name', 'Unknown')} — personal website snapshot\n\n"
            f"- Source URL: {url}\n"
            f"- Captured for: {parsed.get('university', '')} / {parsed.get('department', '')}\n"
            f"- Captured at: {parsed.get('extraction_timestamp_utc', '')}\n\n"
            "---\n\n"
        )
        md_path.write_text(header + body, encoding="utf-8")
        # Path RELATIVE to the per-pair JSON (siblings), so the JSON is portable.
        prof["personal_website_content_file"] = str(md_path.relative_to(uni_dir).as_posix())
        n_written += 1

    return n_written


# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT TEMPLATING
# ══════════════════════════════════════════════════════════════════════════════
def normalize_chunks(raw_chunks) -> list[dict]:
    """Accept either a list of strings or list of dicts; return list of {url, title}."""
    out = []
    if not raw_chunks:
        return out
    for c in raw_chunks:
        if isinstance(c, str):
            out.append({"url": c, "title": ""})
        elif isinstance(c, dict):
            url = c.get("url") or c.get("real_url") or c.get("redirect_url") or ""
            title = c.get("title") or c.get("domain") or ""
            if url:
                out.append({"url": url, "title": title})
    return out


def build_initial_user_prompt(prompt_tpl: str, row: dict, cfg: dict) -> str:
    uni = row.get(cfg.get("university_field", "university"), "")
    dept = row.get(cfg.get("department_field", "department"), "")
    country = row.get(cfg.get("country_field", "country"), "") or ""
    raw_chunks = row.get(cfg.get("grounding_chunks_field", "grounding_chunks"), []) or []
    chunks = normalize_chunks(raw_chunks)

    repl = {
        "{UNIVERSITY}": uni,
        "{COUNTRY}": country,
        "{DEPARTMENT}": dept,
        "{GROUNDING_CHUNKS_JSON}": json.dumps(chunks, ensure_ascii=False),
        "{MAX_PROFESSORS}": str(cfg.get("max_professors_per_directory", 100)),
        "{FETCH_PERSONAL_WEBSITES}": "true" if cfg.get("fetch_personal_websites", True) else "false",
        "{MAX_PERSONAL_SITE_CHARS}": str(cfg.get("max_personal_site_chars", 8000)),
    }
    p = prompt_tpl
    for k, v in repl.items():
        p = p.replace(k, str(v))

    p += (
        "\n\n=== RUNNER OVERRIDES (these take precedence) ===\n"
        "- You ONLY have web_fetch (no web_search, no file tools).\n"
        "- Your FINAL assistant message must be ONE valid JSON object — no prose, "
        "no markdown fences (no ```json ... ```), no commentary.\n"
        "- Anti-hallucination: leave optional fields null, never invent.\n"
    )
    return p


# ══════════════════════════════════════════════════════════════════════════════
#  OUTPUT VALIDATION
# ══════════════════════════════════════════════════════════════════════════════
def extract_json_from_text(text: str) -> tuple[Optional[dict], Optional[str]]:
    """The prompt forbids markdown fences, but defensively strip them. Returns
    (parsed_obj, error_or_None)."""
    if not text:
        return None, "empty model text"
    s = text.strip()
    # Strip optional ```json ... ``` wrapper if model leaks one
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    # Try direct parse
    try:
        return json.loads(s), None
    except json.JSONDecodeError as e:
        # Try to find the largest balanced object substring
        first = s.find("{")
        last = s.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(s[first:last + 1]), None
            except json.JSONDecodeError as e2:
                return None, f"json parse failed (substring): {e2}"
        return None, f"json parse failed: {e}"


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def setup_logging(verbose: bool):
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
        datefmt="%H:%M:%S",
    )


_STATE_LOCK = threading.Lock()


def _append_state(path: Path, record: dict):
    """Thread-safe append. Workers serialise on a single lock around the disk write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _STATE_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def process_pair_with_bedrock(
    *,
    client,
    pair_id: str,
    row: dict,
    uni: str,
    dept: str,
    initial: str,
    output_dir: Path,
    uni_dir: Path,
    out_path: Path,
    state_path: Path,
    skipped_path: Path,
    cfg: dict,
    verbose: bool,
    region: Optional[str] = None,
) -> None:
    """Runs ONE pair end-to-end: opens its own AgentCore Browser session,
    drives the tool-use loop, parses the model JSON, writes per-prof markdown
    files, persists the per-pair JSON, and records state. Designed to be safe
    to call from a ThreadPoolExecutor — each call gets its own AgentCore
    session, has no shared mutable state with other workers (the Bedrock
    client is thread-safe; state writes go through _STATE_LOCK)."""
    started = time.time()
    last_err: Optional[Exception] = None
    result = None
    retry_attempts = 0

    for attempt in range(MAX_PAIR_RETRIES + 1):
        if attempt > 0:
            sleep_sec = PAIR_RETRY_BASE_SLEEP_SEC * (3 ** (attempt - 1))
            log.warning("[RETRY %d/%d] %s :: prior error: %s — sleeping %.0fs before retry",
                        attempt, MAX_PAIR_RETRIES, pair_id,
                        str(last_err)[:120] if last_err else "?",
                        sleep_sec)
            time.sleep(sleep_sec)
            retry_attempts = attempt
        try:
            with agentcore_page(region) as page:
                result = tool_use_loop(client, PRIMARY_MODEL, FALLBACK_MODEL,
                                       page, initial, verbose=verbose)
            break  # success — exit retry loop
        except Exception as e:
            last_err = e
            if attempt >= MAX_PAIR_RETRIES:
                log.exception("[FAIL after %d attempts] pair %s (region=%s): %s",
                              attempt + 1, pair_id, region, e)
                _append_state(state_path, {
                    "pair_id": pair_id,
                    "university": uni, "department": dept,
                    "status": "error", "err": str(e),
                    "region": region,
                    "retry_attempts": attempt,
                    "ended_at": datetime.utcnow().isoformat() + "Z",
                    "duration_s": int(time.time() - started),
                })
                return
            # else: continue to next iteration of the retry loop

    parsed, parse_err = extract_json_from_text(result["text"])
    dur = int(time.time() - started)

    if parsed is None:
        uni_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_path.with_suffix(".raw.txt")
        raw_path.write_text(result["text"], encoding="utf-8")
        log.warning("[PARSE-ERR %s] %s: %s — raw saved to %s", region, pair_id, parse_err, raw_path.name)
        _append_state(state_path, {
            "pair_id": pair_id,
            "university": uni, "department": dept,
            "status": "parse_error",
            "err": parse_err,
            "region": region,
            "raw_file": str(raw_path),
            "model_used": result["model_used"],
            "iterations": result["iterations"],
            "tokens_in": result["tokens_in"], "tokens_out": result["tokens_out"],
            "duration_s": dur,
            "ended_at": datetime.utcnow().isoformat() + "Z",
        })
        return

    uni_dir.mkdir(parents=True, exist_ok=True)
    n_personal_files = 0
    try:
        n_personal_files = write_personal_site_files(
            parsed,
            result.get("fetch_cache") or {},
            uni_dir,
            dept,
            int(cfg.get("max_personal_site_chars", 50000)),
        )
    except Exception as e:
        log.warning("[POST] personal-site write failed for %s: %s", pair_id, e)

    out_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
    n_profs = parsed.get("professor_count")
    if not isinstance(n_profs, int):
        n_profs = len(parsed.get("professors", []) or [])
    model_status = parsed.get("status", "ok")
    skip_reason = parsed.get("skip_reason")

    if model_status == "skipped":
        log.info("[SKIPPED %s] %s -> %s  reason=%s  iters=%d  tok=%d/%d  %ds",
                 region, pair_id, out_path.relative_to(output_dir),
                 skip_reason, result["iterations"],
                 result["tokens_in"], result["tokens_out"], dur)
        _append_state(skipped_path, {
            "pair_id": pair_id, "university": uni, "department": dept,
            "skip_reason": skip_reason or "model returned status=skipped",
            "chunks_tried": parsed.get("chunks_tried", []),
            "ended_at": datetime.utcnow().isoformat() + "Z",
        })
    else:
        cache_read = result.get("cache_read_tokens", 0)
        cache_write = result.get("cache_write_tokens", 0)
        log.info("[OK %s] %s -> %s  profs=%d  md=%d  iters=%d  tok=%d/%d (cache r=%d w=%d)  model=%s  %ds",
                 region, pair_id, out_path.relative_to(output_dir),
                 n_profs, n_personal_files,
                 result["iterations"], result["tokens_in"], result["tokens_out"],
                 cache_read, cache_write, result["model_used"], dur)

    _append_state(state_path, {
        "pair_id": pair_id,
        "university": uni, "department": dept,
        "status": model_status,
        "skip_reason": skip_reason,
        "region": region,
        "selected_url": parsed.get("selected_url"),
        "selection_strategy": parsed.get("selection_strategy"),
        "output_file": str(out_path.relative_to(output_dir)),
        "professor_count": n_profs,
        "personal_sites_written": n_personal_files,
        "model_used": result["model_used"],
        "iterations": result["iterations"],
        "tokens_in": result["tokens_in"], "tokens_out": result["tokens_out"],
        "cache_read_tokens": result.get("cache_read_tokens", 0),
        "cache_write_tokens": result.get("cache_write_tokens", 0),
        "retry_attempts": retry_attempts,
        "duration_s": dur,
        "ended_at": datetime.utcnow().isoformat() + "Z",
    })


def main():
    parser = argparse.ArgumentParser(description="professor_extract runner (Bedrock + AgentCore Browser)")
    parser.add_argument("--config", required=True, help="professor_extract_config.json path")
    parser.add_argument("--pair", help='Run ONE pair: "<university>|<department>" (exact match)')
    parser.add_argument("--all", action="store_true", help="Run all rows in input JSONL")
    parser.add_argument("--limit", type=int, help="Stop after N pairs (use with --all)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip pairs whose output JSON already exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="Template prompt and print first 5k chars; no Bedrock/AgentCore call")
    parser.add_argument("--workers", type=int, default=1,
                        help="Concurrent pairs to process (each worker opens its own AgentCore session). Default 1 (serial).")
    parser.add_argument("--regions", default=None,
                        help="Comma-separated regions for multi-region pool dispatch "
                             "(e.g., 'ap-south-1,us-east-1,us-west-2,eu-west-1'). "
                             "If unset, single region from AWS_REGION env. Each region gets "
                             "workers/N workers and uses its own Bedrock + AgentCore endpoints.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)

    cfg_path = Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    # Project root: ascend from config until we find the course_data folder
    project_root = cfg_path.parent
    while project_root != project_root.parent and project_root.name not in ("course_data",):
        project_root = project_root.parent

    def resolve(p):
        p = Path(p)
        return p if p.is_absolute() else (project_root / p)

    jsonl_path = resolve(cfg["input_jsonl"])
    prompt_path = resolve(cfg["prompt_file"])
    output_dir = resolve(cfg.get("output_dir", "Professors_info/output_professors"))
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.jsonl"
    skipped_path = output_dir / cfg.get("skipped_log", "skipped.jsonl")

    if not jsonl_path.exists():
        log.error("Input JSONL not found: %s", jsonl_path)
        log.error("Run consolidate_faculty_urls.py first (Phase 1).")
        sys.exit(1)
    if not prompt_path.exists():
        log.error("Prompt file not found: %s", prompt_path)
        sys.exit(1)

    prompt_tpl = prompt_path.read_text(encoding="utf-8")
    log.info("Config: input=%s  prompt=%s  output=%s",
             jsonl_path.name, prompt_path.name, output_dir)

    # Determine target rows
    target_pair = None
    if args.pair:
        if "|" not in args.pair:
            parser.error('--pair must be "<university>|<department>"')
        u, d = args.pair.split("|", 1)
        target_pair = (u.strip(), d.strip())
    elif args.all:
        pass
    else:
        parser.error("Must pass --pair or --all")

    # Resolve regional pool list: --regions overrides AWS_REGION; defaults to single region.
    if args.regions:
        regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    else:
        regions = [BEDROCK_REGION]

    clients: dict = {}
    if not args.dry_run:
        if not os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
            log.error("AWS_BEARER_TOKEN_BEDROCK is not set (required for Claude calls).")
            sys.exit(1)
        if not HAS_AGENTCORE:
            log.error("bedrock-agentcore / playwright not installed. Run: "
                      "pip install bedrock-agentcore playwright && playwright install chromium")
            sys.exit(1)
        clients = {r: bedrock_runtime_client(r) for r in regions}
        log.info("Pools: %d region(s) %s  primary=%s  fallback=%s",
                 len(regions), regions, PRIMARY_MODEL, FALLBACK_MODEL)

    uni_field = cfg.get("university_field", "university")
    dept_field = cfg.get("department_field", "department")
    chunks_field = cfg.get("grounding_chunks_field", "grounding_chunks")

    # ── Pass 1: walk the input JSONL once. For each row, decide:
    #     (a) skip outright (filter mismatch, --skip-existing hit, dry-run)
    #     (b) handle inline RIGHT NOW (empty-chunks short-circuit — no API call)
    #     (c) defer to Bedrock workers (build prompt + queue for processing)
    # We do all the cheap work serially so workers only do the expensive,
    # network-bound part. Output ordering across workers is non-deterministic.
    deferred = []   # list[dict] of work items for parallel Bedrock processing
    short_circuited = 0
    skipped_existing = 0
    queued = 0

    for row in load_rows(jsonl_path):
        uni = (row.get(uni_field) or "").strip()
        dept = (row.get(dept_field) or "").strip()
        if not uni or not dept:
            continue
        if target_pair and (uni, dept) != target_pair:
            continue

        uni_dir, out_path = output_paths(output_dir, uni, dept)
        if args.skip_existing and out_path.exists():
            log.info("[SKIP] %s / %s already extracted", uni, dept)
            skipped_existing += 1
            continue

        pair_id = row.get("pair_id") or derive_pair_id(uni, dept)
        chunks = normalize_chunks(row.get(chunks_field) or [])
        log.info("[PAIR] %s  (%s / %s)  chunks=%d", pair_id, uni, dept, len(chunks))

        # Empty-chunks short-circuit
        if not chunks:
            uni_dir.mkdir(parents=True, exist_ok=True)
            empty = {
                "university": uni,
                "country": row.get(cfg.get("country_field", "country"), "") or "",
                "department": dept,
                "status": "skipped",
                "selection_strategy": "none",
                "selected_url": None,
                "skip_reason": "no grounding chunks provided",
                "chunks_provided": [],
                "chunks_tried": [],
                "extraction_timestamp_utc": datetime.utcnow().isoformat() + "Z",
                "professor_count": 0,
                "professors": [],
                "extraction_notes": "Pre-runtime skip: input row had empty/missing grounding_chunks.",
            }
            out_path.write_text(json.dumps(empty, indent=2, ensure_ascii=False), encoding="utf-8")
            _append_state(skipped_path, {
                "pair_id": pair_id, "university": uni, "department": dept,
                "skip_reason": empty["skip_reason"],
                "ended_at": empty["extraction_timestamp_utc"],
            })
            _append_state(state_path, {
                "pair_id": pair_id, "university": uni, "department": dept,
                "status": "skipped", "skip_reason": empty["skip_reason"],
                "duration_s": 0,
                "ended_at": empty["extraction_timestamp_utc"],
            })
            short_circuited += 1
            if args.limit and (short_circuited + len(deferred)) >= args.limit:
                break
            continue

        initial = build_initial_user_prompt(prompt_tpl, row, cfg)

        if args.dry_run:
            print("=" * 80)
            print(f"DRY-RUN  pair_id={pair_id}")
            print(f"output -> {out_path}")
            print(f"chunks ({len(chunks)}):")
            for i, c in enumerate(chunks[:10]):
                print(f"  [{i}] {c['url']}  (title: {c['title']})")
            if len(chunks) > 10:
                print(f"  ... and {len(chunks)-10} more")
            print("=" * 80)
            print(initial[:5000])
            print(f"\n[... truncated at 5000 of {len(initial)} chars ...]")
            queued += 1
            if args.limit and (short_circuited + queued) >= args.limit:
                break
            continue

        deferred.append({
            "pair_id": pair_id, "row": row, "uni": uni, "dept": dept,
            "uni_dir": uni_dir, "out_path": out_path,
            "initial": initial,
        })
        queued += 1
        if args.limit and (short_circuited + queued) >= args.limit:
            break

    log.info("Plan: %d deferred (Bedrock workers), %d short-circuited (empty chunks), "
             "%d skipped (already done). workers=%d.",
             len(deferred), short_circuited, skipped_existing, args.workers)

    if args.dry_run or not deferred:
        log.info("Done. (dry-run or nothing to process via Bedrock.)")
        return

    # ── Pass 2: run deferred pairs through Bedrock + AgentCore ──
    workers = max(1, int(args.workers))

    # Round-robin pin each pair to a region. Pre-assigning here (not at submit
    # time) keeps state.jsonl entries deterministic per pair and means the
    # per-region pool sizing is exact rather than statistical.
    for i, d in enumerate(deferred):
        d["region"] = regions[i % len(regions)]

    if len(regions) == 1 and workers == 1:
        # Serial single-region path — same observable behaviour as before; no thread overhead.
        only_region = regions[0]
        only_client = clients[only_region]
        for d in deferred:
            process_pair_with_bedrock(
                client=only_client,
                region=only_region,
                pair_id=d["pair_id"], row=d["row"],
                uni=d["uni"], dept=d["dept"],
                initial=d["initial"],
                output_dir=output_dir, uni_dir=d["uni_dir"], out_path=d["out_path"],
                state_path=state_path, skipped_path=skipped_path,
                cfg=cfg, verbose=args.verbose,
            )
    else:
        # Multi-region OR multi-worker: one ThreadPoolExecutor per region.
        # This guarantees each region stays under its 25-AgentCore-session
        # cap regardless of how the work happens to be distributed. If one
        # region throttles (e.g., daily token quota), its pool drains while
        # the others keep working unaffected.
        workers_per_region = max(1, workers // len(regions))
        log.info("Running %d pairs across %d region(s) × %d workers each (%d total workers).",
                 len(deferred), len(regions), workers_per_region,
                 workers_per_region * len(regions))

        per_region_queues: dict = {r: [] for r in regions}
        for d in deferred:
            per_region_queues[d["region"]].append(d)
        for r, items in per_region_queues.items():
            log.info("  pool[%s]: %d pairs queued", r, len(items))

        pools = {r: ThreadPoolExecutor(max_workers=workers_per_region,
                                       thread_name_prefix=f"pool-{r}")
                 for r in regions}
        all_futs: dict = {}
        try:
            for region, items in per_region_queues.items():
                rclient = clients[region]
                for d in items:
                    fut = pools[region].submit(
                        process_pair_with_bedrock,
                        client=rclient,
                        region=region,
                        pair_id=d["pair_id"], row=d["row"],
                        uni=d["uni"], dept=d["dept"],
                        initial=d["initial"],
                        output_dir=output_dir, uni_dir=d["uni_dir"], out_path=d["out_path"],
                        state_path=state_path, skipped_path=skipped_path,
                        cfg=cfg, verbose=args.verbose,
                    )
                    all_futs[fut] = (d["pair_id"], region)

            done = 0
            for fut in as_completed(all_futs):
                pid, region = all_futs[fut]
                try:
                    fut.result()
                except Exception as e:
                    # process_pair_with_bedrock catches its own internal errors
                    # and writes them to state. A re-raise here means the worker
                    # itself crashed (rare). Log and continue.
                    log.exception("[WORKER-CRASH %s] %s: %s", region, pid, e)
                done += 1
                if done % 5 == 0 or done == len(deferred):
                    log.info("[progress] %d / %d done", done, len(deferred))
        finally:
            for p in pools.values():
                p.shutdown(wait=False)

    log.info("Done. Bedrock-processed %d pairs (+ %d short-circuited, + %d skipped existing).",
             len(deferred), short_circuited, skipped_existing)


if __name__ == "__main__":
    main()
