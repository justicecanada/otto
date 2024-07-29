from prometheus_client import Counter

chat_negative_feedback_total = Counter(
    "chat_negative_feedback_total",
    "number of negative feedback submitted for a chat message",
    labelnames=["user", "message"],
)

chat_positive_feedback_total = Counter(
    "chat_positive_feedback_total",
    "number of positive feedback submitted for a chat message",
    labelnames=["user", "message"],
)
