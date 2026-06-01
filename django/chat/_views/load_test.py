"""
Load testing functions for chat functionality.
This module contains load testing scenarios for the chat app,
particularly focusing on file processing and summarization.
"""

import os

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.http import HttpResponse, HttpResponseServerError
from django.utils import timezone

from asgiref.sync import async_to_sync
from structlog import get_logger

from chat.models import Chat, ChatFile, Message
from chat.responses import chat_response, summarize_response
from librarian.models import SavedFile

logger = get_logger(__name__)

User = get_user_model()


def exhaust_streaming_response(streaming_response):
    """
    Helper function to fully consume a Django StreamingHttpResponse with async content.
    Returns tuple of (content_string, chunk_count).
    """

    @async_to_sync
    async def collect_all_chunks():
        response_content = ""
        chunk_count = 0
        async for chunk in streaming_response.streaming_content:
            if isinstance(chunk, bytes):
                response_content += chunk.decode("utf-8")
            else:
                response_content += str(chunk)
            chunk_count += 1
        return response_content, chunk_count

    return collect_all_chunks()


def create_test_chat_with_multiple_pdfs(
    user,
    title="Load Test Chat - Multiple Files",
    file_count=3,
    pdf_filename="example.pdf",
    summarize_model="gpt-4.1-nano",
):
    """
    Helper function to create a test chat with multiple uploaded PDF files.
    Returns tuple of (chat, user_message, response_message, [saved_files]).
    """
    # Create a chat for summarization
    chat = Chat.objects.create(user=user, title=title)
    chat.options.mode = "summarize"
    if summarize_model:
        # Use cheapest model for load testing unless overridden.
        chat.options.summarize_model = summarize_model
    chat.options.save()

    # Create user message
    user_message = Message.objects.create(
        chat=chat,
        text=f"Please summarize these {file_count} documents.",
        is_bot=False,
        mode="summarize",
    )

    # Create response message
    response_message = Message.objects.create(
        chat=chat, text="", is_bot=True, mode="summarize", parent=user_message
    )

    # Load test PDF file
    this_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    pdf_path = os.path.join(this_dir, f"tests/librarian/test_files/{pdf_filename}")

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Test PDF file not found at {pdf_path}")

    with open(pdf_path, "rb") as f:
        pdf_content = f.read()

    saved_files = []

    # Create multiple files (same content, different names)
    for i in range(file_count):
        # Create SavedFile
        saved_file = SavedFile.objects.create(content_type="application/pdf")
        filename = f"load_test_{i + 1}_{pdf_filename}"
        saved_file.file.save(filename, ContentFile(pdf_content))
        saved_file.generate_hash()
        saved_files.append(saved_file)

        # Create ChatFile to associate with the message
        ChatFile.objects.create(
            message=user_message,
            filename=filename,
            saved_file=saved_file,
        )

    return chat, user_message, response_message, saved_files


def create_test_chat_for_chat_mode(
    user,
    message_text="Hello",
    title="Load Test Chat - Chat Mode",
    chat_model=None,
):
    """
    Helper function to create a test chat in chat mode.
    Returns tuple of (chat, user_message, response_message).
    """
    chat = Chat.objects.create(user=user, title=title)
    chat.options.mode = "chat"
    if chat_model:
        chat.options.chat_model = chat_model
    chat.options.save()

    user_message = Message.objects.create(
        chat=chat,
        text=message_text,
        is_bot=False,
        mode="chat",
    )

    response_message = Message.objects.create(
        chat=chat, text="", is_bot=True, mode="chat", parent=user_message
    )

    return chat, user_message, response_message


def measure_streaming_response_performance(response_func, *args, **kwargs):
    """
    Helper function to measure the performance of a streaming response function.
    Returns dict with timing and response metrics.
    """
    start_time = timezone.now()

    try:
        response = response_func(*args, **kwargs)
        response_content, chunk_count = exhaust_streaming_response(response)

        end_time = timezone.now()
        total_time = (end_time - start_time).total_seconds()

        return {
            "success": True,
            "total_time": total_time,
            "content_length": len(response_content),
            "chunk_count": chunk_count,
            "content": response_content,
            "error": None,
        }
    except Exception as e:
        end_time = timezone.now()
        total_time = (end_time - start_time).total_seconds()

        return {
            "success": False,
            "total_time": total_time,
            "content_length": 0,
            "chunk_count": 0,
            "content": "",
            "error": str(e),
        }


def load_test_summarize_pdf(
    request,
    file_count=1,
    pdf_filename="example.pdf",
    summarize_model="gpt-4.1-nano",
):
    """
    Load test for the summarize_response function with PDF file(s).
    This simulates the most memory-intensive operation in the chat app.

    Args:
        request: Django request object
        file_count: Number of files to include in the test (default: 1)
        pdf_filename: Name of the PDF file to use (default: "example.pdf")
    """
    # Prefer the real request user (closer to production cost attribution),
    # but fall back to any existing user for non-auth load tests.
    # NOTE: This still bypasses the full HTMX upload/chat_message flow.
    test_user = (
        request.user
        if getattr(request, "user", None) and request.user.is_authenticated
        else User.objects.first()
    )
    if not test_user:
        return HttpResponseServerError("No users found in the database")

    try:
        # Create test chat with PDF(s) using the multiple files helper.
        # By default, force summarize_model to gpt-4.1-nano for cost control
        # unless a different model is supplied.
        chat, user_message, response_message, saved_files = (
            create_test_chat_with_multiple_pdfs(
                test_user,
                f"Load Test Chat - {file_count} x {pdf_filename}",
                file_count,
                pdf_filename,
                summarize_model=summarize_model,
            )
        )

        # Measure performance of the summarize response
        result = measure_streaming_response_performance(
            summarize_response, chat, response_message
        )

        # Clean up
        chat.delete()  # This will cascade delete messages and chat files
        for saved_file in saved_files:
            saved_file.safe_delete()

        if result["success"]:
            file_desc = (
                f"{file_count} x {pdf_filename}" if file_count > 1 else pdf_filename
            )
            return HttpResponse(
                f"PDF summarization load test ({file_desc}) completed in {result['total_time']:.2f} seconds. "
                f"Generated {result['content_length']} characters in {result['chunk_count']} chunks."
            )
        else:
            return HttpResponseServerError(
                f"PDF summarization failed after {result['total_time']:.2f} seconds: {result['error']}"
            )

    except Exception as e:
        logger.exception(f"Error in load_test_summarize_pdf: {e}")
        return HttpResponseServerError(f"Load test failed: {str(e)}")


def load_test_chat_stream(request, message_text="Hello", chat_model=None):
    """
    Load test for chat_response streaming (closest to the real chat SSE path).

    Args:
        request: Django request object
        message_text: Message content for the test user message
        chat_model: Optional override for chat.options.chat_model
    """
    test_user = (
        request.user
        if getattr(request, "user", None) and request.user.is_authenticated
        else User.objects.first()
    )
    if not test_user:
        return HttpResponseServerError("No users found in the database")

    try:
        chat, user_message, response_message = create_test_chat_for_chat_mode(
            test_user,
            message_text=message_text,
            title="Load Test Chat - Chat Streaming",
            chat_model=chat_model,
        )

        result = measure_streaming_response_performance(
            chat_response,
            chat,
            response_message,
            False,
            request=request,
        )

        chat.delete()

        if result["success"]:
            return HttpResponse(
                f"Chat streaming load test completed in {result['total_time']:.2f} seconds. "
                f"Generated {result['content_length']} characters in {result['chunk_count']} chunks."
            )
        return HttpResponseServerError(
            f"Chat streaming failed after {result['total_time']:.2f} seconds: {result['error']}"
        )
    except Exception as e:
        logger.exception(f"Error in load_test_chat_stream: {e}")
        return HttpResponseServerError(f"Load test failed: {str(e)}")
