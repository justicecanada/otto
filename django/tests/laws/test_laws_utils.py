import asyncio
from unittest import mock

from django.core.cache import cache
from django.db import connections

import pytest
from asgiref.sync import sync_to_async

from laws.utils import (
    format_llm_string,
    get_law_url,
    get_other_lang_node,
    get_source_node,
    htmx_sse_error,
    htmx_sse_response,
    num_tokens,
)

# def test_get_other_lang_node(): TO-DO
#     node_id = "test_eng_node_id"
#     with mock.patch("utils.get_source_node") as mock_get_source_node:
#         mock_get_source_node.return_value = {
#             "text": "text",
#             "metadata": {"key": "value"},
#         }
#         result = get_other_lang_node(node_id)
#         assert result == {"text": "text", "metadata": {"key": "value"}}


def test_get_law_url():
    law = mock.MagicMock()
    law.ref_number = "Const"
    law.type = "act"
    assert (
        get_law_url(law, "en")
        == "https://laws-lois.justice.gc.ca/eng/Const/Const_index.html"
    )
    assert (
        get_law_url(law, "fr")
        == "https://laws-lois.justice.gc.ca/fra/ConstRpt/Const_index.html"
    )


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
            "data: <div hx-swap-oob='true' id='answer-sse'><div class=\"markdown-text\" data-md=\"&quot;Hello world&quot;\"></div><div class='mb-2 text-muted' style='font-size:0.875rem !important;'>$13.80</div></div>\n\n",
        ]

        assert result == expected


@pytest.mark.asyncio
async def test_htmx_sse_error():
    result = []
    async for item in htmx_sse_error():
        result.append(item)

    assert result == [
        "data: <div hx-swap-oob='true' id='answer-sse'><div>An error occurred while processing the request.</div></div>\n\n"
    ]
