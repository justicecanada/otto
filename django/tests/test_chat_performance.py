"""
Test to measure chat view performance.
Run with: pytest django/tests/test_chat_performance.py -v -s
"""

import time

from django.db import connection, reset_queries
from django.test.utils import override_settings
from django.urls import reverse

import pytest

from chat.models import Chat, Message


@pytest.mark.django_db
@override_settings(DEBUG=True)  # Enable query logging
def test_chat_view_performance(client, all_apps_user):
    """Measure query count and time for the chat view."""
    user = all_apps_user()

    # Create a chat with messages (Chat.create() auto-creates options)
    chat = Chat.objects.create(
        user=user,
        title="Performance Test Chat",
    )

    # Add messages to main chat
    for i in range(20):
        Message.objects.create(chat=chat, text=f"Test message {i}", is_bot=(i % 2 == 1))

    # Create additional chats for the user to test sidebar performance (N+1 query detection)
    for i in range(30):
        other_chat = Chat.objects.create(
            user=user,
            title=f"Other chat {i}",
        )
        # Add varying number of messages to each chat
        for j in range(i % 5 + 1):
            Message.objects.create(
                chat=other_chat, text=f"Message {j} in chat {i}", is_bot=(j % 2 == 1)
            )

    # Note: Skipping additional document creation to keep test simple
    # The chat view performance is primarily affected by chat/message queries

    print(f"\n{'=' * 70}")
    print("Testing chat view performance")
    print(f"User has {Chat.objects.filter(user=user).count()} chats")
    print(f"Main chat has {Message.objects.filter(chat=chat).count()} messages")
    print(f"{'=' * 70}")

    # Run multiple times to get average
    num_runs = 10
    results = []

    for run in range(num_runs):
        # Reset query log and make request
        reset_queries()
        start_time = time.time()

        client.force_login(user)
        response = client.get(reverse("chat:chat", args=[chat.id]))

        elapsed = time.time() - start_time

        assert response.status_code == 200

        # Analyze queries
        query_count = len(connection.queries)
        total_query_time = sum(float(q["time"]) for q in connection.queries)

        results.append(
            {
                "elapsed": elapsed,
                "query_count": query_count,
                "total_query_time": total_query_time,
            }
        )

    # Calculate averages
    avg_elapsed = sum(r["elapsed"] for r in results) / num_runs
    avg_query_count = sum(r["query_count"] for r in results) / num_runs
    avg_total_query_time = sum(r["total_query_time"] for r in results) / num_runs

    print(f"\nPerformance Metrics (averaged over {num_runs} runs):")
    print(f"  Avg wall-clock time: {avg_elapsed:.3f}s")
    print(f"  Avg query count: {avg_query_count:.1f}")
    print(f"  Avg total query time: {avg_total_query_time:.3f}s")
    if avg_query_count > 0:
        print(f"  Avg time per query: {(avg_total_query_time / avg_query_count):.4f}s")

    # Show details from last run
    last_run = results[-1]
    print("\nLast run details:")
    print(f"  Wall-clock time: {last_run['elapsed']:.3f}s")
    print(f"  Query count: {last_run['query_count']}")
    print(f"  Total query time: {last_run['total_query_time']:.3f}s")

    # Show slowest queries from last run
    sorted_queries = sorted(
        connection.queries, key=lambda q: float(q["time"]), reverse=True
    )
    print("\nTop 10 slowest queries (last run):")
    for i, q in enumerate(sorted_queries[:10], 1):
        sql = q["sql"][:150] + "..." if len(q["sql"]) > 150 else q["sql"]
        print(f"  {i}. {float(q['time']):.4f}s - {sql}")

    # Check for N+1 patterns
    query_patterns = {}
    for q in connection.queries:
        # Extract table name from SQL
        sql_lower = q["sql"].lower()
        if 'from "' in sql_lower:
            table = (
                sql_lower.split('from "')[1].split('"')[0]
                if 'from "' in sql_lower
                else "unknown"
            )
            query_patterns[table] = query_patterns.get(table, 0) + 1

    print("\nQuery count by table:")
    for table, count in sorted(query_patterns.items(), key=lambda x: -x[1])[:15]:
        print(f"  {table}: {count}")

    print(f"\n{'=' * 70}\n")

    return {
        "avg_elapsed": avg_elapsed,
        "avg_query_count": avg_query_count,
        "avg_total_query_time": avg_total_query_time,
        "runs": results,
    }
