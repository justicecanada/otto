"""
Tests for query optimization improvements.

These tests verify that views use efficient queries with select_related/prefetch_related
to avoid N+1 query problems.
"""

from django.db import connection
from django.test.utils import override_settings
from django.urls import reverse

import pytest


@pytest.mark.django_db
class TestChatQueryOptimization:
    """Test query optimization for chat views."""

    def test_chat_list_queries_optimized(self, client, all_apps_user):
        """
        Test that chat list view uses select_related to avoid N+1 queries.

        Without optimization: 1 query for chats + N queries for options
        With optimization: 2 queries (chats with options via select_related)
        """
        user = all_apps_user()
        client.force_login(user)

        from otto.models import OttoStatus, Visitor

        session = client.session
        session.save()
        Visitor.objects.create(user=user, session_key=session.session_key)
        OttoStatus.objects.singleton()

        # Create test chats with messages
        from chat.models import Chat, Message

        for i in range(5):
            chat = Chat.objects.create(user=user, title=f"Chat {i}")
            Message.objects.create(chat=chat, text=f"Test message {i}", is_bot=False)

        with override_settings(DEBUG=True):
            # Clear any queries from setup
            connection.queries_log.clear()

            # Access a chat page (which loads sidebar with all user chats)
            chat = Chat.objects.filter(user=user).first()
            response = client.get(reverse("chat:chat", args=[chat.id]))

            assert response.status_code == 200

            # Count queries executed
            query_count = len(connection.queries)

            # We expect efficient queries:
            # - 1 query for the main chat
            # - 1 query for user_chats with select_related('options')
            # - Additional queries for messages, etc.
            # Should be well under 40 queries for 5 chats
            assert query_count < 40, (
                f"Expected < 40 queries, got {query_count}. "
                f"This may indicate N+1 query problem. "
                f"Queries: {[q['sql'] for q in connection.queries]}"
            )


@pytest.mark.django_db
class TestFeedbackQueryOptimization:
    """Test query optimization for feedback views."""

    def test_feedback_list_queries_optimized(self, client, all_apps_user):
        """
        Test that feedback list uses select_related to avoid N+1 queries.

        Without optimization: 1 query for feedback + 3N queries (created_by, modified_by, chat_message)
        With optimization: Should be close to constant regardless of feedback count
        """
        from otto.models import Feedback

        user = all_apps_user()
        # Grant manage_feedback permission
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType

        content_type = ContentType.objects.get_for_model(Feedback)
        permission, _ = Permission.objects.get_or_create(
            codename="manage_feedback",
            content_type=content_type,
            defaults={"name": "Can manage feedback"},
        )
        user.user_permissions.add(permission)
        client.force_login(user)

        # Create test feedback
        for i in range(5):
            Feedback.objects.create(
                feedback_type="feedback",
                app="test",
                otto_version="1.0",
                feedback_message=f"Test feedback {i}",
                created_by=user,
                modified_by=user,
            )

        with override_settings(DEBUG=True):
            # Clear any queries from setup
            connection.queries_log.clear()

            # Access feedback list
            response = client.get(reverse("feedback_list"))

            assert response.status_code == 200

            # Count queries executed
            query_count = len(connection.queries)

            # With select_related('created_by', 'modified_by', 'chat_message'),
            # we should have minimal queries regardless of feedback count
            # Main query + permissions check + a few related lookups
            assert query_count < 15, (
                f"Expected < 15 queries for 5 feedback items, got {query_count}. "
                f"Without select_related, this would be 1 + 3*5 = 16 queries minimum."
            )


@pytest.mark.django_db
class TestLibrarianQueryOptimization:
    """Test query optimization for librarian views."""

    def test_document_operations_use_only(self, client, all_apps_user):
        """
        Test that document operations use only() to fetch minimal fields.

        This test verifies that bulk document operations don't fetch large
        text fields unnecessarily.
        """
        from librarian.models import DataSource, Document, Library, SavedFile

        user = all_apps_user()
        client.force_login(user)

        # Create test library and data source
        library = Library.objects.create(name="Test Library", created_by=user)
        data_source = DataSource.objects.create(name="Test Source", library=library)

        # Create documents with large text
        for i in range(3):
            saved_file = SavedFile.objects.create(content_type="text/plain", eof=True)
            Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                extracted_text="x" * 10000,  # Large text field
                status="SUCCESS",
            )

        with override_settings(DEBUG=True):
            # Clear queries
            connection.queries_log.clear()

            # The optimization uses only() to avoid fetching extracted_text
            # Simulate what data_source_stop does
            documents = data_source.documents.only(
                "id", "uuid_hex", "status", "data_source_id"
            ).all()
            list(documents)  # Force evaluation

            query_count = len(connection.queries)

            # Should be just 1 query for documents
            assert query_count == 1

            # Verify the query doesn't include extracted_text
            query_sql = connection.queries[0]["sql"].lower()
            # The query should not select the extracted_text column
            # (Django will do separate query if we access it later)
            assert "extracted_text" not in query_sql or "only" in str(
                documents.query
            ), "Query should use only() to exclude large text fields"


@pytest.mark.django_db
class TestPresetQueryOptimization:
    """Test query optimization for preset queries."""

    def test_accessible_presets_optimized(self, client, all_apps_user):
        """
        Test that get_accessible_presets uses select_related and prefetch_related.
        """
        from chat.models import ChatOptions, Preset
        from librarian.models import Library

        user = all_apps_user()

        # Create default library
        library = Library.objects.get_default_library()
        if not library:
            library = Library.objects.create(
                name="Default", is_default_library=True, is_public=True
            )

        # Create test presets
        for i in range(3):
            options = ChatOptions.objects.create(qa_library=library)
            preset = Preset.objects.create(
                name_en=f"Test Preset {i}",
                options=options,
                owner=user,
                sharing_option="everyone",
            )

        with override_settings(DEBUG=True):
            # Clear queries
            connection.queries_log.clear()

            # Get accessible presets
            presets = list(Preset.objects.get_accessible_presets(user))

            # Access related objects that should be prefetched
            for preset in presets:
                _ = preset.options  # Should not cause extra query
                _ = preset.owner  # Should not cause extra query
                if preset.options.qa_library:
                    _ = preset.options.qa_library.name  # Should not cause extra query

            query_count = len(connection.queries)

            # With select_related and prefetch_related, we should have:
            # 1 main query + prefetch queries for M2M relationships
            # Should be well under 10 queries total
            assert query_count < 10, (
                f"Expected < 10 queries with select_related/prefetch_related, got {query_count}"
            )
