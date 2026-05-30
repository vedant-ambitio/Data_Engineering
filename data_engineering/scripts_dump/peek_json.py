import ijson
import json

def peek_all_results(file_path, num_items=10):
    count = 0
    print(f"Peeking first {num_items} items from {file_path}:\n")
    try:
        with open(file_path, 'rb') as f:
            # all_results.json seems to be a single large object { url: content, ... }
            parser = ijson.kvitems(f, '')
            for key, value in parser:
                print(f"URL: {key}")
                # Print first 200 chars of the content
                snippet = str(value).replace('\n', ' ')[:200]
                print(f"Content Snippet: {snippet}...")
                print("-" * 40)
                count += 1
                if count >= num_items:
                    break
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    peek_all_results('all_results.json')
