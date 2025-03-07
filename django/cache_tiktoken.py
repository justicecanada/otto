import os

import tiktoken

# Set the cache directory
cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR", "/opt/tiktoken_cache")

# Ensure the cache directory exists
os.makedirs(cache_dir, exist_ok=True)

# Models to cache
models = ["gpt-4o-mini", "gpt-4o"]

# Cache the encodings
for model in models:
    tiktoken.encoding_for_model(model)
