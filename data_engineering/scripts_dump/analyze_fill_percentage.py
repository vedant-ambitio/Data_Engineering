import os
import json

raw_dir = r"Olympiad\olympiad_data\raw"
important_fields = [
    "activity_name",
    "organizer",
    "level",
    "subject",
    "registration_close_date",
    "modality",
    "cost_chip",
    "entry_route",
    "about_description",
    "eligibility_text",
    "structure_format",
    "how_to_apply",
    "rewards_outcomes",
    "official_website"
]

stats = {field: 0 for field in important_fields}
total_files = 0

if os.path.exists(raw_dir):
    for filename in os.listdir(raw_dir):
        if filename.endswith(".json"):
            total_files += 1
            file_path = os.path.join(raw_dir, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for field in important_fields:
                        val = data.get(field)
                        if val is not None and val != "" and val != []:
                            stats[field] += 1
            except Exception as e:
                print(f"Error reading {filename}: {e}")

if total_files > 0:
    print(f"Analysis of {total_files} files:")
    print("-" * 50)
    for field in important_fields:
        count = stats[field]
        percentage = (count / total_files) * 100
        print(f"{field:<25}: {percentage:>6.2f}% ({count}/{total_files})")
else:
    print("No files found to analyze.")
