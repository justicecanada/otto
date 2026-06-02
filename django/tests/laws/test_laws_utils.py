from unittest import mock

from django.core.cache import cache

import pytest

from laws.utils import (
    format_llm_string,
    get_law_url,
    get_other_lang_node,
    html_render,
    htmx_sse_error,
    htmx_sse_response,
)

def test_get_other_lang_node():
    """Ensure get_other_lang_node transforms node IDs between eng/fra and calls get_source_node."""
    with mock.patch("laws.utils.get_source_node") as mock_get_source_node:
        mock_get_source_node.return_value = {
            "text": "text",
            "metadata": {"key": "value"},
        }

        # English -> French replacements
        node_id = "LAW_eng_SCHEDULE_section_1"
        res = get_other_lang_node(node_id)
        assert res == {"text": "text", "metadata": {"key": "value"}}
        mock_get_source_node.assert_called_with("LAW_fra_ANNEXE_section_1")

        node_id = "LAW_eng_FORM_section_2"
        res = get_other_lang_node(node_id)
        assert res == {"text": "text", "metadata": {"key": "value"}}
        mock_get_source_node.assert_called_with("LAW_fra_FORMULE_section_2")

        # French -> English replacements
        node_id = "LAW_fra_ANNEXE_section_3"
        res = get_other_lang_node(node_id)
        assert res == {"text": "text", "metadata": {"key": "value"}}
        mock_get_source_node.assert_called_with("LAW_eng_SCHEDULE_section_3")

        node_id = "LAW_fra_FORMULE_section_4"
        res = get_other_lang_node(node_id)
        assert res == {"text": "text", "metadata": {"key": "value"}}
        mock_get_source_node.assert_called_with("LAW_eng_FORM_section_4")


def test_get_law_url():
    """Test URL generation for various law types without making actual HTTP requests."""
    law = mock.MagicMock()
    # Constitution
    law.ref_number_en = "Const"
    law.ref_number_fr = "Const"
    law.type = "act"
    url = get_law_url(law, "eng")
    assert url == "https://laws-lois.justice.gc.ca/eng/Const/Const_index.html"
    url_fra = get_law_url(law, "fra")
    assert url_fra == "https://laws-lois.justice.gc.ca/fra/ConstRpt/Const_index.html"

    # Regulation with C.R.C.
    law = mock.MagicMock()
    law.ref_number_en = "C.R.C., c. 1314"
    law.ref_number_fr = "C.R.C., ch. 1314"
    law.type = "regulation"
    url = get_law_url(law, "eng")
    assert url == "https://laws-lois.justice.gc.ca/eng/regulations/C.R.C.,_c._1314/"
    url_fra = get_law_url(law, "fra")
    assert url_fra == "https://laws-lois.justice.gc.ca/fra/reglements/C.R.C.,_ch._1314/"

    # Regulation SOR/2010-203
    law = mock.MagicMock()
    law.ref_number_en = "SOR/2010-203"
    law.ref_number_fr = "DORS/2010-203"
    law.type = "regulation"
    url = get_law_url(law, "eng")
    assert url == "https://laws-lois.justice.gc.ca/eng/regulations/SOR-2010-203/"
    url_fra = get_law_url(law, "fra")
    assert url_fra == "https://laws-lois.justice.gc.ca/fra/reglements/DORS-2010-203/"

    # Regulation SI/2006-79
    law = mock.MagicMock()
    law.ref_number_en = "SI/2006-79"
    law.ref_number_fr = "TR/2006-79"
    law.type = "regulation"
    url = get_law_url(law, "eng")
    assert url == "https://laws-lois.justice.gc.ca/eng/regulations/SI-2006-79/"
    # response = requests.get(url)
    # assert response.status_code == 200
    url_fra = get_law_url(law, "fra")
    assert url_fra == "https://laws-lois.justice.gc.ca/fra/reglements/TR-2006-79/"
    # response = requests.get(url_fra)
    # assert response.status_code == 200

    # Act
    law = mock.MagicMock()
    law.ref_number_en = "A-11.31"
    law.ref_number_fr = "A-11.31"
    law.type = "act"
    url = get_law_url(law, "eng")
    assert url == "https://laws-lois.justice.gc.ca/eng/acts/A-11.31/"
    # response = requests.get(url)
    # assert response.status_code == 200
    url_fra = get_law_url(law, "fra")
    assert url_fra == "https://laws-lois.justice.gc.ca/fra/lois/A-11.31/"
    # response = requests.get(url_fra)
    # assert response.status_code == 200


wrap_llm_response = mock.MagicMock(return_value="wrapped_response")


def test_format_llm_string():
    llm_string = "Hello **world**"
    with mock.patch(
        "chat.utils.wrap_llm_response",
        return_value='<div class="markdown-text" data-md="Hello **world**"></div>',
    ):
        result = format_llm_string(llm_string)
        expected = 'data: <div><div class="markdown-text" data-md="&quot;Hello **world**&quot;"></div></div>\n\n'
        assert result == expected

    llm_string = "Hello **world"
    with mock.patch(
        "chat.utils.wrap_llm_response",
        return_value='<div class="markdown-text" data-md="Hello **world"></div>',
    ):
        result = format_llm_string(llm_string)
        expected = 'data: <div><div class="markdown-text" data-md="&quot;Hello **world**&quot;"></div></div>\n\n'
        assert result == expected


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_htmx_sse_response():
    response_gen = iter(["Hello", " world"])
    llm = mock.MagicMock()
    llm.create_costs = mock.MagicMock(return_value=10.0)
    query_uuid = "test_uuid"
    cache.set(query_uuid, {"answer": ""})

    with mock.patch("otto.utils.common.display_cad_cost", return_value="display_cost"):
        result = []
        async for item in htmx_sse_response(response_gen, llm, query_uuid):
            result.append(item)

        expected = [
            'data: <div><div class="markdown-text" data-md="&quot;Hello&quot;"></div></div>\n\n',
            'data: <div><div class="markdown-text" data-md="&quot;Hello world&quot;"></div></div>\n\n',
            "data: <div hx-swap-oob='true' id='answer-sse'><div class=\"markdown-text\" data-md=\"&quot;Hello world&quot;\"></div></div><div id='answer-cost' hx-swap-oob='true'><div class='mb-2 text-muted' style='font-size:0.875rem !important;'>$13.80</div></div>\n\n",
            "event: done\ndata: complete\n\n",
        ]

        assert result == expected


@pytest.mark.asyncio
async def test_htmx_sse_error():
    result = []
    async for item in htmx_sse_error():
        result.append(item)

    # Check if the result contains the expected string
    expected_substring = "An error occurred while processing the request."
    assert any(expected_substring in item for item in result), (
        f"Expected substring '{expected_substring}' not found in result: {result}"
    )


@pytest.mark.asyncio
def test_html_render():
    import markdown

    md = markdown.Markdown(extensions=["fenced_code", "nl2br", "tables"], tab_length=2)

    text = "A nested list that was split into chunks. \n\n  - This is the first list item in this chunk."

    html_output = html_render(text, md)

    assert "<code>" not in html_output
    assert "<ul>\n<li>\n<ul>\n<li>" in html_output
