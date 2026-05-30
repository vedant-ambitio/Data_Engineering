"""
Step 1: 200-record Gemini 2.5 Pro pilot.
Sample 200 IDs from the existing 500-pilot, run Gemini with the same prompt,
compare against Claude's answers.
"""
import json
import os
import random
from pathlib import Path

KEY = r"c:/Users/HP/OneDrive/Desktop/course_data/dashboard/gcp-key.json"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = KEY
PROJECT = json.load(open(KEY))["project_id"]
LOCATION = "global"
MODEL = "gemini-3.1-pro-preview"

BASE = Path(r"c:/Users/HP/OneDrive/Desktop/course_data/Logo_url_extract")
SOURCE_500 = BASE / "test_500_filled.json"
OUTPUT_GEMINI = BASE / "gemini_200_filled.json"
OUTPUT_COMPARE = BASE / "gemini_vs_claude_200.json"
OUTPUT_SUMMARY = BASE / "gemini_200_summary.txt"

SEED = 42
SAMPLE_N = 200

PROMPT_INSTRUCTIONS = """You are matching organization names to their official primary domain.

Rules:
- Only return a domain if you are at least 70% confident it is the organization's actual primary domain from your training data.
- Return null for: small/local entities, generic category names like "Hospital Systems" or "Public Relations Firms", ambiguous single-word names ("Bentley", "Icon"), or anything you'd need to web-search to verify.
- Do NOT construct domains by pattern (e.g. companyname.com). Only return domains you actually recall from training.
- Strip protocol and www prefix. Return bare domain (e.g. "amazon.com" not "https://www.amazon.com").
- For acquired/renamed companies, return the current parent domain (e.g. Sheraton -> marriott.com, Harris Corp -> l3harris.com).

For each input record, output a JSON object with these fields exactly:
{
  "id": "<id from input>",
  "name": "<name from input>",
  "domain": "<domain>" or null,
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one short sentence explaining your answer>"
}

Output a JSON array containing one object per input record, in the same order as the input.
Output ONLY the JSON array - no markdown fences, no commentary."""


def main() -> None:
    # Load the 500 records that Claude already processed
    with SOURCE_500.open(encoding="utf-8") as f:
        claude_data = json.load(f)
    print(f"Loaded {len(claude_data)} Claude-processed records")

    # Sample 200 deterministically
    random.seed(SEED)
    sampled = random.sample(claude_data, SAMPLE_N)
    print(f"Sampled {SAMPLE_N} records (seed={SEED})")

    # Strip Claude's answers — only keep the input fields
    inputs = [{"id": r["id"], "name": r["name"]} for r in sampled]

    # Build prompt
    prompt = PROMPT_INSTRUCTIONS + "\n\nInput records:\n" + json.dumps(inputs, indent=2)
    print(f"Prompt size: {len(prompt):,} chars (~{len(prompt)//4:,} tokens)")
    print()

    # Call Gemini
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    print(f"Calling {MODEL} on Vertex AI ({LOCATION})...")

    resp = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=32768,
            temperature=0.1,
        ),
    )

    # Usage
    if resp.usage_metadata:
        u = resp.usage_metadata
        in_tok = u.prompt_token_count
        out_tok = u.candidates_token_count
        # Cost calc (Gemini 3.1 Pro preview pricing for ≤200K context: $2/$12 per M)
        cost = in_tok * 2.00 / 1_000_000 + out_tok * 12.00 / 1_000_000
        print(f"Tokens — input: {in_tok:,}, output: {out_tok:,}")
        print(f"Cost: ${cost:.4f}")
        print()

    # Parse output
    text = resp.text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json\n").rstrip()
    gemini_results = json.loads(text)
    print(f"Gemini returned {len(gemini_results)} records")
    OUTPUT_GEMINI.write_text(json.dumps(gemini_results, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    print(f"Wrote -> {OUTPUT_GEMINI.name}")

    # Build a comparison: by id, line up Claude vs Gemini
    claude_by_id = {r["id"]: r for r in sampled}
    gemini_by_id = {r["id"]: r for r in gemini_results}
    common_ids = sorted(set(claude_by_id) & set(gemini_by_id))

    comparisons = []
    agree_filled = 0
    agree_null = 0
    disagree_diff_domain = 0
    disagree_only_claude = 0
    disagree_only_gemini = 0
    for cid in common_ids:
        c = claude_by_id[cid]
        g = gemini_by_id[cid]
        c_dom = c.get("domain")
        g_dom = g.get("domain")
        if c_dom and g_dom:
            if c_dom.lower() == g_dom.lower():
                cls = "agree_filled"
                agree_filled += 1
            else:
                cls = "disagree_different_domain"
                disagree_diff_domain += 1
        elif c_dom and not g_dom:
            cls = "only_claude_filled"
            disagree_only_claude += 1
        elif g_dom and not c_dom:
            cls = "only_gemini_filled"
            disagree_only_gemini += 1
        else:
            cls = "agree_both_null"
            agree_null += 1
        comparisons.append({
            "id": cid,
            "name": c["name"],
            "claude_domain": c_dom,
            "claude_conf": c.get("confidence"),
            "gemini_domain": g_dom,
            "gemini_conf": g.get("confidence"),
            "verdict": cls,
        })

    OUTPUT_COMPARE.write_text(json.dumps(comparisons, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    print(f"Wrote -> {OUTPUT_COMPARE.name}")

    # Summary
    n = len(common_ids)
    total_agree = agree_filled + agree_null
    lines = []
    lines.append(f"Gemini 2.5 Pro vs Claude Opus 4.7 — 200-record pilot comparison")
    lines.append(f"Sample size: {n}")
    lines.append("=" * 65)
    lines.append("")
    lines.append(f"Agreement breakdown:")
    lines.append(f"  Both filled with SAME domain   : {agree_filled:>4}  ({agree_filled/n*100:.1f}%)")
    lines.append(f"  Both returned null             : {agree_null:>4}  ({agree_null/n*100:.1f}%)")
    lines.append(f"  Filled with DIFFERENT domains  : {disagree_diff_domain:>4}  ({disagree_diff_domain/n*100:.1f}%)")
    lines.append(f"  Only Claude filled (Gemini null): {disagree_only_claude:>4}  ({disagree_only_claude/n*100:.1f}%)")
    lines.append(f"  Only Gemini filled (Claude null): {disagree_only_gemini:>4}  ({disagree_only_gemini/n*100:.1f}%)")
    lines.append("")
    lines.append(f"Total agreement: {total_agree}/{n}  ({total_agree/n*100:.1f}%)")
    lines.append("")
    lines.append("=" * 65)
    lines.append("DISAGREEMENTS (worth manual review)")
    lines.append("=" * 65)
    for c in comparisons:
        if c["verdict"] != "agree_filled" and c["verdict"] != "agree_both_null":
            lines.append(
                f"  [{c['verdict'][:25]:<25}]  {c['name'][:35]:<35}  "
                f"Claude={c['claude_domain']!s:<25}  Gemini={c['gemini_domain']!s}"
            )

    summary = "\n".join(lines)
    OUTPUT_SUMMARY.write_text(summary, encoding="utf-8")
    print()
    print(summary)


if __name__ == "__main__":
    main()
