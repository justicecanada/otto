from datetime import datetime

from django.utils.translation import gettext_lazy as _


def build_query_reformulation_prompt(
    qa_mode: str = "rag",
    qa_process_mode: str = "combined_docs",
    document_names: list[str] | None = None,
) -> str:
    """
    Build the query reformulation prompt dynamically based on Q&A mode settings.

    Args:
        qa_mode: "rag" (top excerpts) or "summarize" (full documents)
        qa_process_mode: "combined_docs" or "per_doc" (separate)
        document_names: List of document names in scope (for context)
    """
    is_rag = qa_mode == "rag"
    is_separate = qa_process_mode == "per_doc"

    # Build mode-specific instructions for llm_prompt
    if is_separate:
        llm_prompt_mode_instructions = """
   - IMPORTANT: The system is in "separate documents" mode. Your prompt will be applied to EACH document individually in parallel.
   - DO NOT reference specific document names or list multiple files - the prompt applies to ONE document at a time.
   - DO NOT say things like "summarize each of the documents" or "for each file" - just state what to do with "the document" or "this document".
   - Examples of CORRECT prompts in separate mode:
     * "Summarize this document with nice markdown formatting" (NOT "summarize each of the 3 documents")
     * "What are the key findings in this document?" (NOT "what are the key findings in Document A, B, and C?")
     * "Extract all dates mentioned in this document" (NOT "extract dates from the uploaded files")"""
    else:
        if is_rag:
            llm_prompt_mode_instructions = """
   - The system is in "combined" mode with RAG (retrieval). Your prompt will receive relevant excerpts from across all documents.
   - You may reference searching across documents or finding information from the collection.
   - IMPORTANT: Do NOT include specific document filenames unless the user's CURRENT question explicitly names them (or strongly implies, e.g. "the last document I uploaded").
   - The user can change which documents are selected via the sidebar, so filenames from conversation history may be outdated."""
        else:
            llm_prompt_mode_instructions = """
   - The system is in "combined full documents" mode. Your prompt will receive the full text of all documents combined.
   - IMPORTANT: Do NOT include specific document filenames from conversation history unless the user's CURRENT question explicitly names them.
   - The user can change which documents are selected at any time via the sidebar, so document names mentioned earlier in the conversation may no longer be in scope.
   - Keep generic references generic: "the documents", "the selected documents", "compare these documents" should NOT be expanded to include specific filenames.
   - Only include specific filenames if the user explicitly names them in their current question."""

    # Build mode-specific instructions for rag_query
    if is_rag:
        rag_query_instructions = """
3. rag_query: A concise search query optimized for hybrid search (keyword + semantic vector search).
   - This is used to find relevant document chunks in RAG mode
   - The keyword search does not support "exact matches" or boolean operators; it matches any word (OR logic)
   - Resolve pronouns/references to concrete referents from context
   - Include important keywords for keyword matching
   - Be specific enough for semantic similarity
   - ONLY contain search terms - NO formatting/response instructions
   - CRITICAL: Use ONLY terms and concepts EXPLICITLY present in the user's question or conversation history
   - DO NOT expand concepts based on LLM world knowledge (e.g., don't add related terms, synonyms, or assumed meanings)
   - Keep it concise - prefer the user's exact wording when it's already a good search query
   - It's OK to return the question verbatim if it's already a good search query
   - It's OK to simplify questions into better search terms using ONLY the words present
   - Only add context from history when the user's question contains pronouns or references that need resolution"""
    else:
        rag_query_instructions = """
3. rag_query: Not used in full documents mode. Return an empty string or the user's question."""

    # Build document context info
    if document_names and len(document_names) > 0:
        if is_separate:
            doc_context = f"\n\n<Document Context>\nThe user has {len(document_names)} document(s) CURRENTLY selected. In separate mode, each will be processed individually.\nNote: These may differ from documents mentioned in conversation history. Only reference specific filenames if the user's CURRENT question names them.\n</Document Context>"
        else:
            doc_list = ", ".join(f'"{name}"' for name in document_names[:10])
            if len(document_names) > 10:
                doc_list += f" (and {len(document_names) - 10} more)"
            doc_context = f"\n\n<Document Context>\nCurrently selected documents: {doc_list}\nIMPORTANT: These may differ from documents mentioned earlier in conversation history. Do NOT reference specific filenames unless the user's CURRENT question explicitly names them.\n</Document Context>"
    else:
        doc_context = ""

    prompt = _(
        """
Given a conversation history and a follow-up question, generate a structured query reformulation.

You must provide four outputs:

1. should_search: Boolean indicating whether document search (RAG or full documents) is needed.
   - CRITICAL: Any information-seeking question AT ALL must set this to True
   - Information-seeking questions include: questions seeking facts, explanations, details, definitions, data, or any form of knowledge
   - Even if a question seems broad or philosophical (e.g., "what is the meaning of life?"), if it seeks information, should_search MUST be True
   - The answer must come from the scope of the search results (either RAG sources or full documents in <context></context> tags)
   - Only set to False when the request is purely to reformat/reorganize a previous response without seeking any new information
   - Examples where should_search = True:
     * "What is X?" (seeks information)
     * "Tell me about Y" (seeks information)
     * "How does Z work?" (seeks information)
   - Examples where should_search = False:
     * "Can you make that a table?" (reformatting only)
     * "Put that in a bulleted list" (reformatting only)
     * "Translate the previous answer to French" (reformatting only)

2. llm_prompt: The reformulated question/instruction to pass to the LLM for generating the response.
   - CRITICAL: The LLM will receive ONLY this prompt and the retrieved document excerpts. It will NOT receive the conversation history separately.
   - CRITICAL: If there is NO meaningful conversation history (empty history or only system messages), you MUST return the user's question EXACTLY as written, word-for-word. DO NOT expand, elaborate, or add any details.
   - CRITICAL: You will be PENALIZED for adding information that is not explicitly present in either the user's question OR the conversation history. Stay strictly grounded.
   - If the question is self-contained and unrelated to history, return it verbatim
   - ONLY modify the question when it truly requires context from history to be answerable
   - When modifying, incorporate relevant context from history to make it self-contained
   - Otherwise, output the provided question EXACTLY as-is, word-for-word, with no changes
   - DO NOT add phrases like "using the provided conversation history" - the LLM won't have those
{llm_prompt_mode_instructions}

{rag_query_instructions}

4. history_answer: (ONLY when should_search=False) The complete, formatted answer to the user's question based ONLY on the conversation history.
   - CRITICAL: This field is ONLY filled when should_search=False (i.e., no document search needed)
   - When should_search=True, this field MUST be null/None
   - When should_search=False, provide a complete, well-formatted answer using ONLY information from the conversation history
   - The answer should directly fulfill the user's request (e.g., reformat as table, translate, create list, etc.)
   - Use proper markdown formatting for tables, lists, emphasis, etc.
{doc_context}
<Conversation History>
{{history_str}}

<Current Question>
{{user_question}}

Generate the structured reformulation:"""
    )

    return prompt.format(
        llm_prompt_mode_instructions=llm_prompt_mode_instructions,
        rag_query_instructions=rag_query_instructions,
        doc_context=doc_context,
    )


def current_time_prompt():
    return _("Current date:") + datetime.now().strftime("%Y-%m-%d") + "\n"


def chat_file_capabilities_prompt(include_images=True, include_pdfs=False):
    """
    Generate dynamic prompt text describing file capabilities based on toggle settings.

    Args:
        include_images: Whether images are being passed to the model
        include_pdfs: Whether PDFs are being passed to the model
    """
    if include_images and include_pdfs:
        file_types = _("images (PNG, JPG, GIF, WebP, BMP) and PDFs")
        can_read = _(
            "You CAN directly read and analyze images and PDFs uploaded to the current chat."
        )
    elif include_images:
        file_types = _("images (PNG, JPG, GIF, WebP, BMP)")
        can_read = _(
            "You CAN directly read and analyze images uploaded to the current chat. "
            "For PDFs, direct the user to enable the 'include uploaded PDFs' toggle or use Q&A or Summarize modes. "
            "For other document types, direct the user to Q&A or Summarize modes."
        )
    elif include_pdfs:
        file_types = _("PDFs")
        can_read = _(
            "You CAN directly read and analyze PDFs uploaded to the current chat. "
            "For images, direct the user to enable the 'include uploaded images' toggle. "
            "For other document types, direct the user to Q&A or Summarize modes."
        )
    else:
        # Neither enabled
        return _(
            """
---
FILE HANDLING CAPABILITIES (ACTIVE SETTINGS):
You currently CANNOT directly read files uploaded to the chat. The "Include images" and "Include PDFs" options are disabled in the settings.
- For document analysis, direct the user to Q&A or Summarize modes.
- If the user has uploaded files and expects you to read them, inform them that they need to enable the appropriate toggle in the Chat settings sidebar, or switch to Q&A/Summarize mode.
---
"""
        )

    return _(
        """
---
FILE HANDLING CAPABILITIES (ACTIVE SETTINGS):
You can read and analyze %(file_types)s that are uploaded to the current chat. When the user has uploaded supported files, base your answers on their content.
- %(can_read)s
- For unsupported file types (Word, Excel, etc.), direct users to Q&A or Summarize modes.
---
"""
    ) % {"file_types": file_types, "can_read": can_read}
