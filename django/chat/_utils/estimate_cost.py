from decimal import Decimal
from typing import Any, List

from structlog import get_logger

from otto.models import CostType
from otto.utils.common import cad_cost

from librarian.models import Document

logger = get_logger(__name__)

EST_CHARS_PER_TOKEN = 4


def _estimate_tokens_from_text(text: str) -> int:
    """Estimate token count based on character count."""
    return len(text) // EST_CHARS_PER_TOKEN


def _calculate_cost_for_units(cost_type_name: str, unit_count: int) -> Decimal:
    """Calculate cost for a given number of units and cost type short name."""
    try:
        cost_type = CostType.objects.get(short_name=cost_type_name)
    except CostType.DoesNotExist:
        # TODO: add proper pricing support for all new models/providers.
        # For now, if we don't have a CostType configured (e.g., experimental
        # models like cohere-command-a or gpt-oss-120b), skip cost estimation
        # rather than raising and breaking the UI.
        logger.warning(
            "missing_cost_type",
            cost_type_name=cost_type_name,
            unit_count=unit_count,
        )
        return Decimal("0")

    return (unit_count * cost_type.unit_cost) / cost_type.unit_quantity


def _estimate_cost_of_string(text: str, cost_type: str) -> Decimal:
    """Estimate cost of a text string based on the cost type."""
    if cost_type.startswith("translate-"):
        count = len(text)
    else:
        count = _estimate_tokens_from_text(text)

    return _calculate_cost_for_units(cost_type, count)


def _get_translate_cost_type(chat: Any, user_message: Any) -> str:
    """Determine the appropriate cost type for translation mode."""
    translate_model = chat.options.translate_model

    if "gpt" in translate_model:
        return f"{translate_model}-in"
    elif translate_model == "azure_custom":
        return "translate-custom"
    elif translate_model == "azure":
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
        docs = list(chat.options.qa_documents.all())
    elif chat.options.qa_scope == "data_sources":
        docs_qs = (
            Document.objects.filter(data_source__in=chat.options.qa_data_sources.all())
            | chat.options.qa_additional_documents.all()
        ).distinct()

        excluded_document_ids = chat.options.qa_excluded_documents.values_list(
            "id", flat=True
        )
        docs_qs = docs_qs.exclude(id__in=excluded_document_ids)

        docs = list(docs_qs)
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

    return cost


def _estimate_chat_mode_cost(chat: Any, user_message: Any) -> Decimal:
    """Estimate cost for chat mode including system prompt and history."""
    from chat.utils import current_time_prompt, is_text_to_summarize

    model = chat.options.chat_model

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
    from chat._llm.models import get_model

    user_message = response_message.parent
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
        # For unknown modes, use chat model as fallback
        model = chat.options.chat_model
        cost += _estimate_cost_of_string(user_message.text, model + "-in")

    # Add response cost (except for translate mode)
    if mode != "translate":
        # Get the appropriate model based on mode
        if mode == "qa":
            model = chat.options.qa_model
            reasoning_effort = chat.options.qa_reasoning_effort
        elif mode == "summarize":
            model = chat.options.summarize_model
            reasoning_effort = chat.options.summarize_reasoning_effort
        elif mode == "chat":
            model = chat.options.chat_model
            reasoning_effort = chat.options.chat_reasoning_effort
        else:
            model = chat.options.chat_model  # fallback
            reasoning_effort = "minimal"

        # Check if model is a reasoning model and adjust token estimate
        llm_config = get_model(model)
        total_response_tokens = response_estimation_count
        if llm_config and llm_config.reasoning:
            # Reasoning models produce additional reasoning tokens based on effort level
            # These are charged at output token rates
            reasoning_multipliers = {
                "none": 0.00,  # No additional reasoning tokens
                "minimal": 0.05,  # 5% additional reasoning tokens
                "low": 0.25,  # 25% additional reasoning tokens
                "medium": 0.50,  # 50% additional reasoning tokens
                "high": 1.00,  # 100% additional reasoning tokens (doubles output)
            }
            multiplier = reasoning_multipliers.get(reasoning_effort, 0.05)
            reasoning_tokens = int(response_estimation_count * multiplier)
            total_response_tokens = response_estimation_count + reasoning_tokens

        cost += _calculate_cost_for_units(model + "-out", total_response_tokens)
        # Testing has shown that for non-translation modes, estimation is 20% below actual
        cost = cost + (cost * Decimal("0.2"))

    return cad_cost(cost)


def _estimate_translate_mode_cost(chat: Any, user_message: Any) -> Decimal:
    """Estimate cost for translate mode (GPT: tokens, Azure: characters)."""
    cost = Decimal("0")
    files = user_message.sorted_files.all()
    translate_model = chat.options.translate_model

    if "gpt" in translate_model:
        # User message cost (input tokens)
        input_tokens = _estimate_tokens_from_text(user_message.text)
        cost += _calculate_cost_for_units(f"{translate_model}-in", input_tokens)
        # Bot response cost (output tokens, same as input)
        cost += _calculate_cost_for_units(f"{translate_model}-out", input_tokens)

        # Files: cost for input and output tokens
        for file in files:
            if file.text:
                file_tokens = _estimate_tokens_from_text(file.text)
                cost += _calculate_cost_for_units(f"{translate_model}-in", file_tokens)
                cost += _calculate_cost_for_units(f"{translate_model}-out", file_tokens)

    elif "azure" in translate_model:
        # User message cost (input characters)
        input_chars = len(user_message.text)
        cost_type = _get_translate_cost_type(chat, user_message)
        cost += _calculate_cost_for_units(cost_type, input_chars)

        # Files: cost for input characters
        for file in files:
            if file.text:
                file_chars = len(file.text)
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
