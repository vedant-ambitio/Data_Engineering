"""
Build region coverage JSON by comparing top university lists against our data.
Generates SEPARATE coverage for PhD, Masters, and UG programs.
Outputs university_coverage_by_region.json used by the dashboard.
"""

import json
import sys
import io
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")

# Load university folders separately for each program
def load_uni_folders(path):
    """Load university folder names from a data directory."""
    if not path.exists():
        return set()
    return set(f.name for f in path.iterdir() if f.is_dir() and "__MACOSX" not in f.name)

phd_unis = load_uni_folders(BASE / "phd_data" / "phd")
masters_unis = load_uni_folders(BASE / "masters_data" / "masters")
ug_unis = load_uni_folders(BASE / "ug_data_0k_tokens" / "ug")

print(f"PhD universities:     {len(phd_unis)}")
print(f"Masters universities: {len(masters_unis)}")
print(f"UG universities:      {len(ug_unis)}")


# Normalize name for matching
def normalize(name):
    return name.lower().replace("(", "").replace(")", "").replace(",", "").replace(".", "").replace("-", " ").replace("'", "").replace("\u2019", "").replace("the ", "").strip()

def find_match(uni_name, our_unis):
    """Try to find a matching university in our data."""
    norm = normalize(uni_name)

    for our in our_unis:
        our_norm = normalize(our.replace("_", " "))

        # Exact match
        if norm == our_norm:
            return our

        # Key words match
        words = [w for w in norm.split() if len(w) > 3 and w not in ("university", "of", "the", "and", "college", "institute", "technology")]
        if words and all(w in our_norm for w in words):
            return our

        # Partial match - main name
        if len(norm) > 10:
            key = norm.split("university")[0].strip() if "university" in norm else norm.split("institute")[0].strip() if "institute" in norm else norm.split()[0]
            if len(key) > 3 and key in our_norm:
                return our

    return None


# UK universities (from agent result)
uk_top = [
    {"rank": "2", "name": "Imperial College London"},
    {"rank": "3", "name": "University of Oxford"},
    {"rank": "5", "name": "University of Cambridge"},
    {"rank": "9", "name": "UCL (University College London)"},
    {"rank": "27", "name": "University of Edinburgh"},
    {"rank": "34", "name": "University of Manchester"},
    {"rank": "40", "name": "King's College London"},
    {"rank": "44", "name": "London School of Economics and Political Science (LSE)"},
    {"rank": "50", "name": "University of Bristol"},
    {"rank": "54", "name": "University of Warwick"},
    {"rank": "55", "name": "University of Leeds"},
    {"rank": "59", "name": "University of Glasgow"},
    {"rank": "65", "name": "Durham University"},
    {"rank": "67", "name": "University of Southampton"},
    {"rank": "69", "name": "University of Birmingham"},
    {"rank": "73", "name": "University of St Andrews"},
    {"rank": "78", "name": "University of Nottingham"},
    {"rank": "81", "name": "University of Sheffield"},
    {"rank": "82", "name": "Newcastle University"},
    {"rank": "92", "name": "Lancaster University"},
    {"rank": "105", "name": "University of Bath"},
    {"rank": "108", "name": "Queen Mary University of London"},
    {"rank": "120", "name": "University of Liverpool"},
    {"rank": "123", "name": "University of Exeter"},
    {"rank": "130", "name": "University of York"},
    {"rank": "148", "name": "University of Aberdeen"},
    {"rank": "154", "name": "Cardiff University"},
    {"rank": "160", "name": "University of Reading"},
    {"rank": "165", "name": "Queen's University Belfast"},
    {"rank": "168", "name": "Loughborough University"},
    {"rank": "174", "name": "Heriot-Watt University"},
    {"rank": "192", "name": "University of Sussex"},
    {"rank": "197", "name": "University of Leicester"},
    {"rank": "205", "name": "University of Surrey"},
    {"rank": "212", "name": "University of Dundee"},
    {"rank": "224", "name": "University of Strathclyde"},
    {"rank": "236", "name": "Royal Holloway, University of London"},
    {"rank": "244", "name": "SOAS University of London"},
    {"rank": "250", "name": "Brunel University London"},
    {"rank": "256", "name": "University of East Anglia (UEA)"},
    {"rank": "267", "name": "Swansea University"},
    {"rank": "272", "name": "Birkbeck, University of London"},
    {"rank": "282", "name": "City, University of London"},
    {"rank": "292", "name": "University of Essex"},
    {"rank": "298", "name": "University of Stirling"},
    {"rank": "305", "name": "Aston University"},
    {"rank": "311", "name": "Oxford Brookes University"},
    {"rank": "317", "name": "Goldsmiths, University of London"},
    {"rank": "328", "name": "University of Kent"},
    {"rank": "345", "name": "Bangor University"},
    {"rank": "355", "name": "Northumbria University"},
    {"rank": "364", "name": "University of Plymouth"},
    {"rank": "382", "name": "University of the West of England"},
    {"rank": "392", "name": "Aberystwyth University"},
    {"rank": "395", "name": "Coventry University"},
    {"rank": "407", "name": "University of Huddersfield"},
    {"rank": "421", "name": "University of Portsmouth"},
    {"rank": "436", "name": "Keele University"},
    {"rank": "446", "name": "University of Bradford"},
    {"rank": "452", "name": "Manchester Metropolitan University"},
    {"rank": "460", "name": "Nottingham Trent University"},
    {"rank": "465", "name": "University of Hull"},
    {"rank": "479", "name": "Ulster University"},
    {"rank": "485", "name": "Sheffield Hallam University"},
    {"rank": "498", "name": "Edinburgh Napier University"},
    {"rank": "511", "name": "University of Westminster"},
    {"rank": "521", "name": "University of Central Lancashire"},
    {"rank": "534", "name": "Liverpool John Moores University"},
    {"rank": "548", "name": "De Montfort University"},
    {"rank": "561", "name": "University of Greenwich"},
    {"rank": "571", "name": "University of Hertfordshire"},
    {"rank": "581", "name": "Kingston University"},
    {"rank": "601", "name": "Leeds Beckett University"},
    {"rank": "611", "name": "Bournemouth University"},
    {"rank": "621", "name": "University of Salford"},
    {"rank": "641", "name": "Birmingham City University"},
    {"rank": "651", "name": "University of Brighton"},
    {"rank": "661", "name": "Glasgow Caledonian University"},
    {"rank": "671", "name": "Robert Gordon University"},
    {"rank": "681", "name": "Middlesex University"},
]

# Load agent-created files
def load_py_list(filepath, varname=None):
    """Load a Python list from a .py file by executing it."""
    content = filepath.read_text(encoding="utf-8")
    namespace = {}
    try:
        exec(content, namespace)
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return []

    for k, v in namespace.items():
        if isinstance(v, list) and len(v) > 10:
            if varname and k != varname:
                continue
            return v
    return []


def process_region(name, top_list, our_unis):
    """Match top universities against a SPECIFIC program's university set."""
    have = []
    missing = []

    for uni in top_list:
        uni_name = uni["name"]
        match = find_match(uni_name, our_unis)
        entry = {"rank": uni.get("rank", ""), "name": uni_name}
        if match:
            have.append(entry)
        else:
            missing.append(entry)

    total = len(top_list)
    pct = round(len(have) / total * 100, 1) if total > 0 else 0

    return {
        "total_top_universities": total,
        "we_have": len(have),
        "we_dont_have": len(missing),
        "coverage_pct": pct,
        "universities_we_have": have,
        "universities_missing": missing,
    }


def build_regions_for_program(program_name, our_unis, region_lists):
    """Build region coverage for one program."""
    print(f"\n  {program_name} ({len(our_unis)} universities):")
    regions = {}
    for region_name, (short_name, top_list) in region_lists.items():
        result = process_region(short_name, top_list, our_unis)
        regions[region_name] = result
        print(f"    {short_name:<12}: {result['we_have']}/{result['total_top_universities']} ({result['coverage_pct']}%)")
    return regions


def main():
    # Load top university lists
    us_file = BASE / "top_150_us_universities_qs2026.py"
    europe_file = BASE / "top_150_continental_europe_universities.py"
    asia_file = BASE / "top_80_asian_universities_qs2025.py"
    ausnz_canada_file = BASE / "top_universities_aus_nz_canada.py"

    us_list = load_py_list(us_file) if us_file.exists() else []
    europe_list = load_py_list(europe_file) if europe_file.exists() else []
    asia_list = load_py_list(asia_file) if asia_file.exists() else []

    ausnz_list = []
    canada_list = []
    if ausnz_canada_file.exists():
        content = ausnz_canada_file.read_text(encoding="utf-8")
        namespace = {}
        try:
            exec(content, namespace)
        except Exception as e:
            print(f"Error loading ausnz_canada: {e}")
        for k, v in namespace.items():
            if isinstance(v, list):
                if "aus" in k.lower() or "nz" in k.lower():
                    ausnz_list = v
                elif "canada" in k.lower():
                    canada_list = v

    # Limit to target sizes
    us_list = us_list[:150]
    europe_list = europe_list[:150]
    asia_list = asia_list[:80]
    ausnz_list = ausnz_list[:80]
    canada_list = canada_list[:50]

    print(f"Top lists: US={len(us_list)} UK={len(uk_top)} Europe={len(europe_list)} Asia={len(asia_list)} AusNZ={len(ausnz_list)} Canada={len(canada_list)}")

    # Define region lists (shared across programs)
    region_lists = {
        "UNITED STATES": ("US", us_list),
        "EUROPE - UNITED KINGDOM": ("UK", uk_top),
        "EUROPE - CONTINENTAL": ("Europe", europe_list),
        "ASIA": ("Asia", asia_list),
        "AUSTRALIA & NEW ZEALAND": ("Aus/NZ", ausnz_list),
        "CANADA": ("Canada", canada_list),
    }

    # Build coverage separately for each program
    output = {
        "phd": build_regions_for_program("PhD", phd_unis, region_lists),
        "masters": build_regions_for_program("Masters", masters_unis, region_lists),
        "ug": build_regions_for_program("UG", ug_unis, region_lists),
    }

    # Save
    out = BASE / "university_coverage_by_region.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nSaved to {out}")

    # Summary
    for prog in ["phd", "masters", "ug"]:
        total_top = sum(r["total_top_universities"] for r in output[prog].values())
        total_have = sum(r["we_have"] for r in output[prog].values())
        print(f"  {prog.upper():>8}: {total_have}/{total_top} ({total_have/total_top*100:.1f}%)")


if __name__ == "__main__":
    main()
