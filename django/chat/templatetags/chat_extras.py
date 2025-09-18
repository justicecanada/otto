import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


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
