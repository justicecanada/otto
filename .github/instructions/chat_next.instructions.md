---
applyTo: "django/chat_next/**"
---
"chat_next" is a refactored, stripped-down version of "chat" app that does away with "modes" such as Summarize, Q&A, Chat.
Unlike "chat" app which uses LlamaIndex, "chat_next" uses the OpenAI Responses API directly along with Conversations API 
for tool-calling. For example, Library Q&A is accomplished through a tool call rather than a specific mode. (WIP)

Chat requests initially go to views.py "chat_message" which creates a user Message object
and bot response Message. The returned HTML fragment initiates HTMX SSE request to responses.py
"otto_response" which returns StreamingHttpResponse, which populates the bot response Message.
The generator for the stream may be partly defined in responses.py but ultimately "htmx_stream"
function returns the AsyncGenerator that yields the SSE events.
Q&A functions are supported by the "librarian" app.
Scripts could be in several HTML or static JS files, not just "scripts.js".
Frontend JS is intentionally split by responsibility: `scripts.js` holds shared chat runtime/markdown/scroll behavior, `reasoningWidget.js` owns processing steps plus approvals, and `filePreview.js` owns message file preview/navigation behavior.
