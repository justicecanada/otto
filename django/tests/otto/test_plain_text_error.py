#!/usr/bin/env python
"""
Test script to compare plain text vs markdown error formatting.
Run from /workspace/django directory.
"""

import os
import sys

import django

from otto.utils.common import generate_ai_error_summary

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")
sys.path.insert(0, os.path.dirname(__file__))
django.setup()


def test_comparison():
    """Compare markdown vs plain text formatting."""

    # Create a test error
    try:
        1 / 0
    except Exception as e:
        print("=" * 60)
        print("MARKDOWN FORMAT (for chat/web display):")
        print("=" * 60)
        markdown_summary = generate_ai_error_summary(
            e, "test001", include_trace=False, plain_text=False
        )
        print(markdown_summary)
        print()

        print("=" * 60)
        print("PLAIN TEXT FORMAT (for text extractor):")
        print("=" * 60)
        plain_summary = generate_ai_error_summary(
            e, "test002", include_trace=False, plain_text=True
        )
        print(plain_summary)
        print()


if __name__ == "__main__":
    test_comparison()
