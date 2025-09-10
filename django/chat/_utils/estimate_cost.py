from decimal import Decimal
from typing import Any, List

from structlog import get_logger

from otto.models import CostType
from otto.utils.common import cad_cost

logger = get_logger(__name__)

EST_CHARS_PER_TOKEN = 4


def _estimate_tokens_from_text(text: str) -> int:
    """Estimate token count based on character count."""
    return len(text) // EST_CHARS_PER_TOKEN


def _estimate_file_size_tokens(file_size: int) -> int:
    """Estimate token count based on file size."""
    return file_size // 5  # Very rough estimate


def _calculate_cost_for_units(cost_type_name: str, unit_count: int) -> Decimal:
    """Calculate cost for a given number of units and cost type short name."""
    cost_type = CostType.objects.get(short_name=cost_type_name)
    return (unit_count * cost_type.unit_cost) / cost_type.unit_quantity


def _estimate_cost_of_string(text: str, cost_type: str) -> Decimal:
    """Estimate cost of a text string based on the cost type."""
    if cost_type.startswith("translate-"):
        count = len(text)
    else:
        count = _estimate_tokens_from_text(text)

    return _calculate_cost_for_units(cost_type, count)


def _estimate_file_tokens(file: Any) -> int:
    """Estimate token count for a file, with special handling for PDFs."""
    if file.text:
        return _estimate_tokens_from_text(file.text)

    # If file doesn't have text yet, estimate based on file type
    try:
        # Special handling for PDFs - try to get page count
        if (
            file.saved_file.content_type == "application/pdf"
            or file.filename.lower().endswith(".pdf")
        ):
            try:
                import pymupdf

                with file.saved_file.file.open("rb") as pdf_file:
                    doc = pymupdf.open(stream=pdf_file.read())
                    page_count = doc.page_count
                    doc.close()
                    # Estimate ~300 tokens per page (conservative estimate)
                    return page_count * 300
            except Exception as pdf_error:
                logger.warning(
                    f"Failed to get page count for PDF {file.filename}: {pdf_error}"
                )
                # Fall back to file size estimation
                return _estimate_file_size_tokens(file.saved_file.file.size)
        else:
            # For non-PDF files, use file size estimation
            return _estimate_file_size_tokens(file.saved_file.file.size)
    except Exception:
        # If we can't estimate, use a conservative high estimate
        return 5000  # Conservative default: 5000 tokens


def _get_translate_cost_type(chat: Any, user_message: Any) -> str:
    """Determine the appropriate cost type for translation mode."""
    if chat.options.translate_model == "gpt":
        return "gpt-4.1-mini-in"
    elif chat.options.translate_model == "azure_custom":
        return "translate-custom"
    elif chat.options.translate_model == "azure":
        # Check if user message has files attached
        if user_message.sorted_files.exists():
            return "translate-file"
        else:
            return "translate-text"
    return "translate-text"  # fallback


def _estimate_qa_documents_cost(chat: Any, model: str) -> Decimal:
    """Estimate cost for QA mode document processing."""
    cost = Decimal("0")

    # Gather the documents based on scope
    if chat.options.qa_scope == "documents":
        docs = chat.options.qa_documents.all()
    else:
        if chat.options.qa_scope == "all":
            data_sources = chat.options.qa_library.sorted_data_sources
        else:
            data_sources = chat.options.qa_data_sources
        docs = [
            doc
            for data_source in data_sources.all()
            for doc in data_source.documents.all()
        ]

    # Estimate cost based on QA mode
    if chat.options.qa_mode == "rag":
        # RAG mode: estimate based on chunk count
        total_chunks_of_library = 0
        for doc in docs:
            if doc.num_chunks is not None:
                total_chunks_of_library += doc.num_chunks

        chunk_count = chat.options.qa_topk
        if chat.options.qa_process_mode == "per_doc":
            chunk_count = chunk_count * len(docs)

        chunk_count = min(total_chunks_of_library, chunk_count)
        token_count = 768 * chunk_count
        cost += _calculate_cost_for_units(model + "-in", token_count)
    else:
        # Non-RAG mode: estimate based on full document text
        for doc in docs:
            if doc.extracted_text is not None:
                cost += _estimate_cost_of_string(doc.extracted_text, model + "-in")

    return cost


def _estimate_file_processing_cost(files: List[Any], cost_type: str) -> Decimal:
    """Estimate cost for processing files in summarize/translate modes."""
    cost = Decimal("0")

    for file in files:
        if file.text:
            # If file already has extracted text, use it for cost estimation
            cost += _estimate_cost_of_string(file.text, cost_type)
        else:
            # Estimate based on file properties
            estimated_tokens = _estimate_file_tokens(file)
            cost += _calculate_cost_for_units(cost_type, estimated_tokens)

    return cost


def _estimate_chat_mode_cost(chat: Any, model: str) -> Decimal:
    """Estimate cost for chat mode including system prompt and history."""
    from chat.utils import current_time_prompt, is_text_to_summarize

    system_prompt = current_time_prompt() + chat.options.chat_system_prompt
    history_text = system_prompt

    for message in chat.messages.all():
        if not is_text_to_summarize(message):
            history_text += message.text
        else:
            history_text += "<text to summarize...>"

    return _estimate_cost_of_string(history_text, model + "-in")


def estimate_cost_of_request(
    chat: Any, response_message: Any, response_estimation_count: int = 512
) -> Decimal:
    """
    Estimate the total cost of a chat request based on the mode and options.

    Args:
        chat: The chat instance
        response_message: The response message being generated
        response_estimation_count: Estimated token count for the response (default: 512)

    Returns:
        Decimal: Estimated cost in CAD
    """
    user_message = response_message.parent
    model = chat.options.chat_model
    mode = chat.options.mode
    cost = Decimal("0")

    # Estimate cost based on mode
    mode_to_func = {
        "translate": _estimate_translate_mode_cost,
        "qa": _estimate_qa_mode_cost,
        "summarize": _estimate_summarize_mode_cost,
        "chat": _estimate_chat_mode_cost,
    }
    if mode in mode_to_func:
        cost += mode_to_func[mode](chat, user_message)
    else:
        cost += _estimate_cost_of_string(user_message.text, model + "-in")

    # Add response cost (except for translate mode)
    if mode != "translate":
        cost += _calculate_cost_for_units(model + "-out", response_estimation_count)
        # Testing has shown that for non-translation modes, estimation is 20% below actual
        cost = cost + (cost * Decimal("0.2"))

    return cad_cost(cost)


def _estimate_translate_mode_cost(chat: Any, user_message: Any) -> Decimal:
    """Estimate cost for translate mode (GPT: tokens, Azure: characters)."""
    cost = Decimal("0")
    files = user_message.sorted_files.all()

    if chat.options.translate_model == "gpt":
        # User message cost (input tokens)
        input_tokens = _estimate_tokens_from_text(user_message.text)
        cost += _calculate_cost_for_units("gpt-4.1-mini-in", input_tokens)
        # Bot response cost (output tokens, same as input)
        cost += _calculate_cost_for_units("gpt-4.1-mini-out", input_tokens)

        # Files: cost for input and output tokens
        for file in files:
            if file.text:
                file_tokens = _estimate_tokens_from_text(file.text)
            else:
                file_tokens = _estimate_file_tokens(file)
            cost += _calculate_cost_for_units("gpt-4.1-mini-in", file_tokens)
            cost += _calculate_cost_for_units("gpt-4.1-mini-out", file_tokens)

    elif "azure" in chat.options.translate_model:
        # User message cost (input characters)
        input_chars = len(user_message.text)
        cost_type = _get_translate_cost_type(chat, user_message)
        cost += _calculate_cost_for_units(cost_type, input_chars)

        # Files: cost for input characters
        for file in files:
            if file.text:
                file_chars = len(file.text)
            else:
                file_tokens = _estimate_file_tokens(file)
                file_chars = file_tokens * EST_CHARS_PER_TOKEN
            cost += _calculate_cost_for_units(cost_type, file_chars)

    return cost


def _estimate_qa_mode_cost(chat: Any, user_message: Any) -> Decimal:
    """Estimate cost for QA mode."""
    cost = Decimal("0")
    model = chat.options.qa_model

    # Cost of user message
    cost += _estimate_cost_of_string(user_message.text, model + "-in")

    # Cost of document processing
    cost += _estimate_qa_documents_cost(chat, model)

    return cost


def _estimate_summarize_mode_cost(chat: Any, user_message: Any) -> Decimal:
    """Estimate cost for summarize mode."""
    cost = Decimal("0")
    model = chat.options.summarize_model

    # Cost of user message
    cost += _estimate_cost_of_string(user_message.text, model + "-in")

    # Cost of files to summarize
    files = user_message.sorted_files.all()
    cost += _estimate_file_processing_cost(files, model + "-in")

    return cost
