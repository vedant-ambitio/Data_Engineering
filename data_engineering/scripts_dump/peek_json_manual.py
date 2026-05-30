import json

def manual_peek(file_path, num_items=10):
    print(f"Peeking first {num_items} items from {file_path}:\n")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # Skip the opening brace
            char = f.read(1)
            while char and char != '{':
                char = f.read(1)
            
            for i in range(num_items):
                # This is a very crude parser for "key": "value" pairs where value is a string
                # Read key
                key_buf = []
                while True:
                    c = f.read(1)
                    if not c: break
                    if c == '"': break
                
                while True:
                    c = f.read(1)
                    if not c: break
                    if c == '"': break
                    key_buf.append(c)
                key = "".join(key_buf)
                
                # Skip to start of value
                while True:
                    c = f.read(1)
                    if not c: break
                    if c == '"': break
                
                # Read value with basic escape handling
                val_buf = []
                while True:
                    c = f.read(1)
                    if not c: break
                    if c == '\\':
                        next_c = f.read(1)
                        if next_c == '"':
                            val_buf.append('"')
                        else:
                            val_buf.append('\\')
                            val_buf.append(next_c)
                    elif c == '"':
                        break
                    else:
                        val_buf.append(c)
                
                value = "".join(val_buf)
                print(f"URL: {key}")
                snippet = value.replace('\n', ' ')[:200]
                print(f"Content Snippet: {snippet}...")
                print("-" * 40)
                
                # Skip to next pair (comma)
                while True:
                    c = f.read(1)
                    if not c or c == ',' or c == '}':
                        break
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    manual_peek('all_results.json')
