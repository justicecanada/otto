from unittest.mock import MagicMock, patch

from django.utils.html import escape

import pytest

from chat.utils import extract_claims_from_llm, highlight_claims, mark_sentences


def test_mark_sentences():
    text = (
        "This is a test sentence.\n"
        "This is another test sentence.\r\n"
        "This is a third test sentence with special characters: $^&*().\n"
        "This is a fourth test sentence.\n"
        "This is a fifth test sentence."
    )
    good_matches = [
        "This is a test sentence.",
        "This is a third test sentence with special characters: $^&*().",
        "This is a fifth test sentence.",
    ]

    expected_output = (
        "<mark>This is a test sentence.</mark>\n"
        "This is another test sentence.\n"
        "<mark>This is a third test sentence with special characters: $^&*().</mark>\n"
        "This is a fourth test sentence.\n"
        "<mark>This is a fifth test sentence.</mark>"
    )

    result = mark_sentences(text, good_matches)
    assert (
        result == expected_output
    ), f"Expected: {escape(expected_output)}, but got: {escape(result)}"


@pytest.mark.django_db
def test_highlight_claims():
    text = (
        "This is a test sentence.\n"
        "This is another test sentence.\n"
        "This is a third test sentence with special characters: $^&*().\n"
        "This is a fourth test sentence.\n"
        "This is a fifth test sentence."
    )
    claims_list = [
        "This is a test sentence.",
        "This is a third test sentence with special characters: $^&*().",
        "This is a fifth test sentence.",
    ]

    expected_output = (
        "<mark>This is a test sentence.</mark>\n"
        "This is another test sentence.\n"
        "<mark>This is a third test sentence with special characters: $^&*().</mark>\n"
        "This is a fourth test sentence.\n"
        "<mark>This is a fifth test sentence.</mark>"
    )

    with patch("chat.utils.OttoLLM") as MockOttoLLM:
        mock_llm = MockOttoLLM.return_value
        mock_retriever = MagicMock()
        mock_retriever.retrieve.side_effect = lambda claim: [
            (
                MagicMock(score=0.9, text=claim)
                if claim in claims_list
                else MagicMock(score=0.5, text=claim)
            )
        ]
        mock_llm.temp_index_from_nodes.return_value.as_retriever.return_value = (
            mock_retriever
        )

        result = highlight_claims(claims_list, text)
        assert (
            result == expected_output
        ), f"Expected: {expected_output}, but got: {result}"


def test_highlight_claims_with_no_matches():
    text = (
        "This is a test sentence.\n"
        "This is another test sentence.\n"
        "This is a third test sentence with special characters: $^&*().\n"
        "This is a fourth test sentence.\n"
        "This is a fifth test sentence."
    )
    claims_list = [
        "This is a non-matching claim.",
        "This is another non-matching claim.",
    ]

    expected_output = text

    with patch("chat.utils.OttoLLM") as MockOttoLLM:
        mock_llm = MockOttoLLM.return_value
        mock_retriever = MagicMock()
        mock_retriever.retrieve.side_effect = lambda claim: [
            MagicMock(score=0.5, text=claim)
        ]
        mock_llm.temp_index_from_nodes.return_value.as_retriever.return_value = (
            mock_retriever
        )

        result = highlight_claims(claims_list, text)
        assert (
            result == expected_output
        ), f"Expected: {expected_output}, but got: {result}"


def test_highlight_claims_with_threshold():
    text = (
        "This is a test sentence.\n"
        "This is another test sentence.\n"
        "This is a third test sentence with special characters: $^&*().\n"
        "This is a fourth test sentence.\n"
        "This is a fifth test sentence."
    )
    claims_list = [
        "This is a test sentence.",
        "This is a third test sentence with special characters: $^&*().",
        "This is a fifth test sentence.",
    ]

    expected_output = (
        "<mark>This is a test sentence.</mark>\n"
        "This is another test sentence.\n"
        "<mark>This is a third test sentence with special characters: $^&*().</mark>\n"
        "This is a fourth test sentence.\n"
        "<mark>This is a fifth test sentence.</mark>"
    )

    with patch("chat.utils.OttoLLM") as MockOttoLLM:
        mock_llm = MockOttoLLM.return_value
        mock_retriever = MagicMock()
        mock_retriever.retrieve.side_effect = lambda claim: [
            (
                MagicMock(score=0.9, text=claim)
                if claim in claims_list
                else MagicMock(score=0.5, text=claim)
            )
        ]
        mock_llm.temp_index_from_nodes.return_value.as_retriever.return_value = (
            mock_retriever
        )

        result = highlight_claims(claims_list, text, threshold=0.7)
        assert (
            result == expected_output
        ), f"Expected: {expected_output}, but got: {result}"


@pytest.mark.django_db
def test_extract_claims_from_llm_tags_present():
    llm_response_text = (
        "This is a factual statement.\n"
        "Here is another fact.\n"
        "This is a quote: 'To be or not to be.'\n"
        "This is an analysis and should not be included."
    )

    expected_claims = [
        "This is a factual statement.",
        "Here is another fact.",
        "This is a quote: 'To be or not to be.'",
    ]
    result = extract_claims_from_llm(llm_response_text)
    assert result == expected_claims, f"Expected: {expected_claims}, but got: {result}"
