import os
import json

raw_dir = r"Olympiad\olympiad_data\raw"
output_file = r"Olympiad\official_olympiad_urls.txt"

unique_urls = set()

if os.path.exists(raw_dir):
    for filename in os.listdir(raw_dir):
        if filename.endswith(".json"):
            file_path = os.path.join(raw_dir, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    url = data.get("official_website")
                    if url:
                        unique_urls.add(url.strip())
            except Exception as e:
                print(f"Error reading {filename}: {e}")

with open(output_file, 'w', encoding='utf-8') as f:
    for url in sorted(list(unique_urls)):
        f.write(url + "\n")

print(f"Extracted {len(unique_urls)} unique URLs to {output_file}")
