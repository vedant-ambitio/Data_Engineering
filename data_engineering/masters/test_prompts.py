"""
test_prompts.py

Head-to-head test of the NEW (search-first) production prompts on:
  - gemini-3.5-flash
  - gemini-3.1-flash-lite

Picks 5 real gaps from the registry, calls each model with the actual
production prompts, reports grounding chunks per call and side-by-side
answer quality.

Usage:
    python masters_v2/scripts/test_prompts.py
"""

import json
import os
import sys
import time
from pathlib import Path

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
GCP_KEY = BASE / "dashboard" / "gcp-key.json"

# Reuse the actual production prompt builders + helpers
sys.path.insert(0, str(BASE / "masters_v2" / "scripts"))
from run_gemini_pipeline import (  # noqa: E402
    build_section_prompt,
    build_entity_prompt,
    clean_program_name,
    clean_university_name,
)

MODELS_TO_TEST = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

# Hand-picked diverse gaps — mix of section + entity, well-known + obscure
TEST_GAPS = [
    # 1. Section gap: tuition for a well-known program
    {
        "course": {"file": "Stanford_University_Master_of_Science_Computer_Science.md",
                   "college": "Stanford_University"},
        "gap": {"type": "section", "field": "tuition_and_fees", "section_heading": "Tuition & Fees"},
    },
    # 2. Section gap: deadlines
    {
        "course": {"file": "Imperial_College_London_MSc_Business_Analytics.md",
                   "college": "Imperial_College_London"},
        "gap": {"type": "section", "field": "application_deadlines", "section_heading": "Application Deadlines"},
    },
    # 3. Entity: TOEFL minimum
    {
        "course": {"file": "Technical_University_of_Munich_Master_of_Science_Informatics.md",
                   "college": "Technical_University_of_Munich"},
        "gap": {"type": "entity", "field": "toefl", "section_heading": "Admission Requirements"},
    },
    # 4. Entity: GPA
    {
        "course": {"file": "ETH_Zurich_Master_of_Science_Data_Science.md",
                   "college": "ETH_Zurich"},
        "gap": {"type": "entity", "field": "gpa", "section_heading": "Admission Requirements"},
    },
    # 5. Entity: application fee
    {
        "course": {"file": "University_of_Toronto_Master_of_Engineering_Industrial_Engineering.md",
                   "college": "University_of_Toronto"},
        "gap": {"type": "entity", "field": "app_fee", "section_heading": "Admission Requirements"},
    },
]


def init_client():
    from google import genai
    if not GCP_KEY.exists():
        print(f"ERROR: GCP key not found at {GCP_KEY}")
        sys.exit(1)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(GCP_KEY)
    with open(GCP_KEY, encoding="utf-8") as f:
        key_data = json.load(f)
    client = genai.Client(vertexai=True, project=key_data["project_id"], location="global")
    return client


def call_once(client, model, prompt):
    from google.genai import types
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.1,
    )
    t0 = time.time()
    try:
        resp = client.models.generate_content(model=model, contents=prompt, config=config)
    except Exception as e:
        return {"ok": False, "err": str(e)[:200], "elapsed": time.time() - t0}
    elapsed = time.time() - t0
    text = ""
    try: text = resp.text or ""
    except: pass
    n_chunks = 0
    n_queries = 0
    if resp.candidates:
        gm = getattr(resp.candidates[0], "grounding_metadata", None)
        if gm:
            n_chunks = len(gm.grounding_chunks or [])
            n_queries = len(getattr(gm, "web_search_queries", []) or [])
    return {"ok": True, "elapsed": elapsed, "chunks": n_chunks, "queries": n_queries,
            "text": text[:250]}


def build_prompt(test):
    course = test["course"]
    gap = test["gap"]
    program = clean_program_name(course["file"], course["college"])
    university = clean_university_name(course["college"])
    if gap["type"] == "section":
        return build_section_prompt(gap["field"], program, university, gap)
    return build_entity_prompt(gap["field"], program, university, gap)


def main():
    client = init_client()
    print(f"Testing {len(MODELS_TO_TEST)} models on {len(TEST_GAPS)} prompts each = "
          f"{len(MODELS_TO_TEST)*len(TEST_GAPS)} calls\n")

    results = {m: [] for m in MODELS_TO_TEST}

    for i, test in enumerate(TEST_GAPS, 1):
        gap = test["gap"]
        course = test["course"]
        label = f"{gap['field']} @ {course['college']}"
        print(f"--- Test {i}/{len(TEST_GAPS)}: {label} ---")
        prompt = build_prompt(test)
        for model in MODELS_TO_TEST:
            r = call_once(client, model, prompt)
            results[model].append(r)
            if r["ok"]:
                status = "GROUNDED" if r["chunks"] > 0 else "UNGROUNDED"
                print(f"  {model:25s}  {status:10s} chunks={r['chunks']}  queries={r['queries']}  {r['elapsed']:5.1f}s")
                print(f"    answer: {r['text'][:120]}...")
            else:
                print(f"  {model:25s}  FAIL  {r['err'][:80]}")
        print()

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for model in MODELS_TO_TEST:
        rs = results[model]
        ok = [r for r in rs if r.get("ok")]
        grounded = [r for r in ok if r["chunks"] > 0]
        avg_chunks = sum(r["chunks"] for r in ok) / len(ok) if ok else 0
        avg_time = sum(r["elapsed"] for r in ok) / len(ok) if ok else 0
        print(f"\n  {model}")
        print(f"    Grounded: {len(grounded)}/{len(rs)} ({len(grounded)*100/len(rs):.0f}%)")
        print(f"    Avg chunks/call: {avg_chunks:.1f}")
        print(f"    Avg latency:     {avg_time:.1f}s")


if __name__ == "__main__":
    main()
