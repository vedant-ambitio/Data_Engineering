"""
Stratified sample of 500 records from the 27K query_result for domain-extraction testing.

Buckets and target counts:
  big_global_brand        75
  university_college     100
  other_named_org        175
  generic_category       100
  indian_company          25
  indian_school           25
  TOTAL                  500

Output: test_500_stratified.json (same format as query_result)
"""

import json
import random
import re
from pathlib import Path
from collections import Counter

SEED = 42
SRC = Path(__file__).parent / "query_result_2026-05-06T14_11_02.778206369+05_30.json"
DST = Path(__file__).parent / "test_500_stratified.json"

TARGETS = {
    "big_global_brand": 75,
    "university_college": 100,
    "other_named_org": 175,
    "generic_category": 100,
    "indian_company": 25,
    "indian_school": 25,
}

GENERIC_KEYWORDS = re.compile(
    r"\b(sector|sectors|industries|industry|districts|boards|organi[sz]ations|"
    r"companies|firms|institutions|enterprises|associations|agencies|"
    r"departments|ministries|councils|committees|federations|unions)\b",
    re.I,
)
GENERIC_PREFIX = re.compile(
    r"^(local|various|other|all|major|leading|public|private|non[-\s]?profit|"
    r"governmental|community|small|medium|large|regional|national|international|"
    r"global|state|central|federal)\s",
    re.I,
)
GENERIC_STANDALONE = {
    "ngo", "ngos", "schools", "colleges", "universities", "hospitals",
    "startups", "businesses", "corporates", "msmes", "smes",
}

INDIAN_SCHOOL = re.compile(
    r"\b(delhi public school|dps\s|d\.p\.s\.|kendriya vidyalaya|k\.v\.|kv\s|"
    r"sainik school|navodaya|jawahar navodaya|jnv|dav\s|d\.a\.v\.|"
    r"ryan international|chinmaya|amrita vidyalayam|saraswati shishu mandir|"
    r"vidya mandir|vidya niketan|saraswati vidya|ramakrishna mission school|"
    r"st\.?\s?(?:xavier|joseph|mary|ann|patrick|paul|peter|stephen)'?s?\s+(?:school|high)|"
    r"don bosco|bishop cotton|la martiniere|la marteniere|cathedral|loyola|"
    r"modern school|springdales|sanskriti school|the heritage school|shri ram school|"
    r"tagore international|presidency school|deens academy|inventure academy|"
    r"oakridge|greenwood high|silver oaks|trio world|stonehill international)\b",
    re.I,
)

INDIAN_BIG_COMPANIES = {
    "tata", "infosys", "wipro", "reliance", "mahindra", "adani", "bajaj",
    "birla", "hcl", "larsen", "l&t", "maruti", "itc", "ongc", "bhel",
    "sbi", "lic", "hdfc", "icici", "kotak", "axis", "pnb", "canara",
    "godrej", "piramal", "emami", "dabur", "marico", "asian paints",
    "ultratech", "jsw", "jindal", "vedanta", "hindalco", "gail",
    "indian oil", "hpcl", "bpcl", "powergrid", "ntpc", "sail",
    "coal india", "cipla", "sun pharma", "lupin", "biocon", "dr reddy",
    "glenmark", "torrent pharma", "cadila", "aurobindo",
    "apollo hospitals", "fortis", "narayana health", "amul", "parle",
    "britannia", "haldiram", "mdh", "patanjali", "flipkart", "paytm",
    "ola", "oyo", "zomato", "swiggy", "phonepe", "byju", "unacademy",
    "urban company", "razorpay", "meesho", "cred", "bharti airtel",
    "jio", "reliance jio", "vodafone idea", "idea cellular", "tcs",
    "tech mahindra", "mphasis", "mindtree", "ltimindtree", "persistent",
    "zoho",
}

UNIVERSITY = re.compile(
    r"\b(university|universidad|università|université|universitat|"
    r"college|institute of technology|institutes of technology|"
    r"polytechnic|business school|law school|medical school|"
    r"school of (business|law|medicine|engineering|management|design|public health|"
    r"public policy|government|education|architecture|nursing|pharmacy|dentistry)|"
    r"academy of (sciences|arts|music|management)|"
    r"\biit\b|\biim\b|\bnit\b|\biiit\b|\biisc\b|\bisb\b|\bxlri\b|\bnls\b|\bnlu\b|"
    r"\bbits\b|\bvit\b|\bsrm\b|\bnmims\b|\bjnu\b|\bdu\b)\b",
    re.I,
)

BIG_GLOBAL_BRANDS = {
    "apple", "google", "microsoft", "amazon", "meta", "facebook", "tesla",
    "nvidia", "samsung", "sony", "lg", "panasonic", "toyota", "honda",
    "ford", "bmw", "mercedes", "mercedes-benz", "volkswagen", "audi",
    "volvo", "nestle", "unilever", "pepsi", "pepsico", "coca-cola",
    "coca cola", "jpmorgan", "jp morgan", "goldman sachs", "morgan stanley",
    "visa", "mastercard", "paypal", "stripe", "adobe", "oracle", "sap",
    "ibm", "intel", "amd", "qualcomm", "cisco", "hp", "hewlett packard",
    "dell", "lenovo", "huawei", "xiaomi", "spotify", "netflix", "disney",
    "warner bros", "hbo", "paramount", "walmart", "target", "costco",
    "ikea", "mcdonald", "mcdonald's", "starbucks", "kfc", "subway",
    "burger king", "nike", "adidas", "puma", "reebok", "levis", "levi's",
    "gap", "h&m", "zara", "uniqlo", "prada", "gucci", "louis vuitton",
    "lvmh", "hermes", "hermès", "chanel", "rolex", "omega", "tag heuer",
    "porsche", "ferrari", "lamborghini", "bugatti", "aston martin",
    "jaguar", "bentley", "rolls royce", "rolls-royce", "maserati",
    "tencent", "alibaba", "baidu", "jd.com", "meituan", "didi",
    "pearson", "mcgraw hill", "mcgraw-hill", "wiley", "elsevier",
    "hugo boss", "discover", "aarp", "salesforce", "workday", "shopify",
    "snowflake", "databricks", "palantir", "atlassian", "twilio",
    "square", "block", "airbnb", "uber", "lyft", "doordash", "instacart",
    "ebay", "etsy", "wayfair", "asos", "siemens", "bosch", "philips",
    "general electric", "ge", "general motors", "gm", "boeing", "airbus",
    "lockheed martin", "raytheon", "northrop grumman", "exxon", "exxonmobil",
    "shell", "bp", "chevron", "totalenergies", "saudi aramco", "aramco",
    "deloitte", "pwc", "ey", "ernst & young", "kpmg", "accenture",
    "mckinsey", "bcg", "bain", "capgemini", "infosys consulting",
    "berkshire hathaway", "blackrock", "vanguard", "fidelity", "schwab",
    "wells fargo", "citigroup", "citi", "bank of america", "hsbc",
    "barclays", "credit suisse", "ubs", "deutsche bank", "santander",
    "ing", "bnp paribas", "société générale", "rabobank", "mufg", "smbc",
    "nomura", "daiwa", "icbc", "abc bank", "hdfc bank", "axis bank",
    "kotak mahindra bank",
}


def normalize(name: str) -> str:
    return name.strip().lower()


def categorize(name: str) -> str:
    n = normalize(name)
    if not n:
        return "other_named_org"

    tokens = n.split()
    first = tokens[0] if tokens else ""

    # 1) Generic categories — pure descriptors, not real entities
    if n in GENERIC_STANDALONE:
        return "generic_category"
    if GENERIC_PREFIX.match(n):
        return "generic_category"
    if GENERIC_KEYWORDS.search(n) and len(tokens) <= 5:
        # Real org names rarely have these words AND are short
        # ("Pearson Industries" passes; "Tata Consultancy Services" doesn't because >5 toks? — needs care)
        # We only flag if NO proper noun signal (capitalized or known brand).
        # Heuristic: if the original (un-normalized) starts lowercase OR contains "the" early, lean generic.
        if name.strip()[0].islower() or n.startswith(("the ", "a ", "an ")):
            return "generic_category"
        # Generic-keyword + plural form like "School Boards", "Local School Districts"
        if any(t in {"districts", "boards", "sectors", "industries", "agencies",
                     "ministries", "councils", "committees"} for t in tokens):
            return "generic_category"

    # 2) Indian schools — specific name patterns
    if INDIAN_SCHOOL.search(n):
        return "indian_school"

    # 3) Indian big companies — known brand list
    if any(brand in n for brand in INDIAN_BIG_COMPANIES):
        # but avoid matching "tata" inside unrelated tokens — cheap check
        if first in INDIAN_BIG_COMPANIES or any(
            f" {b} " in f" {n} " or n.startswith(f"{b} ") or n.endswith(f" {b}")
            or n == b
            for b in INDIAN_BIG_COMPANIES
        ):
            return "indian_company"

    # 4) Universities / colleges
    if UNIVERSITY.search(n):
        return "university_college"

    # 5) Big global brands
    for b in BIG_GLOBAL_BRANDS:
        if first == b or n == b or n.startswith(f"{b} ") or n.endswith(f" {b}"):
            return "big_global_brand"
        if f" {b} " in f" {n} ":
            return "big_global_brand"

    # 6) Default
    return "other_named_org"


def main():
    random.seed(SEED)
    records = json.loads(SRC.read_text(encoding="utf-8"))
    print(f"Loaded {len(records):,} records")

    buckets: dict[str, list] = {k: [] for k in TARGETS}
    for r in records:
        buckets[categorize(r["name"])].append(r)

    print("\nBucket sizes in full dataset:")
    for k, v in buckets.items():
        pct = len(v) / len(records) * 100
        print(f"  {k:22s} {len(v):6,}  ({pct:5.1f}%)")

    # First pass: take what we can from each bucket; track shortfalls.
    sample = []
    chosen_ids: set[str] = set()
    spillover = 0
    for bucket, target in TARGETS.items():
        if bucket == "other_named_org":
            continue  # handled last so it absorbs any spillover
        pool = buckets[bucket]
        if len(pool) < target:
            shortfall = target - len(pool)
            print(f"\n  WARN: {bucket} has only {len(pool)} records, target was {target}. "
                  f"Taking all; shifting {shortfall} to other_named_org.")
            picked = pool
            spillover += shortfall
        else:
            picked = random.sample(pool, target)
        for r in picked:
            r = dict(r)
            r["_bucket"] = bucket
            sample.append(r)
            chosen_ids.add(r["id"])

    # Second pass: other_named_org gets its target + spillover.
    other_target = TARGETS["other_named_org"] + spillover
    other_pool = [r for r in buckets["other_named_org"] if r["id"] not in chosen_ids]
    if len(other_pool) < other_target:
        print(f"\n  WARN: other_named_org pool ({len(other_pool)}) < target ({other_target}). Taking all.")
        picked = other_pool
    else:
        picked = random.sample(other_pool, other_target)
    for r in picked:
        r = dict(r)
        r["_bucket"] = "other_named_org"
        sample.append(r)

    actual = Counter(r["_bucket"] for r in sample)
    print(f"\nSampled {len(sample)} records:")
    for bucket, target in TARGETS.items():
        print(f"  {bucket:22s} target={target:3d}  picked={actual[bucket]:3d}")

    out = [{"id": r["id"], "name": r["name"], "isActive": r["isActive"]} for r in sample]
    DST.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(out)} records to {DST}")

    print("\nFirst 3 of each bucket (sanity check):")
    seen = Counter()
    for r in sample:
        b = r["_bucket"]
        if seen[b] < 3:
            print(f"  [{b}] {r['name']!r}")
            seen[b] += 1


if __name__ == "__main__":
    main()
