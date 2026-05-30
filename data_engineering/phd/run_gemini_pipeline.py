"""
run_gemini_pipeline.py  (PhD edition)

Reads a PhD gaps registry (pilot or full), and for each gap calls Gemini
3 Flash with Google Search grounding. Writes:

  - phd_v2/evidence/<college>/<file>.evidence.json
        Full grounding payload per course: prompt, answer text, grounding chunks
        (title/url snippets), grounding supports (which text spans cite which
        chunk), search queries Gemini issued, token usage, per-call cost.

  - phd_v2/phd/<college>/<file>__patch.md
        A NEW patch MD containing only the re-verified sections, each followed
        by an inline <citation> block in the same format as the original MDs.
        Filename has `__patch.md` suffix so it's distinguishable from originals.

  - phd_v2/pipeline_state/cost_log.jsonl
        One line per Gemini call: timestamp, course, field, tokens, cost.

The script is resumable: if an evidence file already exists with no errors,
the course is skipped. If it has errored fields, only those fields get retried.
Override with --force.

PhD-specific differences vs the masters pipeline:
  - All "master's program" wording replaced with "doctoral (PhD) program".
  - Tuition prompt is rewritten around funding/stipend (PhDs are often fully
    funded with monthly stipend, no tuition).

Usage:
    # Pilot run (10 courses):
    python phd_v2/scripts/run_gemini_pipeline.py --registry gaps_registry_pilot_10.json

    # Full run (~6.4k PhD courses):
    python phd_v2/scripts/run_gemini_pipeline.py --registry gaps_registry.json

    # Override model:
    python phd_v2/scripts/run_gemini_pipeline.py --registry gaps_registry_pilot_10.json --model gemini-2.5-flash

    # Force re-process even if evidence exists:
    python phd_v2/scripts/run_gemini_pipeline.py --registry gaps_registry_pilot_10.json --force
"""

import argparse
import json
import os
import re
import sys
import io
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
V2 = BASE / "phd_v2"
GCP_KEY = BASE / "dashboard" / "gcp-key.json"

EVIDENCE_DIR = V2 / "evidence"
PATCH_DIR = V2 / "phd"
STATE_DIR = V2 / "pipeline_state"
COST_LOG = STATE_DIR / "cost_log.jsonl"

# Default model — Gemini 3 Flash (public preview as of May 2026).
# The actual API ID has no ".0" — it's "gemini-3-flash-preview".
# If/when it goes GA, the ID may simplify to "gemini-3-flash".
# Override via --model if needed (e.g. gemini-3.1-flash-lite, gemini-2.5-flash).
DEFAULT_MODEL = "gemini-3-flash-preview"

# Rough Gemini Flash pricing (USD per 1K tokens). Adjust if your billing differs.
COST_PER_1K_INPUT = 0.000075
COST_PER_1K_OUTPUT = 0.0003
COST_PER_GROUNDING_CALL = 0.000035  # per grounded call after free tier

# Lock for the shared cost log file when multiple worker threads append to it.
_cost_log_lock = threading.Lock()

# In-process cache for resolved Vertex redirect URLs so we don't follow the
# same redirect twice across courses/workers.
_redirect_cache = {}
_redirect_lock = threading.Lock()


def resolve_vertex_redirect(redirect_url, timeout=10):
    """Follow a vertexaisearch.cloud.google.com grounding-api-redirect URL to
    find the real source URL it points at. Returns the original URL on failure.
    Cached in-process to avoid repeated HTTP calls."""
    with _redirect_lock:
        if redirect_url in _redirect_cache:
            return _redirect_cache[redirect_url]
    try:
        import requests
        # HEAD is cheap; if it doesn't follow, fall back to GET.
        resp = requests.head(redirect_url, allow_redirects=True, timeout=timeout)
        final = resp.url
        if final == redirect_url:  # didn't actually redirect
            resp = requests.get(redirect_url, allow_redirects=True, timeout=timeout, stream=True)
            final = resp.url
            resp.close()
    except Exception:
        final = redirect_url  # give up, return original
    with _redirect_lock:
        _redirect_cache[redirect_url] = final
    return final


# ── Field-specific prompts ──────────────────────────────────────────────────

def clean_program_name(filename, college):
    """Turn 'Aalborg_..._Masters_Computer_Science_IT.md' into 'Masters Computer Science IT'."""
    name = filename[:-3] if filename.endswith(".md") else filename
    name = name.replace(college + "_", "", 1)
    return name.replace("_", " ").strip()


def clean_university_name(college):
    return college.replace("_", " ").strip()


def build_section_prompt(field, program, university, gap):
    crawled = gap.get("crawled_urls") or []
    hint = ""
    if crawled:
        hint = "\n\nPreviously crawled URLs (may help, please verify against current pages):\n" + \
               "\n".join(f"- {u}" for u in crawled[:5])

    source_directive = (
        "\n\nIMPORTANT — prioritize OFFICIAL university sources only. Use the program's own "
        "university page or the university's admissions/finance pages as ground truth. "
        "Avoid third-party aggregators (e.g. edukasyon.ph, leverageedu, shiksha, mastersportal, "
        "studyabroad, gradschoolhub) unless they directly quote the official university page "
        "and the official page is currently inaccessible."
    )
    style_directive = (
        "\n\nWrite your answer as clean prose paragraphs only. Do NOT include URLs in your "
        "response text. Do NOT add 'Source:', 'Sources:', or 'Official Sources:' lines or "
        "lists. The URLs you ground on will be tracked separately and attached to your answer."
    )

    if field == "tuition_and_fees":
        return (
            f"Find the CURRENT tuition, funding, and stipend details for the \"{program}\" "
            f"doctoral (PhD) program at \"{university}\".\n\n"
            f"NOTE: PhD programs are often FULLY FUNDED — students may pay no tuition and "
            f"instead receive a monthly stipend / salary. If this is the case, document the "
            f"stipend amount, funding source, and duration of guaranteed funding instead of "
            f"a tuition figure. If tuition IS charged, document it normally.\n\n"
            f"Format your answer as a Markdown bulleted list in this EXACT structure (no paragraphs):\n\n"
            f"*   **Tuition Rates (Academic Year YYYY-YYYY):**\n"
            f"    *   **International Students (Non-EU/EEA):** <amount per year, OR 'Fully funded — no tuition'>\n"
            f"    *   **Domestic / EU / EEA Students:** <amount per year, OR 'Fully funded — no tuition'>\n"
            f"*   **PhD Stipend / Salary:** <monthly or annual stipend amount, duration of funding (e.g. 3-4 years), OR 'Information not available'>\n"
            f"*   **Application Fee:** <amount with currency, or 'No application fee'>\n"
            f"*   **Living Expenses:** <estimated annual or monthly cost of living>\n"
            f"*   **Scholarships / External Funding:** <brief list of named scholarships, research council funding, fellowships, OR 'Information not available'>\n"
            f"*   **Additional Fees:** <any other charges, or 'Information not available'>\n\n"
            f"Example of good output (fully-funded PhD):\n"
            f"*   **Tuition Rates (Academic Year 2026-2027):**\n"
            f"    *   **International Students (Non-EU/EEA):** Fully funded — no tuition charged.\n"
            f"    *   **Domestic / EU / EEA Students:** Fully funded — no tuition charged.\n"
            f"*   **PhD Stipend / Salary:** Approximately DKK 27,000-35,000 gross per month, guaranteed for 3 years (5+3 model: 5 years if combined with master's).\n"
            f"*   **Application Fee:** No application fee.\n"
            f"*   **Living Expenses:** Approximately DKK 8,000-11,500 per month in Aalborg.\n"
            f"*   **Scholarships / External Funding:** Danish Council for Independent Research grants, EU Marie Skłodowska-Curie Actions, departmental stipends.\n"
            f"*   **Additional Fees:** Information not available.\n\n"
            f"Rules:\n"
            f"- Use bullets ONLY. No introductory or closing paragraphs.\n"
            f"- Do NOT include URLs in your response. Do NOT add 'Source:' lines.\n"
            f"- If a category is unknown, write 'Information not available' rather than omitting it."
            f"{hint}{source_directive}"
        )

    if field == "application_deadlines":
        return (
            f"Find the CURRENT application deadlines for the \"{program}\" doctoral (PhD) program at \"{university}\".\n\n"
            f"IMPORTANT CONTEXT: PhD admissions usually do NOT follow a standard 'Fall/Spring intake' "
            f"pattern like master's programs. Instead, PhD applications work in one of these modes:\n"
            f"  (a) Per-vacancy: Specific PhD positions are advertised individually with their own "
            f"deadlines (common at European universities like Aalborg, Aarhus, Aalto).\n"
            f"  (b) Annual/biannual call: A general PhD application window with one or two deadlines "
            f"per year (common at US universities like MIT, Stanford).\n"
            f"  (c) Rolling: Applications accepted year-round (less common for PhDs).\n\n"
            f"Document whichever applies. If per-vacancy, list 3-6 representative current/upcoming positions "
            f"with their deadlines. If annual, list the cycle deadlines (e.g. December 1 for the following "
            f"Fall start)."
            f"{hint}{source_directive}\n\n"
            f"Format your answer as a Markdown bulleted list. Use this exact structure:\n\n"
            f"*   **Application deadline (exact date)**:\n"
            f"    *   <Position name or intake>: <deadline date>\n"
            f"    *   <Position name or intake>: <deadline date>\n"
            f"    *   (... etc, OR 'Rolling admissions / per-vacancy advertised on university vacancies page')\n"
            f"*   **Priority / early deadline if applicable**: <date or 'Information not available'>\n"
            f"*   **International vs domestic deadline if different**: <difference or 'Same deadline for all applicants'>\n"
            f"*   **Decision notification timeline**: <weeks/months after deadline, or 'Information not available'>\n"
            f"*   **Offer acceptance / response deadline**: <date or 'Information not available'>\n"
            f"*   **Rolling admissions status**: <'Rolling/year-round' OR 'Per-vacancy, no single deadline' OR 'Annual cycle' OR 'Information not available'>\n\n"
            f"Example for per-vacancy (European PhD):\n"
            f"*   **Application deadline (exact date)**:\n"
            f"    *   PhD stipend in Machine Learning (AAU Energy): April 18, 2026.\n"
            f"    *   PhD position in NLP (Department of Computer Science): May 1, 2026.\n"
            f"    *   PhD stipend in Quantum Information (AAU Physics): April 12, 2026.\n"
            f"*   **Priority / early deadline if applicable**: Not applicable due to individual vacancy postings.\n"
            f"*   **International vs domestic deadline if different**: Deadlines are typically the same for all applicants to a specific vacancy.\n"
            f"*   **Decision notification timeline**: Information not available.\n"
            f"*   **Offer acceptance / response deadline**: Information not available.\n"
            f"*   **Rolling admissions status**: Per-vacancy, no single deadline. Positions advertised continuously on the university vacancies page.\n\n"
            f"Example for annual cycle (US PhD):\n"
            f"*   **Application deadline (exact date)**:\n"
            f"    *   Fall 2026 intake: December 1, 2025 (international); December 15, 2025 (domestic).\n"
            f"*   **Priority / early deadline if applicable**: None — single annual deadline.\n"
            f"*   **International vs domestic deadline if different**: International deadline is 2 weeks earlier.\n"
            f"*   **Decision notification timeline**: Mid-February to early March.\n"
            f"*   **Offer acceptance / response deadline**: April 15.\n"
            f"*   **Rolling admissions status**: Annual cycle, no rolling.\n\n"
            f"Rules:\n"
            f"- Do NOT include URLs in your response. Do NOT add 'Source:' lines.\n"
            f"- Do NOT add introductory or closing paragraphs — bullets only.\n"
            f"- If a field is unknown, write 'Information not available' rather than omitting it."
        )

    # Fallback for any other section
    return (
        f"Find current information about \"{field}\" for the \"{program}\" doctoral (PhD) program "
        f"at \"{university}\".{hint}{source_directive}{style_directive}"
    )


ENTITY_QUESTIONS = {
    "toefl":           "What is the minimum TOEFL iBT score required for admission to \"{program}\" at \"{university}\"?",
    "ielts":           "What is the minimum IELTS Academic score required for admission to \"{program}\" at \"{university}\"?",
    "duolingo":        "Is the Duolingo English Test accepted for \"{program}\" at \"{university}\"? If yes, what is the minimum score?",
    "pte":             "What is the minimum PTE Academic score required for admission to \"{program}\" at \"{university}\"?",
    "cambridge":       "What is the minimum Cambridge English (C1 Advanced / C2 Proficiency) score required for admission to \"{program}\" at \"{university}\"?",
    "gpa":             "What is the minimum GPA required for admission to \"{program}\" at \"{university}\"? For PhD programs, this is usually the GPA of the applicant's most recent qualifying degree (typically a Master's, or Bachelor's for 4+4 / direct-entry PhDs). Provide the value with its scale (e.g. 3.0/4.0) and specify which degree the GPA refers to.",
    "gre_status":      "Is the GRE (General Test) required, recommended, optional, or waived for admission to the \"{program}\" PhD program at \"{university}\"? Also note if a GRE Subject Test is required or recommended.",
    "gre_score":       "What is the minimum or recommended GRE General Test score (Quant / Verbal / AW) for admission to the \"{program}\" PhD program at \"{university}\"? Include Subject Test minimums if applicable.",
    "gmat_status":     "Is the GMAT required, optional, or waived for admission to \"{program}\" at \"{university}\"?",
    "app_fee":         "What is the application fee for \"{program}\" at \"{university}\"? Include amount and currency. If there's no fee, state that explicitly.",
    "lor_count":       "How many letters of recommendation are required for admission to \"{program}\" at \"{university}\"?",
    "work_experience": "Is work experience required, recommended, or not required for admission to \"{program}\" at \"{university}\"? How many years/months if applicable?",
    "sop":             "Is a research proposal / project proposal / Statement of Purpose required for admission to \"{program}\" at \"{university}\"? PhD programs typically require a research proposal (not just an SOP). State the document type, required length/word limit, and what it should cover.",
}

# Display name to use in the Admission Requirements bullet (e.g. "TOEFL iBT", "GPA requirements")
ENTITY_BULLET_LABEL = {
    "toefl":           "TOEFL iBT",
    "ielts":           "IELTS",
    "duolingo":        "Duolingo English Test",
    "pte":             "PTE Academic",
    "cambridge":       "Cambridge English",
    "gpa":             "GPA requirements",
    "gre_status":      "GRE",
    "gre_score":       "GRE score",
    "gmat_status":     "GMAT",
    "app_fee":         "Application fee",
    "lor_count":       "Letters of recommendation",
    "work_experience": "Work experience",
    "sop":             "Research Proposal / Statement of Purpose",
}


def build_entity_prompt(field, program, university, gap):
    question_tmpl = ENTITY_QUESTIONS.get(
        field,
        f"What is the \"{field}\" requirement for \"{{program}}\" at \"{{university}}\"?",
    )
    question = question_tmpl.format(program=program, university=university)

    md_value = gap.get("md_value")
    md_hint = ""
    if md_value not in (None, "", [], {}):
        md_hint = f"\n\nThe current MD value (which we suspect is wrong/outdated): {md_value}"

    label = ENTITY_BULLET_LABEL.get(field, field.replace("_", " ").title())

    return (
        f"{question}\n"
        f"{md_hint}\n\n"
        f"Search the official university page for this program (not third-party aggregators "
        f"like edukasyon.ph, leverageedu, shiksha, mastersportal unless they directly quote "
        f"the official page).\n\n"
        f"Format your answer as ONE single Markdown bullet point in this EXACT style:\n"
        f"*   **{label}:** <one or two concise sentences stating the value and any short context>\n\n"
        f"Examples of good format:\n"
        f"*   **TOEFL iBT:** Minimum score of 88, with component minimums of 18 (Reading), 17 (Listening), 20 (Speaking), 17 (Writing).\n"
        f"*   **GPA requirements:** Minimum 2.6/4.0, equivalent to a UK 2:2 Bachelor's (Honours) degree.\n"
        f"*   **Application fee:** No application fee for postgraduate programs when applying through the university portal.\n\n"
        f"Rules:\n"
        f"- Start your answer with '*   **{label}:**' (three spaces after the asterisk).\n"
        f"- Do NOT include URLs in your response. Do NOT add 'Source:' lines.\n"
        f"- Do NOT add a heading or any text before the bullet.\n"
        f"- Keep the entire bullet to 1-3 sentences."
    )


def build_md_missing_prompt(course, gap):
    program = clean_program_name(course["file"], course["college"])
    university = clean_university_name(course["college"])
    section = gap.get("section_heading", "the missing section")
    notes = (gap.get("notes") or "")[:300]
    return (
        f"Find information about \"{section}\" for the \"{program}\" doctoral (PhD) program at \"{university}\".\n\n"
        f"A previous attempt to find this data left these notes:\n  {notes}\n\n"
        f"IMPORTANT — prioritize OFFICIAL university sources only. Use the program's own "
        f"university page or the university's admissions pages as ground truth. Avoid "
        f"third-party aggregators (e.g. edukasyon.ph, leverageedu, shiksha, mastersportal) "
        f"unless they directly quote the official page. If the data is genuinely unavailable "
        f"on the official site, say so explicitly.\n\n"
        f"Write your answer as plain prose only. Do NOT include URLs in your response text and "
        f"do NOT add 'Source:' lines. The URLs you ground on will be tracked separately."
    )


# Regex that grabs the URL portion of a Markdown link: [label](https://...)
# Even with explicit prompt instructions not to include URLs, Gemini sometimes
# slips them in. We extract for the citation block (real URLs) and then strip
# them from the body to match the old MD style (clean prose + citation block).
MD_LINK_URL_RE = re.compile(r"\]\((https?://[^\s)]+)\)")
MD_LINK_FULL_RE = re.compile(r"\[([^\]]+)\]\(https?://[^\s)]+\)")
BARE_URL_RE = re.compile(r"https?://\S+")
SOURCE_LINE_RE = re.compile(
    r"^\s*(?:\*+\s*)?\*?\*?\s*(?:Official\s+)?Sources?\*?\*?\s*:.*$",
    re.MULTILINE | re.IGNORECASE,
)


def extract_urls_from_answer(answer_text):
    """Return a deduplicated list of real URLs found inline in the Gemini answer."""
    urls = []
    seen = set()
    for url in MD_LINK_URL_RE.findall(answer_text or ""):
        url = url.rstrip(".,;)")
        if url and url not in seen and "vertexaisearch.cloud.google.com" not in url:
            seen.add(url)
            urls.append(url)
    return urls


def clean_answer_to_prose(answer_text):
    """Strip URLs and 'Source:' lines from the answer so the body matches the
    old MD format (clean prose only; URLs live in the <citation> block)."""
    if not answer_text:
        return answer_text
    # 1. Convert markdown links to just their label text.
    text = MD_LINK_FULL_RE.sub(r"\1", answer_text)
    # 2. Remove any bare URLs that slipped through.
    text = BARE_URL_RE.sub("", text)
    # 3. Remove "Source: ..." / "Sources: ..." / "Official Sources: ..." lines.
    text = SOURCE_LINE_RE.sub("", text)
    # 4. Clean up resulting empty bullet lines (e.g. "*   " or "- ") with no content.
    text = re.sub(r"^\s*[\*\-]\s*$", "", text, flags=re.MULTILINE)
    # 5. Collapse 3+ consecutive newlines down to 2.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def gap_to_field_key(gap):
    if gap.get("type") == "section":
        return gap["field"]
    if gap.get("type") == "entity":
        return f"entity__{gap['field']}"
    if "section_heading" in gap:
        slug = re.sub(r"[^a-z0-9]+", "_", gap["section_heading"].lower()).strip("_")
        return f"md_missing__{slug}"
    return "unknown"


# ── Gemini wrapper ──────────────────────────────────────────────────────────

def init_client():
    """Initialize the new google-genai client against Vertex AI."""
    try:
        from google import genai  # noqa: F401
    except ImportError:
        print("ERROR: 'google-genai' package not installed.")
        print("  Run:  pip install google-genai")
        sys.exit(1)

    if not GCP_KEY.exists():
        print(f"ERROR: GCP key not found at {GCP_KEY}")
        sys.exit(1)

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(GCP_KEY)
    with open(GCP_KEY, encoding="utf-8") as f:
        key_data = json.load(f)
    project_id = key_data.get("project_id")
    if not project_id:
        print("ERROR: project_id not found in gcp-key.json")
        sys.exit(1)

    # Gemini 3 preview models (gemini-3-flash-preview, gemini-3.1-pro-preview, etc.)
    # are ONLY available on the "global" Vertex AI endpoint. Using a regional
    # endpoint like "us-central1" returns a 404 model-not-found error.
    # See: https://discuss.ai.google.dev/t/request-for-regional-access-to-gemini-3-preview-models-in-vertex-ai/144515
    from google import genai
    client = genai.Client(
        vertexai=True,
        project=project_id,
        location="global",
    )
    return client, project_id


def call_gemini(client, model, prompt, max_retries=6):
    """Gemini call with Google Search grounding + auto-retry on empty grounding.

    Two layers:
      1. Per-API-call retry (handled inside _call_gemini_once below): rate
         limits, server errors, transient network issues. 6 attempts.
      2. Grounding-quality retry (this wrapper): if the first call returns
         zero grounding chunks (Gemini answered from training memory without
         searching), we re-prompt ONCE with a stronger "you MUST cite sources"
         directive. The retry result is always used in place of the original
         even if it ALSO returns empty chunks — its answer is more honest
         because the stronger prompt instructs Gemini to say "Data not
         available from official sources" when it can't find a citation.
    """
    result = _call_gemini_once(client, model, prompt, max_retries)
    if not result.get("grounding_chunks"):
        stronger_directive = (
            "STRICT REQUIREMENT: You MUST use Google Search and ground your answer in "
            "at least one OFFICIAL university source. Do NOT answer from training "
            "memory alone. If after searching you cannot find an authoritative "
            "official source (the university's own domain or government source), "
            "your answer MUST explicitly state 'Data not available from official "
            "sources at this time' rather than offering an unsupported value.\n\n"
        )
        result_retry = _call_gemini_once(client, model, stronger_directive + prompt, max_retries)
        result_retry["retried_for_grounding"] = True
        result_retry["original_chunks_empty"] = True
        return result_retry
    return result


def _call_gemini_once(client, model, prompt, max_retries=6):
    """Single Gemini call with Google Search grounding. Returns parsed result dict.

    Retry policy:
      - Rate limit / quota errors (429, RESOURCE_EXHAUSTED): long backoff
        (15s, 30s, 60s, 120s, 240s, 480s) — preview models throttle hard.
      - Server errors (500, 503, UNAVAILABLE, DEADLINE_EXCEEDED): short backoff
        (2s, 4s, 8s, 16s, 32s, 64s).
      - Other client errors (400, INVALID_ARGUMENT): fail immediately
        (retrying won't help; usually a bad prompt).
    """
    from google.genai import types

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.1,
    )

    def _classify_error(err):
        s = str(err).upper()
        if "429" in s or "RESOURCE_EXHAUSTED" in s or "QUOTA" in s or "RATE" in s:
            return "rate_limit"
        if "500" in s or "503" in s or "UNAVAILABLE" in s or "DEADLINE_EXCEEDED" in s or "INTERNAL" in s:
            return "transient"
        if "400" in s or "INVALID_ARGUMENT" in s:
            return "fatal"
        return "transient"  # default: assume retryable

    last_err = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )

            text = response.text or ""
            grounding_chunks = []
            grounding_supports = []
            search_queries = []
            candidate = response.candidates[0] if response.candidates else None
            if candidate and getattr(candidate, "grounding_metadata", None):
                gm = candidate.grounding_metadata
                for chunk in (gm.grounding_chunks or []):
                    if getattr(chunk, "web", None):
                        grounding_chunks.append({
                            "title": chunk.web.title or "",
                            "url": chunk.web.uri or "",
                        })

                # Resolve Vertex redirect URLs to their real source domains in
                # parallel. Each resolved chunk gets:
                #   - "url":          the real URL (e.g. https://www.en.aau.dk/...)
                #   - "redirect_url": the original Vertex redirect (preserved
                #                     for traceability / regression debugging)
                if grounding_chunks:
                    def _resolve_one(c):
                        raw = c.get("url", "")
                        if raw and "vertexaisearch.cloud.google.com" in raw:
                            c["redirect_url"] = raw
                            c["url"] = resolve_vertex_redirect(raw)
                        return c
                    n_workers = min(10, len(grounding_chunks))
                    with ThreadPoolExecutor(max_workers=n_workers) as ex:
                        grounding_chunks = list(ex.map(_resolve_one, grounding_chunks))
                for support in (gm.grounding_supports or []):
                    seg = getattr(support, "segment", None)
                    grounding_supports.append({
                        "segment_text": (seg.text if seg and seg.text else "") if seg else "",
                        "chunk_indices": list(getattr(support, "grounding_chunk_indices", []) or []),
                    })
                search_queries = list(getattr(gm, "web_search_queries", []) or [])

            usage = {"input_tokens": 0, "output_tokens": 0}
            if getattr(response, "usage_metadata", None):
                um = response.usage_metadata
                usage["input_tokens"] = getattr(um, "prompt_token_count", 0) or 0
                usage["output_tokens"] = getattr(um, "candidates_token_count", 0) or 0

            return {
                "text": text,
                "grounding_chunks": grounding_chunks,
                "grounding_supports": grounding_supports,
                "search_queries": search_queries,
                "usage": usage,
            }
        except Exception as e:
            last_err = e
            err_class = _classify_error(e)
            if err_class == "fatal":
                print(f"      [fatal — not retrying] {type(e).__name__}: {str(e)[:120]}")
                raise
            if err_class == "rate_limit":
                wait = min(480, 15 * (2 ** attempt))  # 15, 30, 60, 120, 240, 480 (capped)
            else:
                wait = min(64, 2 * (2 ** attempt))   # 2, 4, 8, 16, 32, 64
            print(f"      [retry {attempt + 1}/{max_retries} in {wait}s, type={err_class}] {type(e).__name__}: {str(e)[:120]}")
            time.sleep(wait)

    raise last_err


def estimate_cost(usage):
    in_cost = (usage["input_tokens"] / 1000) * COST_PER_1K_INPUT
    out_cost = (usage["output_tokens"] / 1000) * COST_PER_1K_OUTPUT
    return in_cost + out_cost + COST_PER_GROUNDING_CALL


# ── Per-course processing ───────────────────────────────────────────────────

def process_course(course, client, model, force=False):
    college = course["college"]
    filename = course["file"]
    program = clean_program_name(filename, college)
    university = clean_university_name(college)

    evidence_path = EVIDENCE_DIR / college / (filename[:-3] + ".evidence.json")

    # Smart resume: if the evidence file exists, check it for previously-failed
    # fields. If every field has an answer (no error), skip the course. If any
    # field has an "error" key, keep the good fields and retry only the failed
    # ones — saves cost on the already-verified data.
    previous_fields = {}
    if evidence_path.exists() and not force:
        try:
            existing = json.loads(evidence_path.read_text(encoding="utf-8"))
            previous_fields = existing.get("fields", {}) or {}
            failed_keys = [k for k, v in previous_fields.items() if "error" in v]
            if not failed_keys:
                print(f"  - already done (all fields good), skipping")
                return existing
            print(f"  - resuming: {len(failed_keys)} failed field(s) to retry, {len(previous_fields) - len(failed_keys)} good field(s) preserved")
        except Exception:
            previous_fields = {}

    evidence = {
        "course_id": course["course_id"],
        "college": college,
        "file": filename,
        "program": program,
        "university": university,
        "tier": course.get("tier"),
        "confidence_score": course.get("confidence_score"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gemini_model": model,
        # Preserve previously-successful fields; failed ones get re-tried below.
        "fields": {k: v for k, v in previous_fields.items() if "error" not in v},
        "total_cost_usd": sum(v.get("cost_usd", 0.0) for v in previous_fields.values() if "error" not in v),
    }

    all_gaps = []
    for g in (course.get("report_gaps") or []):
        all_gaps.append(("report", g))
    for g in (course.get("md_gaps") or []):
        all_gaps.append(("md", g))

    for source, gap in all_gaps:
        key = gap_to_field_key(gap)
        if key in evidence["fields"]:
            # Field was preserved from a previous successful run (smart resume).
            continue
        if source == "report":
            if gap["type"] == "section":
                prompt = build_section_prompt(gap["field"], program, university, gap)
            else:
                prompt = build_entity_prompt(gap["field"], program, university, gap)
        else:
            prompt = build_md_missing_prompt(course, gap)

        print(f"  - [{key}] calling Gemini...")
        try:
            result = call_gemini(client, model, prompt)
        except Exception as e:
            print(f"    FAILED after retries: {e}")
            evidence["fields"][key] = {"error": str(e), "prompt": prompt, "gap": gap}
            continue

        cost = estimate_cost(result["usage"])
        evidence["total_cost_usd"] += cost
        evidence["fields"][key] = {
            "gap": gap,
            "prompt": prompt,
            "answer": result["text"],
            "grounding_chunks": result["grounding_chunks"],
            "grounding_supports": result["grounding_supports"],
            "search_queries": result["search_queries"],
            "usage": result["usage"],
            "cost_usd": round(cost, 6),
        }

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log_line = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "course_id": course["course_id"],
            "field": key,
            "input_tokens": result["usage"]["input_tokens"],
            "output_tokens": result["usage"]["output_tokens"],
            "n_chunks": len(result["grounding_chunks"]),
            "cost_usd": round(cost, 6),
        }) + "\n"
        with _cost_log_lock:
            with COST_LOG.open("a", encoding="utf-8") as f:
                f.write(log_line)

    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    return evidence


# ── MD patch generation ─────────────────────────────────────────────────────

def section_title_for(field_key, gap):
    if gap.get("type") == "section":
        return gap["field"].replace("_", " ").title()
    if gap.get("type") == "entity":
        ent = gap["field"]
        pretty = {
            "toefl": "TOEFL Requirement",
            "ielts": "IELTS Requirement",
            "duolingo": "Duolingo English Test Requirement",
            "pte": "PTE Academic Requirement",
            "cambridge": "Cambridge English Requirement",
            "gpa": "GPA Requirement",
            "gre_status": "GRE Status",
            "gre_score": "GRE Score Requirement",
            "gmat_status": "GMAT Status",
            "app_fee": "Application Fee",
            "lor_count": "Letters of Recommendation",
            "work_experience": "Work Experience Requirement",
            "sop": "Research Proposal / Statement of Purpose",
        }
        return pretty.get(ent, ent.replace("_", " ").title())
    if "section_heading" in gap:
        return gap["section_heading"]
    return "Re-verified Field"


def _urls_for_field(field_ev):
    """Return a deduplicated list of real source URLs supporting one field."""
    answer_raw = (field_ev.get("answer") or "").strip()
    urls = extract_urls_from_answer(answer_raw)
    if not urls:
        seen = set()
        for c in field_ev.get("grounding_chunks", []):
            u = c.get("url", "").strip()
            if not u:
                continue
            if "vertexaisearch.cloud.google.com" in u:
                u = resolve_vertex_redirect(u)
            if u and u not in seen and "vertexaisearch.cloud.google.com" not in u:
                seen.add(u)
                urls.append(u)
    return urls


def _citation_block(urls):
    """Build a <citation> block.

    Status semantics:
      - "verified"   : Gemini returned at least one real source URL we could
                       attach (some or all bullets grounded successfully).
      - "ungrounded" : Gemini answered without ANY web grounding for this
                       section — the value is plausibly from training memory
                       and has NOT been confirmed against an official source.
                       Reviewers should treat ungrounded sections as needing
                       manual verification.
    """
    if urls:
        url_lines = "\n".join(f"- {u}" for u in urls)
        urls_block = f"urls:\n{url_lines}"
        status = "verified"
        note = "Re-verified via Gemini grounded web search."
    else:
        urls_block = "urls: []"
        status = "ungrounded"
        note = ("Gemini answered without web grounding (no official source found). "
                "Value is plausibly correct but NOT verified — review manually before trusting.")
    return (
        "<citation>\n"
        f"status: {status}\n"
        f"{urls_block}\n"
        f"notes: {note}\n"
        "</citation>"
    )


def field_evidence_to_md_section(field_key, field_ev):
    """Build a full `## Heading + body + <citation>` block for a SECTION-level
    gap (tuition_and_fees, application_deadlines, md_missing). NOT used for
    entity gaps — those are grouped under one Admission Requirements section
    via `entity_evidence_to_bullet` and `build_admission_section`."""
    if "error" in field_ev:
        return None
    gap = field_ev.get("gap", {})

    # Use the original section heading from the old MD where possible.
    if gap.get("type") == "section":
        # Convert internal name to old-MD style heading
        title_map = {
            "tuition_and_fees": "Tuition & Fees",
            "application_deadlines": "Application Deadlines",
        }
        title = title_map.get(gap["field"], gap["field"].replace("_", " ").title())
    elif "section_heading" in gap:
        title = gap["section_heading"]
    else:
        title = section_title_for(field_key, gap)

    answer_raw = (field_ev.get("answer") or "").strip()
    if not answer_raw:
        return None

    urls = _urls_for_field(field_ev)

    # Strip URLs and 'Source:' lines from the body.
    answer = clean_answer_to_prose(answer_raw)
    if not answer:
        return None

    return f"## {title}\n\n{answer}\n\n{_citation_block(urls)}\n"


def entity_evidence_to_bullet(field_ev):
    """Build the bullet line(s) for ONE entity gap. Returns (bullet_text, urls)
    or (None, None) if the field has an error / empty answer.

    The Gemini prompt asks for output as `*   **Label:** ...` already, but we
    defensively clean and normalize the indentation."""
    if "error" in field_ev:
        return None, None
    answer_raw = (field_ev.get("answer") or "").strip()
    if not answer_raw:
        return None, None

    urls = _urls_for_field(field_ev)
    cleaned = clean_answer_to_prose(answer_raw)
    if not cleaned:
        return None, None

    # If the answer already starts with `*` or `-`, trust it. Otherwise wrap.
    first = cleaned.lstrip().split("\n", 1)[0].lstrip()
    if first.startswith("*") or first.startswith("-"):
        # Normalize line starts to `*   ` for top-level bullets.
        bullet = re.sub(r"^[\-\*]\s+", "*   ", cleaned, flags=re.MULTILINE)
    else:
        # Gemini ignored the format — fall back to wrapping with a label.
        gap = field_ev.get("gap", {})
        label = ENTITY_BULLET_LABEL.get(gap.get("field", ""), gap.get("field", "").replace("_", " ").title())
        bullet = f"*   **{label}:** {cleaned}"

    return bullet, urls


def build_admission_section(entity_bullets):
    """Combine multiple entity bullets into a single `## Admission Requirements`
    section with one combined citation block.

    Args:
        entity_bullets: list of (bullet_text, urls) tuples.
    """
    bullets = []
    all_urls = []
    seen = set()
    for bullet, urls in entity_bullets:
        if not bullet:
            continue
        bullets.append(bullet.rstrip())
        for u in (urls or []):
            if u not in seen:
                seen.add(u)
                all_urls.append(u)

    if not bullets:
        return None

    body = "\n".join(bullets)
    return f"## Admission Requirements\n\n{body}\n\n{_citation_block(all_urls)}\n"


def write_patched_md(course, evidence):
    college = course["college"]
    filename = course["file"]
    # Insert __patch before the .md extension so it's obvious this is a patch
    # file (e.g. "..._Computer_Science_IT__patch.md").
    patch_filename = filename[:-3] + "__patch.md" if filename.endswith(".md") else filename + "__patch.md"
    out_path = PATCH_DIR / college / patch_filename
    out_path.parent.mkdir(parents=True, exist_ok=True)

    program = clean_program_name(filename, college)
    university = clean_university_name(college)

    header = [
        f"# {university} — {program}",
        "",
        f"_Patch file: re-verified fields only. Original MD: `{course['md_path']}`_",
        f"_Generated: {evidence['generated_at']}_  ",
        f"_Pipeline: Gemini grounded re-verification ({evidence['gemini_model']})_  ",
        f"_Estimated cost for this course: ${evidence['total_cost_usd']:.4f}_",
        "",
    ]

    # Split fields into section-level (tuition/deadlines/md_missing) and
    # entity-level (TOEFL/IELTS/GPA/...). Entities get grouped under one
    # `## Admission Requirements` section to match the old MD style.
    section_blocks = []
    entity_bullets = []

    for key, field_ev in evidence["fields"].items():
        gap = field_ev.get("gap", {})
        if gap.get("type") == "entity":
            bullet, urls = entity_evidence_to_bullet(field_ev)
            if bullet:
                entity_bullets.append((bullet, urls))
        else:
            md = field_evidence_to_md_section(key, field_ev)
            if md:
                section_blocks.append(md)

    if entity_bullets:
        adm = build_admission_section(entity_bullets)
        if adm:
            section_blocks.append(adm)

    out_path.write_text("\n".join(header) + "\n" + "\n".join(section_blocks), encoding="utf-8")
    return out_path


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="gaps_registry_pilot_10.json",
                        help="Registry filename in phd_v2/gaps/")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Gemini model ID (default: {DEFAULT_MODEL})")
    parser.add_argument("--force", action="store_true",
                        help="Re-process courses even if evidence already exists")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only first N courses (after registry load)")
    parser.add_argument("--workers", type=int, default=5,
                        help="Number of courses to process in parallel (default: 5). "
                             "Each worker shares one Gemini client. Stay <=10 unless "
                             "you have a quota increase; preview models often cap at 60 RPM.")
    args = parser.parse_args()

    registry_path = V2 / "gaps" / args.registry
    if not registry_path.exists():
        print(f"ERROR: registry not found at {registry_path}")
        sys.exit(1)

    print(f"Loading registry: {registry_path}")
    courses = json.loads(registry_path.read_text(encoding="utf-8"))
    if args.limit:
        courses = courses[:args.limit]
    print(f"  {len(courses)} courses to process\n")

    print("Initializing Vertex AI client...")
    client, project_id = init_client()
    print(f"  project: {project_id}")
    print(f"  model:   {args.model}\n")

    total_cost = 0.0
    succeeded = 0
    failed = 0
    t0 = time.time()
    print(f"Running with {args.workers} parallel worker(s)\n")

    def _worker(idx_and_course):
        idx, course = idx_and_course
        label = f"[{idx + 1}/{len(courses)}] {course['file']}  ({course.get('total_gaps', '?')} gaps, tier={course.get('tier')})"
        try:
            evidence = process_course(course, client, args.model, force=args.force)
            patch_path = write_patched_md(course, evidence)
            return ("ok", course, evidence["total_cost_usd"], patch_path, label, None)
        except Exception as e:
            return ("err", course, 0.0, None, label, e)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(_worker, (i, c)) for i, c in enumerate(courses)]
            for fut in as_completed(futures):
                status, _course, cost, patch_path, label, err = fut.result()
                print(label)
                if status == "ok":
                    total_cost += cost
                    succeeded += 1
                    print(f"  -> wrote {patch_path.name}, cost ${cost:.4f}")
                else:
                    failed += 1
                    print(f"  -> FAILED: {type(err).__name__}: {err}")
    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    elapsed = time.time() - t0
    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"Succeeded:    {succeeded}")
    print(f"Failed:       {failed}")
    print(f"Total cost:   ${total_cost:.4f}")
    print(f"Elapsed:      {elapsed:.1f}s ({elapsed / max(1, succeeded):.1f}s per course)")
    print(f"Evidence dir: {EVIDENCE_DIR}")
    print(f"Patch dir:    {PATCH_DIR}")
    print(f"Cost log:     {COST_LOG}")


if __name__ == "__main__":
    main()
