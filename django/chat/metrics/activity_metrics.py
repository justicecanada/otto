from prometheus_client import Counter

chat_new_session_started_total = Counter(
    name="chat_session_started_total",
    documentation="number of chat sessions started by users",
    labelnames=["user", "mode"],
)


chat_session_restored_total = Counter(
    name="chat_session_restored_total",
    documentation="number of chat sessions restored by users",
    labelnames=["user", "mode"],
)


chat_request_type_total = Counter(
    name="chat_request_type_total",
    documentation="number of translation requests sent by users and type",
    labelnames=["user", "type"],
)
