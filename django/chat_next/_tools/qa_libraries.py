import os

from django.db import models as django_models
from django.utils import timezone

from structlog import get_logger

from chat_next._llm.constants import (
    CODE_INTERPRETER_SUPPORTED_EXTENSIONS,
    is_code_interpreter_supported,
)
from chat_next._tools.base import TOOL_REGISTRY, OttoTool, ToolContext
from chat_next._tools.utils import (
    EST_CHARS_PER_TOKEN,
    TOKENS_PER_CHUNK,
    _calculate_cost_for_units,
    _get_model_id,
)

logger = get_logger(__name__)

DEFAULT_VECTOR_WEIGHT = 0.6
DEFAULT_QA_SEARCH_TOP_K = 5
MAX_QA_SEARCH_TOP_K = 200
MAX_GET_DOCUMENT_TEXT_DOCUMENTS = 50
TRANSIENT_OPENAI_FILE_CLEANUP_DELAY_SECONDS = 24 * 60 * 60
VISION_PDF_MAX_FILE_BYTES = 32 * 1024 * 1024
VISION_PDF_UPLOAD_HARD_MAX_FILE_BYTES = 32 * 1024 * 1024
VISION_PDF_TARGET_SLICE_BYTES = 8 * 1024 * 1024
VISION_PDF_MAX_PAGES_PER_SLICE = 10
VISION_PDF_MAX_AUTO_SLICES = 20


def _format_file_size_limit(limit_bytes: int) -> str:
    """Return a human-friendly file-size label including bytes."""
    limit_mib = limit_bytes / (1024 * 1024)
    if limit_mib.is_integer():
        size_label = f"{int(limit_mib)}MB"
    else:
        size_label = f"{limit_mib:.2f}MB"
    return f"{size_label} ({limit_bytes} bytes)"


def _is_upload_limit_error_for_bytes(exc: Exception, limit_bytes: int) -> bool:
    """Return True when an exception appears to indicate the configured upload cap."""
    message = str(exc or "").lower()
    if not message:
        return False

    if str(limit_bytes) in message:
        return True

    limit_mib = limit_bytes / (1024 * 1024)
    mb_tokens = {f"{limit_mib:g}mb", f"{limit_mib:g} mb"}
    if any(token in message for token in mb_tokens):
        return True

    return "upload limit" in message and any(
        token in message for token in ("mb", "bytes")
    )


def _parse_optional_page_number(value, field_name: str) -> int | None:
    """Parse an optional page-number tool argument."""
    if value is None:
        return None

    try:
        page_num = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a positive integer")

    if page_num < 1:
        raise ValueError(f"{field_name} must be a positive integer")

    return page_num


def _build_pdf_page_range_vision_item(
    saved_file,
    filename: str,
    start_page: int | None,
    end_page: int | None,
    openai_client=None,
) -> tuple[dict, int, int, int]:
    """Upload a sliced PDF page range to OpenAI Files API and return a file_id item.

    The original PDF is streamed from storage, the requested pages are written to a
    temporary local file, that file is uploaded to OpenAI, and an async Celery task
    is scheduled to delete the remote file after use.  The local temp file is always
    cleaned up immediately.

    An ``openai_client`` can be passed in to reuse an existing client; if *None* a
    new :class:`AzureOpenAI` client is created.
    """
    import tempfile

    from django.conf import settings

    from openai import AzureOpenAI
    from pypdf import PdfReader, PdfWriter

    from otto.utils.common import get_temp_dir

    from chat_next.tasks import delete_openai_file_async

    temp_path = None

    try:
        with saved_file.file.open("rb") as source_file:
            reader = PdfReader(source_file)
            total_pages = len(reader.pages)

            if total_pages < 1:
                raise ValueError("PDF has no pages")

            effective_start = start_page if start_page is not None else 1
            effective_end = (
                end_page
                if end_page is not None
                else (start_page if start_page is not None else total_pages)
            )

            if effective_start > total_pages:
                raise ValueError(
                    f"Requested start_page {effective_start} exceeds PDF length ({total_pages} pages)"
                )
            if effective_end > total_pages:
                raise ValueError(
                    f"Requested end_page {effective_end} exceeds PDF length ({total_pages} pages)"
                )
            if effective_start > effective_end:
                raise ValueError(
                    f"start_page ({effective_start}) cannot be after end_page ({effective_end})"
                )

            writer = PdfWriter()
            for page_index in range(effective_start - 1, effective_end):
                writer.add_page(reader.pages[page_index])

            temp_dir = get_temp_dir()
            with tempfile.NamedTemporaryFile(
                dir=temp_dir,
                suffix=".pdf",
                delete=False,
            ) as temp_file:
                temp_path = temp_file.name
                writer.write(temp_file)

        stem, ext = os.path.splitext(filename)
        derived_filename = (
            f"{stem}_pages_{effective_start}_{effective_end}{ext or '.pdf'}"
        )

        # Upload sliced PDF to OpenAI Files API
        if openai_client is None:
            api_version = settings.AZURE_AI_SERVICES_VERSION
            if not api_version or api_version in ("v1", "v1/"):
                api_version = "2025-03-01-preview"
            openai_client = AzureOpenAI(
                api_key=settings.AZURE_AI_SERVICES_KEY,
                azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
                api_version=api_version,
            )

        with open(temp_path, "rb") as sliced_pdf:
            sliced_size_bytes = os.path.getsize(temp_path)
            if sliced_size_bytes > VISION_PDF_UPLOAD_HARD_MAX_FILE_BYTES:
                raise ValueError(
                    "Requested page range"
                    f" {effective_start}-{effective_end} exceeds "
                    f"{_format_file_size_limit(VISION_PDF_UPLOAD_HARD_MAX_FILE_BYTES)} "
                    "upload limit "
                    f"({sliced_size_bytes} bytes); narrow the range."
                )

            response = openai_client.files.create(
                file=(derived_filename, sliced_pdf),
                purpose="assistants",
            )
        file_id = response.id

        logger.info(
            "Uploaded sliced PDF page range to OpenAI",
            filename=derived_filename,
            file_id=file_id,
            start_page=effective_start,
            end_page=effective_end,
        )

        # Schedule delayed async cleanup — deleting immediately can race the
        # very next Responses API call that needs to read this uploaded file.
        # A delayed fallback keeps the file available for same-turn tool
        # continuation and manual-approval resumes while still ensuring
        # eventual cleanup.
        try:
            delete_openai_file_async.apply_async(
                args=[file_id],
                countdown=TRANSIENT_OPENAI_FILE_CLEANUP_DELAY_SECONDS,
            )
        except Exception:
            logger.warning(
                "Failed to schedule cleanup for sliced PDF file",
                file_id=file_id,
            )

        return (
            {
                "type": "input_file",
                "file_id": file_id,
            },
            total_pages,
            effective_start,
            effective_end,
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                logger.warning("Failed to delete temporary sliced PDF", path=temp_path)


def _resolve_vector_weight(arguments: dict) -> tuple[float | None, str | None]:
    """Resolve optional vector_weight argument with validation.

    Accepts numeric values in the inclusive range [0, 1].
    Returns (value, None) on success or (None, error_message) on failure.
    """

    raw_value = arguments.get("vector_weight", DEFAULT_VECTOR_WEIGHT)
    try:
        vector_weight = float(raw_value)
    except (TypeError, ValueError):
        return None, "vector_weight must be a number between 0 and 1"

    if not 0 <= vector_weight <= 1:
        return None, "vector_weight must be between 0 and 1"

    return vector_weight, None


def _get_qa_search_top_k(arguments: dict) -> int:
    """Return a safe, bounded top_k for Q&A document search tools."""

    top_k = arguments.get("top_k", DEFAULT_QA_SEARCH_TOP_K)
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        return DEFAULT_QA_SEARCH_TOP_K
    return max(1, min(top_k, MAX_QA_SEARCH_TOP_K))


def _parse_positive_integer_argument(
    arguments: dict, field_name: str
) -> tuple[int | None, str | None]:
    """Parse a positive integer tool argument when present."""

    value = arguments.get(field_name)
    if value is None:
        return None, None

    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        return None, f"{field_name} must be a positive integer"

    if parsed_value <= 0:
        return None, f"{field_name} must be a positive integer"

    return parsed_value, None


def _parse_positive_integer_list_argument(
    arguments: dict,
    field_name: str,
) -> tuple[list[int] | None, str | None]:
    """Parse a list of positive integer tool arguments when present."""

    value = arguments.get(field_name)
    if value is None:
        return None, None

    raw_values = value if isinstance(value, list) else [value]
    parsed_values: list[int] = []
    for index, raw_value in enumerate(raw_values):
        try:
            parsed_value = int(raw_value)
        except (TypeError, ValueError):
            return None, f"{field_name}[{index}] must be a positive integer"

        if parsed_value <= 0:
            return None, f"{field_name}[{index}] must be a positive integer"

        parsed_values.append(parsed_value)

    if not parsed_values:
        return None, f"{field_name} must include at least one ID"

    deduped_values: list[int] = []
    for parsed_value in parsed_values:
        if parsed_value not in deduped_values:
            deduped_values.append(parsed_value)

    return deduped_values, None


def _parse_rag_search_scope_arguments(
    arguments: dict,
) -> tuple[dict[str, int | list[int]] | None, str | None]:
    """Validate that exactly one rag_search scope identifier was provided."""

    parsed_scope: dict[str, int | list[int]] = {}

    library_id, library_error = _parse_positive_integer_argument(
        arguments, "library_id"
    )
    if library_error:
        return None, library_error
    if library_id is not None:
        parsed_scope["library_id"] = library_id

    data_source_ids, data_source_error = _parse_positive_integer_list_argument(
        arguments,
        "data_source_ids",
    )
    if data_source_error:
        return None, data_source_error
    if data_source_ids is not None:
        parsed_scope["data_source_ids"] = data_source_ids

    document_ids, document_error = _parse_positive_integer_list_argument(
        arguments,
        "document_ids",
    )
    if document_error:
        return None, document_error
    if document_ids is not None:
        parsed_scope["document_ids"] = document_ids

    if len(parsed_scope) != 1:
        return None, (
            "Provide exactly one of library_id, data_source_ids, or document_ids"
        )

    return parsed_scope, None


def _can_access_libraries(user, chat=None) -> bool:
    """Check if user can access Q&A library tools."""
    # All authenticated users can access libraries they have permission for
    return user is not None and user.is_authenticated


def _user_can_view_library(user, library) -> bool:
    """Check if user can view a library, using centralized rules.

    Includes created_by fallback for cases where the creator may not have
    an explicit LibraryUserRole (e.g. programmatically created libraries).
    """
    from otto.rules import can_access_library_in_chat

    return library.created_by == user or can_access_library_in_chat(user, library)


def _user_can_view_data_source(user, data_source) -> bool:
    """Check if user can view a data source (folder), using centralized rules."""
    from otto.rules import can_access_data_source_in_chat

    return data_source.library.created_by == user or can_access_data_source_in_chat(
        user, data_source
    )


def _user_can_view_document(user, document) -> bool:
    """Check if user can view a document, using centralized rules."""
    from otto.rules import can_access_document_in_chat

    return (
        document.data_source.library.created_by == user
        or can_access_document_in_chat(user, document)
    )


def _touch_library_accessed_at_sync(library_id: int | None) -> None:
    """Update a library's accessed timestamp without loading full model state."""
    if not library_id:
        return

    from librarian.models import Library

    Library.objects.filter(pk=library_id).update(accessed_at=timezone.now())


def _touch_libraries_accessed_at_sync(library_ids) -> None:
    """Batch-update accessed_at for one or more libraries."""
    ids = sorted({library_id for library_id in (library_ids or []) if library_id})
    if not ids:
        return

    from librarian.models import Library

    Library.objects.filter(pk__in=ids).update(accessed_at=timezone.now())


def _count_paused_documents_in_scope(
    *,
    library_id: int | None = None,
    data_source_id: int | None = None,
    data_source_ids: list[int] | None = None,
    document_id: int | None = None,
    document_ids: list[int] | None = None,
) -> int:
    """Count non-container documents paused pending manual embedding."""
    from librarian.models import Document

    qs = Document.objects.filter(status="PAUSED", is_container=False)

    if document_ids is not None:
        qs = qs.filter(id__in=document_ids)
    elif document_id is not None:
        qs = qs.filter(id=document_id)
    elif data_source_ids is not None:
        qs = qs.filter(data_source_id__in=data_source_ids)
    elif data_source_id is not None:
        qs = qs.filter(data_source_id=data_source_id)
    elif library_id is not None:
        qs = qs.filter(data_source__library_id=library_id)

    return qs.count()


def _build_paused_embedding_warning(paused_count: int) -> str | None:
    """Explain that paused documents are readable directly but not searchable via RAG."""
    if paused_count <= 0:
        return None

    noun = "document is" if paused_count == 1 else "documents are"
    return (
        f"{paused_count} large {noun} paused pending manual embedding. "
        "Paused documents are readable with get_document_text and find_in_document, "
        "can be loaded with load_library_files, and can often be viewed with view_library_files, "
        "but they will NOT appear in semantic search results until embedded."
    )


def _maybe_add_paused_embedding_warning(response: dict, paused_count: int) -> dict:
    """Attach paused-embedding warning text when relevant."""
    warning = _build_paused_embedding_warning(paused_count)
    if warning:
        response["WARNING"] = warning
    return response


def _get_total_chunks_for_scope(arguments: dict) -> int | None:
    """Sum ``num_chunks`` across documents in scope (library / folder / document)."""
    from django.db.models import Sum

    from librarian.models import DataSource, Document, Library

    document_ids = arguments.get("document_ids")
    if not document_ids and arguments.get("document_id"):
        document_ids = [arguments.get("document_id")]

    data_source_ids = arguments.get("data_source_ids")
    if not data_source_ids and arguments.get("data_source_id"):
        data_source_ids = [arguments.get("data_source_id")]

    library_id = arguments.get("library_id")

    if document_ids:
        normalized_document_ids = (
            document_ids if isinstance(document_ids, list) else [document_ids]
        )
        if Document.objects.filter(id__in=normalized_document_ids).count() != len(
            set(normalized_document_ids)
        ):
            return None
        return (
            Document.objects.filter(id__in=normalized_document_ids).aggregate(
                total=Sum("num_chunks")
            )["total"]
            or 0
        )
    elif data_source_ids:
        normalized_data_source_ids = (
            data_source_ids if isinstance(data_source_ids, list) else [data_source_ids]
        )
        if DataSource.objects.filter(id__in=normalized_data_source_ids).count() != len(
            set(normalized_data_source_ids)
        ):
            return None
        return (
            Document.objects.filter(
                data_source_id__in=normalized_data_source_ids,
                is_container=False,
            ).aggregate(total=Sum("num_chunks"))["total"]
            or 0
        )
    elif library_id:
        try:
            Library.objects.get(id=library_id)
        except Library.DoesNotExist:
            return None
        return (
            Document.objects.filter(
                data_source__library_id=library_id, is_container=False
            ).aggregate(total=Sum("num_chunks"))["total"]
            or 0
        )

    return None


def _format_missing_scope_ids(label: str, missing_ids: list[int]) -> str:
    """Format a consistent not-found error for one or more scope IDs."""

    if len(missing_ids) == 1:
        singular = label[:-1] if label.endswith("s") else label
        return f"{singular.capitalize()} with id {missing_ids[0]} not found"

    joined_ids = ", ".join(str(value) for value in missing_ids)
    return f"{label.capitalize()} with ids {joined_ids} not found"


def _resolve_rag_search_scope_sync(
    user,
    scope: dict[str, int | list[int]],
) -> tuple[dict | None, str | None]:
    """Resolve and authorize the target scope for rag_search."""

    from librarian.models import DataSource, Document, Library

    if "library_id" in scope:
        library_id = int(scope["library_id"])
        try:
            library = Library.objects.get(id=library_id)
        except Library.DoesNotExist:
            return None, f"Library with id {library_id} not found"

        if not _user_can_view_library(user, library):
            return None, "You don't have permission to access this library"

        return {
            "scope_type": "library",
            "libraries": [library],
            "library": library,
            "data_sources": [],
            "data_source": None,
            "documents": [],
            "document": None,
            "paused_kwargs": {"library_id": library.id},
        }, None

    if "data_source_ids" in scope:
        requested_ids = list(scope["data_source_ids"])
        data_sources = list(
            DataSource.objects.select_related("library").filter(id__in=requested_ids)
        )
        data_sources_by_id = {
            data_source.id: data_source for data_source in data_sources
        }
        missing_ids = [
            value for value in requested_ids if value not in data_sources_by_id
        ]
        if missing_ids:
            return None, _format_missing_scope_ids("folders", missing_ids)

        ordered_data_sources = [data_sources_by_id[value] for value in requested_ids]
        for data_source in ordered_data_sources:
            if not _user_can_view_data_source(user, data_source):
                return None, "You don't have permission to access one or more folders"

        libraries = []
        seen_library_ids = set()
        for data_source in ordered_data_sources:
            if data_source.library_id not in seen_library_ids:
                libraries.append(data_source.library)
                seen_library_ids.add(data_source.library_id)

        return {
            "scope_type": "folder",
            "libraries": libraries,
            "library": libraries[0] if len(libraries) == 1 else None,
            "data_sources": ordered_data_sources,
            "data_source": ordered_data_sources[0]
            if len(ordered_data_sources) == 1
            else None,
            "documents": [],
            "document": None,
            "paused_kwargs": {"data_source_ids": requested_ids},
        }, None

    requested_ids = list(scope["document_ids"])
    documents = list(
        Document.objects.select_related("data_source", "data_source__library").filter(
            id__in=requested_ids
        )
    )
    documents_by_id = {document.id: document for document in documents}
    missing_ids = [value for value in requested_ids if value not in documents_by_id]
    if missing_ids:
        return None, _format_missing_scope_ids("documents", missing_ids)

    ordered_documents = [documents_by_id[value] for value in requested_ids]
    for document in ordered_documents:
        if not document.data_source or not document.data_source.library:
            return None, "One or more documents are not part of a library"
        if not _user_can_view_document(user, document):
            return None, "You don't have permission to access one or more documents"

    libraries = []
    seen_library_ids = set()
    for document in ordered_documents:
        library = document.data_source.library
        if library.id not in seen_library_ids:
            libraries.append(library)
            seen_library_ids.add(library.id)

    data_sources = []
    seen_data_source_ids = set()
    for document in ordered_documents:
        data_source = document.data_source
        if data_source.id not in seen_data_source_ids:
            data_sources.append(data_source)
            seen_data_source_ids.add(data_source.id)

    return {
        "scope_type": "document",
        "libraries": libraries,
        "library": libraries[0] if len(libraries) == 1 else None,
        "data_sources": data_sources,
        "data_source": data_sources[0] if len(data_sources) == 1 else None,
        "documents": ordered_documents,
        "document": ordered_documents[0] if len(ordered_documents) == 1 else None,
        "paused_kwargs": {"document_ids": requested_ids},
    }, None


def _build_chunk_only_filters(*extra_filters):
    """Return retriever filters that exclude document-level nodes."""

    from llama_index.core.vector_stores import MetadataFilter, MetadataFilters

    return MetadataFilters(
        filters=[
            MetadataFilter(
                key="node_type",
                value="document",
                operator="!=",
            ),
            *extra_filters,
        ]
    )


def _run_rag_retrieval_sync(
    *,
    library,
    query: str,
    top_k: int,
    vector_weight: float,
    filters,
    scope_type: str,
    document=None,
    log_context: dict | None = None,
):
    """Execute scoped RAG retrieval and format consistent results."""

    from chat._llm import OttoLLM

    llm = OttoLLM()
    vector_store_table = library.uuid_hex
    use_hnsw = library.use_hnsw_for_query()

    try:
        retriever = llm.get_retriever(
            vector_store_table,
            filters,
            top_k,
            vector_weight=vector_weight,
            hnsw=use_hnsw,
        )
        source_nodes = retriever.retrieve(query)
    except Exception as e:
        logger.exception(
            "RAG search failed",
            scope_type=scope_type,
            library_id=library.id,
            data_source_id=(log_context or {}).get("data_source_id")
            or getattr(getattr(document, "data_source", None), "id", None),
            data_source_ids=(log_context or {}).get("data_source_ids"),
            document_id=(log_context or {}).get("document_id")
            or getattr(document, "id", None),
            document_ids=(log_context or {}).get("document_ids"),
            error=str(e),
        )
        return None, f"Search failed: {str(e)}"

    if not source_nodes:
        return [], None

    results = []
    extracted_text = document.extracted_text or "" if document else ""
    page_boundaries = _compute_page_boundaries(extracted_text) if extracted_text else []
    default_document_title = (
        document.title or document.filename or "Untitled" if document else None
    )

    for node in source_nodes:
        metadata = node.metadata or {}
        document_title = (
            metadata.get("manual_title")
            or metadata.get("extracted_title")
            or metadata.get("filename")
            or default_document_title
            or "Untitled"
        )
        document_id = metadata.get("doc_id") or getattr(document, "id", None)
        start_char = metadata.get("start_char")
        page_number = metadata.get("start_page")

        if document and start_char is None and extracted_text:
            pos = extracted_text.find(node.text)
            if pos >= 0:
                start_char = pos
                if page_boundaries and page_number is None:
                    page_number = _get_page_for_offset(page_boundaries, pos)

        result = {
            "text": node.text,
            "score": round(node.score, 3) if node.score else None,
            "document_id": document_id,
            "document_title": document_title,
            "start_char": start_char,
            "page_number": page_number,
        }

        chunk_number = metadata.get("chunk_number")
        if chunk_number is not None:
            result["chunk_number"] = chunk_number

        results.append(result)

    return results, None


def _score_rag_result(result: dict) -> tuple[int, float]:
    """Return a stable sort key for merged rag_search results."""

    score = result.get("score")
    return (1 if score is not None else 0, float(score or 0))


def _merge_rag_results(results: list[dict], top_k: int) -> list[dict]:
    """Merge and deduplicate rag results from one or more retrieval groups."""

    deduped_results: list[dict] = []
    seen_keys = set()

    for result in sorted(results, key=_score_rag_result, reverse=True):
        dedupe_key = (
            result.get("document_id"),
            result.get("start_char"),
            result.get("page_number"),
            result.get("text"),
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        deduped_results.append(result)
        if len(deduped_results) >= top_k:
            break

    return deduped_results


def estimate_qa_search_cost(arguments: dict, chat=None) -> str | None:
    """Estimate cost for a Q&A library search (RAG retrieval).

    Accounts for retrieved chunks fed back to the model as input tokens.
    Uses ``top_k * 768 tokens/chunk`` following the old chat app formula
    from ``_estimate_qa_documents_cost``, capped at the total chunks
    actually available in the library/folder/document scope.

    Returns a formatted cost string (e.g. "0.05") or None when cost is zero.
    """
    from otto.utils.common import cad_cost

    model_id = _get_model_id(chat)
    if not model_id:
        return None

    query = arguments.get("query", "")
    top_k = _get_qa_search_top_k(arguments)
    if not query:
        return None

    chunk_count = top_k

    # Cap at actual available chunks
    total_chunks = _get_total_chunks_for_scope(arguments)
    if total_chunks is not None:
        chunk_count = min(chunk_count, total_chunks)

    chunk_tokens = TOKENS_PER_CHUNK * chunk_count
    total_cost = _calculate_cost_for_units(f"{model_id}-in", chunk_tokens)

    if total_cost == 0:
        return None

    cad_total = cad_cost(total_cost)
    return f"{cad_total:.4f}" if cad_total < 1 else f"{cad_total:.2f}"


def estimate_get_document_text_cost(arguments: dict, chat=None) -> str | None:
    """Estimate cost for reading document text (full-document mode).

    When the model calls ``get_document_text``, the returned text becomes
    input tokens in the next API round-trip.  This estimates that cost based
    on the document's ``extracted_text`` length (or ``num_chunks`` fallback),
    capped by ``MAX_DOCUMENT_CHARS`` and any requested character/page range.

    Returns a formatted cost string (e.g. "0.12") or None when cost is zero.
    """
    from decimal import Decimal

    from otto.utils.common import cad_cost

    from librarian.models import Document

    model_id = _get_model_id(chat)
    if not model_id:
        return None

    document_id = arguments.get("document_id")
    document_ids = arguments.get("document_ids")
    if document_id not in (None, "") and document_ids:
        return None

    if document_ids is not None:
        if not isinstance(document_ids, list) or not document_ids:
            return None
        normalized_document_ids = [
            doc_id for doc_id in document_ids if isinstance(doc_id, int) and doc_id > 0
        ]
        if len(normalized_document_ids) != len(document_ids):
            return None
    elif isinstance(document_id, int) and document_id > 0:
        normalized_document_ids = [document_id]
    else:
        return None

    start_char = arguments.get("start_char", 0) or 0
    end_char = arguments.get("end_char")
    start_page = arguments.get("start_page")
    end_page = arguments.get("end_page")

    total_cost = Decimal("0")

    for current_document_id in normalized_document_ids:
        try:
            doc = Document.objects.get(id=current_document_id)
        except Document.DoesNotExist:
            continue

        if doc.extracted_text:
            total_chars = len(doc.extracted_text)
        elif doc.num_chunks:
            total_chars = doc.num_chunks * TOKENS_PER_CHUNK * EST_CHARS_PER_TOKEN
        else:
            continue

        if start_page is not None or end_page is not None:
            page_count = (
                _get_page_count_from_text(doc.extracted_text or "")
                if doc.extracted_text
                else None
            )
            if page_count and page_count > 0:
                chars_per_page = total_chars / page_count
                sp = start_page if start_page is not None else 1
                ep = end_page if end_page is not None else page_count
                estimated_chars = int((ep - sp + 1) * chars_per_page)
            else:
                estimated_chars = total_chars
        elif end_char is not None and end_char != -1:
            estimated_chars = max(0, end_char - start_char)
        else:
            estimated_chars = max(0, total_chars - start_char)

        estimated_chars = min(estimated_chars, MAX_DOCUMENT_CHARS)
        if estimated_chars == 0:
            continue

        token_count = estimated_chars // EST_CHARS_PER_TOKEN
        total_cost += _calculate_cost_for_units(f"{model_id}-in", token_count)

    if total_cost == 0:
        return None

    return f"{cad_cost(total_cost):.2f}"


async def list_libraries(arguments: dict, context: ToolContext) -> list[dict]:
    """
    List Q&A document libraries accessible to the user.

    Returns library info so the AI knows what's available for searching.
    """
    from asgiref.sync import sync_to_async

    from librarian.models import Library

    user = context.user

    @sync_to_async
    def get_accessible_libraries():
        libraries = []

        from otto.rules import (
            GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN,
            get_skill_referenced_library_ids,
        )

        skill_library_ids = get_skill_referenced_library_ids(user)

        candidate_libraries = (
            Library.objects.filter(
                django_models.Q(is_public=True)
                | django_models.Q(created_by=user)
                | django_models.Q(user_roles__user=user)
                | django_models.Q(team_roles__team__memberships__user=user)
                | django_models.Q(id__in=skill_library_ids)
                | django_models.Q(name_en=GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN)
            )
            .distinct()
            .order_by("-is_personal_library", "-is_public", "order", "-created_at")
        )

        for lib in candidate_libraries:
            if not _user_can_view_library(user, lib):
                continue

            doc_count = (
                lib.data_sources.aggregate(
                    total=django_models.Count(
                        "documents",
                        filter=django_models.Q(documents__is_container=False),
                    )
                )["total"]
                or 0
            )

            if doc_count == 0 and not lib.is_personal_library:
                continue

            libraries.append(
                {
                    "id": lib.id,
                    "name": str(lib),
                    "description": lib.description or "",
                    "document_count": doc_count,
                    "is_personal": lib.is_personal_library,
                    "is_default_library": lib.is_default_library,
                }
            )

            if len(libraries) >= 20:
                break

        return libraries

    return await get_accessible_libraries()


async def rag_search(arguments: dict, context: ToolContext) -> dict:
    """Search one library, folder, or document for relevant embedded chunks."""
    from asgiref.sync import sync_to_async
    from llama_index.core.vector_stores import MetadataFilter

    scope, scope_error = _parse_rag_search_scope_arguments(arguments)
    query = arguments.get("query", "")

    top_k = _get_qa_search_top_k(arguments)
    vector_weight, vector_weight_error = _resolve_vector_weight(arguments)

    if scope_error:
        return {"error": scope_error}
    if not query:
        return {"error": "query is required"}
    if vector_weight_error:
        return {"error": vector_weight_error}

    resolved_scope, error = await sync_to_async(_resolve_rag_search_scope_sync)(
        context.user,
        scope,
    )
    if error:
        return {"error": error}

    scope_type = resolved_scope["scope_type"]
    libraries = resolved_scope["libraries"]
    library = resolved_scope["library"]
    data_sources = resolved_scope["data_sources"]
    data_source = resolved_scope["data_source"]
    documents = resolved_scope["documents"]
    document = resolved_scope["document"]

    await sync_to_async(_touch_libraries_accessed_at_sync)(
        [current_library.id for current_library in libraries]
    )

    if document and document.status == "PAUSED":
        return _maybe_add_paused_embedding_warning(
            {
                "scope_type": "document",
                "results": [],
                "document_title": document.title or document.filename or "Untitled",
                "document_id": document.id,
                "query": query,
                "message": (
                    "This document is paused pending manual embedding, so semantic search "
                    "has no indexed chunks to search yet. Use get_document_text, "
                    "find_in_document, load_library_files, or view_library_files instead."
                ),
            },
            1,
        )

    paused_count = await sync_to_async(_count_paused_documents_in_scope)(
        **resolved_scope["paused_kwargs"]
    )

    retrieval_groups = []
    if scope_type == "library":
        retrieval_groups.append(
            {
                "library": library,
                "filters": _build_chunk_only_filters(),
                "document": None,
                "log_context": {"library_id": library.id},
            }
        )
    elif scope_type == "folder":
        for current_library in libraries:
            current_data_sources = [
                current_data_source
                for current_data_source in data_sources
                if current_data_source.library_id == current_library.id
            ]
            retrieval_groups.append(
                {
                    "library": current_library,
                    "filters": _build_chunk_only_filters(
                        MetadataFilter(
                            key="data_source_uuid",
                            value=[
                                current_data_source.uuid_hex
                                for current_data_source in current_data_sources
                            ],
                            operator="in",
                        )
                    ),
                    "document": None,
                    "log_context": {
                        "data_source_id": current_data_sources[0].id
                        if len(current_data_sources) == 1
                        else None,
                        "data_source_ids": [
                            current_data_source.id
                            for current_data_source in current_data_sources
                        ],
                    },
                }
            )
    else:
        for current_library in libraries:
            current_documents = [
                current_document
                for current_document in documents
                if current_document.data_source.library_id == current_library.id
            ]
            retrieval_groups.append(
                {
                    "library": current_library,
                    "filters": _build_chunk_only_filters(
                        MetadataFilter(
                            key="doc_id",
                            value=[
                                current_document.id
                                for current_document in current_documents
                            ],
                            operator="in",
                        )
                    ),
                    "document": current_documents[0]
                    if len(current_documents) == 1
                    else None,
                    "log_context": {
                        "document_id": current_documents[0].id
                        if len(current_documents) == 1
                        else None,
                        "document_ids": [
                            current_document.id
                            for current_document in current_documents
                        ],
                    },
                }
            )

    all_results: list[dict] = []
    for retrieval_group in retrieval_groups:
        group_results, error = await sync_to_async(_run_rag_retrieval_sync)(
            library=retrieval_group["library"],
            query=query,
            top_k=top_k,
            vector_weight=vector_weight,
            filters=retrieval_group["filters"],
            scope_type=scope_type,
            document=retrieval_group["document"],
            log_context=retrieval_group["log_context"],
        )
        if error:
            return {"error": error}
        all_results.extend(group_results)

    results = _merge_rag_results(all_results, top_k)
    library_names = [str(current_library) for current_library in libraries]
    folder_names = [current_data_source.name for current_data_source in data_sources]
    document_titles = [
        current_document.title or current_document.filename or "Untitled"
        for current_document in documents
    ]

    if not results:
        empty_messages = {
            "library": "No relevant content found in this library for your query.",
            "folder": "No relevant content found in this folder for your query.",
            "document": "No relevant content found in this document for your query.",
        }
        response = {
            "scope_type": scope_type,
            "results": [],
            "query": query,
            "message": empty_messages[scope_type],
        }
        if len(library_names) == 1:
            response["library_name"] = library_names[0]
        elif library_names:
            response["library_names"] = library_names
        if data_source is not None:
            response["folder_name"] = data_source.name
        elif folder_names:
            response["folder_names"] = folder_names
        if document is not None:
            response["document_title"] = (
                document.title or document.filename or "Untitled"
            )
            response["document_id"] = document.id
        elif document_titles:
            response["document_titles"] = document_titles
            response["document_ids"] = [
                current_document.id for current_document in documents
            ]

        return _maybe_add_paused_embedding_warning(response, paused_count)

    response = {
        "scope_type": scope_type,
        "results": results,
        "query": query,
    }

    if scope_type == "library":
        response["library_name"] = library_names[0]
        response["TIP"] = (
            "To read more context around a result: use get_document_text with "
            "document_id and start_char or start_page from the result above."
        )
    elif scope_type == "folder":
        if len(library_names) == 1:
            response["library_name"] = library_names[0]
        else:
            response["library_names"] = library_names
        if data_source is not None:
            response["folder_name"] = data_source.name
        else:
            response["folder_names"] = folder_names
        response["TIP"] = (
            "To read more context around a result: use get_document_text with "
            "document_id and start_char or start_page from the result above."
        )
    else:
        if len(library_names) == 1:
            response["library_name"] = library_names[0]
        else:
            response["library_names"] = library_names
        if data_source is not None:
            response["folder_name"] = data_source.name if data_source else None
        elif folder_names:
            response["folder_names"] = folder_names

        if document is not None:
            total_chars = len(document.extracted_text) if document.extracted_text else 0
            response["document_title"] = (
                document.title or document.filename or "Untitled"
            )
            response["document_id"] = document.id
            response["total_document_chars"] = total_chars
            if total_chars > MAX_DOCUMENT_CHARS:
                response["TIP"] = (
                    "This document is too large to read in one call. "
                    "Use get_document_text with start_char or start_page from the results above."
                )
        else:
            response["document_titles"] = document_titles
            response["document_ids"] = [
                current_document.id for current_document in documents
            ]
            response["TIP"] = (
                "To read more context around a result: use get_document_text with "
                "document_id and start_char or start_page from the result above."
            )

    return _maybe_add_paused_embedding_warning(response, paused_count)


async def list_documents(arguments: dict, context: ToolContext) -> dict:
    """
    List documents in a library, optionally filtered to a specific data source (folder).

    Returns document info including IDs, titles, sizes, and structure.
    Supports pagination via start_at/limit and an optional compact output format.
    """
    from asgiref.sync import sync_to_async

    from librarian.models import DataSource, Document, Library

    data_source_id = arguments.get("data_source_id")  # Optional folder filter
    library_id = arguments.get("library_id")
    start_at = arguments.get("start_at", 0)
    limit = arguments.get("limit", 50)
    compact = bool(arguments.get("compact", False))
    limit_capped = False
    max_limit = 1000

    try:
        start_at = int(start_at)
    except (TypeError, ValueError):
        return {"error": "start_at must be a non-negative integer"}

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return {"error": "limit must be an integer between 1 and 1000"}

    if start_at < 0:
        return {"error": "start_at must be a non-negative integer"}
    if limit < 1:
        return {"error": "limit must be an integer between 1 and 1000"}
    if limit > max_limit:
        limit = max_limit
        limit_capped = True

    if not library_id and not data_source_id:
        return {"error": "library_id or data_source_id is required"}

    user = context.user

    @sync_to_async
    def get_documents():
        from django.db.models.functions import Length

        def build_document_info(doc):
            return {
                "id": doc.id,
                "title": doc.title or doc.filename or "Untitled",
                "filename": doc.filename,
                "status": doc.status,
                "num_chunks": doc.num_chunks,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "text_length": doc.text_length,
                "is_container": doc.is_container,
                "is_loadable": is_code_interpreter_supported(doc.filename or ""),
            }

        # Allow callers to provide a folder directly and infer its parent library.
        if data_source_id and not library_id:
            try:
                data_source = DataSource.objects.select_related("library").get(
                    id=data_source_id
                )
            except DataSource.DoesNotExist:
                return None, f"Folder with id {data_source_id} not found"

            if not _user_can_view_data_source(user, data_source):
                return None, "You don't have permission to access this folder"

            library = data_source.library
            _touch_library_accessed_at_sync(library.id)
            docs_qs = data_source.documents.all()  # Include containers like ZIP files
            folder_name = data_source.name
        else:
            # Get and verify library access
            try:
                library = Library.objects.get(id=library_id)
            except Library.DoesNotExist:
                return None, f"Library with id {library_id} not found"

            if not _user_can_view_library(user, library):
                return None, "You don't have permission to access this library"

            _touch_library_accessed_at_sync(library.id)

            # Build document queryset - include containers (ZIP, etc.) for Code Interpreter access
            if data_source_id:
                try:
                    data_source = DataSource.objects.get(
                        id=data_source_id, library=library
                    )
                except DataSource.DoesNotExist:
                    return (
                        None,
                        f"Folder with id {data_source_id} not found in this library",
                    )
                docs_qs = (
                    data_source.documents.all()
                )  # Include containers like ZIP files
                folder_name = data_source.name
            else:
                docs_qs = Document.objects.filter(
                    data_source__library=library,
                )
                folder_name = None

        # Avoid pulling large extracted_text blobs into Python.
        # Use DB-side length instead (fast + doesn't materialize the whole text).
        total_count = docs_qs.count()
        docs_qs = docs_qs.annotate(text_length=Length("extracted_text")).order_by(
            "-created_at"
        )[start_at : start_at + limit]

        documents = [build_document_info(doc) for doc in docs_qs]
        has_more = start_at + len(documents) < total_count
        result = {
            "library_name": str(library),
            "folder_name": folder_name,
            "document_count": total_count,
            "returned_count": len(documents),
            "start_at": start_at,
            "limit": limit,
            "has_more": has_more,
            **({"next_start_at": start_at + len(documents)} if has_more else {}),
            **({"limit_capped": True, "limit_max": max_limit} if limit_capped else {}),
        }

        if compact:
            result["fields"] = [
                "id",
                "title",
                "filename",
                "status",
                "num_chunks",
                "created_at",
                "text_length",
                "is_container",
                "is_loadable",
            ]
            result["rows"] = [
                [
                    doc["id"],
                    doc["title"],
                    doc["filename"],
                    doc["status"],
                    doc["num_chunks"],
                    doc["created_at"],
                    doc["text_length"],
                    doc["is_container"],
                    doc["is_loadable"],
                ]
                for doc in documents
            ]
        else:
            result["documents"] = documents

        return result, None

    result, error = await get_documents()
    if error:
        return {"error": error}
    return result


# Maximum characters to return per document to avoid context overflow
# ~200k chars is roughly 50k tokens, fits comfortably in most modern context windows
MAX_DOCUMENT_CHARS = 200000


def _map_pages_to_char_offsets(text: str) -> dict[int, tuple[int, int]]:
    """
    Parse <page_N>...</page_N> tags in extracted text and return a mapping
    of page_number -> (start_char, end_char) covering the full tag range.

    Returns an empty dict if no page tags are found.
    """
    import re

    page_map = {}
    for match in re.finditer(r"<page_(\d+)>", text):
        page_num = int(match.group(1))
        start = match.start()
        # Find the closing tag
        close_tag = f"</page_{page_num}>"
        close_idx = text.find(close_tag, match.end())
        if close_idx != -1:
            end = close_idx + len(close_tag)
        else:
            # No closing tag found; extend to next page tag or end of text
            end = len(text)
        page_map[page_num] = (start, end)
    return page_map


def _get_page_count_from_text(text: str) -> int | None:
    """Count pages by looking for <page_N> tags. Returns None if no tags found."""
    import re

    pages = re.findall(r"<page_(\d+)>", text)
    return len(pages) if pages else None


def _compute_page_boundaries(text: str) -> list[tuple[int, int]]:
    """
    Parse <page_N> tags and return sorted list of (tag_start, page_num).
    Used to determine which page a given character offset falls on.
    """
    import re

    boundaries = []
    for match in re.finditer(r"<page_(\d+)>", text):
        boundaries.append((match.start(), int(match.group(1))))
    return sorted(boundaries, key=lambda x: x[0])


def _get_page_for_offset(
    page_boundaries: list[tuple[int, int]], char_offset: int
) -> int | None:
    """Given sorted page_boundaries list, find which page contains char_offset."""
    page = None
    for tag_start, page_num in page_boundaries:
        if tag_start > char_offset:
            break
        page = page_num
    return page


def _normalize_get_document_text_ids(arguments: dict) -> tuple[list[int], str | None]:
    document_id = arguments.get("document_id")
    document_ids = arguments.get("document_ids")

    if document_id not in (None, "") and document_ids not in (None, []):
        return [], "Provide either document_id or document_ids, not both."

    if document_ids not in (None, []):
        if not isinstance(document_ids, list):
            return [], "document_ids must be an array of positive integers"
        if len(document_ids) > MAX_GET_DOCUMENT_TEXT_DOCUMENTS:
            return (
                [],
                f"document_ids supports at most {MAX_GET_DOCUMENT_TEXT_DOCUMENTS} documents per call.",
            )
        normalized_ids = []
        for index, current_document_id in enumerate(document_ids):
            if not isinstance(current_document_id, int) or current_document_id <= 0:
                return (
                    [],
                    f"document_ids[{index}] must be a positive integer",
                )
            normalized_ids.append(current_document_id)
        if not normalized_ids:
            return [], "document_ids must include at least one document ID"
        return normalized_ids, None

    if not isinstance(document_id, int) or document_id <= 0:
        return [], "document_id is required"

    return [document_id], None


def _parse_get_document_text_position_args(
    arguments: dict,
) -> tuple[dict | None, str | None]:
    has_char_args = (
        arguments.get("start_char") is not None or arguments.get("end_char") is not None
    )
    has_page_args = (
        arguments.get("start_page") is not None or arguments.get("end_page") is not None
    )

    if has_char_args and has_page_args:
        return None, "Cannot combine start_char/end_char with start_page/end_page"

    start_char = arguments.get("start_char", 0)
    end_char = arguments.get("end_char")
    if start_char is None:
        start_char = 0
    if not isinstance(start_char, int):
        return None, "start_char must be an integer"
    if end_char is not None and not isinstance(end_char, int):
        return None, "end_char must be an integer"

    try:
        start_page = _parse_optional_page_number(
            arguments.get("start_page"), "start_page"
        )
        end_page = _parse_optional_page_number(arguments.get("end_page"), "end_page")
    except ValueError as exc:
        return None, str(exc)

    if start_page is not None and end_page is not None and start_page > end_page:
        return None, "start_page cannot be after end_page"

    return {
        "start_char": start_char,
        "end_char": end_char,
        "start_page": start_page,
        "end_page": end_page,
    }, None


def _get_document_text_result_sync(
    *,
    document_id: int,
    user,
    start_char: int,
    end_char: int | None,
    start_page: int | None,
    end_page: int | None,
) -> tuple[dict | None, str | None]:
    from librarian.models import Document

    try:
        doc = Document.objects.select_related(
            "data_source", "data_source__library"
        ).get(id=document_id)
    except Document.DoesNotExist:
        return (
            None,
            f"Document {document_id} not found. Verify the ID using list_documents.",
        )

    if not doc.data_source or not doc.data_source.library:
        return None, f"Document {document_id} is not part of a library"

    if not _user_can_view_document(user, doc):
        return None, f"No permission to access document {document_id}"

    _touch_library_accessed_at_sync(doc.data_source.library_id)

    if not doc.extracted_text and doc.status in (
        "PENDING",
        "INIT",
        "PROCESSING",
    ):
        import time as _time

        logger.info(
            "Waiting for text extraction",
            document_id=document_id,
            status=doc.status,
        )
        _deadline = _time.monotonic() + 5
        while _time.monotonic() < _deadline:
            _time.sleep(2)
            doc.refresh_from_db(fields=["extracted_text", "status"])
            if doc.extracted_text or doc.status not in (
                "PENDING",
                "INIT",
                "PROCESSING",
            ):
                break

    if not doc.extracted_text:
        return None, (
            f"Document {document_id} has no extracted text. "
            "Try view_library_files to see the original file visually, "
            "or load_library_files for programmatic access."
        )

    full_text = doc.extracted_text
    total_length = len(full_text)
    page_count = _get_page_count_from_text(full_text)

    requested_start = start_char
    requested_end = end_char

    if start_page is not None or end_page is not None:
        page_map = _map_pages_to_char_offsets(full_text)
        if not page_map:
            return None, (
                f"Document {document_id} has no page tags (<page_N>). "
                "Use start_char/end_char for character-based positioning instead."
            )
        sorted_pages = sorted(page_map.keys())
        min_page = sorted_pages[0]
        max_page = sorted_pages[-1]
        sp = start_page if start_page is not None else min_page
        ep = end_page if end_page is not None else max_page
        sp = max(sp, min_page)
        ep = min(ep, max_page)
        if sp > ep:
            return None, (
                f"start_page ({sp}) is after end_page ({ep}). "
                f"Valid page range: {min_page}-{max_page}."
            )
        start_char_actual = page_map[sp][0]
        end_char_actual = page_map[ep][1]
        text_portion = full_text[start_char_actual:end_char_actual]
    else:
        if requested_start < 0:
            start_char_actual = max(0, total_length + requested_start)
        else:
            start_char_actual = min(requested_start, total_length)

        if requested_end is None or requested_end == -1:
            end_char_actual = total_length
        elif requested_end < 0:
            end_char_actual = max(0, total_length + requested_end)
        else:
            end_char_actual = min(requested_end, total_length)

        text_portion = full_text[start_char_actual:end_char_actual]

    if len(text_portion) > MAX_DOCUMENT_CHARS:
        text_portion = text_portion[:MAX_DOCUMENT_CHARS]
        end_char_actual = start_char_actual + MAX_DOCUMENT_CHARS

    coverage_pct = round((end_char_actual - start_char_actual) / total_length * 100, 1)

    coverage_data = {
        "total_document_chars": total_length,
        "chars_returned": f"{start_char_actual:,}-{end_char_actual:,}",
        "coverage_pct": coverage_pct,
    }
    if page_count is not None:
        coverage_data["total_pages"] = page_count
    if start_page is not None or end_page is not None:
        coverage_data["pages_returned"] = f"{sp}-{ep}"

    result = {
        "COVERAGE": coverage_data,
        "document_id": doc.id,
        "title": doc.title or doc.filename or "Untitled",
        "text": text_portion,
    }

    if doc.status == "PAUSED":
        result["SEARCHABILITY_NOTE"] = _build_paused_embedding_warning(1)

    if end_char_actual < total_length:
        result["COVERAGE"]["next_start_char"] = end_char_actual
        result["COVERAGE"]["MORE_TO_READ"] = (
            f"You read {coverage_pct}% of the document "
            f"(chars {start_char_actual:,}-{end_char_actual:,} of {total_length:,}). "
            f"To continue, call with start_char={end_char_actual}."
        )

    filename_lower = (doc.filename or "").lower()
    is_image_file = filename_lower.endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")
    )
    if page_count and page_count > 0 and page_count <= 50:
        chars_per_page = total_length / page_count
        if chars_per_page < 500 or is_image_file:
            reason = (
                f"Image file ({doc.filename})"
                if is_image_file
                else f"Low text density (~{int(chars_per_page)} chars/page)"
            )
            result["VISION_RECOMMENDED"] = (
                f"{reason} across {page_count} page(s). "
                "Use view_library_files only if you need visual verification, and "
                "prefer a narrow start_page/end_page range instead of loading the "
                "whole document. Do NOT paraphrase around suspected OCR errors — "
                "verify visually first."
            )

    return result, None


async def get_document_text(arguments: dict, context: ToolContext) -> dict:
    """
    Get the text content of a document, with optional character or page-based positioning.

    Supports flexible reading:
    - Character-based: use start_char/end_char for precise positioning
    - Page-based: use start_page/end_page (maps to <page_N> tags in extracted text)
    - Omit positioning parameters to read from the beginning
    - Returns metadata about total length and what was returned

    Note: Text is automatically truncated to MAX_DOCUMENT_CHARS per call to prevent
    context overflow. Use positioning parameters to read specific portions of large documents.
    """
    from asgiref.sync import sync_to_async

    document_ids, ids_error = _normalize_get_document_text_ids(arguments)
    if ids_error:
        return {"error": ids_error}

    position_args, position_error = _parse_get_document_text_position_args(arguments)
    if position_error:
        return {"error": position_error}

    user = context.user

    @sync_to_async
    def get_texts():
        results = []
        for current_document_id in document_ids:
            result, error = _get_document_text_result_sync(
                document_id=current_document_id,
                user=user,
                start_char=position_args["start_char"],
                end_char=position_args["end_char"],
                start_page=position_args["start_page"],
                end_page=position_args["end_page"],
            )
            if error:
                results.append(
                    {
                        "status": "error",
                        "document_id": current_document_id,
                        "error": error,
                    }
                )
            else:
                results.append({"status": "ok", **result})
        return results

    results = await get_texts()
    if len(document_ids) == 1 and arguments.get("document_ids") in (None, []):
        single_result = results[0]
        if single_result["status"] == "error":
            return {"error": single_result["error"]}
        single_result.pop("status", None)
        return single_result

    success_count = sum(1 for result in results if result.get("status") == "ok")
    error_count = len(results) - success_count
    return {
        "ordering_preserved": True,
        "requested_document_ids": document_ids,
        "success_count": success_count,
        "error_count": error_count,
        "results": results,
    }


# Maximum context characters to show around a match
FIND_CONTEXT_CHARS = 200


async def find_in_document(arguments: dict, context: ToolContext) -> dict:
    """
    Find text in a document and return character positions with context.

    This is a text search (not semantic) - useful for:
    - Locating specific text found in RAG search results
    - Finding exact phrases, terms, or patterns
    - Getting character positions to use with get_document_text

    Returns matches with their exact character positions and surrounding context.
    """
    import re

    from asgiref.sync import sync_to_async

    from librarian.models import Document

    document_id = arguments.get("document_id")
    search_text = arguments.get("search_text", "")
    case_sensitive = arguments.get("case_sensitive", False)
    max_matches = min(arguments.get("max_matches", 10), 20)  # Cap at 20
    context_chars = min(arguments.get("context_chars", FIND_CONTEXT_CHARS), 500)

    if not document_id:
        return {"error": "document_id is required"}
    if not search_text:
        return {"error": "search_text is required"}
    if len(search_text) < 3:
        return {"error": "search_text must be at least 3 characters"}

    user = context.user

    @sync_to_async
    def do_search():
        try:
            doc = Document.objects.select_related(
                "data_source", "data_source__library"
            ).get(id=document_id)
        except Document.DoesNotExist:
            return None, None, f"Document with id {document_id} not found"

        if not doc.data_source or not doc.data_source.library:
            return None, None, "Document is not part of a library"

        if not _user_can_view_document(user, doc):
            return None, None, "You don't have permission to access this document"

        _touch_library_accessed_at_sync(doc.data_source.library_id)

        if not doc.extracted_text:
            return None, None, "Document has no extracted text"

        full_text = doc.extracted_text
        total_length = len(full_text)

        # Perform the search
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            # Escape special regex chars but allow basic wildcards
            # Users can use .* for wildcards if needed
            pattern = re.compile(re.escape(search_text), flags)
        except re.error as e:
            return None, None, f"Invalid search pattern: {e}"

        # Find ALL matches first (to get total count)
        all_matches = list(pattern.finditer(full_text))
        total_matches = len(all_matches)

        # Build detailed results for first N matches
        matches = []
        for match in all_matches[:max_matches]:
            start_pos = match.start()
            end_pos = match.end()

            # Get context before and after
            context_start = max(0, start_pos - context_chars)
            context_end = min(total_length, end_pos + context_chars)

            # Extract context with the match highlighted
            before_text = full_text[context_start:start_pos]
            match_text = full_text[start_pos:end_pos]
            after_text = full_text[end_pos:context_end]

            # Add ellipsis if truncated
            if context_start > 0:
                before_text = "..." + before_text
            if context_end < total_length:
                after_text = after_text + "..."

            matches.append(
                {
                    "char_start": start_pos,
                    "char_end": end_pos,
                    "match": match_text,
                    "context": f"{before_text}>>>{match_text}<<<{after_text}",
                }
            )

        return (
            _maybe_add_paused_embedding_warning(
                {
                    "document_id": doc.id,
                    "title": doc.title or doc.filename or "Untitled",
                    "total_document_chars": total_length,
                    "search_text": search_text,
                    "case_sensitive": case_sensitive,
                    "total_matches": total_matches,
                    "matches_returned": len(matches),
                    "matches": matches,
                },
                1 if doc.status == "PAUSED" else 0,
            ),
            doc,
            None,
        )

    result, doc, error = await do_search()
    if error:
        return {"error": error}
    return result


async def list_folders(arguments: dict, context: ToolContext) -> list[dict]:
    """
    List the folders (data sources) within a library.

    Useful for understanding library structure before searching specific folders.
    """
    from asgiref.sync import sync_to_async

    from librarian.models import Library

    library_id = arguments.get("library_id")

    if not library_id:
        return {"error": "library_id is required"}

    user = context.user

    @sync_to_async
    def get_folders():
        try:
            library = Library.objects.get(id=library_id)
        except Library.DoesNotExist:
            return None, f"Library with id {library_id} not found"

        if not _user_can_view_library(user, library):
            return None, "You don't have permission to access this library"

        _touch_library_accessed_at_sync(library.id)

        folders = []
        for ds in library.data_sources.all().order_by("order", "name"):
            doc_count = ds.documents.filter(is_container=False).count()
            folders.append(
                {
                    "id": ds.id,
                    "name": ds.name,
                    "document_count": doc_count,
                }
            )

        return {
            "library_name": str(library),
            "folder_count": len(folders),
            "folders": folders,
        }, None

    result, error = await get_folders()
    if error:
        return {"error": error}
    return result


async def load_library_files(arguments: dict, context: ToolContext) -> dict:
    """
    Load raw files from a Q&A library into the Code Interpreter environment.

    This uploads the actual binary files (Excel, CSV, images, PDFs, etc.) from library
    documents to OpenAI's Files API, making them available in the code interpreter's
    /mnt/data directory. Use this when you need to process files programmatically
    (e.g., read Excel data with pandas, process images with PIL).

    Unlike get_document_text which returns extracted text, this provides the original
    files for direct manipulation in Python code.

    Requires Code Interpreter to be enabled in the chat.
    """
    from django.conf import settings

    from asgiref.sync import sync_to_async
    from openai import AzureOpenAI

    from librarian.models import Document

    document_ids = arguments.get("document_ids", [])

    if not document_ids:
        return {"error": "document_ids is required (list of document IDs to load)"}

    if len(document_ids) > 20:
        return {"error": "Maximum of 20 documents can be loaded at once"}

    user = context.user
    responses_client = context.extra.get("responses_client")

    if not responses_client:
        logger.warning("load_library_files called without responses_client in context")
        return {"error": "Internal error: Code interpreter context not available"}

    # Check if code interpreter is enabled by checking if it's in the tools list
    if not responses_client.tools or "code_interpreter" not in responses_client.tools:
        return {
            "error": "Code Interpreter is not enabled. Enable it in chat settings to use this tool."
        }

    @sync_to_async
    def get_documents_and_upload():
        """Fetch documents, verify permissions, and upload files to OpenAI."""
        # Create OpenAI client for file uploads
        api_version = settings.AZURE_AI_SERVICES_VERSION
        if not api_version or api_version in ("v1", "v1/"):
            api_version = "2025-03-01-preview"

        client = AzureOpenAI(
            api_key=settings.AZURE_AI_SERVICES_KEY,
            azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
            api_version=api_version,
        )

        loaded_files = []
        errors = []
        touched_library_ids = set()

        for doc_id in document_ids:
            try:
                doc = Document.objects.select_related(
                    "saved_file", "data_source", "data_source__library"
                ).get(id=doc_id)
            except Document.DoesNotExist:
                errors.append(f"Document {doc_id} not found")
                continue

            # Check permission via document
            if not doc.data_source or not doc.data_source.library:
                errors.append(f"Document {doc_id} is not part of a library")
                continue

            if not _user_can_view_document(user, doc):
                errors.append(
                    f"No permission to access document {doc_id} ({doc.filename})"
                )
                continue

            touched_library_ids.add(doc.data_source.library_id)

            # Check if document has a file
            if not doc.saved_file or not doc.saved_file.file:
                errors.append(
                    f"Document {doc_id} ({doc.filename}) has no file attached"
                )
                continue

            saved_file = doc.saved_file
            filename = doc.filename or saved_file.file.name.split("/")[-1]

            # Check if file extension is supported by Code Interpreter
            if not is_code_interpreter_supported(filename):
                ext = os.path.splitext(filename)
                errors.append(
                    f"Document {doc_id} ({filename}) has unsupported extension '{ext}'. "
                    f"Code Interpreter supports: {', '.join(sorted(CODE_INTERPRETER_SUPPORTED_EXTENSIONS))}"
                )
                continue

            try:
                openai_filename = None
                # Check if already uploaded to OpenAI
                if saved_file.openai_file_id:
                    file_id = saved_file.openai_file_id
                    logger.info(
                        "Reusing existing OpenAI file_id for library document",
                        document_id=doc_id,
                        filename=filename,
                        file_id=file_id,
                    )
                    try:
                        file_meta = client.files.retrieve(file_id)
                        retrieved_name = getattr(file_meta, "filename", None)
                        if isinstance(retrieved_name, str) and retrieved_name.strip():
                            openai_filename = retrieved_name
                    except Exception as e:
                        logger.warning(
                            "Failed to retrieve OpenAI file metadata",
                            document_id=doc_id,
                            filename=filename,
                            file_id=file_id,
                            error=str(e),
                        )
                else:
                    # Upload to OpenAI Files API
                    with saved_file.file.open("rb") as f:
                        response = client.files.create(
                            file=(filename, f),
                            purpose="assistants",
                        )
                    file_id = response.id
                    response_filename = getattr(response, "filename", None)
                    if isinstance(response_filename, str) and response_filename.strip():
                        openai_filename = response_filename

                    # Cache on SavedFile for future use
                    saved_file.openai_file_id = file_id
                    saved_file.save(update_fields=["openai_file_id"])

                    logger.info(
                        "Uploaded library document to OpenAI",
                        document_id=doc_id,
                        filename=filename,
                        file_id=file_id,
                    )

                loaded_files.append(
                    {
                        "document_id": doc_id,
                        "filename": filename,
                        "openai_filename": openai_filename,
                        "file_id": file_id,
                        "content_type": saved_file.content_type
                        or "application/octet-stream",
                        "title": doc.title or filename,
                    }
                )

            except Exception as e:
                logger.exception(
                    "Failed to upload library document to OpenAI",
                    document_id=doc_id,
                    filename=filename,
                    error=str(e),
                )
                errors.append(f"Failed to upload {filename}: {str(e)[:100]}")

            _touch_libraries_accessed_at_sync(touched_library_ids)
        return loaded_files, errors

    loaded_files, errors = await get_documents_and_upload()

    # Add the file_ids to the responses client for Code Interpreter
    if loaded_files:
        new_file_ids = [f["file_id"] for f in loaded_files]
        # Add to existing file_ids, avoiding duplicates
        existing_ids = set(responses_client.code_interpreter_file_ids or [])
        for fid in new_file_ids:
            if fid not in existing_ids:
                responses_client.code_interpreter_file_ids.append(fid)
                existing_ids.add(fid)

        logger.info(
            "Added library files to Code Interpreter",
            count=len(new_file_ids),
            total_file_ids=len(responses_client.code_interpreter_file_ids),
        )

    # Build response
    result = {
        "loaded_files": [
            {
                "document_id": f["document_id"],
                "filename": f["filename"],
                "openai_filename": f.get("openai_filename"),
                "file_id": f.get("file_id"),
                "path_glob": f"/mnt/data/*{f['filename']}",
                "title": f["title"],
                "content_type": f["content_type"],
            }
            for f in loaded_files
        ],
        "file_count": len(loaded_files),
    }

    if errors:
        result["errors"] = errors

    if loaded_files:
        # Provide guidance for using the files
        filenames = [f["filename"] for f in loaded_files]
        reported_names = [
            f.get("openai_filename") or f["filename"] for f in loaded_files
        ]
        result["usage_hint"] = (
            (
                "Files are now available in /mnt/data/. The Files API reports the "
                f"filename '{reported_names[0]}', but the runtime may prefix it. "
                "Locate the exact path with glob, e.g.: "
                f"import glob; path = glob.glob('/mnt/data/*{filenames[0]}')[0]; "
                "pd.read_excel(path)"
            )
            if len(filenames) == 1
            else (
                "Files are now available in /mnt/data/. The Files API reports the "
                f"filenames: {', '.join(reported_names)}. The runtime may prefix them; "
                "locate each file with glob, e.g.: "
                "import glob; glob.glob('/mnt/data/*<filename>')"
            )
        )

    return result


async def view_library_files(arguments: dict, context: ToolContext) -> dict:
    """
    Load library documents as visual content for the AI to view directly.

    This makes images and PDFs from library documents visible to the vision model,
    allowing it to analyze charts, diagrams, images, and PDF layouts directly.
    Unlike get_document_text which returns extracted text, this allows the model
    to see the actual visual content.

    Images are passed as base64-encoded data URLs.
    PDFs are uploaded to OpenAI Files API and passed by file_id.
    If start_page/end_page is provided, only the requested PDF pages are sent
    inline as a temporary sliced PDF.

    Note: For most PDFs, get_document_text is more efficient since it returns
    the extracted text. Use this tool when:
    - The extracted text quality is poor
    - The document contains charts, graphs, diagrams, or images
    - Visual layout or formatting is important
    """
    import base64

    from django.conf import settings

    from asgiref.sync import sync_to_async

    from librarian.models import Document

    document_ids = arguments.get("document_ids", [])

    try:
        start_page = _parse_optional_page_number(
            arguments.get("start_page"), "start_page"
        )
        end_page = _parse_optional_page_number(arguments.get("end_page"), "end_page")
    except ValueError as e:
        return {"error": str(e)}

    if start_page is not None and end_page is not None and start_page > end_page:
        return {
            "error": f"start_page ({start_page}) cannot be after end_page ({end_page})"
        }

    page_range_requested = start_page is not None or end_page is not None

    if not document_ids:
        return {"error": "document_ids is required (list of document IDs to view)"}

    if len(document_ids) > 20:
        return {
            "error": "Maximum of 20 documents can be viewed at once (to avoid token limits)"
        }

    user = context.user

    @sync_to_async
    def get_documents_and_prepare_vision():
        """Fetch documents, verify permissions, and prepare vision items."""
        from openai import AzureOpenAI

        client = None

        vision_items = []
        loaded_files = []
        errors = []
        touched_library_ids = set()

        for doc_id in document_ids:
            try:
                doc = Document.objects.select_related(
                    "saved_file", "data_source", "data_source__library"
                ).get(id=doc_id)
            except Document.DoesNotExist:
                errors.append(f"Document {doc_id} not found")
                continue

            # Check permission via document
            if not doc.data_source or not doc.data_source.library:
                errors.append(f"Document {doc_id} is not part of a library")
                continue

            if not _user_can_view_document(user, doc):
                errors.append(
                    f"No permission to access document {doc_id} ({doc.filename})"
                )
                continue

            touched_library_ids.add(doc.data_source.library_id)

            # Check if document has a file
            if not doc.saved_file or not doc.saved_file.file:
                errors.append(
                    f"Document {doc_id} ({doc.filename}) has no file attached"
                )
                continue

            saved_file = doc.saved_file
            filename = doc.filename or saved_file.file.name.split("/")[-1]
            filename_lower = filename.lower()

            # Determine file type
            is_image = (
                saved_file.content_type and saved_file.content_type.startswith("image/")
            ) or filename_lower.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            )

            is_pdf = (
                saved_file.content_type == "application/pdf"
                or filename_lower.endswith(".pdf")
            )

            if not is_image and not is_pdf:
                errors.append(
                    f"Document {doc_id} ({filename}) is not an image or PDF. "
                    f"Use get_document_text for text content or load_library_files for code interpreter."
                )
                continue

            # Gather metadata to help the model decide on follow-up tool usage
            has_text = bool(doc.extracted_text and doc.extracted_text.strip())
            text_char_count = len(doc.extracted_text) if has_text else 0
            doc_page_count = (
                _get_page_count_from_text(doc.extracted_text) if has_text else None
            )

            try:
                if is_image:
                    # Images are passed as base64 data URLs
                    with saved_file.file.open("rb") as f:
                        file_bytes = f.read()

                    mime_type = saved_file.content_type or "image/png"
                    b64_data = base64.b64encode(file_bytes).decode("utf-8")

                    vision_items.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{b64_data}",
                            "detail": "high",
                        }
                    )

                    loaded_files.append(
                        {
                            "document_id": doc_id,
                            "filename": filename,
                            "type": "image",
                            "title": doc.title or filename,
                            "has_extracted_text": has_text,
                            "text_char_count": text_char_count,
                        }
                    )

                    logger.info(
                        "Prepared image for vision",
                        document_id=doc_id,
                        filename=filename,
                        size_bytes=len(file_bytes),
                    )

                elif is_pdf:

                    def is_upload_limit_error(exc: Exception) -> bool:
                        return _is_upload_limit_error_for_bytes(
                            exc,
                            VISION_PDF_MAX_FILE_BYTES,
                        )

                    file_size_bytes = None
                    try:
                        file_size_bytes = int(saved_file.file.size)
                    except Exception:
                        # Some storage backends may not report size reliably.
                        file_size_bytes = None

                    auto_split_large_pdf = not page_range_requested and (
                        file_size_bytes is None
                        or file_size_bytes > VISION_PDF_MAX_FILE_BYTES
                    )

                    if page_range_requested or auto_split_large_pdf:
                        page_ranges: list[tuple[int, int]] = []

                        if page_range_requested:
                            requested_start = (
                                start_page if start_page is not None else 1
                            )
                            requested_end = (
                                end_page
                                if end_page is not None
                                else (
                                    start_page
                                    if start_page is not None
                                    else requested_start
                                )
                            )
                            page_ranges.append((requested_start, requested_end))
                        else:
                            from pypdf import PdfReader

                            with saved_file.file.open("rb") as source_file:
                                reader = PdfReader(source_file)
                                pdf_total_pages = len(reader.pages)

                            if pdf_total_pages < 1:
                                errors.append(
                                    f"Document {doc_id} ({filename}) has no pages"
                                )
                                continue

                            if file_size_bytes is not None:
                                approx_bytes_per_page = max(
                                    1,
                                    (file_size_bytes + pdf_total_pages - 1)
                                    // pdf_total_pages,
                                )
                                pages_per_slice = max(
                                    1,
                                    min(
                                        VISION_PDF_MAX_PAGES_PER_SLICE,
                                        VISION_PDF_TARGET_SLICE_BYTES
                                        // approx_bytes_per_page,
                                    ),
                                )
                            else:
                                # File size unavailable; use the maximum slice size
                                # and rely on recursive splitting if any slice is too large.
                                pages_per_slice = VISION_PDF_MAX_PAGES_PER_SLICE

                            page_ranges = [
                                (
                                    start,
                                    min(pdf_total_pages, start + pages_per_slice - 1),
                                )
                                for start in range(
                                    1, pdf_total_pages + 1, pages_per_slice
                                )
                            ]

                            if len(page_ranges) > VISION_PDF_MAX_AUTO_SLICES:
                                errors.append(
                                    f"Document {doc_id} ({filename}) is too large for automatic visual chunking "
                                    f"({len(page_ranges)} chunks needed). Use start_page/end_page to view targeted sections."
                                )
                                continue

                            logger.info(
                                "Auto-splitting large PDF for vision",
                                document_id=doc_id,
                                filename=filename,
                                file_size_bytes=file_size_bytes,
                                total_pages=pdf_total_pages,
                                pages_per_slice=pages_per_slice,
                                chunk_count=len(page_ranges),
                            )

                        if client is None:
                            api_version = settings.AZURE_AI_SERVICES_VERSION
                            if not api_version or api_version in ("v1", "v1/"):
                                api_version = "2025-03-01-preview"

                            client = AzureOpenAI(
                                api_key=settings.AZURE_AI_SERVICES_KEY,
                                azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
                                api_version=api_version,
                            )

                        pending_ranges = list(page_ranges)
                        while pending_ranges:
                            range_start_page, range_end_page = pending_ranges.pop(0)
                            try:
                                (
                                    vision_item,
                                    pdf_total_pages,
                                    viewed_start_page,
                                    viewed_end_page,
                                ) = _build_pdf_page_range_vision_item(
                                    saved_file=saved_file,
                                    filename=filename,
                                    start_page=range_start_page,
                                    end_page=range_end_page,
                                    openai_client=client,
                                )
                            except Exception as range_exc:
                                can_split_further = (
                                    isinstance(range_start_page, int)
                                    and isinstance(range_end_page, int)
                                    and range_start_page < range_end_page
                                )
                                if (
                                    is_upload_limit_error(range_exc)
                                    and can_split_further
                                ):
                                    midpoint = (range_start_page + range_end_page) // 2
                                    candidate_count = len(pending_ranges) + 2
                                    if candidate_count > VISION_PDF_MAX_AUTO_SLICES:
                                        raise ValueError(
                                            "Automatic PDF chunk splitting exceeded "
                                            f"{VISION_PDF_MAX_AUTO_SLICES} chunks; "
                                            "use narrower start_page/end_page ranges."
                                        ) from range_exc

                                    pending_ranges.insert(
                                        0, (midpoint + 1, range_end_page)
                                    )
                                    pending_ranges.insert(
                                        0, (range_start_page, midpoint)
                                    )
                                    logger.info(
                                        "Splitting oversized PDF range for vision",
                                        document_id=doc_id,
                                        filename=filename,
                                        original_start_page=range_start_page,
                                        original_end_page=range_end_page,
                                        split_left_start_page=range_start_page,
                                        split_left_end_page=midpoint,
                                        split_right_start_page=midpoint + 1,
                                        split_right_end_page=range_end_page,
                                    )
                                    continue
                                raise

                            vision_items.append(vision_item)
                            loaded_files.append(
                                {
                                    "document_id": doc_id,
                                    "filename": filename,
                                    "type": "pdf",
                                    "title": doc.title or filename,
                                    "page_count": doc_page_count or pdf_total_pages,
                                    "pages_viewed": f"{viewed_start_page}-{viewed_end_page}",
                                    "has_extracted_text": has_text,
                                    "text_char_count": text_char_count,
                                }
                            )

                            logger.info(
                                "Prepared ranged PDF for vision",
                                document_id=doc_id,
                                filename=filename,
                                start_page=viewed_start_page,
                                end_page=viewed_end_page,
                                total_pages=pdf_total_pages,
                            )
                    else:
                        # PDFs are uploaded to Files API and passed by file_id
                        # Check if already uploaded
                        if saved_file.openai_file_id:
                            file_id = saved_file.openai_file_id
                            logger.info(
                                "Reusing existing OpenAI file_id for PDF vision",
                                document_id=doc_id,
                                filename=filename,
                                file_id=file_id,
                            )
                        else:
                            if client is None:
                                api_version = settings.AZURE_AI_SERVICES_VERSION
                                if not api_version or api_version in ("v1", "v1/"):
                                    api_version = "2025-03-01-preview"

                                client = AzureOpenAI(
                                    api_key=settings.AZURE_AI_SERVICES_KEY,
                                    azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
                                    api_version=api_version,
                                )

                            # Upload to OpenAI Files API
                            with saved_file.file.open("rb") as f:
                                response = client.files.create(
                                    file=(filename, f),
                                    purpose="assistants",
                                )
                            file_id = response.id

                            # Cache on SavedFile for future use
                            saved_file.openai_file_id = file_id
                            saved_file.save(update_fields=["openai_file_id"])

                            logger.info(
                                "Uploaded PDF for vision",
                                document_id=doc_id,
                                filename=filename,
                                file_id=file_id,
                            )

                        vision_items.append(
                            {
                                "type": "input_file",
                                "file_id": file_id,
                            }
                        )

                        loaded_files.append(
                            {
                                "document_id": doc_id,
                                "filename": filename,
                                "type": "pdf",
                                "file_id": file_id,
                                "title": doc.title or filename,
                                "page_count": doc_page_count,
                                "has_extracted_text": has_text,
                                "text_char_count": text_char_count,
                            }
                        )

            except Exception as e:
                logger.exception(
                    "Failed to prepare library document for vision",
                    document_id=doc_id,
                    filename=filename,
                    error=str(e),
                )
                errors.append(f"Failed to load {filename}: {str(e)[:100]}")

            _touch_libraries_accessed_at_sync(touched_library_ids)
        return vision_items, loaded_files, errors

    vision_items, loaded_files, errors = await get_documents_and_prepare_vision()

    # Build response describing what was loaded
    result = {
        "loaded_files": [
            {
                "document_id": f["document_id"],
                "filename": f["filename"],
                "type": f["type"],
                "title": f["title"],
                **({"page_count": f["page_count"]} if f.get("page_count") else {}),
                **(
                    {"pages_viewed": f["pages_viewed"]} if f.get("pages_viewed") else {}
                ),
                "has_extracted_text": f.get("has_extracted_text", False),
                "text_char_count": f.get("text_char_count", 0),
            }
            for f in loaded_files
        ],
        "file_count": len(loaded_files),
    }

    if errors:
        result["errors"] = errors

    if loaded_files:
        # Describe what was loaded for the model
        image_count = sum(1 for f in loaded_files if f["type"] == "image")
        pdf_count = sum(1 for f in loaded_files if f["type"] == "pdf")
        parts = []
        if image_count:
            parts.append(f"{image_count} image(s)")
        if pdf_count:
            parts.append(
                f"{pdf_count} PDF(s)"
                + (
                    " (page-range limited where requested)"
                    if page_range_requested
                    else ""
                )
            )
        result["description"] = (
            f"Loaded {' and '.join(parts)} for visual analysis. "
            f"The visual content is included below. Please analyze it as requested."
        )

        # Add guidance hint for large documents where text tools may be better
        large_docs = [
            f for f in loaded_files if f.get("page_count") and f["page_count"] > 10
        ]
        if large_docs:
            doc_hints = ", ".join(
                f"{f['filename']} ({f['page_count']} pages)" for f in large_docs
            )
            result["TOOL_HINT"] = (
                f"Large document(s) detected: {doc_hints}. "
                "Visual analysis may miss content in long documents. For exact quotes, "
                "comprehensive analysis, or specific page reads, also use "
                "get_document_text (with start_page/end_page) or rag_search with document_id."
            )

    # Include vision items directly in the function output
    # This uses the _vision_output convention that build_function_call_output recognizes
    if vision_items:
        result["_vision_output"] = vision_items
        logger.info(
            "Returning vision items in function output",
            count=len(vision_items),
        )

    return result


TOOL_REGISTRY.register(
    OttoTool(
        name="list_libraries",
        description=(
            "List the Q&A document libraries that the user has access to."
            # "Call this first to discover available libraries before searching. "
            # "Returns library IDs, names, descriptions, and document counts."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=list_libraries,
        requires_user=True,
        permission_check=_can_access_libraries,
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="rag_search",
        description=(
            "Search one Q&A library, folder, or document using semantic RAG search. "
            "Provide exactly one of library_id, data_source_ids, or document_ids to choose the scope."
            # "This is a hybrid search that finds conceptually similar content - "
            # "it does NOT support boolean operators (AND/OR/NOT) or exact phrase matching. "
            # "Use natural language queries describing what you're looking for. "
            # "Use list_libraries, list_folders, or list_documents first to get IDs. Each result is a document chunk excerpt; "
            # "individual chunks may be up to 768 tokens, so larger top_k values add context quickly. "
            # "Documents with status PAUSED are not embedded yet and will not appear in semantic search results. "
            # "Returns relevant text excerpts with document_id, start_char, and page_number for direct follow-up with get_document_text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "library_id": {
                    "type": "integer",
                    "description": "Optional library ID to search (from list_libraries).",
                    # "Provide exactly one scope identifier.",
                },
                "data_source_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional folder IDs to search (from list_folders).",
                    # "Provide exactly one scope identifier; use a single-element list for one folder.",
                },
                "document_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional document IDs to search (from list_documents or a previous rag_search result).",
                    # "Provide exactly one scope identifier; use a single-element list for one document.",
                },
                "query": {
                    "type": "string",
                    "description": "",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Number of chunk results to return. Each chunk may be up to 768 tokens.",
                    "default": 7,
                },
                "vector_weight": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Hybrid retrieval mix between keyword (0) and vector (1).",
                    "default": 0.6,
                },
            },
            "required": ["query", "top_k", "vector_weight"],
            "additionalProperties": False,
        },
        execute=rag_search,
        requires_user=True,
        requires_chat=False,
        permission_check=_can_access_libraries,
        strict=False,
        estimate_cost=estimate_qa_search_cost,
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="list_folders",
        description=(
            "List the folders (data sources) within a library."
            # "Useful for understanding library structure before browsing or searching specific folders. "
            # "Returns folder IDs, names, descriptions, and document counts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "library_id": {
                    "type": "integer",
                    "description": "The ID of the library to list folders from",
                },
            },
            "required": ["library_id"],
            "additionalProperties": False,
        },
        execute=list_folders,
        requires_user=True,
        permission_check=_can_access_libraries,
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="list_documents",
        description=(
            "List documents in a library, optionally filtered to a specific folder."
            # "Returns document IDs, titles, filenames, statuses, and text lengths. "
            # "Use this to discover documents before reading their content. "
            # "Status PAUSED means extraction is available but semantic Q&A search is not until manual embedding happens. "
            # "Supports pagination with start_at/limit and an optional compact format."
        ),
        parameters={
            "type": "object",
            "properties": {
                "library_id": {
                    "type": "integer",
                    "description": "The ID of the library containing the documents",
                },
                "data_source_id": {
                    "type": "integer",
                    "description": "Optional folder ID to filter documents (from list_folders).",
                },
                "start_at": {
                    "type": "integer",
                    "description": "Zero-based offset for pagination.",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of documents to return (1-1000).",
                    "default": 50,
                },
                "compact": {
                    "type": "boolean",
                    "description": "If true, return a compact rows/fields format for higher information density.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        strict=False,  # library_id and data_source_id are optional at schema level
        execute=list_documents,
        requires_user=True,
        permission_check=_can_access_libraries,
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="get_document_text",
        description=(
            "Read the extracted text of a document. Returns up to 200,000 characters per call. "
            "If the document is still being processed (e.g. just retrieved via retrieve_url_content), "
            "this tool waits for extraction to finish automatically. "
            "This works for PAUSED documents too, even when semantic search cannot find them yet. "
            "Best for: exact quoting, text analysis, counting, and any task requiring precise text. "
            "Supports character-based (start_char/end_char) and page-based (start_page/end_page) navigation. "
            "Page-based navigation uses <page_N> tags in the extracted text — ideal after using "
            "view_library_files to identify pages of interest. "
            "Use this as the default tool for reading, summaries, and quotes. "
            "If the response includes a VISION_RECOMMENDED field, consider targeted "
            "view_library_files verification only for the relevant page(s). Also use "
            "view_library_files when layout or OCR certainty materially affects the answer — "
            "do not paraphrase around suspected OCR errors. "
            "To read multiple documents in a known order, pass document_ids as an ordered array and this tool will return the texts in that same order in one response. "
            "Use single-document calls only when you genuinely need separate reads. "
            "Response includes a COVERAGE object showing what was read. You MUST report this to the user. "
            "NEVER claim to have read the 'full document' unless coverage_pct is 100%."
        ),
        parameters={
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "integer",
                    "description": "The document ID to read (from list_documents or search results).",
                },
                "document_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional ordered list of document IDs to read in one call. Provide either document_id or document_ids, not both.",
                },
                "start_char": {
                    "type": "integer",
                    "description": "Character position to start reading from (0-indexed). Omit to start from beginning. Cannot combine with start_page/end_page.",
                },
                "end_char": {
                    "type": "integer",
                    "description": "Character position to stop reading at. Use -1 or omit to read to end. Cannot combine with start_page/end_page.",
                },
                "start_page": {
                    "type": "integer",
                    "description": "Start reading from this PDF page number (1-based). Uses <page_N> tags. Cannot combine with start_char/end_char.",
                },
                "end_page": {
                    "type": "integer",
                    "description": "Stop reading at this PDF page number (inclusive). Omit to read only start_page. Cannot combine with start_char/end_char.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        strict=False,  # document_id/document_ids and range args are optional at schema level, validated at runtime
        execute=get_document_text,
        requires_user=True,
        permission_check=_can_access_libraries,
        estimate_cost=estimate_get_document_text_cost,
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="find_in_document",
        description=(
            "Find exact text in a document and get character positions plus total match count."
            # "Unlike rag_search (semantic/RAG search), this finds EXACT text matches. "
            # "This works on PAUSED documents after extraction, even when semantic search cannot find them yet. "
            # "Best for: (1) counting occurrences of a specific term/phrase, "
            # "(2) precise quoting — finding exact positions of specific passages, "
            # "(3) pattern searching when you know what text to look for. "
            # "Usually NOT needed to bridge search→read: search results already include start_char/page_number. "
            # "Returns total_matches (count) plus first N matches with context and positions. "
            # "Case-insensitive by default unless case_sensitive=true."
        ),
        parameters={
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "integer",
                    "description": "The ID of the document to search within",
                },
                "search_text": {
                    "type": "string",
                    "description": "The exact text to find (minimum 3 characters).",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "",
                    "default": False,
                },
                "max_matches": {
                    "type": "integer",
                    "description": "",
                    "default": 10,
                },
                "context_chars": {
                    "type": "integer",
                    "description": "Characters of context to show around each match (50-500).",
                    "default": 200,
                },
            },
            "required": ["document_id", "search_text"],
            "additionalProperties": False,
        },
        strict=False,  # Optional parameters
        execute=find_in_document,
        requires_user=True,
        permission_check=_can_access_libraries,
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="load_library_files",
        description=(
            "Load raw files from a Q&A library into the Code Interpreter environment."
            # "Use this to access the actual binary files (Excel, CSV, images, PDFs, etc.) "
            # "for programmatic processing with Python code (e.g., pandas, matplotlib, PIL). "
            # "Unlike get_document_text which returns extracted text, this provides the original "
            # "files in /mnt/data/ for direct manipulation. This can still be used for PAUSED documents because it reads the original file, not the embeddings. Requires Code Interpreter to be enabled. "
            # "Do NOT use this for reading or summarizing document text — use get_document_text instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "document_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of document IDs to load (from list_documents).",
                    "maximum": 20,
                },
            },
            "required": ["document_ids"],
            "additionalProperties": False,
        },
        execute=load_library_files,
        requires_user=True,
        permission_check=_can_access_libraries,
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="view_library_files",
        description=(
            "View images and PDFs from a Q&A library using vision capabilities."
            # "Best for: layout-sensitive questions, charts/graphs/diagrams, photographs, "
            # "scanned documents, handwriting, signatures, checkboxes, forms. "
            # "This can still be used for PAUSED documents because it reads the original file, not semantic embeddings. "
            # "Use this for targeted visual verification when get_document_text suggests it, or when "
            # "you need to inspect layout or OCR uncertainty. "
            # "Response includes metadata: page_count, text_char_count, and has_extracted_text.\n"
            # "Use this when:\n"
            # "- get_document_text returned VISION_RECOMMENDED and visual confirmation is needed\n"
            # "- You are unsure about key text and the doc is ≤ ~50 pages\n"
            # "- Visual layout, formatting, or spatial relationships matter\n"
            # "- Charts, graphs, diagrams, images, or handwriting\n"
            # "- You find yourself choosing a 'safe' paraphrase to avoid quoting uncertain text\n"
            # "- For PDFs, you can provide start_page/end_page to inspect only a targeted page range\n"
            # "Do NOT use this when:\n"
            # "- The document is very long (50+ pages) and OCR text is clearly readable without visual review\n"
            # "For precise text extraction after visual review, use get_document_text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "document_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of document IDs to view (from list_documents).",
                    "maximum": 20,
                },
                "start_page": {
                    "type": "integer",
                    "description": "Optional 1-based PDF start page. If provided without end_page, only this page is viewed.",
                },
                "end_page": {
                    "type": "integer",
                    "description": "Optional inclusive 1-based PDF end page. If provided without start_page, pages 1-end_page are viewed.",
                },
            },
            "required": ["document_ids"],
            "additionalProperties": False,
        },
        strict=False,
        execute=view_library_files,
        requires_user=True,
        permission_check=_can_access_libraries,
    )
)
