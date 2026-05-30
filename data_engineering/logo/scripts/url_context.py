"""
url_context.py — Logo URL extraction via Gemini URL Context (Vertex AI).

Working tool. First run is on gemini_200_filled.json as a sample, but the same
script will be used for the full 22K production run by changing --src and --max.

PIPELINE:
  Stage 1: Load source JSON, filter to records with non-null domain
  Stage 2: Batch into groups of 20, send each batch to Gemini URL Context
           with structured JSON output schema
  Stage 3: HEAD-verify every returned logo URL (catches 404s, mutated URLs)
  Stage 4: Merge into final per-record output JSON + write summary

  (No fallback — failed extractions stay null so we see real Gemini coverage.)

AUTH (Vertex AI by default):
  Uses the service account at ../dashboard/gcp-key.json
  Project: ambitio-ds-v2  (override with GCP_PROJECT env var)
  Region:  us-central1     (override with GCP_LOCATION env var)

REQUIREMENTS:
  pip install google-genai httpx python-dotenv tqdm

USAGE:
  python url_context.py --max 20                # 20-record smoke test
  python url_context.py --max 200               # full sample
  python url_context.py --src domains_22k.json  # production run
  python url_context.py --batch-size 10         # smaller batches if errors
  python url_context.py --model gemini-2.5-flash  # cheaper model
  python url_context.py --no-verify             # skip HEAD verification

OUTPUTS (in pilot_url_context/ folder):
  raw_batches.json         — raw Gemini responses + URL retrieval metadata
  pilot_results_<N>.json   — final per-record output
  pilot_summary.txt        — coverage stats, token usage, cost projection
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from tqdm.asyncio import tqdm_asyncio
from google import genai
from google.genai import types

HERE = Path(__file__).parent
DEFAULT_SRC = HERE / "gemini_200_filled.json"
OUT_DIR = HERE / "pilot_url_context"

# ── Vertex AI auth (default) ────────────────────────────────────────────
load_dotenv(HERE / ".env")

GCP_KEY_PATH = (HERE.parent / "dashboard" / "gcp-key.json").resolve()
GCP_PROJECT = os.environ.get("GCP_PROJECT", "ambitio-ds-v2")
LOGODEV_KEY = os.environ.get("LOGODEV_SECRET_KEY")


def location_for(model_name):
    """Gemini 3.x models live in the 'global' endpoint. 2.x models use regional."""
    m = (model_name or "").lower()
    if m.startswith("gemini-3"):
        return os.environ.get("GCP_LOCATION", "global")
    return os.environ.get("GCP_LOCATION", "us-central1")


def make_client(model_name):
    if not GCP_KEY_PATH.exists():
        raise SystemExit(f"ERROR: Service account key not found at {GCP_KEY_PATH}")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(GCP_KEY_PATH)
    location = location_for(model_name)
    print(f"Auth:    Vertex AI service account ({GCP_KEY_PATH.name})")
    print(f"Project: {GCP_PROJECT}")
    print(f"Region:  {location}  (auto-selected for {model_name})")
    return genai.Client(vertexai=True, project=GCP_PROJECT, location=location)


# ── Structured output schema ───────────────────────────────────────────
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "domain": {"type": "string"},
                    "logo_url": {"type": "string", "nullable": True},
                    "confidence": {"type": "number"},
                    "source_element": {
                        "type": "string",
                        "enum": [
                            "header_img", "svg", "apple_touch_icon",
                            "favicon", "og_image", "none",
                        ],
                    },
                    "notes": {"type": "string", "nullable": True},
                },
                "required": [
                    "index", "domain", "logo_url",
                    "confidence", "source_element",
                ],
            },
        }
    },
    "required": ["results"],
}


def build_prompt(items):
    """items: list of (orig_idx, domain, url) tuples."""
    lines = [f"  index={i}  domain={d}  url={u}" for i, d, u in items]
    table = "\n".join(lines)
    n = len(items)
    return f"""Fetch the homepage of each of the following {n} websites and extract the company logo image URL from the HTML.

Websites:
{table}

For EACH site, find the logo using this priority order:
1. <img> inside <header>, <nav>, or top-of-page sections with "logo" or "wordmark" in the class/id/alt/src attributes (must be a normal logo size — skip giant hero banners)
2. Inline <svg> with "logo" or "wordmark" in class — return null for logo_url unless an image href is extractable
3. <link rel="apple-touch-icon"> with size >= 120x120
4. <link rel="icon"> with size >= 64x64
5. og:image meta tag — ONLY if filename clearly suggests a logo (avoid hero banners, social-share cards, screenshots)
6. If none of the above produce a usable logo, return logo_url: null and source_element: "none"

Rules:
- Resolve all relative paths to absolute URLs (against the page URL)
- If the page is a JS-only SPA where static HTML has no logo, return null
- If you can't fetch the page (404, blocked, timeout), return null
- Do NOT guess or fabricate URLs — only return URLs that actually exist in the HTML
- Use confidence 0.9+ for clear header logos, 0.6-0.8 for favicons, 0.3-0.5 for fallbacks

Return a JSON object with field `results`: an array of exactly {n} entries.
Each entry MUST include: index (matching input), domain (echoing input), logo_url, confidence, source_element.
Optional notes field for any extraction caveats."""


async def call_gemini_batch(client, batch_idx, items, sem, model):
    """Send one batch of up to 20 URLs to Gemini URL Context."""
    async with sem:
        try:
            prompt = build_prompt(items)
            resp = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(url_context=types.UrlContext())],
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                ),
            )

            data = json.loads(resp.text or "{}")
            results = data.get("results", []) or []

            metadata = []
            try:
                for um in resp.candidates[0].url_context_metadata.url_metadata:
                    metadata.append({
                        "url": str(um.retrieved_url),
                        "status": str(um.url_retrieval_status),
                    })
            except Exception:
                pass

            usage = getattr(resp, "usage_metadata", None)
            usage_dict = {}
            if usage:
                usage_dict = {
                    "prompt_tokens": getattr(usage, "prompt_token_count", 0) or 0,
                    "tool_tokens": getattr(usage, "tool_use_prompt_token_count", 0) or 0,
                    "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
                    "thoughts_tokens": getattr(usage, "thoughts_token_count", 0) or 0,
                }

            return {
                "batch_idx": batch_idx,
                "items": [(i, d, u) for i, d, u in items],
                "results": results,
                "url_metadata": metadata,
                "usage": usage_dict,
                "error": None,
            }
        except Exception as e:
            return {
                "batch_idx": batch_idx,
                "items": [(i, d, u) for i, d, u in items],
                "results": [],
                "url_metadata": [],
                "usage": {},
                "error": f"{type(e).__name__}: {str(e)[:300]}",
            }


# ── HEAD verification ──────────────────────────────────────────────────
async def verify_logo_url(http, url):
    if not url or not isinstance(url, str):
        return False
    try:
        r = await http.head(url, follow_redirects=True, timeout=8.0)
        if r.status_code == 405:
            r = await http.get(url, follow_redirects=True, timeout=8.0)
        if r.status_code != 200:
            return False
        ctype = (r.headers.get("content-type") or "").lower()
        return (
            ctype.startswith("image/")
            or "svg" in ctype
            or ctype.startswith("application/octet-stream")
        )
    except Exception:
        return False


# ── Main pipeline ──────────────────────────────────────────────────────
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC),
                    help="Source JSON (default: gemini_200_filled.json)")
    ap.add_argument("--max", type=int, default=200, help="Process first N records")
    ap.add_argument("--batch-size", type=int, default=20, help="URLs per Gemini call (max 20)")
    ap.add_argument("--concurrency", type=int, default=5, help="Parallel batches")
    ap.add_argument("--model", default="gemini-3-flash-preview",
                    help="Gemini model (gemini-3-flash-preview | gemini-3-1-flash-lite | gemini-2.5-flash)")
    ap.add_argument("--no-verify", action="store_true", help="Skip HEAD verification")
    args = ap.parse_args()

    if args.batch_size > 20:
        raise SystemExit("ERROR: --batch-size cannot exceed 20 (URL Context tool limit)")

    OUT_DIR.mkdir(exist_ok=True)
    src_path = Path(args.src)
    if not src_path.is_absolute():
        src_path = HERE / src_path

    # ── Load source ────────────────────────────────────────────────────
    records = json.loads(src_path.read_text(encoding="utf-8"))[: args.max]
    print(f"Source:  {src_path.name}")
    print(f"Loaded:  {len(records)} records (capped to --max {args.max})")

    items = []
    for i, r in enumerate(records):
        dom = (r.get("domain") or "").strip().lower()
        if dom:
            items.append((i, dom, f"https://{dom}"))
    print(f"  with domain (sent to Gemini): {len(items)}")
    print(f"  without domain (auto-null):    {len(records) - len(items)}")

    if not items:
        raise SystemExit("Nothing to process — all records have null domain.")

    # ── Batch ─────────────────────────────────────────────────────────
    bs = args.batch_size
    batches = [items[i: i + bs] for i in range(0, len(items), bs)]
    print(f"\nBatches: {len(batches)} (up to {bs} URLs each)")
    print(f"Model:        {args.model}")
    print(f"Concurrency:  {args.concurrency}")

    # ── Stage 2: Gemini extraction ─────────────────────────────────────
    print("\nStage 2: Gemini URL Context extraction")
    client = make_client(args.model)
    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.time()
    tasks = [call_gemini_batch(client, i, b, sem, args.model) for i, b in enumerate(batches)]
    batch_results = await tqdm_asyncio.gather(*tasks, desc="Gemini batches")
    extract_t = time.time() - t0

    # Save raw responses for debugging
    (OUT_DIR / "raw_batches.json").write_text(
        json.dumps(batch_results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Map: original record index → extraction result
    extracted = {}
    failed = 0
    for br in batch_results:
        if br["error"]:
            failed += 1
            for orig_idx, _dom, _url in br["items"]:
                extracted[orig_idx] = {
                    "logo_url": None,
                    "source_element": "none",
                    "confidence": 0.0,
                    "verified": False,
                    "batch_error": br["error"],
                }
            continue
        by_dom = {(r.get("domain") or "").lower(): r for r in br["results"]}
        for orig_idx, dom, _url in br["items"]:
            res = by_dom.get(dom, {})
            extracted[orig_idx] = {
                "logo_url": res.get("logo_url"),
                "source_element": res.get("source_element", "none"),
                "confidence": res.get("confidence"),
                "verified": False,
                "notes": res.get("notes"),
            }

    if failed:
        print(f"\nWARNING: {failed} batches failed (see raw_batches.json)")

    # ── Stage 3: HEAD verification ─────────────────────────────────────
    verified_count = 0
    if not args.no_verify:
        print("\nStage 3: HEAD verification of returned logo URLs")
        async with httpx.AsyncClient() as http:
            jobs = [(idx, info.get("logo_url")) for idx, info in extracted.items()]
            verify_tasks = [verify_logo_url(http, url) for _, url in jobs]
            verify_res = await tqdm_asyncio.gather(*verify_tasks, desc="HEAD requests")
            for (idx, _url), ok in zip(jobs, verify_res):
                extracted[idx]["verified"] = ok
                if ok:
                    verified_count += 1
                else:
                    if extracted[idx].get("logo_url"):
                        extracted[idx]["unverified_url"] = extracted[idx]["logo_url"]
                        extracted[idx]["logo_url"] = None

    # ── Stage 4: Final per-record output ──────────────────────────────
    final = []
    for i, r in enumerate(records):
        info = extracted.get(i, {})
        final.append({
            "id": r["id"],
            "name": r["name"],
            "domain": r.get("domain"),
            "logo_url": info.get("logo_url"),
            "logo_source": info.get("source_element") or ("none" if r.get("domain") else "no_domain"),
            "logo_confidence": info.get("confidence"),
            "logo_verified": info.get("verified", False),
            "unverified_url": info.get("unverified_url"),
            "notes": info.get("notes"),
        })

    out_path = OUT_DIR / f"pilot_results_{args.max}.json"
    out_path.write_text(
        json.dumps(final, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # ── Summary ───────────────────────────────────────────────────────
    n = len(final)
    with_logo = sum(1 for r in final if r["logo_url"])
    by_source = {}
    for r in final:
        s = r["logo_source"] or "none"
        by_source[s] = by_source.get(s, 0) + 1

    total_prompt = sum(b["usage"].get("prompt_tokens", 0) for b in batch_results)
    total_tool = sum(b["usage"].get("tool_tokens", 0) for b in batch_results)
    total_output = sum(b["usage"].get("output_tokens", 0) for b in batch_results)
    total_input = total_prompt + total_tool

    is_flash3 = "3-flash" in args.model.lower()
    price_in = 0.50 if is_flash3 else 0.30
    price_out = 3.00 if is_flash3 else 2.50
    cost_in = total_input * price_in / 1_000_000
    cost_out = total_output * price_out / 1_000_000
    total_cost = cost_in + cost_out

    lines = [
        "=" * 72,
        f"URL Context extraction — model: {args.model}",
        "=" * 72,
        f"Source:                        {src_path.name}",
        f"Records:                       {n}",
        f"  with valid domain:           {len(items)}",
        f"  without domain (auto-null):  {n - len(items)}",
        f"Batches:                       {len(batches)}  (size up to {bs})",
        f"Failed batches:                {failed}",
        f"Concurrency:                   {args.concurrency}",
        f"Extraction wall-time:          {extract_t:.1f}s",
        "",
        "COVERAGE:",
        f"  Records with logo URL:       {with_logo}/{n}  ({with_logo/n*100:.1f}%)",
        f"  HEAD-verified:               {verified_count}",
        "",
        "BY SOURCE:",
    ]
    for src, cnt in sorted(by_source.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {src:<28s} {cnt:>4d}  ({cnt/n*100:5.1f}%)")
    lines += [
        "",
        "TOKEN USAGE:",
        f"  Prompt:      {total_prompt:>12,}",
        f"  Tool/HTML:   {total_tool:>12,}",
        f"  Output:      {total_output:>12,}",
        f"  Input total: {total_input:>12,}",
        "",
        f"COST ({args.model}, real-time pricing):",
        f"  Input:   ${cost_in:.4f}",
        f"  Output:  ${cost_out:.4f}",
        f"  Total:   ${total_cost:.4f}",
        "",
    ]
    if len(items) > 0 and total_cost > 0:
        per_with_domain = total_cost / len(items)
        lines.append("PROJECTION TO PRODUCTION (~22K with-domain records):")
        lines.append(f"  At this rate ({per_with_domain*1000:.2f} per 1K):  ${per_with_domain*22000:.2f}")
        lines.append(f"  With Batch mode (50% off):              ${per_with_domain*22000/2:.2f}")

    summary = "\n".join(lines)
    (OUT_DIR / "pilot_summary.txt").write_text(summary, encoding="utf-8")

    print("\n" + summary)
    print(f"\nFiles written:")
    print(f"  {out_path}")
    print(f"  {OUT_DIR / 'raw_batches.json'}")
    print(f"  {OUT_DIR / 'pilot_summary.txt'}")


if __name__ == "__main__":
    asyncio.run(main())
