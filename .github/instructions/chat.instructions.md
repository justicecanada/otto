---
applyTo: "django/chat/**"
---
Chat requests initially go to views.py "chat_message" which creates a user Message object
and bot response Message. The returned HTML fragment initiates HTMX SSE request to responses.py
"otto_response" which returns StreamingHttpResponse, which populates the bot response Message.
The generator for the stream may be partly defined in responses.py but ultimately "htmx_stream"
function returns the AsyncGenerator that yields the SSE events.
Q&A functions are supported by the "librarian" app.
Scripts could be in several HTML or static JS files, not just "scripts.js".
