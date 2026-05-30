import os
import re
import json
from pathlib import Path

def analyze_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    fields = {
        "Duration": r"\*\*Duration:\*\* (.*)",
        "Credits": r"\*\*Credits:\*\* (.*)",
        "GPA Requirements": r"### GPA Requirements\n(.*?)\n\n|\*\*GPA Requirements:\*\* (.*)",
        "TOEFL": r"\*\*TOEFL iBT:\*\* (.*)",
        "IELTS": r"\*\*IELTS Academic:\*\* (.*)",
        "GRE/GMAT": r"\*\*Standardized Tests\*\*:\n\* \*\*GRE:\*\* (.*)|\*\*Standardized Tests\*\*:\n\* (.*)|\*\*Standardized Tests:\*\* (.*)",
        "Application Fee": r"\*\*Application Fee:\*\* (.*)",
        "International Tuition": r"\* \*\*International Students:\*\* (.*)|\* \*\*Overseas Students\*\*:\s*(.*)",
        "Domestic Tuition": r"\* \*\*Domestic Students:\*\* (.*)|\* \*\*Home Students\*\*:\s*(.*)",
        "Fall Deadline": r"\* \*\*Fall \d{4} Entry\*\*:\s*(.*)",
        "Curriculum": r"## Curriculum\n(.*?)\n\n",
        "Work Experience": r"\*\*Work Experience\*\*:\n(.*?)\n\n|\*\*Work Experience:\*\* (.*)"
    }

    negative_indicators = [
        "information not available",
        "not explicitly mentioned",
        "not explicitly detailed",
        "not found",
        "not readily available",
        "not applicable",
        "information regarding .* was not found",
        "not specified"
    ]

    results = {}
    for field, pattern in fields.items():
        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            # Get the first non-None group
            val = next((g for g in match.groups() if g is not None), "").strip()
            
            # Check if value is a negative indicator
            is_empty = any(re.search(ind, val, re.IGNORECASE) for ind in negative_indicators)
            
            # Special case: "Not required" for tests/work exp is a VALID VALUE
            if field in ["GRE/GMAT", "Work Experience"] and "not required" in val.lower():
                is_empty = False
                
            results[field] = not is_empty
        else:
            results[field] = False
            
    return results

def main():
    base_dir = Path(r"C:\Users\HP\OneDrive\Desktop\course_data\masters_data\masters")
    all_stats = {}
    field_counts = {}
    
    count = 0
    max_files = 100 # Test with 100 files first
    
    print(f"Analyzing up to {max_files} files for field coverage...")
    
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                file_results = analyze_file(file_path)
                
                for field, has_value in file_results.items():
                    if field not in field_counts:
                        field_counts[field] = {"has_value": 0, "empty": 0}
                    if has_value:
                        field_counts[field]["has_value"] += 1
                    else:
                        field_counts[field]["empty"] += 1
                
                count += 1
                if count >= max_files:
                    break
        if count >= max_files:
            break

    print("\nCoverage Results (Sample of 100):")
    for field, counts in field_counts.items():
        total = counts["has_value"] + counts["empty"]
        pct = (counts["has_value"] / total) * 100 if total > 0 else 0
        print(f"{field:25}: {counts['has_value']:3} has value, {counts['empty']:3} empty ({pct:5.1f}%)")

if __name__ == "__main__":
    main()
