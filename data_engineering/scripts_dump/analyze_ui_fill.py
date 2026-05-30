import json
import os
import glob

# UI Fields requested
UI_FIELDS = [
    "competition_id",
    "activity_name",
    "organizer",
    "organizer_logo",
    "mode",
    "deadline",
    "cost_chip",
    "cost",
    "domain",
    "team_size",
    "is_verified",
    "about_description",
    "eligibility_text",
    "how_to_apply",
    "prizes_detail",
    "prize_amount",
    "structure_format",
    "judging_criteria",
    "submission_format",
    "official_website",
    "registration_url",
    "source_url"
]

EXTRACTED_DIR = "Competitions/competition_data/extracted"

def main():
    files = glob.glob(os.path.join(EXTRACTED_DIR, "*.json"))
    total_files = len(files)
    
    if total_files == 0:
        print("No JSON files found.")
        return

    field_counts = {field: 0 for field in UI_FIELDS}

    for f in files:
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
                if not isinstance(data, dict): continue
                
                for field in UI_FIELDS:
                    val = data.get(field)
                    # Check if value is filled (not null, not empty string, not empty list)
                    if val is not None and val != "" and val != []:
                        field_counts[field] += 1
        except:
            continue

    print(f"\nFill Percentage Analysis ({total_files} files)")
    print("-" * 65)
    print(f"{'UI Field':<25} | {'Filled':<10} | {'Fill %':<10}")
    print("-" * 65)
    
    for field in UI_FIELDS:
        count = field_counts[field]
        percentage = (count / total_files) * 100
        print(f"{field:<25} | {count:<10} | {percentage:>6.1f}%")
    print("-" * 65)

if __name__ == "__main__":
    main()
