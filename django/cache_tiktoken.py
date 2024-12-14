import os
import sys

import tiktoken

print("Starting tiktoken caching process", file=sys.stderr)

# Set the cache directory
cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR", "/opt/tiktoken_cache")
print(f"Cache directory: {cache_dir}", file=sys.stderr)

# Ensure the cache directory exists
os.makedirs(cache_dir, exist_ok=True)

# Models to cache
models = ["gpt-4o-mini", "gpt-4o", "gpt-4-1106-preview", "gpt-35-turbo-0125"]

# Cache the encodings
for model in models:
    try:
        enc = tiktoken.encoding_for_model(model)
        print(f"Encoding for {model}: {enc}", file=sys.stderr)
    except Exception as e:
        print(f"Error caching encoding for {model}: {str(e)}", file=sys.stderr)

# List cache contents
print("Cache contents:", os.listdir(cache_dir), file=sys.stderr)

print("Tiktoken caching process completed", file=sys.stderr)
