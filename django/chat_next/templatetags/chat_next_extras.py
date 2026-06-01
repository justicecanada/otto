import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


IN_PROGRESS_STATUSES = ["PENDING", "INIT", "PROCESSING", "TEXT_EXTRACTED"]


@register.filter(name="get_item")
def get_item(obj, key):
    """Return obj[key], or '' if the key is missing. Enables dict lookup in templates."""
    try:
        return obj[key]
    except (KeyError, TypeError):
        return ""


@register.filter(name="highlight")
def highlight(text, term):
    """
    Highlight occurrences of `term` inside `text` with <mark> wrapping.
    - Escapes the original text to avoid HTML injection
    - Case-insensitive match, preserves original casing in output
    - If term is empty/None, returns escaped text unchanged
    """
    if not text:
        return ""
    escaped = escape(text)
    if not term:
        return escaped
    try:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
    except re.error:
        # Fallback: if the term makes a bad regex, just return escaped text
        return escaped

    def repl(m):
        return f"<mark>{m.group(0)}</mark>"

    highlighted = pattern.sub(repl, escaped)
    return mark_safe(highlighted)


@register.filter(name="has_processing_files")
def has_processing_files(message):
    """
    Returns True if the message has any files with documents still processing.
    Used to determine if message_files.html should poll for updates.
    """
    if not hasattr(message, "files"):
        return False

    for f in message.files.all():
        # File without document yet - still being linked
        if not f.document:
            return True
        # Document still processing
        if f.document.status in IN_PROGRESS_STATUSES:
            return True

    return False
