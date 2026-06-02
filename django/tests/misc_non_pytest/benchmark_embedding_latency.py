#!/usr/bin/env python3
"""
Benchmark embedding latency at various concurrency levels against the real
Azure text-embedding-3-large deployment.

Measures p50/p95/p99 latency and effective TPM (tokens per minute) for
different concurrency levels, with configurable cooldown between tests
to avoid rate-limit bleed-over between levels.

Usage:
    cd django && python tests/misc_non_pytest/benchmark_embedding_latency.py

Requires AZURE_AI_SERVICES_ENDPOINT and AZURE_AI_SERVICES_KEY in the environment
(or a working Django .env with those values).

Results from 2026-05-05 (dev shared, S0 tier, 9.5M TPM quota set):
    Concurrency=  1:  p50=2.04s  1.26M TPM  (0 errors, 30 batches)
    Concurrency= 30:  p50=11.68s 6.16M TPM  (0 errors, 30 batches)
    Concurrency= 75:  p50=11.65s 15.32M TPM (0 errors, 30 batches)
    Concurrency= 50:  p50=26.82s 3.81M TPM  (293 errors, 500 batches sustained)

    Key insight: short bursts at concurrency=75 hit 15.3M TPM with zero errors,
    but sustained runs hit the S0 tier RPM limit. In PLT (higher tier, 9.5M TPM),
    concurrency=30-40 should comfortably saturate the quota. The 429-aware backoff
    in batch_embedding.py handles transient limits gracefully.

    See /workspace/docs/librarian_embedding_requeue_investigation.md for the full
    analysis and tuning recommendations.
"""

import concurrent.futures
import os
import statistics
import sys
import threading
import time

from openai import AzureOpenAI

# Ensure we can import Django settings
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")

import django

django.setup()

from django.conf import settings  # noqa


# ── Configuration ──────────────────────────────────────────────────────────
# Adjust these for your test scenario.

BATCH_SIZE = 50  # chunks per API call (matches EMBEDDING_BATCH_SIZE in settings)
CHUNK_TOKENS = 750  # approximate tokens per chunk (typical for legal docs)

# Quick scan: small batch count, multiple concurrency levels, cooldown between.
#   TOTAL_BATCHES=30, CONCURRENCY_LEVELS=[1, 5, 10, 20, 30, 50, 75], COOLDOWN=30
#
# Sustained test: large batch count, single concurrency level, no cooldown needed.
#   TOTAL_BATCHES=500, CONCURRENCY_LEVELS=[50], COOLDOWN=0
#
# Clean comparison: moderate batches, few levels, long cooldown to avoid bleed-over.
#   TOTAL_BATCHES=30, CONCURRENCY_LEVELS=[1, 30, 75], COOLDOWN=60

TOTAL_BATCHES = 30
WARMUP_BATCHES = 5
COOLDOWN_SECONDS = 30
CONCURRENCY_LEVELS = [1, 5, 10, 20, 30, 50, 75]

# ── Test input ─────────────────────────────────────────────────────────────

# Generate realistic chunk-sized text (~750 tokens ≈ ~3000 chars)
_CHUNK_TEXT = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua ut enim ad minim veniam "
    "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo "
    "consequat duis aute irure dolor in reprehenderit in voluptate velit esse "
    "cillum dolore eu fugiat nulla pariatur excepteur sint occaecat cupidatat "
    "non proident sunt in culpa qui officia deserunt mollit anim id est laborum "
) * 50  # ~3000 chars ≈ ~750 tokens

TEST_INPUTS = [_CHUNK_TEXT] * BATCH_SIZE


def build_client():
    return AzureOpenAI(
        azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
        api_key=settings.AZURE_AI_SERVICES_KEY,
        api_version="2024-08-01-preview",
    )


def run_single_batch(client):
    """Run one embedding batch and return elapsed seconds."""
    t0 = time.monotonic()
    client.embeddings.create(model="text-embedding-3-large", input=TEST_INPUTS)
    return time.monotonic() - t0


def run_concurrency_test(concurrency: int) -> dict:
    """
    Run TOTAL_BATCHES batches at the given concurrency level.
    Returns stats dict.
    """
    client = build_client()
    latencies = []
    errors = 0
    lock = threading.Lock()

    def worker():
        nonlocal errors
        try:
            elapsed = run_single_batch(client)
            with lock:
                latencies.append(elapsed)
        except Exception as e:
            with lock:
                errors += 1
            print(f"  [ERROR] {e}", file=sys.stderr)

    # Warmup
    for _ in range(WARMUP_BATCHES):
        try:
            run_single_batch(client)
        except Exception:
            pass

    # Measured runs
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker) for _ in range(TOTAL_BATCHES)]
        concurrent.futures.wait(futures)

    if not latencies:
        return {"concurrency": concurrency, "error": "all requests failed"}

    latencies.sort()
    n = len(latencies)
    p50 = latencies[int(n * 0.50)]
    p95 = latencies[int(n * 0.95)]
    p99 = latencies[int(n * 0.99)]
    avg = statistics.mean(latencies)

    # Effective TPM = (batches_per_second * batch_tokens * 60)
    # batches_per_second = concurrency / avg_latency (when saturated)
    # But more accurately: total_tokens / total_time
    total_tokens = n * BATCH_SIZE * CHUNK_TOKENS
    total_time = sum(latencies) / concurrency  # wall-clock time
    eff_tpm = total_tokens / (total_time / 60) if total_time > 0 else 0

    return {
        "concurrency": concurrency,
        "batches": n,
        "errors": errors,
        "p50": round(p50, 3),
        "p95": round(p95, 3),
        "p99": round(p99, 3),
        "avg": round(avg, 3),
        "min": round(latencies[0], 3),
        "max": round(latencies[-1], 3),
        "eff_tpm": int(eff_tpm),
    }


def main():
    print("Benchmarking text-embedding-3-large")
    print(f"  Endpoint: {settings.AZURE_AI_SERVICES_ENDPOINT}")
    print(f"  Batch size: {BATCH_SIZE} chunks")
    print(f"  Chunk tokens: ~{CHUNK_TOKENS}")
    print(f"  Batches per level: {TOTAL_BATCHES} (+ {WARMUP_BATCHES} warmup)")
    print()
    print(
        f"{'Concurrency':>12} | {'p50 (s)':>8} | {'p95 (s)':>8} | {'p99 (s)':>8} | {'avg (s)':>8} | {'min (s)':>8} | {'max (s)':>8} | {'Eff. TPM':>10} | {'Errors':>6}"
    )
    print("-" * 100)

    results = []
    for i, c in enumerate(CONCURRENCY_LEVELS):
        if i > 0:
            print(
                f"  Cooling down for {COOLDOWN_SECONDS}s to reset rate limits...",
                file=sys.stderr,
            )
            time.sleep(COOLDOWN_SECONDS)

        print(f"  Testing concurrency={c}...", end=" ", file=sys.stderr)
        sys.stderr.flush()
        r = run_concurrency_test(c)
        results.append(r)
        print(
            f"done (p50={r.get('p50', '?'):.3f}s, eff_tpm={r.get('eff_tpm', 0):,})",
            file=sys.stderr,
        )
        print(
            f"{r['concurrency']:>12} | {r.get('p50', '?'):>8} | {r.get('p95', '?'):>8} | {r.get('p99', '?'):>8} | {r.get('avg', '?'):>8} | {r.get('min', '?'):>8} | {r.get('max', '?'):>8} | {r.get('eff_tpm', 0):>10,} | {r.get('errors', 0):>6}"
        )

    print()
    print("─" * 60)
    print("Summary of effective TPM by concurrency:")
    for r in results:
        print(
            f"  concurrency={r['concurrency']:>3}  →  {r.get('eff_tpm', 0):>12,} TPM  (p50={r.get('p50', '?'):.3f}s)"
        )


if __name__ == "__main__":
    main()
