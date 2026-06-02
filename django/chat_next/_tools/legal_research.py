import re

from structlog import get_logger

from chat_next._tools.approval import (
    ApprovalPolicy,
    custom_approval_rule,
    exact_argument_match,
)
from chat_next._tools.base import TOOL_REGISTRY, OttoTool, ToolContext
from chat_next._utils import a2aj as a2aj_client
from chat_next.models import (
    TOOL_CATEGORY_LEGAL_RESEARCH,
    TOOL_CATEGORY_LEGISLATION,
)

logger = get_logger(__name__)


SAFE_A2AJ_CASE_CITATION_PATTERN = re.compile(
    r"^(?:18|19|20)\d{2}\s+[A-Z]{2,10}\s+\d{1,6}$"
)
SAFE_A2AJ_LEGISLATION_CITATION_PATTERN = re.compile(
    r"^(?:"
    r"(?:RSC|LRC)\s+(?:18|19|20)\d{2}\s*,\s*(?:c|ch)\s+[A-Z0-9][A-Z0-9.-]*"
    r"|(?:SC|LC)\s+(?:18|19|20)\d{2}\s*,\s*(?:c|ch)\s+\d+[A-Z0-9.-]*"
    r"|(?:SOR|DORS)/(?:18|19|20)\d{2}-\d+[A-Z0-9.-]*"
    r")$"
)
SAFE_A2AJ_SECTION_PATTERN = re.compile(r"^\d+[A-Za-z]?(?:\.\d+)*(?:\([0-9A-Za-z]+\))*$")
SAFE_A2AJ_DATASET_FILTER_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]*(?:,[A-Z][A-Z0-9-]*)*$")
SAFE_A2AJ_DATE_FILTER_PATTERN = re.compile(r"^(?:18|19|20)\d{2}-\d{2}-\d{2}$")
SAFE_A2AJ_CASE_BROWSE_SEED_QUERIES = {
    "v.",
    "R.",
    "Canada",
    "Attorney General",
}
SAFE_A2AJ_LEGISLATION_BROWSE_SEEDS = {
    ("Act", "LEGISLATION-FED"),
    ("Regulations", "REGULATIONS-FED"),
    ("Order", "REGULATIONS-FED"),
}
MAX_SAFE_A2AJ_INTEGER_ARGUMENT = 100_000_000


def _coerce_int(value, default: int) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _uses_safe_small_integer_argument(
    value: int | None, *, allow_minus_one: bool
) -> bool:
    if value is None:
        return False
    if allow_minus_one and value == -1:
        return True
    return 0 <= value < MAX_SAFE_A2AJ_INTEGER_ARGUMENT


def _uses_safe_a2aj_fetch_window(arguments: dict) -> bool:
    start_char = _coerce_int(arguments.get("start_char"), 0)
    end_char = _coerce_int(
        arguments.get("end_char"), a2aj_client.DEFAULT_A2AJ_FETCH_END_CHAR
    )
    return _uses_safe_small_integer_argument(
        start_char, allow_minus_one=False
    ) and _uses_safe_small_integer_argument(end_char, allow_minus_one=True)


def _uses_safe_output_language(arguments: dict) -> bool:
    return arguments.get("output_language", "en") in {"en", "fr", "both"}


def _uses_safe_search_language(arguments: dict) -> bool:
    return arguments.get("search_language", "en") == "en"


def _uses_safe_a2aj_search_size(arguments: dict) -> bool:
    size = _coerce_int(arguments.get("size"), 5)
    return size is not None and 1 <= size <= a2aj_client.MAX_A2AJ_SEARCH_RESULTS


def _uses_safe_a2aj_dataset_filter(value: str | None, *, allow_blank: bool) -> bool:
    dataset = (value or "").strip()
    if not dataset:
        return allow_blank
    return bool(SAFE_A2AJ_DATASET_FILTER_PATTERN.fullmatch(dataset))


def _uses_safe_a2aj_date_filters(arguments: dict) -> bool:
    start_date = (arguments.get("start_date") or "").strip()
    end_date = (arguments.get("end_date") or "").strip()
    if start_date and not SAFE_A2AJ_DATE_FILTER_PATTERN.fullmatch(start_date):
        return False
    if end_date and not SAFE_A2AJ_DATE_FILTER_PATTERN.fullmatch(end_date):
        return False
    if start_date and end_date:
        return start_date <= end_date
    return True


def _is_safe_case_fetch_for_auto_approval(context) -> bool:
    arguments = context.arguments
    citation = (arguments.get("citation") or "").strip()
    return (
        bool(SAFE_A2AJ_CASE_CITATION_PATTERN.fullmatch(citation))
        and _uses_safe_output_language(arguments)
        and _uses_safe_a2aj_fetch_window(arguments)
    )


def _is_safe_legislation_fetch_for_auto_approval(context) -> bool:
    arguments = context.arguments
    citation = (arguments.get("citation") or "").strip()
    section = (arguments.get("section") or "").strip()
    if not SAFE_A2AJ_LEGISLATION_CITATION_PATTERN.fullmatch(citation):
        return False
    if not _uses_safe_output_language(arguments):
        return False
    if not _uses_safe_a2aj_fetch_window(arguments):
        return False
    if not section:
        return True
    return bool(SAFE_A2AJ_SECTION_PATTERN.fullmatch(section))


def _is_safe_case_browse_seed_search_for_auto_approval(context) -> bool:
    arguments = context.arguments
    query = (arguments.get("query") or "").strip()
    return (
        query in SAFE_A2AJ_CASE_BROWSE_SEED_QUERIES
        and arguments.get("search_type", "full_text") == "name"
        and arguments.get("sort_results", "default") == "newest_first"
        and _uses_safe_search_language(arguments)
        and _uses_safe_a2aj_search_size(arguments)
        and _uses_safe_a2aj_dataset_filter(arguments.get("dataset"), allow_blank=True)
        and _uses_safe_a2aj_date_filters(arguments)
    )


def _is_safe_legislation_browse_seed_search_for_auto_approval(context) -> bool:
    arguments = context.arguments
    query = (arguments.get("query") or "").strip()
    dataset = (arguments.get("dataset") or "").strip()
    return (
        (query, dataset) in SAFE_A2AJ_LEGISLATION_BROWSE_SEEDS
        and arguments.get("search_type", "full_text") == "name"
        and arguments.get("sort_results", "default") == "newest_first"
        and _uses_safe_search_language(arguments)
        and _uses_safe_a2aj_search_size(arguments)
        and _uses_safe_a2aj_date_filters(arguments)
    )


def _trim_redundant_law_nodes(nodes):
    """
    Remove redundant law nodes by preferring parent sections over child sections.

    When both a parent section and its child section are in the results,
    keep only the parent (which contains the child's content).
    """
    trimmed_nodes = []
    added_ids = set()
    section_ids = set(
        [node.metadata.get("section_id") for node in nodes if node.metadata]
    )

    while nodes:
        node = nodes.pop(0)
        metadata = node.metadata or {}
        section_id = metadata.get("section_id")

        if section_id in added_ids:
            continue
        elif section_id in section_ids:
            # Find the parent node in the nodes list, remove it, and make it the "node"
            parent_id = metadata.get("parent_id")
            parent_index = next(
                (
                    i
                    for i, n in enumerate(nodes)
                    if n.metadata and n.metadata.get("section_id") == parent_id
                ),
                None,
            )
            if parent_index is not None:
                node = nodes.pop(parent_index)
                section_id = node.metadata.get("section_id")

        trimmed_nodes.append(node)
        added_ids.add(section_id)

    return trimmed_nodes


async def search_laws(arguments: dict, context: ToolContext) -> dict:
    """
    Search Canadian federal legislation (Acts and Regulations).

    Uses vector search to find relevant sections and subsections.
    Returns section text with metadata but not full law text.
    """
    from asgiref.sync import sync_to_async
    from llama_index.core.schema import MetadataMode
    from llama_index.core.vector_stores import MetadataFilter, MetadataFilters

    from chat._llm import OttoLLM

    query = arguments.get("query", "")
    top_k = min(arguments.get("top_k", 5), 10)  # Cap at 10 to limit context size

    if not query:
        return {"error": "query is required"}

    @sync_to_async
    def do_retrieval():
        try:
            # Only retrieve law chunks (not full docs)
            filters = MetadataFilters(
                filters=[
                    MetadataFilter(
                        key="node_type",
                        value="chunk",
                        operator="==",
                    )
                ]
            )

            llm = OttoLLM()
            retriever = llm.get_retriever(
                vector_store_table="laws_lois__",
                filters=filters,
                top_k=top_k * 2,  # Retrieve more for redundancy trimming
                vector_weight=0.6,  # Hybrid search: 60% vector, 40% keyword
                hnsw=True,  # Use HNSW index for performance
            )

            source_nodes = retriever.retrieve(query)

            if not source_nodes:
                return [], None

            # Trim redundant nodes (prefer parent sections)
            trimmed_nodes = _trim_redundant_law_nodes(source_nodes)

            # Limit to requested top_k after trimming
            trimmed_nodes = trimmed_nodes[:top_k]

            # Format results for the AI
            results = []
            for node in trimmed_nodes:
                try:
                    # Get the formatted text with metadata
                    section_text = node.get_content(metadata_mode=MetadataMode.LLM)
                    metadata = node.metadata or {}

                    results.append(
                        {
                            "text": section_text,
                            "score": round(node.score, 3)
                            if hasattr(node, "score") and node.score
                            else None,
                            "law_title": metadata.get("file_id", ""),
                            "section": metadata.get("section", ""),
                            "headings": metadata.get("headings", ""),
                            "section_id": metadata.get("section_id", ""),
                            "language": metadata.get("lang", ""),
                        }
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to format law section",
                        error=str(e),
                        section_id=metadata.get("section_id"),
                    )
                    continue

            return results, None

        except Exception as e:
            logger.exception("Laws search failed", error=str(e))
            return None, f"Search failed: {str(e)}"

    results, error = await do_retrieval()
    if error:
        return {"error": error}

    if not results:
        return {
            "results": [],
            "message": "No relevant legislation found for your query.",
        }

    return {
        "results": results,
        "query": query,
        "count": len(results),
    }


# =========================================================================
# Canadian Case Law Tool (A2AJ public API)
# =========================================================================


async def search_canadian_case_law(arguments: dict, context: ToolContext) -> dict:
    """Search Canadian case law and tribunal decisions via the A2AJ API."""
    query = (arguments.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    search_type = arguments.get("search_type", "full_text")
    if search_type not in ("full_text", "name"):
        return {"error": "search_type must be 'full_text' or 'name'"}

    search_language = arguments.get("search_language", "en")
    if search_language not in ("en", "fr"):
        return {"error": "search_language must be 'en' or 'fr'"}

    sort_results = arguments.get("sort_results", "default")
    if sort_results not in ("default", "newest_first", "oldest_first"):
        return {
            "error": (
                "sort_results must be 'default', 'newest_first', or 'oldest_first'"
            )
        }

    size = arguments.get("size", 5)
    try:
        size = int(size)
    except (TypeError, ValueError):
        return {"error": "size must be an integer between 1 and 50"}
    if size < 1 or size > a2aj_client.MAX_A2AJ_SEARCH_RESULTS:
        return {"error": "size must be an integer between 1 and 50"}

    return a2aj_client.search_cases(
        query=query,
        search_type=search_type,
        size=size,
        search_language=search_language,
        sort_results=sort_results,
        dataset=(arguments.get("dataset") or "").strip(),
        start_date=(arguments.get("start_date") or None),
        end_date=(arguments.get("end_date") or None),
    )


async def list_canadian_legal_datasets(arguments: dict, context: ToolContext) -> dict:
    """List available A2AJ datasets with counts and date coverage."""
    doc_type = (arguments.get("doc_type") or "both").strip()
    if doc_type not in (*a2aj_client.VALID_A2AJ_DOC_TYPES, "both"):
        return {"error": "doc_type must be 'cases', 'laws', or 'both'"}

    return a2aj_client.list_dataset_coverage(doc_type=doc_type)


async def fetch_canadian_case_by_citation(
    arguments: dict, context: ToolContext
) -> dict:
    """Fetch a Canadian case by citation via the A2AJ API."""
    citation = (arguments.get("citation") or "").strip()
    if not citation:
        return {"error": "citation is required"}

    output_language = arguments.get("output_language", "en")
    if output_language not in ("en", "fr", "both"):
        return {"error": "output_language must be 'en', 'fr', or 'both'"}

    start_char = arguments.get("start_char", 0)
    end_char = arguments.get("end_char", a2aj_client.DEFAULT_A2AJ_FETCH_END_CHAR)
    try:
        start_char = int(start_char)
        end_char = int(end_char)
    except (TypeError, ValueError):
        return {"error": "start_char and end_char must be integers"}
    if start_char < 0:
        return {"error": "start_char must be zero or greater"}
    if end_char != -1 and end_char <= start_char:
        return {"error": "end_char must be greater than start_char or -1"}

    return a2aj_client.fetch_case_by_citation(
        citation=citation,
        output_language=output_language,
        start_char=start_char,
        end_char=end_char,
    )


async def search_canadian_legislation(arguments: dict, context: ToolContext) -> dict:
    """Search Canadian legislation and regulations available via the A2AJ API."""
    query = (arguments.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    search_type = arguments.get("search_type", "full_text")
    if search_type not in ("full_text", "name"):
        return {"error": "search_type must be 'full_text' or 'name'"}

    search_language = arguments.get("search_language", "en")
    if search_language not in ("en", "fr"):
        return {"error": "search_language must be 'en' or 'fr'"}

    sort_results = arguments.get("sort_results", "default")
    if sort_results not in ("default", "newest_first", "oldest_first"):
        return {
            "error": (
                "sort_results must be 'default', 'newest_first', or 'oldest_first'"
            )
        }

    size = arguments.get("size", 5)
    try:
        size = int(size)
    except (TypeError, ValueError):
        return {"error": "size must be an integer between 1 and 50"}
    if size < 1 or size > a2aj_client.MAX_A2AJ_SEARCH_RESULTS:
        return {"error": "size must be an integer between 1 and 50"}

    return a2aj_client.search_laws(
        query=query,
        search_type=search_type,
        size=size,
        search_language=search_language,
        sort_results=sort_results,
        dataset=(arguments.get("dataset") or "").strip(),
        start_date=(arguments.get("start_date") or None),
        end_date=(arguments.get("end_date") or None),
    )


async def fetch_canadian_legislation_by_citation(
    arguments: dict, context: ToolContext
) -> dict:
    """Fetch Canadian legislation or regulations by citation via the A2AJ API."""
    citation = (arguments.get("citation") or "").strip()
    if not citation:
        return {"error": "citation is required"}

    output_language = arguments.get("output_language", "en")
    if output_language not in ("en", "fr", "both"):
        return {"error": "output_language must be 'en', 'fr', or 'both'"}

    section = (arguments.get("section") or "").strip()

    start_char = arguments.get("start_char", 0)
    end_char = arguments.get("end_char", a2aj_client.DEFAULT_A2AJ_FETCH_END_CHAR)
    try:
        start_char = int(start_char)
        end_char = int(end_char)
    except (TypeError, ValueError):
        return {"error": "start_char and end_char must be integers"}
    if start_char < 0:
        return {"error": "start_char must be zero or greater"}
    if end_char != -1 and end_char <= start_char and not section:
        return {"error": "end_char must be greater than start_char or -1"}

    return a2aj_client.fetch_law_by_citation(
        citation=citation,
        output_language=output_language,
        section=section,
        start_char=start_char,
        end_char=end_char,
    )


# Register Laws Search tool

TOOL_REGISTRY.register(
    OttoTool(
        name="search_laws",
        description=(
            "Search Canadian federal legislation (Acts and Regulations)."
            # "Uses vector search to find relevant sections and subsections. "
            # "This searches the official consolidated laws and regulations database. "
            # "Returns section text with metadata (law title, section number, headings) "
            # "but not full law text. Use natural language queries describing the legal "
            # "topic, question, or keywords you're looking for."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query - legal topic, question, keywords, or law name to search for",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (1-10).",
                    "default": 5,
                },
            },
            "required": ["query", "top_k"],
            "additionalProperties": False,
        },
        execute=search_laws,
        category=TOOL_CATEGORY_LEGISLATION,
        requires_user=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="search_canadian_case_law",
        description=(
            "Search Canadian case law and tribunal decisions using A2AJ's public legal data API. "
            "Use this for case names, legal issues, keywords, and broader case-law research."
            # "If you need available dataset codes first, call list_canadian_legal_datasets. "
            # "Supports full_text and name search, English/French queries, advanced query syntax, date filters, and optional dataset filters. "
            # 'Advanced syntax examples include Charter AND discrimination, "constructive dismissal", '
            # "(duty OR obligation) AND consult*, and privacy NEAR/5 workplace. "
            # "French Boolean operators ET/OU/NON are also supported. "
            # "Returns compact case metadata and snippets. "
            # "Default sort uses relevance plus A2AJ dataset boosting unless you explicitly sort by newest or oldest. "
            # "Use to identify candidates before fetching full reasons.\n"
            # "**Query hygiene / broad-browse fallback:** "
            # "Do not call this tool with blank, whitespace-only, or stopword-only queries. "
            # "If the user wants “any recent case” and gives no topic, use a broad title search instead of a stopword query: "
            # 'prefer `search_type="name"` with `sort_results="newest_first"` and a high-frequency case-title token appropriate to the target dataset, '
            # 'such as `query="v."` for general English case titles, `query="R."` for criminal cases, '
            # 'or `query="Canada"` / `query="Attorney General"` for recent SCC public-law cases. '
            # "Treat these as browse seeds, not exhaustive retrieval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The case-law query: issue, keyword, party name, or citation fragment.",
                    # "Advanced syntax is supported, including AND/OR/NOT, ET/OU/NON, quoted phrases, parentheses, wildcard suffixes like consult*, and proximity such as privacy NEAR/5 workplace.",
                },
                "search_type": {
                    "type": "string",
                    "enum": ["full_text", "name"],
                    "description": "Use 'full_text' for content search or 'name' for case-title search.",
                    "default": "full_text",
                },
                "size": {
                    "type": "integer",
                    "description": "Number of results to return (1-50).",
                    "default": 5,
                },
                "search_language": {
                    "type": "string",
                    "enum": ["en", "fr"],
                    "description": "",
                    "default": "en",
                },
                "sort_results": {
                    "type": "string",
                    "enum": ["default", "newest_first", "oldest_first"],
                    "description": "Sort order. Default uses A2AJ relevance plus dataset boosting.",
                    "default": "default",
                },
                "dataset": {
                    "type": "string",
                    "description": "Optional comma-separated dataset filter such as 'SCC,ONCA'.",
                },
                "start_date": {
                    "type": "string",
                    "description": "Optional start date filter in YYYY-MM-DD format.",
                },
                "end_date": {
                    "type": "string",
                    "description": "Optional end date filter in YYYY-MM-DD format.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        execute=search_canadian_case_law,
        category=TOOL_CATEGORY_LEGAL_RESEARCH,
        requires_user=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
        strict=False,
        requires_approval=True,
        is_external_tool=True,
        external_service_name="A2AJ",
        approval_policy=ApprovalPolicy(
            rules=[
                custom_approval_rule(
                    _is_safe_case_browse_seed_search_for_auto_approval,
                    rule_name="a2aj_safe_case_browse_seed_search",
                )
            ]
        ),
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="list_canadian_legal_datasets",
        description=(
            "List available A2AJ datasets with document counts and date ranges."
            # "Use this when the user asks what datasets are available in A2AJ, wants "
            # "valid dataset codes before filtering, or wants to understand coverage for "
            # "case law versus legislation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": ["cases", "laws", "both"],
                    "description": "Dataset family to list.",
                    "default": "both",
                },
            },
            "additionalProperties": False,
        },
        execute=list_canadian_legal_datasets,
        category=TOOL_CATEGORY_LEGAL_RESEARCH,
        requires_user=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
        strict=False,
        requires_approval=True,
        allow_auto_approve=True,
        is_external_tool=True,
        external_service_name="A2AJ",
        approval_policy=ApprovalPolicy(
            rules=[
                exact_argument_match(
                    "doc_type",
                    allowed_values={"cases", "laws", "both"},
                    rule_name="a2aj_coverage_doc_type_safe_list",
                )
            ]
        ),
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="fetch_canadian_case_by_citation",
        description=(
            "Fetch a Canadian case by citation (e.g. '2020 SCC 5') using A2AJ's public legal data API. "
            # "Use this after search_canadian_case_law or when the user gives a citation like '2020 SCC 5'. "
            # "Returns compact case metadata plus an unofficial text excerpt. "
            # "The default first-pass window is 25000 chars and is usually appropriate for screening. "
            # "If the case is clearly relevant, prefer one larger follow-up fetch—or end_char=-1 when you want the rest/full text—instead of many small contiguous slices. "
            # "Because this tool may require user approval, aim to finish a case in about 1-2 fetches total when possible."
        ),
        parameters={
            "type": "object",
            "properties": {
                "citation": {
                    "type": "string",
                    "description": "Case citation in English or French.",
                },
                "output_language": {
                    "type": "string",
                    "enum": ["en", "fr", "both"],
                    "description": "",
                    "default": "en",
                },
                "start_char": {
                    "type": "integer",
                    "description": "",
                    "default": 0,
                },
                "end_char": {
                    "type": "integer",
                    "description": "",
                    "default": 25000,
                },
            },
            "required": ["citation"],
            "additionalProperties": False,
        },
        execute=fetch_canadian_case_by_citation,
        category=TOOL_CATEGORY_LEGAL_RESEARCH,
        requires_user=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
        strict=False,
        requires_approval=True,
        allow_auto_approve=False,
        is_external_tool=True,
        external_service_name="A2AJ",
        approval_policy=ApprovalPolicy(
            rules=[
                custom_approval_rule(
                    _is_safe_case_fetch_for_auto_approval,
                    rule_name="a2aj_safe_case_citation_fetch",
                )
            ]
        ),
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="search_canadian_legislation",
        description=(
            "Search Canadian legislation and regulations available through A2AJ's public legal data API. "
            "Use this for law names, citations, section topics, and broader legislation research."
            # "across the currently available federal, Ontario, and British Columbia datasets. "
            # "If you need available dataset codes first, call list_canadian_legal_datasets. "
            # "Supports full_text and name search, English/French queries, advanced query syntax, date filters, and optional dataset filters. "
            # 'Advanced syntax examples include "duty to accommodate", (employment OR labour) AND reprisal, privacy NEAR/5 workplace, and French queries using ET/OU/NON or EXACT(...). '
            # "Default sort uses relevance plus A2AJ dataset boosting unless you explicitly sort by newest or oldest. "
            # "Returns compact legislation metadata and snippets with repetitive upstream license metadata removed.\n"
            # "**Query hygiene / broad-browse fallback:** "
            # "Do not call this tool with blank, whitespace-only, or stopword-only queries. "
            # 'If the user wants “any recent law” and gives no topic, use a broad title search with `search_type="name"` and `sort_results="newest_first"` plus a dataset-appropriate title token: '
            # 'prefer `query="Act"` with `dataset="LEGISLATION-FED"` for recent federal statutes, and `query="Regulations"` or `query="Order"` with `dataset="REGULATIONS-FED"` for recent federal regulations/orders. '
            # 'Avoid using bare stopwords or an unfiltered `query="Act"` browse when the user likely wants newly enacted legislation rather than recently updated consolidated statutes.'
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The legislation query: Act/regulation name, issue, topic, or citation fragment.",
                    # "Advanced syntax is supported, including AND/OR/NOT, ET/OU/NON, quoted phrases, parentheses, wildcard suffixes, proximity, and French EXACT(...).",
                },
                "search_type": {
                    "type": "string",
                    "enum": ["full_text", "name"],
                    "description": "Use 'full_text' for content search or 'name' for title search.",
                    "default": "full_text",
                },
                "size": {
                    "type": "integer",
                    "description": "Number of results to return (1-50).",
                    "default": 5,
                },
                "search_language": {
                    "type": "string",
                    "enum": ["en", "fr"],
                    "description": "Search language: 'en' or 'fr'.",
                    "default": "en",
                },
                "sort_results": {
                    "type": "string",
                    "enum": ["default", "newest_first", "oldest_first"],
                    "description": "Sort order. Default uses A2AJ relevance plus dataset boosting.",
                    "default": "default",
                },
                "dataset": {
                    "type": "string",
                    "description": "Optional comma-separated dataset filter such as 'LEGISLATION-FED,REGULATIONS-FED' or Ontario/British Columbia dataset codes returned by list_canadian_legal_datasets.",
                },
                "start_date": {
                    "type": "string",
                    "description": "Optional start date filter in YYYY-MM-DD format.",
                },
                "end_date": {
                    "type": "string",
                    "description": "Optional end date filter in YYYY-MM-DD format.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        execute=search_canadian_legislation,
        category=TOOL_CATEGORY_LEGAL_RESEARCH,
        requires_user=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
        strict=False,
        requires_approval=True,
        is_external_tool=True,
        external_service_name="A2AJ",
        approval_policy=ApprovalPolicy(
            rules=[
                custom_approval_rule(
                    _is_safe_legislation_browse_seed_search_for_auto_approval,
                    rule_name="a2aj_safe_legislation_browse_seed_search",
                )
            ]
        ),
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="fetch_canadian_legislation_by_citation",
        description=(
            "Fetch Canadian legislation or regulations by citation (e.g. 'RSC 1985, c C-46') using A2AJ's public legal data API. "
            "Use this *after* search_canadian_legislation or when the user gives a citation like 'RSC 1985, c C-46'."
            # "This tool covers legislation available through A2AJ, including current federal, Ontario, and British Columbia datasets. "
            # "For legislation, prefer section-specific fetches when the user identifies a section. Otherwise the default first-pass window is 25000 chars, and if you truly need the rest/full text you should prefer end_char=-1 or one large continuation instead of many small contiguous slices."
        ),
        parameters={
            "type": "object",
            "properties": {
                "citation": {
                    "type": "string",
                    "description": "Legislation citation in English or French (e.g. 'RSC 1985, c C-46').",
                },
                "output_language": {
                    "type": "string",
                    "enum": ["en", "fr", "both"],
                    "default": "en",
                    "description": "",
                },
                "section": {
                    "type": "string",
                    "description": "Optional section to fetch directly, such as '219'.",
                },
                "start_char": {
                    "type": "integer",
                    "description": "",
                    "default": 0,
                },
                "end_char": {
                    "type": "integer",
                    "description": "",
                    "default": 25000,
                },
            },
            "required": ["citation"],
            "additionalProperties": False,
        },
        execute=fetch_canadian_legislation_by_citation,
        category=TOOL_CATEGORY_LEGAL_RESEARCH,
        requires_user=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
        strict=False,
        requires_approval=True,
        allow_auto_approve=False,
        is_external_tool=True,
        external_service_name="A2AJ",
        approval_policy=ApprovalPolicy(
            rules=[
                custom_approval_rule(
                    _is_safe_legislation_fetch_for_auto_approval,
                    rule_name="a2aj_safe_legislation_citation_fetch",
                )
            ]
        ),
    )
)
