"""
Step 0: Verify Vertex AI access via service account key.
Tries Gemini 3 Pro first, falls back to other model names.
"""
import os
from pathlib import Path

KEY_PATH = r"c:/Users/HP/OneDrive/Desktop/course_data/dashboard/gcp-key.json"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = KEY_PATH

import json
with open(KEY_PATH) as f:
    project_id = json.load(f)["project_id"]

print(f"Project: {project_id}")
print(f"Key:     {KEY_PATH}")
print()

from google import genai

client = genai.Client(
    vertexai=True,
    project=project_id,
    location="global",  # try global first
)

candidates = [
    "gemini-3-pro-preview",
    "gemini-3-pro",
    "gemini-2.5-pro",
]

for model_name in candidates:
    try:
        print(f"Trying model: {model_name}...")
        resp = client.models.generate_content(
            model=model_name,
            contents="Reply with exactly: OK",
        )
        print(f"  SUCCESS — {model_name}")
        print(f"  Response: {resp.text!r}")
        if resp.usage_metadata:
            print(f"  Tokens — input: {resp.usage_metadata.prompt_token_count}, "
                  f"output: {resp.usage_metadata.candidates_token_count}")
        print()
        print(f"USE THIS MODEL: {model_name}")
        break
    except Exception as e:
        msg = str(e)
        print(f"  FAILED: {type(e).__name__}: {msg[:200]}")
        print()
else:
    print("All model candidates failed.")
