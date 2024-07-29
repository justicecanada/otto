from prometheus_client import Counter

librarian_access_total = Counter(
    name="librarian_access_total",
    documentation="number of times the librarian application page has been accessed by users",
    labelnames=["user"],
)


librarian_new_library_total = Counter(
    name="librarian_new_library_total",
    documentation="number of times a new library has been added to the librarian application",
    labelnames=["user"],
)


librarian_library_delete_total = Counter(
    name="librarian_library_delete_total",
    documentation="number of times a library has been removed from the librarian application",
    labelnames=["user"],
)


librarian_data_source_new_total = Counter(
    name="librarian_new_data_source_total",
    documentation="number of times a new data_source has been added to a library in the librarian application",
    labelnames=["user", "library"],
)


librarian_data_source_update_total = Counter(
    name="librarian_data_source_update_total",
    documentation="number of times data_sources have been updated in a library in the librarian application",
    labelnames=["user", "library"],
)

librarian_data_source_delete_total = Counter(
    name="librarian_data_source_delete_total",
    documentation="number of times data_sources have been removed from a library in the librarian application",
    labelnames=["user", "library"],
)

librarian_document_block_total = Counter(
    name="librarian_document_block_total",
    documentation="number of times a document has been blocked in the librarian application",
    labelnames=["user", "document"],
)


librarian_document_unblock_total = Counter(
    name="librarian_document_unblock_total",
    documentation="number of times a document has been unblocked in the librarian application",
    labelnames=["user", "document"],
)
