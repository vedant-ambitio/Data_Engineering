import os
import re
import json
from pathlib import Path
from collections import Counter

def analyze_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Patterns
    header_pattern = re.compile(r'^## (.*)')
    bold_field_pattern = re.compile(r'\*\*(.*?):\*\* (.*)')
    
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
        r"not yet available"
    ]

    file_results = {}
    current_section = None
    
    for line in lines:
        line = line.strip()
        
        # Check for Headers
        h_match = header_pattern.match(line)
        if h_match:
            current_section = h_match.group(1).strip()
            # We don't track coverage for headers themselves yet, 
            # but we can track if the section has any real content later if needed.
            continue
            
        # Check for Bolded Fields
        b_match = bold_field_pattern.search(line)
        if b_match:
            key = b_match.group(1).strip()
            val = b_match.group(2).strip()
            
            # Clean up the key if it has extra formatting
            key = re.sub(r'[\*\#]', '', key)
            
            # Identify if it's empty
            is_empty = any(re.search(ind, val, re.IGNORECASE) for ind in negative_indicators)
            
            # Special exceptions for status fields where "not required" is valid
            if any(k in key.lower() for k in ["gre", "gmat", "work experience", "standardized tests"]):
                if "not required" in val.lower():
                    is_empty = False
            
            # Store result (if key repeats in file, we take 'True' if any instance has a value)
            has_val = not is_empty
            if key in file_results:
                file_results[key] = file_results[key] or has_val
            else:
                file_results[key] = has_val

    return file_results

def main():
    base_dir = Path(r"C:\Users\HP\OneDrive\Desktop\course_data\masters_data\masters")
    global_counts = {} # {field_name: {"has_value": X, "empty": Y}}
    
    print("Starting full Master's field analysis...")
    
    file_list = list(base_dir.rglob("*.md"))
    total_files = len(file_list)
    print(f"Found {total_files} files.")

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
        except Exception as e:
            pass # Skip corrupted files
            
        processed += 1
        if processed % 500 == 0:
            print(f"Processed {processed}/{total_files} files...")

    # Post-process: Filter out noise (fields that appear very rarely)
    # This keeps the dashboard clean from OCR/Markdown artifacts
    min_occurrence = 50 
    filtered_results = {}
    for field, counts in global_counts.items():
        total = counts["has_value"] + counts["empty"]
        if total >= min_occurrence:
            filtered_results[field] = counts

    # Sort by total occurrence
    sorted_fields = dict(sorted(filtered_results.items(), key=lambda x: (x[1]["has_value"] + x[1]["empty"]), reverse=True))

    output_path = Path(r"C:\Users\HP\OneDrive\Desktop\course_data\masters_field_coverage.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(sorted_fields, f, indent=2)

    print(f"\nAnalysis complete. Results saved to {output_path}")
    print(f"Total unique fields tracked: {len(sorted_fields)}")

if __name__ == "__main__":
    main()
