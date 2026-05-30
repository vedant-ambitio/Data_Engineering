"""
Extract official URLs from course markdown files.

Strategy (Seed & Expand):
1. For each .md file, extract the official "Program page URL" from ## Important Links
2. Derive the root domain (seed) using tldextract
3. Scan ALL URLs in the file (citations, inline, Sources section)
4. Keep only URLs whose root domain matches the seed
5. Fallback: if no seed from Important Links, try citation after Important Links,
   then citation after Program Overview
"""

import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict

import tldextract


# ---------- URL extraction helpers ----------

URL_REGEX = re.compile(r'https?://[^\s)<>\]\`\'"]+')


def extract_urls_from_text(text: str) -> list[str]:
    """Extract all URLs from a block of text, stripping trailing punctuation."""
    urls = URL_REGEX.findall(text)
    cleaned = []
    for url in urls:
        # Strip trailing punctuation that's not part of the URL
        url = url.rstrip(".,;:!?'\")")
        # Strip trailing markdown artifacts
        url = url.rstrip("`")
        cleaned.append(url)
    return cleaned


def get_root_domain(url: str) -> str:
    """Extract the registrable root domain from a URL using tldextract.
    e.g. https://homes.cs.aau.dk/~ivan/ -> aau.dk
         https://gsas.columbia.edu/content -> columbia.edu
         https://www.ox.ac.uk/admissions -> ox.ac.uk
    """
    ext = tldextract.extract(url)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return ""


# ---------- Seed extraction from Important Links ----------

def extract_seed_from_important_links(content: str) -> tuple[str, str]:
    """Extract the Program page URL from ## Important Links section.
    Returns (seed_url, seed_domain) or ("", "").
    """
    # Find the Important Links section
    match = re.search(
        r'## Important Links\s*\n(.*?)(?=\n## |\n---|\Z)',
        content,
        re.DOTALL
    )
    if not match:
        return "", ""

    section = match.group(1)

    # Look for Program page URL specifically first (highest confidence)
    program_url_patterns = [
        r'\*\*Program [Pp]age (?:URL|url)\s*[:：]\*\*\s*[`]?(https?://[^\s)<>\]\`\'"]+)',
        r'\*\*Program [Pp]age\s*[:：]\*\*\s*[`]?(https?://[^\s)<>\]\`\'"]+)',
        r'Program [Pp]age (?:URL|url)\s*[:：]\s*[`]?(https?://[^\s)<>\]\`\'"]+)',
    ]
    for pattern in program_url_patterns:
        m = re.search(pattern, section)
        if m:
            url = m.group(1).rstrip(".,;:!?'\")`")
            domain = get_root_domain(url)
            if domain:
                return url, domain

    # Fallback: grab ANY URL from the Important Links section
    urls = extract_urls_from_text(section)
    for url in urls:
        domain = get_root_domain(url)
        if domain:
            return url, domain

    return "", ""


def extract_seed_from_citation_after_section(content: str, section_name: str) -> tuple[str, str]:
    """Extract seed from the <citation> block following a given section header."""
    pattern = rf'## {re.escape(section_name)}\s*\n.*?<citation>\s*\n.*?urls:\s*\n(.*?)</citation>'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return "", ""

    citation_urls_text = match.group(1)
    urls = extract_urls_from_text(citation_urls_text)

    # Among citation URLs, try to find one that looks like a university domain
    # Heuristic: prefer .edu, .ac.*, or shorter domains (universities tend to have short domains)
    candidates = []
    for url in urls:
        domain = get_root_domain(url)
        if domain:
            candidates.append((url, domain))

    if not candidates:
        return "", ""

    # Prefer .edu or academic TLDs
    for url, domain in candidates:
        if '.edu' in domain or '.ac.' in domain:
            return url, domain

    # Otherwise return the first one (imperfect but better than nothing)
    return candidates[0]


def extract_seed_from_important_links_citation(content: str) -> tuple[str, str]:
    """Extract seed from the <citation> block that follows ## Important Links."""
    # Find Important Links section and its following citation
    match = re.search(
        r'## Important Links\s*\n.*?<citation>\s*\n.*?urls:\s*\n(.*?)</citation>',
        content,
        re.DOTALL
    )
    if not match:
        return "", ""

    citation_text = match.group(1)
    urls = extract_urls_from_text(citation_text)

    # Among these, find the most likely official one
    candidates = [(url, get_root_domain(url)) for url in urls if get_root_domain(url)]
    if not candidates:
        return "", ""

    # Prefer .edu or academic TLDs
    for url, domain in candidates:
        if '.edu' in domain or '.ac.' in domain:
            return url, domain

    return candidates[0]


# ---------- Main per-file extraction ----------

def get_seed_domain(content: str) -> tuple[str, str, str]:
    """Try multiple strategies to find the seed domain for a file.
    Returns (seed_url, seed_domain, method_used).
    """
    # Strategy 1: Program page URL from Important Links
    url, domain = extract_seed_from_important_links(content)
    if domain:
        return url, domain, "important_links_inline"

    # Strategy 2: Citation block after Important Links
    url, domain = extract_seed_from_important_links_citation(content)
    if domain:
        return url, domain, "important_links_citation"

    # Strategy 3: Citation block after Program Overview
    url, domain = extract_seed_from_citation_after_section(content, "Program Overview")
    if domain:
        return url, domain, "program_overview_citation"

    return "", "", "none"


def extract_official_urls_from_file(content: str) -> dict:
    """Extract all official URLs from a single markdown file.
    Returns dict with seed info and list of official URLs.
    """
    seed_url, seed_domain, method = get_seed_domain(content)

    if not seed_domain:
        return {
            "seed_domain": "",
            "seed_url": "",
            "seed_method": method,
            "official_urls": []
        }

    # Extract ALL URLs from the entire file
    all_urls = extract_urls_from_text(content)

    # Filter: keep only URLs whose root domain matches the seed
    official_urls = []
    seen = set()
    for url in all_urls:
        if url in seen:
            continue
        seen.add(url)
        if get_root_domain(url) == seed_domain:
            official_urls.append(url)

    return {
        "seed_domain": seed_domain,
        "seed_url": seed_url,
        "seed_method": method,
        "official_urls": sorted(set(official_urls))
    }


# ---------- Process all files ----------

def process_directory(base_dir: str, program_type: str) -> tuple[dict, list]:
    """Process all .md files in a directory tree.
    Returns (results_dict, failures_list).
    """
    results = {}
    failures = []
    file_count = 0

    base_path = Path(base_dir)
    # Find the inner directory (masters/masters/ or phd/phd/)
    md_files = sorted(base_path.rglob("*.md"))

    for md_file in md_files:
        # Skip macOS metadata junk folders
        if '__MACOSX' in md_file.parts or md_file.name.startswith('._'):
            continue

        file_count += 1
        rel_parts = md_file.relative_to(base_path).parts

        # Expected structure: masters/CollegeName/file.md or phd/CollegeName/file.md
        if len(rel_parts) != 3:
            continue

        college_name = rel_parts[1]  # e.g., "Columbia_University"
        file_name = rel_parts[2]     # e.g., "Columbia_University_Master_of_Arts_Biotechnology.md"

        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            failures.append({
                "file": str(md_file),
                "college": college_name,
                "reason": f"read_error: {e}"
            })
            continue

        result = extract_official_urls_from_file(content)

        if not result["seed_domain"]:
            failures.append({
                "file": str(md_file),
                "college": college_name,
                "reason": "no_seed_found",
                "seed_method": result["seed_method"]
            })

        # Store in nested dict: college -> file -> data
        if college_name not in results:
            results[college_name] = {}

        results[college_name][file_name] = {
            "seed_domain": result["seed_domain"],
            "seed_url": result["seed_url"],
            "official_urls": result["official_urls"]
        }

        if file_count % 1000 == 0:
            print(f"  Processed {file_count} files...", flush=True)

    print(f"  Total: {file_count} files processed.", flush=True)
    return results, failures


def main():
    course_data_dir = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
    output_dir = course_data_dir / "official_urls"
    output_dir.mkdir(exist_ok=True)

    masters_dir = course_data_dir / "masters_data"
    phd_dir = course_data_dir / "phd_data"

    all_failures = []
    total_files = 0
    total_with_seed = 0
    total_without_seed = 0
    total_official_urls = 0

    for program_type, base_dir in [("masters", masters_dir), ("phd", phd_dir)]:
        print(f"\nProcessing {program_type}...")
        if not base_dir.exists():
            print(f"  Directory not found: {base_dir}")
            continue

        results, failures = process_directory(str(base_dir), program_type)

        # Count stats
        for college, files in results.items():
            for fname, data in files.items():
                total_files += 1
                if data["seed_domain"]:
                    total_with_seed += 1
                    total_official_urls += len(data["official_urls"])
                else:
                    total_without_seed += 1

        all_failures.extend(failures)

        # Write results
        output_file = output_dir / f"{program_type}_official_urls.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"  Saved to {output_file}")

    # Write extraction report
    report = {
        "total_files_processed": total_files,
        "files_with_seed": total_with_seed,
        "files_without_seed": total_without_seed,
        "total_official_urls_extracted": total_official_urls,
        "failure_count": len(all_failures),
        "failures": all_failures
    }
    report_file = output_dir / "extraction_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved to {report_file}")

    # Print summary
    print(f"\n{'='*50}")
    print(f"SUMMARY")
    print(f"{'='*50}")
    print(f"Total files processed:       {total_files}")
    print(f"Files with seed (success):   {total_with_seed}")
    print(f"Files without seed (failed): {total_without_seed}")
    print(f"Total official URLs found:   {total_official_urls}")
    print(f"Success rate:                {total_with_seed/max(total_files,1)*100:.1f}%")


if __name__ == "__main__":
    main()
