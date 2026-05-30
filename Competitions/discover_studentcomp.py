#!/usr/bin/env python3
"""
discover_studentcomp.py — Discovery with High School filtering
==============================================================
"""

import json
import os
import re
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "competition_data", "input_studentcomp")
BROWSEROS_MCP_URL = "http://127.0.0.1:9000/mcp"

PAGES = [
    "https://studentcompetitions.com/competitions?page=1",
    "https://studentcompetitions.com/competitions?page=2",
    "https://studentcompetitions.com/competitions?page=3",
]

_mcp_call_id = 0

def mcp_tool(tool_name, arguments=None):
    global _mcp_call_id
    _mcp_call_id += 1
    payload = {
        "jsonrpc": "2.0",
        "id": _mcp_call_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", BROWSEROS_MCP_URL,
             "-H", "Content-Type: application/json",
             "-H", "Accept: application/json, text/event-stream",
             "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=60,
        )
        resp = json.loads(result.stdout)
        return resp.get("result", {})
    except Exception as e:
        print(f"  [MCP ERROR] {e}")
        return None

def get_active_page_id():
    result = mcp_tool("get_active_page")
    if result:
        return result.get("structuredContent", {}).get("page", {}).get("pageId", 1)
    return 1

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    page_id = get_active_page_id()
    print(f"BrowserOS connected (page ID: {page_id})")

    all_hs_slugs = set()

    for i, page_url in enumerate(PAGES):
        print(f"\nPage {i+1}/{len(PAGES)}: {page_url}")
        mcp_tool("navigate_page", {"page": page_id, "url": page_url})
        time.sleep(5)

        result = mcp_tool("get_page_content", {"page": page_id})
        if result:
            content = ""
            for p in result.get("content", []):
                if p.get("type") == "text":
                    content += p.get("text", "") + "\n"
            
            # Split by competition cards (usually separated by headers or lines)
            # We look for slugs that have "High school" nearby in the text
            cards = re.split(r'\n(?=\[)', content) # Split at markdown links
            for card in cards:
                if "high school" in card.lower():
                    match = re.search(r'studentcompetitions\.com/competitions/([a-z0-9][-a-z0-9]+)', card, re.IGNORECASE)
                    if match:
                        slug = match.group(1)
                        all_hs_slugs.add(slug)
                        print(f"  FOUND HS: {slug}")

    print(f"\nTotal HS unique: {len(all_hs_slugs)}")

    # Save input JSONs
    for slug in sorted(all_hs_slugs):
        data = {
            "competition_id": f"sc_{slug}",
            "source_url": f"https://studentcompetitions.com/competitions/{slug}",
        }
        out_file = os.path.join(OUTPUT_DIR, f"sc_{slug}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    print(f"Saved {len(all_hs_slugs)} HS input JSONs to {OUTPUT_DIR}/")

if __name__ == "__main__":
    main()
