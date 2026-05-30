"""
Stage A: Extract official URLs per section from course markdown files.

For each .md file, parses the <citation> blocks inside 3 target sections:
  - Admission Requirements
  - Tuition & Fees
  - Application Deadlines

Filters URLs using the seed_domain from our previously extracted official_urls JSONs.
Deduplicates within each section (but same URL can appear across sections).
"""

import json
import re
from pathlib import Path

import tldextract


URL_RE = re.compile(r'https?://[^\s)<>\]\`\'"]+')

TARGET_SECTIONS = {
    "admission_requirements": "Admission Requirements",
    "tuition_and_fees": "Tuition & Fees",
    "application_deadlines": "Application Deadlines",
}


def get_root_domain(url: str) -> str:
    ext = tldextract.extract(url)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return ""


def extract_section_with_citations(content: str, section_heading: str) -> str:
    """Extract the full text of a section (including all citation blocks)
    from ## heading to the next ## heading."""
    pattern = rf'^## {re.escape(section_heading)}\s*\n(.*?)(?=\n## |\Z)'
    matches = re.findall(pattern, content, re.DOTALL | re.MULTILINE)
    # Join all matches (handles duplicated content in files)
    return "\n".join(matches)


def extract_official_urls_from_citations(section_text: str, seed_domain: str) -> list[str]:
    """Extract official URLs from <citation> blocks within a section."""
    # Find all citation blocks
    citation_blocks = re.findall(
        r'<citation>.*?urls:\s*\n(.*?)</citation>',
        section_text,
        re.DOTALL
    )

    official_urls = set()
    for block in citation_blocks:
        urls = URL_RE.findall(block)
        for url in urls:
            url = url.rstrip(".,;:!?'\")`")
            if get_root_domain(url) == seed_domain:
                official_urls.add(url)

    return sorted(official_urls)


def extract_official_urls_from_inline(section_text: str, official_domains: set[str]) -> list[str]:
    """Extract official URLs from inline (Source: URL) patterns within a section.
    Matches against a set of known official domains (not just one seed domain)."""
    # Match (Source: https://...)
    source_urls = re.findall(r'\(Source:\s*(https?://[^\s\)]+)\)', section_text)

    official_urls = set()
    for url in source_urls:
        url = url.rstrip(".,;:!?'\")`")
        if get_root_domain(url) in official_domains:
            official_urls.add(url)

    return sorted(official_urls)


def process_files(official_urls_json: str, md_base_dir: str, program_type: str) -> dict:
    """Process all files for a program type (masters or phd)."""
    with open(official_urls_json, encoding="utf-8") as f:
        official_data = json.load(f)

    base_path = Path(md_base_dir)
    results = {}
    file_count = 0
    files_with_no_section_urls = 0

    for college, files in official_data.items():
        results[college] = {}

        for fname, fdata in files.items():
            seed_domain = fdata.get("seed_domain", "")
            if not seed_domain:
                # No seed domain — store empty sections
                results[college][fname] = {
                    "seed_domain": "",
                    "admission_requirements": [],
                    "tuition_and_fees": [],
                    "application_deadlines": [],
                }
                file_count += 1
                files_with_no_section_urls += 1
                continue

            fpath = base_path / college / fname
            if not fpath.exists():
                continue

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            file_result = {"seed_domain": seed_domain}
            has_any_url = False

            for key, heading in TARGET_SECTIONS.items():
                section_text = extract_section_with_citations(content, heading)
                urls = extract_official_urls_from_citations(section_text, seed_domain)
                file_result[key] = urls
                if urls:
                    has_any_url = True

            results[college][fname] = file_result
            file_count += 1

            if not has_any_url:
                files_with_no_section_urls += 1

            if file_count % 2000 == 0:
                print(f"  Processed {file_count} files...", flush=True)

    print(f"  Total: {file_count} files processed.", flush=True)
    return results


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename (match run_gemini_scraper.py logic exactly)."""
    name = re.sub(r'[:*?"<>|]', '_', name)
    name = re.sub(r'[\s]+', '_', name)
    name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', name)
    name = re.sub(r'_+', '_', name)
    return name.strip('_')


def build_official_domains_from_csv(csv_path: str) -> dict[str, set[str]]:
    """Build a mapping of university_name -> set of official root domains from CSV data.
    Uses officialPageLink + all URLs in officialLinks to discover official domains."""
    import csv

    uni_domains = {}  # university_name -> set of root domains

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            university = row.get("University Name", "").strip()
            if not university:
                continue

            level = row.get("course_level", "").strip().lower()
            if level not in ("bachelor", "undergraduate", "ug"):
                continue

            uni_key = sanitize_filename(university)
            if uni_key not in uni_domains:
                uni_domains[uni_key] = set()

            # Extract domain from officialPageLink
            official_url = row.get("officialPageLink", "").strip()
            if official_url:
                d = get_root_domain(official_url)
                if d:
                    uni_domains[uni_key].add(d)

            # Extract domains from officialLinks array
            raw_links = row.get("officialLinks", "").strip()
            if raw_links and raw_links != "[]":
                raw_links = raw_links.strip("[]")
                links = [u.strip().strip('"') for u in re.split(r'"\s+"', raw_links) if u.strip().strip('"')]
                for link in links:
                    d = get_root_domain(link)
                    if d:
                        uni_domains[uni_key].add(d)

    # Print summary
    for uni, domains in sorted(uni_domains.items()):
        if len(domains) > 1:
            print(f"  {uni}: {sorted(domains)}")

    return uni_domains


def process_files_csv(csv_path: str, md_base_dir: str) -> dict:
    """Process UG files using CSV data for official domains (inline Source: URLs)."""
    import csv

    base_path = Path(md_base_dir)

    # First pass: build official domain sets per university
    print("  Building official domain sets from CSV...")
    uni_domains = build_official_domains_from_csv(csv_path)

    results = {}
    file_count = 0
    files_with_no_section_urls = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            university = row.get("University Name", "").strip()
            if not university:
                continue

            # Only process UG
            level = row.get("course_level", "").strip().lower()
            if level not in ("bachelor", "undergraduate", "ug"):
                continue

            # Get official domains for this university
            uni_dir = sanitize_filename(university)
            official_domains = uni_domains.get(uni_dir, set())

            # Build expected filename (match run_gemini_scraper.py logic)
            major = row.get("course_major_name", "").strip()
            specialization = row.get("course_specialization_name", "").strip()
            degree_name = row.get("course_degree_name", "").strip()
            program_name = specialization if specialization and specialization != major else major

            if degree_name and degree_name != "?":
                fname = f"{sanitize_filename(university)}_{sanitize_filename(degree_name)}_{sanitize_filename(program_name)}.md"
            else:
                fname = f"{sanitize_filename(university)}_{sanitize_filename(program_name)}.md"

            fpath = base_path / uni_dir / fname
            if not fpath.exists():
                continue

            # Initialize college in results
            if uni_dir not in results:
                results[uni_dir] = {}

            if not official_domains:
                results[uni_dir][fname] = {
                    "official_domains": [],
                    "admission_requirements": [],
                    "tuition_and_fees": [],
                    "application_deadlines": [],
                }
                file_count += 1
                files_with_no_section_urls += 1
                continue

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            file_result = {"official_domains": sorted(official_domains)}
            has_any_url = False

            for key, heading in TARGET_SECTIONS.items():
                section_text = extract_section_with_citations(content, heading)
                urls = extract_official_urls_from_inline(section_text, official_domains)
                file_result[key] = urls
                if urls:
                    has_any_url = True

            results[uni_dir][fname] = file_result
            file_count += 1

            if not has_any_url:
                files_with_no_section_urls += 1

            if file_count % 500 == 0:
                print(f"  Processed {file_count} files...", flush=True)

    print(f"  Total: {file_count} files processed.", flush=True)
    return results


def compute_stats(results: dict) -> dict:
    """Compute summary statistics for the extraction."""
    total_files = 0
    files_with_urls = 0
    per_section_counts = {key: 0 for key in TARGET_SECTIONS}
    per_section_url_totals = {key: 0 for key in TARGET_SECTIONS}
    all_urls = set()

    for college, files in results.items():
        for fname, fdata in files.items():
            total_files += 1
            has_any = False
            for key in TARGET_SECTIONS:
                urls = fdata.get(key, [])
                if urls:
                    has_any = True
                    per_section_counts[key] += 1
                    per_section_url_totals[key] += len(urls)
                    all_urls.update(urls)
            if has_any:
                files_with_urls += 1

    return {
        "total_files": total_files,
        "files_with_at_least_one_section_url": files_with_urls,
        "files_with_no_section_urls": total_files - files_with_urls,
        "unique_urls_across_all_sections": len(all_urls),
        "per_section": {
            heading: {
                "files_with_urls": per_section_counts[key],
                "total_url_references": per_section_url_totals[key],
            }
            for key, heading in TARGET_SECTIONS.items()
        },
    }


def main():
    course_data = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
    output_dir = course_data / "official_urls"

    configs = [
        {
            "name": "masters",
            "official_json": output_dir / "masters_official_urls.json",
            "md_base": course_data / "masters_data" / "masters",
            "output": output_dir / "masters_section_urls.json",
        },
        {
            "name": "phd",
            "official_json": output_dir / "phd_official_urls.json",
            "md_base": course_data / "phd_data" / "phd",
            "output": output_dir / "phd_section_urls.json",
        },
    ]

    combined_stats = {}

    for cfg in configs:
        print(f"\nProcessing {cfg['name']}...")
        results = process_files(
            str(cfg["official_json"]),
            str(cfg["md_base"]),
            cfg["name"],
        )

        # Save results
        with open(cfg["output"], "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"  Saved to {cfg['output']}")

        # Compute stats
        stats = compute_stats(results)
        combined_stats[cfg["name"]] = stats

        print(f"  Files: {stats['total_files']}")
        print(f"  With section URLs: {stats['files_with_at_least_one_section_url']}")
        print(f"  Without section URLs: {stats['files_with_no_section_urls']}")
        print(f"  Unique URLs: {stats['unique_urls_across_all_sections']}")
        for heading, sec_stats in stats["per_section"].items():
            print(f"    {heading}: {sec_stats['files_with_urls']} files, {sec_stats['total_url_references']} URL refs")

    # ── UG processing (inline Source: URLs from CSV) ──
    ug_csv = course_data / "ug_programs_data_2026-03-30T18_31_17.92687006+05_30.csv"
    ug_md_base = course_data / "ug_data_0k_tokens" / "ug"
    ug_output = output_dir / "ug_section_urls.json"

    if ug_csv.exists() and ug_md_base.exists():
        print(f"\nProcessing ug (from CSV + inline sources)...")
        ug_results = process_files_csv(str(ug_csv), str(ug_md_base))

        with open(ug_output, "w", encoding="utf-8") as f:
            json.dump(ug_results, f, indent=2, ensure_ascii=False)
        print(f"  Saved to {ug_output}")

        ug_stats = compute_stats(ug_results)
        combined_stats["ug"] = ug_stats

        print(f"  Files: {ug_stats['total_files']}")
        print(f"  With section URLs: {ug_stats['files_with_at_least_one_section_url']}")
        print(f"  Without section URLs: {ug_stats['files_with_no_section_urls']}")
        print(f"  Unique URLs: {ug_stats['unique_urls_across_all_sections']}")
        for heading, sec_stats in ug_stats["per_section"].items():
            print(f"    {heading}: {sec_stats['files_with_urls']} files, {sec_stats['total_url_references']} URL refs")

    # Compute combined unique URLs
    all_unique = set()
    for cfg in configs:
        with open(cfg["output"], encoding="utf-8") as f:
            data = json.load(f)
        for college, files in data.items():
            for fname, fdata in files.items():
                for key in TARGET_SECTIONS:
                    all_unique.update(fdata.get(key, []))

    print(f"\n{'='*50}")
    print(f"COMBINED SUMMARY")
    print(f"{'='*50}")
    total_files = sum(s["total_files"] for s in combined_stats.values())
    total_with = sum(s["files_with_at_least_one_section_url"] for s in combined_stats.values())
    print(f"Total files: {total_files}")
    print(f"Files with section URLs: {total_with}")
    print(f"Total unique URLs to crawl: {len(all_unique)}")


if __name__ == "__main__":
    main()
