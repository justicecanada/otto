#!/usr/bin/env python3
"""
Test script to compare performance of different retriever approaches.

Usage:
    cd /workspace/django && python test_retriever_performance.py
"""

import os
import sys
import time

import django

from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters

from chat.llm import OttoLLM

# Setup Django
sys.path.insert(0, "/workspace/django")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")
django.setup()


def test_retriever_performance():
    """Test different retriever approaches for performance."""

    # Test query
    test_query = "cannabis regulations and licensing requirements"

    # Common filters
    filters = MetadataFilters(
        filters=[
            MetadataFilter(key="lang", value="eng"),
            MetadataFilter(key="node_type", value="chunk"),
        ]
    )

    # Initialize LLM
    llm = OttoLLM(mock_embedding=True)  # Use mock embedding for consistent testing

    print("🧪 Testing Retriever Performance\n")

    iterations = 5  # Number of iterations for averaging

    # Test 2: Vector-only retriever
    print("\n2️⃣ Testing vector-only retriever...")
    total_time = 0

    for _ in range(iterations):
        start = time.time()
        try:
            vector_retriever = llm.get_fast_vector_retriever(
                vector_store_table="laws_lois__", filters=filters, top_k=10, hnsw=True
            )

            vector_retriever.retrieve(test_query)
            elapsed = time.time() - start
            total_time += elapsed
        except Exception as e:
            print(f"   ❌ Vector-only failed: {e}")
            return

    avg_time = total_time / iterations
    print(f"   ✅ Vector-only: {avg_time:.2f}s (average over {iterations} runs)")

    # Test 3: Text-only retriever
    print("\n3️⃣ Testing text-only retriever...")
    total_time = 0

    for _ in range(iterations):
        start = time.time()
        try:
            text_retriever = llm.get_fast_text_retriever(
                vector_store_table="laws_lois__", filters=filters, top_k=10
            )

            text_retriever.retrieve(test_query)
            elapsed = time.time() - start
            total_time += elapsed
        except Exception as e:
            print(f"   ❌ Text-only failed: {e}")
            return

    avg_time = total_time / iterations
    print(f"   ✅ Text-only: {avg_time:.2f}s (average over {iterations} runs)")

    # Test 4: Original hybrid retriever for comparison
    print("\n4️⃣ Testing original hybrid retriever...")
    total_time = 0

    for _ in range(iterations):
        start = time.time()
        try:
            original_retriever = llm.get_retriever(
                vector_store_table="laws_lois__",
                filters=filters,
                top_k=10,
                vector_weight=0.6,
                hnsw=True,
            )

            original_retriever.retrieve(test_query)
            elapsed = time.time() - start
            total_time += elapsed
        except Exception as e:
            print(f"   ❌ Original hybrid failed: {e}")
            return

    avg_time = total_time / iterations
    print(f"   ✅ Original hybrid: {avg_time:.2f}s (average over {iterations} runs)")

    print("\n🎯 Performance test complete!")


if __name__ == "__main__":
    test_retriever_performance()
