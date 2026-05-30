"""
test_models.py

Standalone smoke test: does gemini-3.5-flash and gemini-3.1-flash-lite
actually work on this Vertex AI project with Google Search grounding?

Makes ONE grounded call per model, reports:
  - success / failure
  - latency
  - token usage + estimated cost
  - number of grounding chunks
  - first 300 chars of the answer

Does NOT touch the gaps registry, evidence files, or cost log.
Burns ~$0.03 worth of credits total (2 grounded calls).

Usage:
    python masters_v2/scripts/test_models.py
"""

import json
import os
import sys
import time
from pathlib import Path

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
GCP_KEY = BASE / "dashboard" / "gcp-key.json"

MODELS_TO_TEST = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
]

TEST_PROMPT = (
    "What is the current application deadline for the Master of Business "
    "Administration program at MIT Sloan School of Management for the Fall 2026 "
    "intake? Search the official MIT Sloan admissions website. Reply with the "
    "exact deadline date in 1-2 sentences."
)


def init_client():
    try:
        from google import genai
    except ImportError:
        print("ERROR: 'google-genai' package not installed. Run: pip install google-genai")
        sys.exit(1)

    if not GCP_KEY.exists():
        print(f"ERROR: GCP key not found at {GCP_KEY}")
        sys.exit(1)

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(GCP_KEY)
    with open(GCP_KEY, encoding="utf-8") as f:
        key_data = json.load(f)
    project_id = key_data["project_id"]

    client = genai.Client(
        vertexai=True,
        project=project_id,
        location="global",
    )
    return client, project_id


def test_one(client, model):
    from google.genai import types

    print(f"\n{'=' * 70}")
    print(f"Testing: {model}")
    print('=' * 70)

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.1,
    )

    t0 = time.time()
    try:
        response = client.models.generate_content(
            model=model,
            contents=TEST_PROMPT,
            config=config,
        )
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  [FAIL] {type(e).__name__} after {elapsed:.1f}s")
        print(f"  Error: {str(e)[:300]}")
        return {"model": model, "success": False, "error": str(e)[:300], "elapsed": elapsed}

    elapsed = time.time() - t0

    text = ""
    try:
        text = response.text or ""
    except Exception:
        if response.candidates:
            parts = response.candidates[0].content.parts or []
            text = "".join(p.text or "" for p in parts if hasattr(p, "text"))

    n_chunks = 0
    n_queries = 0
    if response.candidates:
        cand = response.candidates[0]
        gm = getattr(cand, "grounding_metadata", None)
        if gm:
            n_chunks = len(gm.grounding_chunks or [])
            n_queries = len(getattr(gm, "web_search_queries", []) or [])

    usage = getattr(response, "usage_metadata", None)
    in_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
    out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0

    print(f"  [OK]   latency={elapsed:.1f}s  tokens={in_tok}/{out_tok}  "
          f"chunks={n_chunks}  search_queries={n_queries}")
    print(f"  Answer: {text[:300]}{'...' if len(text) > 300 else ''}")

    return {
        "model": model,
        "success": True,
        "elapsed": elapsed,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "grounding_chunks": n_chunks,
        "search_queries": n_queries,
        "answer_preview": text[:300],
    }


def main():
    client, project_id = init_client()
    print(f"Project: {project_id}")
    print(f"Endpoint: global (Vertex AI)")
    print(f"Models to test: {MODELS_TO_TEST}")

    results = []
    for model in MODELS_TO_TEST:
        results.append(test_one(client, model))

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print('=' * 70)
    for r in results:
        status = "OK  " if r["success"] else "FAIL"
        if r["success"]:
            print(f"  {status}  {r['model']:30s}  {r['elapsed']:5.1f}s  "
                  f"in={r['input_tokens']:4d} out={r['output_tokens']:4d}  "
                  f"chunks={r['grounding_chunks']}")
        else:
            err = r.get("error", "")[:80]
            print(f"  {status}  {r['model']:30s}  {r['elapsed']:5.1f}s  {err}")


if __name__ == "__main__":
    main()
