import os
import re
import json
from pathlib import Path

def analyze_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Patterns for fields
    # 1. Bolded labels: **Key:** Value
    bold_field_pattern = re.compile(r'\*\*(.*?):\*\* (.*)')
    
    # 2. Specific headers that act as fields (e.g., ### GPA Requirements)
    # We'll just look at the line after them if they match "Information not available"
    headers_to_check = ["GPA Requirements", "Curriculum", "Work Experience", "Educational Background and Prerequisites"]

    negative_indicators = [
        r"information not available",
        r"not explicitly mentioned",
        r"not explicitly detailed",
        r"not found",
        r"not readily available",
        r"not applicable",
        r"not specified",
        r"not explicitly stated",
        r"not yet officially published",
        r"not yet available",
        r"none explicitly mentioned"
    ]

    file_results = {}
    
    # Check bolded fields
    lines = content.split('\n')
    for line in lines:
        b_match = bold_field_pattern.search(line)
        if b_match:
            key = b_match.group(1).strip().replace('*', '').replace('#', '')
            val = b_match.group(2).strip()
            
            is_empty = any(re.search(ind, val, re.IGNORECASE) for ind in negative_indicators)
            
            # Exceptions
            if any(k in key.lower() for k in ["gre", "gmat", "work experience", "standardized tests"]):
                if "not required" in val.lower():
                    is_empty = False
            
            has_val = not is_empty
            if key in file_results:
                file_results[key] = file_results[key] or has_val
            else:
                file_results[key] = has_val
                
    # Check specific headers if they weren't caught as bolded fields
    for header in headers_to_check:
        header_pattern = re.compile(rf'##+ {header}\n(.*?)\n', re.IGNORECASE | re.DOTALL)
        h_match = header_pattern.search(content)
        if h_match:
            val = h_match.group(1).strip()
            is_empty = any(re.search(ind, val, re.IGNORECASE) for ind in negative_indicators)
            
            if header not in file_results or file_results[header] == False:
                file_results[header] = not is_empty

    return file_results

def run_analysis(ptype, folder_path):
    base_dir = Path(folder_path)
    global_counts = {}
    
    print(f"Analyzing {ptype} field coverage in {folder_path}...")
    file_list = list(base_dir.rglob("*.md"))
    total_files = len(file_list)
    
    processed = 0
    for file_path in file_list:
        if "__MACOSX" in str(file_path) or file_path.name.startswith("._"):
            continue
        try:
            results = analyze_file(file_path)
            for field, has_val in results.items():
                if field not in global_counts:
                    global_counts[field] = {"has_value": 0, "empty": 0}
                if has_val:
                    global_counts[field]["has_value"] += 1
                else:
                    global_counts[field]["empty"] += 1
        except:
            pass
        processed += 1
        if processed % 1000 == 0:
            print(f"  {processed}/{total_files}...")

    # Filter and sort
    min_occurrence = 50
    filtered = {f: c for f, c in global_counts.items() if (c["has_value"] + c["empty"]) >= min_occurrence}
    sorted_fields = dict(sorted(filtered.items(), key=lambda x: (x[1]["has_value"] + x[1]["empty"]), reverse=True))
    
    return sorted_fields

def main():
    BASE = r"C:\Users\HP\OneDrive\Desktop\course_data"
    
    # Masters
    m_data = run_analysis("Masters", os.path.join(BASE, "masters_data", "masters"))
    with open(os.path.join(BASE, "masters_field_coverage.json"), 'w', encoding='utf-8') as f:
        json.dump(m_data, f, indent=2)
        
    # PhD
    p_data = run_analysis("PhD", os.path.join(BASE, "phd_data", "phd"))
    with open(os.path.join(BASE, "phd_field_coverage.json"), 'w', encoding='utf-8') as f:
        json.dump(p_data, f, indent=2)

    print("\nAll analyses complete.")

if __name__ == "__main__":
    main()
