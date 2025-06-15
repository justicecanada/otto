import os

import tiktoken

# Set the cache directory
cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR", "/opt/tiktoken_cache")

# Ensure the cache directory exists
os.makedirs(cache_dir, exist_ok=True)

# Models to cache
models = ["gpt-4o-mini", "gpt-4o", "o3-mini"]

# Cache the encodings
for model in models:
    tiktoken.encoding_for_model(model)

# All the models we use, including gpt-4.1 (currently not in tiktoken) use o200k_base
tiktoken.get_encoding("o200k_base")
# Embedding models use cl100k_baseAdd commentMore actions
tiktoken.get_encoding("cl100k_base")
