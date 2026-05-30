#!/usr/bin/env python3
"""
discover_devpost.py — Use BrowserOS to discover HS hackathons from devpost
===========================================================================

Opens devpost.com/hackathons with "high school" search filter in BrowserOS,
scrapes all hackathon URLs across all pages, saves minimal input JSONs.

Output: Competitions/competition_data/input/{slug}.json
  Each file: {"competition_id": "hackamerica", "source_url": "https://hackamerica.devpost.com/"}

Usage:
  python discover_devpost.py
  python discover_devpost.py --dry-run
"""

import json
import os
import re
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "competition_data", "input")
BROWSEROS_MCP_URL = "http://127.0.0.1:9000/mcp"

# Devpost search URL for HS hackathons (upcoming + open)
# TRICK: Devpost uses virtual scrolling — only ~9 cards render at a time.
# Sorting by "prize-amount" forces all cards to load in the DOM at once.
# Other sort orders (recently-added, submission-date) only show 9.
# For cron job: always use order_by=prize-amount to get all results.
DEVPOST_PAGES = [
    "https://devpost.com/hackathons?search=high+school&status[]=upcoming&status[]=open&order_by=prize-amount",
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
        if "error" in resp:
            print(f"  [MCP ERROR] {resp['error']}")
            return None
        return resp.get("result", {})
    except Exception as e:
        print(f"  [MCP ERROR] {e}")
        return None


def get_active_page_id():
    result = mcp_tool("get_active_page")
    if result:
        sc = result.get("structuredContent", {})
        page = sc.get("page", {})
        return page.get("pageId", 1)
    return 1


def navigate(page_id, url):
    print(f"  [BROWSER] Opening {url[:80]}...")
    mcp_tool("navigate_page", {"page": page_id, "url": url})
    time.sleep(5)  # devpost is JS-heavy, needs time to render


def get_page_content(page_id):
    result = mcp_tool("get_page_content", {"page": page_id})
    if result:
        content_parts = result.get("content", [])
        text = ""
        for part in content_parts:
            if part.get("type") == "text":
                text += part["text"] + "\n"
        return text.strip()
    return ""


def get_page_links(page_id):
    result = mcp_tool("get_page_links", {"page": page_id})
    if result:
        content_parts = result.get("content", [])
        for part in content_parts:
            if part.get("type") == "text":
                return part["text"]
    return ""


def extract_hackathon_urls(content, links_text):
    """Extract devpost hackathon URLs from page content and links."""
    urls = set()

    # Pattern: anything.devpost.com (hackathon subdomains)
    all_text = (content or "") + "\n" + (links_text or "")
    matches = re.findall(r'https?://([a-z0-9-]+)\.devpost\.com/?', all_text, re.IGNORECASE)

    for slug in matches:
        # Skip devpost's own pages
        if slug in ("www", "devpost", "api", "info", "blog", "help", "support"):
            continue
        url = f"https://{slug}.devpost.com/"
        urls.add((slug, url))

    return list(urls)


def main():
    dry_run = "--dry-run" in sys.argv

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Check BrowserOS
    print("Checking BrowserOS connection...")
    page_id = get_active_page_id()
    if not page_id:
        print("[ERROR] Cannot connect to BrowserOS. Is it running?")
        sys.exit(1)
    print(f"BrowserOS connected (page ID: {page_id})\n")

    all_hackathons = []

    for i, page_url in enumerate(DEVPOST_PAGES):
        print(f"\n--- Page {i+1}/{len(DEVPOST_PAGES)} ---")

        if dry_run:
            print(f"  [DRY RUN] Would open: {page_url}")
            continue

        navigate(page_id, page_url)
        content = get_page_content(page_id)
        links = get_page_links(page_id)

        print(f"  Content: {len(content)} chars, Links: {len(links)} chars")

        hackathons = extract_hackathon_urls(content, links)
        print(f"  Found {len(hackathons)} hackathon URLs")

        for slug, url in hackathons:
            print(f"    {slug:<35} {url}")

        all_hackathons.extend(hackathons)
        time.sleep(2)

    # Deduplicate
    seen = set()
    unique = []
    for slug, url in all_hackathons:
        if slug not in seen:
            seen.add(slug)
            unique.append((slug, url))

    print(f"\n{'='*60}")
    print(f"  Total unique hackathons: {len(unique)}")
    print(f"{'='*60}")

    if dry_run:
        return

    # Save input JSONs
    for slug, url in unique:
        input_json = {
            "competition_id": slug,
            "source_url": url,
        }
        out_file = os.path.join(OUTPUT_DIR, f"{slug}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(input_json, f, indent=2)

    print(f"\nSaved {len(unique)} input JSONs to {OUTPUT_DIR}/")

    # Also save a summary
    summary = {
        "discovered": len(unique),
        "source": "devpost.com",
        "search": "high school (upcoming + open)",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hackathons": [{"id": slug, "url": url} for slug, url in sorted(unique)],
    }
    summary_file = os.path.join(SCRIPT_DIR, "competition_data", "_discovery_summary.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary saved to {summary_file}")


if __name__ == "__main__":
    main()
