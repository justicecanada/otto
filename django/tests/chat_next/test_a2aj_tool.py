import asyncio
from unittest.mock import MagicMock, patch

from django.urls import reverse

import pytest
from asgiref.sync import sync_to_async
from chat_next._llm.openai_responses import ResponsesAPIClient
from chat_next.models import Chat
from chat_next.tools import (
    ToolContext,
    fetch_canadian_case_by_citation,
    list_canadian_legal_datasets,
    search_canadian_case_law,
)


@pytest.mark.django_db
class TestLocalLegalResearchTools:
    def test_build_request_params_with_local_legal_research_tool(self, all_apps_user):
        """A2AJ legal-research tools should register as local function tools only."""
        user = all_apps_user()
        client = ResponsesAPIClient(
            model="gpt-5.1",
            tools=["local_legal_research"],
            user=user,
        )

        params = asyncio.run(
            client._build_request_params(
                input_items=[
                    {"role": "user", "content": "Search Canadian laws and cases"}
                ],
            )
        )

        assert params["model"] == "gpt-5.1"
        assert "tools" in params
        assert any(
            tool["type"] == "function"
            and tool["name"] == "list_canadian_legal_datasets"
            for tool in params["tools"]
        )
        assert any(
            tool["type"] == "function" and tool["name"] == "search_canadian_case_law"
            for tool in params["tools"]
        )
        assert any(
            tool["type"] == "function"
            and tool["name"] == "fetch_canadian_case_by_citation"
            for tool in params["tools"]
        )
        assert any(
            tool["type"] == "function" and tool["name"] == "search_canadian_legislation"
            for tool in params["tools"]
        )
        assert any(
            tool["type"] == "function"
            and tool["name"] == "fetch_canadian_legislation_by_citation"
            for tool in params["tools"]
        )
        assert all(tool["type"] != "mcp" for tool in params["tools"])

        search_case_tool = next(
            tool
            for tool in params["tools"]
            if tool["type"] == "function" and tool["name"] == "search_canadian_case_law"
        )
        size_schema = search_case_tool["parameters"]["properties"]["size"]
        assert size_schema["type"] == "integer"
        assert "1-50" in size_schema["description"]

        coverage_tool = next(
            tool
            for tool in params["tools"]
            if tool["type"] == "function"
            and tool["name"] == "list_canadian_legal_datasets"
        )
        assert coverage_tool["strict"] is False

        search_legislation_tool = next(
            tool
            for tool in params["tools"]
            if tool["type"] == "function"
            and tool["name"] == "search_canadian_legislation"
        )
        size_schema = search_legislation_tool["parameters"]["properties"]["size"]
        assert size_schema["type"] == "integer"
        assert "1-50" in size_schema["description"]

    def test_build_request_params_with_mixed_tools(self, all_apps_user):
        """A2AJ legal-research tools should compose with built-in tools."""
        user = all_apps_user()
        client = ResponsesAPIClient(
            model="gpt-5.1",
            tools=["code_interpreter", "local_legal_research"],
            user=user,
        )

        params = asyncio.run(
            client._build_request_params(
                input_items=[{"role": "user", "content": "Search laws"}],
            )
        )

        types = [t["type"] for t in params["tools"]]
        assert "code_interpreter" in types
        assert any(
            t["type"] == "function" and t["name"] == "search_canadian_legislation"
            for t in params["tools"]
        )


@pytest.mark.django_db
class TestA2AJPayloadFiltering:
    @pytest.mark.asyncio
    async def test_case_law_size_validation_matches_live_api_cap(self, all_apps_user):
        user = await sync_to_async(all_apps_user)()

        result = await search_canadian_case_law(
            {"query": "Charter", "size": 51},
            ToolContext(user=user),
        )

        assert result == {"error": "size must be an integer between 1 and 50"}

    @pytest.mark.asyncio
    async def test_list_canadian_legal_datasets_returns_grouped_coverage(
        self, all_apps_user
    ):
        user = await sync_to_async(all_apps_user)()

        case_response = MagicMock()
        case_response.raise_for_status.return_value = None
        case_response.json.return_value = {
            "results": [
                {
                    "dataset": "SCC",
                    "description_en": "Supreme Court of Canada",
                    "description_fr": "Cour suprême du Canada",
                    "earliest_document_date": "1876-01-01",
                    "latest_document_date": "2026-03-31",
                    "number_of_documents": 9876,
                }
            ]
        }

        law_response = MagicMock()
        law_response.raise_for_status.return_value = None
        law_response.json.return_value = {
            "results": [
                {
                    "dataset": "LEGISLATION-FED",
                    "description_en": "Federal statutes",
                    "description_fr": "Lois fédérales",
                    "earliest_document_date": "1867-07-01",
                    "latest_document_date": "2026-04-01",
                    "number_of_documents": 1200,
                }
            ]
        }

        with patch(
            "chat_next._utils.a2aj.requests.get",
            side_effect=[case_response, law_response],
        ) as mock_get:
            result = await list_canadian_legal_datasets({}, ToolContext(user=user))

        assert result["doc_type"] == "both"
        assert result["result_count"] == 2
        assert result["results"]["cases"][0]["dataset"] == "SCC"
        assert result["results"]["cases"][0]["description"] == "Supreme Court of Canada"
        assert result["results"]["laws"][0]["dataset"] == "LEGISLATION-FED"
        assert result["results"]["laws"][0]["descriptions"]["fr"] == "Lois fédérales"
        assert "dataset filter" in result["TIP"]
        assert mock_get.call_count == 2
        assert mock_get.call_args_list[0].kwargs["params"]["doc_type"] == "cases"
        assert mock_get.call_args_list[1].kwargs["params"]["doc_type"] == "laws"

    @pytest.mark.asyncio
    async def test_search_case_law_filters_noisy_upstream_metadata(self, all_apps_user):
        user = await sync_to_async(all_apps_user)()

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "results": [
                {
                    "dataset": "SCC",
                    "citation_en": "2020 SCC 5",
                    "citation_fr": "2020 CSC 5",
                    "citation2_en": "[2020] 1 SCR 166",
                    "name_en": "Nevsun Resources Ltd. v. Araya",
                    "name_fr": "Nevsun Resources Ltd. c. Araya",
                    "document_date_en": "2020-02-28T00:00:00+00:00",
                    "url_en": "https://example.test/en",
                    "url_fr": "https://example.test/fr",
                    "score": 1119.3268,
                    "snippet": "<em>Nevsun</em> Resources Ltd. v.",
                    "scraped_timestamp_en": "2023-04-13T15:24:45.124000+00:00",
                    "upstream_license": "very noisy license text",
                }
            ]
        }

        with patch("chat_next._utils.a2aj.requests.get", return_value=mock_response):
            result = await search_canadian_case_law(
                {"query": "Nevsun", "size": 2},
                ToolContext(user=user),
            )

        assert result["result_count"] == 1
        case = result["results"][0]
        assert case["citation"] == "2020 SCC 5"
        assert case["snippet"] == "Nevsun Resources Ltd. v."
        assert "25000 chars" in result["TIP"]
        assert "end_char=-1" in result["TIP"]
        assert "upstream_license" not in case
        assert "scraped_timestamp_en" not in case

    @pytest.mark.asyncio
    async def test_search_legislation_filters_noisy_upstream_metadata(
        self, all_apps_user
    ):
        user = await sync_to_async(all_apps_user)()

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "results": [
                {
                    "dataset": "LEGISLATION-FED",
                    "citation_en": "RSC 1985, c C-46",
                    "citation_fr": "LRC 1985, c C-46",
                    "citation2_en": "C-46",
                    "name_en": "Criminal Code",
                    "name_fr": "Code criminel",
                    "document_date_en": "1988-12-12T00:00:00+00:00",
                    "source_url_en": "https://example.test/source",
                    "num_sections_en": 1612,
                    "score": 173.33583,
                    "snippet": "<em>Criminal</em> Code",
                    "scraped_timestamp_en": "2026-03-01T00:00:00+00:00",
                    "upstream_license": "very noisy license text",
                }
            ]
        }

        with patch("chat_next._utils.a2aj.requests.get", return_value=mock_response):
            from chat_next.tools import search_canadian_legislation

            result = await search_canadian_legislation(
                {"query": "Criminal Code", "search_type": "name", "size": 2},
                ToolContext(user=user),
            )

        assert result["result_count"] == 1
        law = result["results"][0]
        assert law["citation"] == "RSC 1985, c C-46"
        assert law["title"] == "Criminal Code"
        assert law["num_sections"] == 1612
        assert law["snippet"] == "Criminal Code"
        assert "upstream_license" not in law
        assert "scraped_timestamp_en" not in law

    @pytest.mark.asyncio
    async def test_legislation_size_validation_matches_live_api_cap(
        self, all_apps_user
    ):
        user = await sync_to_async(all_apps_user)()

        from chat_next.tools import search_canadian_legislation

        result = await search_canadian_legislation(
            {"query": "Criminal Code", "size": 51},
            ToolContext(user=user),
        )

        assert result == {"error": "size must be an integer between 1 and 50"}

    @pytest.mark.asyncio
    async def test_fetch_case_by_citation_filters_license_and_defaults_to_slice(
        self, all_apps_user
    ):
        user = await sync_to_async(all_apps_user)()

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "results": [
                {
                    "dataset": "SCC",
                    "citation_en": "2020 SCC 5",
                    "name_en": "Nevsun Resources Ltd. v. Araya",
                    "document_date_en": "2020-02-28T00:00:00+00:00",
                    "url_en": "https://example.test/en",
                    "unofficial_text_en": "Case heading\n\nBody text",
                    "upstream_license": "very noisy license text",
                }
            ]
        }

        with patch(
            "chat_next._utils.a2aj.requests.get", return_value=mock_response
        ) as mock_get:
            result = await fetch_canadian_case_by_citation(
                {"citation": "2020 SCC 5"},
                ToolContext(user=user),
            )

        assert result["result_count"] == 1
        case = result["results"][0]
        assert case["text"] == "Case heading\n\nBody text"
        assert case["requested_window"]["start_char"] == 0
        assert case["requested_window"]["end_char"] == 25000
        assert "upstream_license" not in case
        mock_get.assert_called_once()
        assert mock_get.call_args.kwargs["params"]["doc_type"] == "cases"

    @pytest.mark.asyncio
    async def test_fetch_legislation_by_citation_supports_section_fetch(
        self, all_apps_user
    ):
        user = await sync_to_async(all_apps_user)()

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "results": [
                {
                    "dataset": "LEGISLATION-FED",
                    "citation_en": "RSC 1985, c C-46",
                    "name_en": "Criminal Code",
                    "document_date_en": "1988-12-12T00:00:00+00:00",
                    "source_url_en": "https://example.test/source",
                    "unofficial_text_en": "Criminal negligence (1) Every one is criminally negligent...",
                    "upstream_license": "very noisy license text",
                }
            ]
        }

        with patch(
            "chat_next._utils.a2aj.requests.get", return_value=mock_response
        ) as mock_get:
            from chat_next.tools import fetch_canadian_legislation_by_citation

            result = await fetch_canadian_legislation_by_citation(
                {"citation": "RSC 1985, c C-46", "section": "219"},
                ToolContext(user=user),
            )

        assert result["result_count"] == 1
        law = result["results"][0]
        assert law["text"].startswith("Criminal negligence")
        assert law["section_requested"] == "219"
        assert "requested_window" not in law
        assert "upstream_license" not in law
        mock_get.assert_called_once()
        assert mock_get.call_args.kwargs["params"]["doc_type"] == "laws"
        assert mock_get.call_args.kwargs["params"]["section"] == "219"


@pytest.mark.django_db
class TestApprovalAll:
    def test_handle_approval_all_adds_tool_to_auto_approve(self, client, all_apps_user):
        """Test that handle_approval_all adds tool to auto_approve list."""
        user = all_apps_user()
        client.force_login(user)

        # Create a chat with a message
        chat = Chat.objects.create(user=user)
        from chat_next.models import Message

        message = Message.objects.create(
            chat=chat,
            is_bot=True,
            text="Testing approval",
            details={"response_output": []},
        )

        # Call the approval_all endpoint
        url = reverse("chat_next:handle_approval_all", args=[message.id])
        response = client.get(url + "?tool_id=termium_lookup")

        assert response.status_code == 200

        # Verify the tool was added to auto_approve list
        chat.refresh_from_db()
        assert "termium_lookup" in chat.settings.chat_auto_approve_tools

    def test_handle_approval_all_canonicalizes_legacy_tool_label(
        self, client, all_apps_user
    ):
        """Legacy label-based approvals should still save the canonical tool ID."""
        user = all_apps_user()
        client.force_login(user)

        chat = Chat.objects.create(user=user)
        from chat_next.models import Message

        message = Message.objects.create(
            chat=chat,
            is_bot=True,
            text="Testing approval",
            details={"response_output": []},
        )

        url = reverse("chat_next:handle_approval_all", args=[message.id])
        response = client.get(url + "?tool_label=List%20Canadian%20legal%20datasets")

        assert response.status_code == 200

        chat.refresh_from_db()
        assert "list_canadian_legal_datasets" in chat.settings.chat_auto_approve_tools
        assert (
            "List Canadian legal datasets" not in chat.settings.chat_auto_approve_tools
        )
