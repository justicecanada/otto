import pytest

from chat.prompts import build_query_reformulation_prompt


def test_build_query_prompt_separate_mode_instructions():
    prompt = build_query_reformulation_prompt(
        qa_mode="rag",
        qa_process_mode="per_doc",
        document_names=["Case Notes", "Meeting Minutes"],
    )

    assert 'The system is in "separate documents" mode' in prompt
    assert "applied to EACH document individually" in prompt
    assert "processed individually" in prompt
    assert (
        "Note: These may differ from documents mentioned in conversation history"
        in prompt
    )


def test_build_query_prompt_combined_full_documents_doc_list_truncation():
    document_names = [f"Doc {i}" for i in range(1, 13)]

    prompt = build_query_reformulation_prompt(
        qa_mode="summarize",
        qa_process_mode="combined_docs",
        document_names=document_names,
    )

    assert "combined full documents" in prompt
    assert "rag_query: Not used in full documents mode" in prompt

    expected_list_prefix = (
        'Currently selected documents: "Doc 1", "Doc 2", "Doc 3", "Doc 4", "Doc 5", '
        '"Doc 6", "Doc 7", "Doc 8", "Doc 9", "Doc 10" (and 2 more)'
    )
    assert expected_list_prefix in prompt


@pytest.mark.parametrize(
    "qa_mode,rag_instruction",
    [
        ("rag", "A concise search query optimized for hybrid search"),
        ("summarize", "Not used in full documents mode"),
    ],
)
def test_build_query_prompt_rag_query_instructions_vary_by_mode(
    qa_mode, rag_instruction
):
    prompt = build_query_reformulation_prompt(
        qa_mode=qa_mode,
        qa_process_mode="combined_docs",
        document_names=None,
    )

    assert rag_instruction in prompt
