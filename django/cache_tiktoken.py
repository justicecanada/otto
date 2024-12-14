import os

import tiktoken

# Set the cache directory
cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR", "/opt/tiktoken_cache")

# Ensure the cache directory exists
os.makedirs(cache_dir, exist_ok=True)

# Models to cache
models = ["gpt-4o-mini", "gpt-4o", "gpt-4-1106-preview", "gpt-35-turbo-0125"]

# Cache the encodings
for model in models:
    enc = tiktoken.encoding_for_model(model)
    print(f"Encoding for {model}: {enc}")

# List cache contents
print("Cache contents:", os.listdir(cache_dir))
