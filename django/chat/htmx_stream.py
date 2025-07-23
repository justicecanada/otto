import asyncio
import html
import json
import uuid
from typing import AsyncGenerator, Generator

from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from asgiref.sync import sync_to_async
from structlog import get_logger

from chat.llm import OttoLLM
from chat.models import AnswerSource, Chat, ChatOptions, Message
from otto.models import CostType, SecurityLabel

logger = get_logger(__name__)


def wrap_llm_response(llm_response_str, div_class="markdown-text"):
    return f'<div class="{div_class}" data-md="{html.escape(json.dumps(str(llm_response_str)))}"></div>'


async def htmx_stream(
    chat: Chat,
    message_id: int,
    llm: OttoLLM,
    response_generator: Generator = None,
    response_replacer: AsyncGenerator = None,
    response_str: str = "",
    wrap_markdown: bool = True,
    dots: bool = False,
    source_nodes: list = [],
    remove_stop: bool = False,
    cost_warning_buttons: str = None,
) -> AsyncGenerator:
    """
    Formats responses into HTTP Server-Sent Events (SSE) for HTMX streaming.
    This function is a generator that yields SSE strings (lines starting with "data: ").

    There are 3 ways to use this function:
    1. response_generator: A custom generator that yields response chunks.
       Each chunk will be *appended* to the previous chunk.
    2. response_replacer: A custom generator that yields complete response strings.
       Unlike response_generator, each response will *replace* the previous response.
    3. response_str: A static response string.

    If dots is True, typing dots will be added to the end of the response.

    The function typically expects markdown responses from LLM, but can also handle
    HTML responses from other sources. Set wrap_markdown=False for plain HTML output.

    By default, the response will be saved as a Message object in the database after
    the response is finished. Set save_message=False to disable this behavior.
    """

    from chat.utils import (
        close_md_code_blocks,
        save_sources_and_update_security_label,
        stream_to_replacer,
        title_chat,
    )

    # Helper function to format a string as an SSE message
    def sse_string(
        message: str,
        wrap_markdown=True,
        dots=False,
        remove_stop=False,
        cost_warning_buttons=None,
        steps_html="",
    ) -> str:
        sse_joiner = "\ndata: "
        if wrap_markdown:
            message = steps_html + wrap_llm_response(message)
        if dots:
            message += dots
        out_string = "data: "
        out_string += sse_joiner.join(message.split("\n"))

        if cost_warning_buttons:
            # Render the form template asynchronously
            out_string += cost_warning_buttons

        if remove_stop:
            out_string += "<div hx-swap-oob='true' id='stop-button'></div>"

        out_string += "\n\n"  # End of SSE message
        return out_string

    ##############################
    # Start of the main function #
    ##############################
    message = await sync_to_async(Message.objects.get)(id=message_id)
    is_untitled_chat = chat.title.strip() == ""
    full_message = ""
    steps = []
    stop_warning_message = _(
        "Response stopped early. Costs may still be incurred after stopping."
    )
    generation_stopped = False
    dots_html = '<div class="typing"><span></span><span></span><span></span></div>'
    if dots:
        dots = dots_html
    try:
        if response_generator:
            response_replacer = stream_to_replacer(response_generator)
        if response_str:
            response_replacer = stream_to_replacer([response_str])

        # Stream the response text
        first_message = True
        async for response in response_replacer:

            if response is None:
                continue

            # If the response is a tuple, unpack it to "response, steps"
            if isinstance(response, tuple):
                response, steps = response
                message.details["steps"] = steps
                steps_html = render_to_string(
                    "chat/components/agent_steps.html",
                    {"message": message},
                )

            if response != "<|batchboundary|>":
                if remove_stop or not cache.get(f"stop_response_{message_id}", False):
                    full_message = response
                elif not generation_stopped:
                    generation_stopped = True
                    if wrap_markdown:
                        full_message = close_md_code_blocks(full_message)
                        stop_warning_message = f"\n\n_{stop_warning_message}_"
                    else:
                        stop_warning_message = f"<p><em>{stop_warning_message}</em></p>"
                    full_message = f"{full_message}{stop_warning_message}"
                    message = await sync_to_async(Message.objects.get)(id=message_id)
                    message.text = full_message
                    await sync_to_async(message.save)()
            elif generation_stopped:
                break
            # Avoid overwhelming client with markdown rendering:
            # slow down yields if the message is large
            length = len(full_message)
            yield_every = length // 2000 + 1
            # if length < 1000 or length % yield_every == 0:
            if True:
                yield sse_string(
                    full_message,
                    wrap_markdown,
                    dots=dots if not generation_stopped else False,
                    remove_stop=remove_stop or generation_stopped,
                    steps_html=steps_html,
                )
            await asyncio.sleep(0.01)

        yield sse_string(
            full_message,
            wrap_markdown,
            dots=False,
            remove_stop=True,
            steps_html=steps_html,
        )
        await asyncio.sleep(0.01)

        await sync_to_async(llm.create_costs)()

        message.text = full_message
        if steps:
            message.details["steps"] = steps
        finished_at = timezone.now()
        message.seconds_elapsed = (finished_at - message.date_created).total_seconds()
        await sync_to_async(message.save)()

        if is_untitled_chat:
            title_llm = OttoLLM()
            await sync_to_async(title_chat)(chat.id, force_title=False, llm=title_llm)
            await sync_to_async(title_llm.create_costs)()
            await sync_to_async(message.chat.refresh_from_db)()

        # Update message text with markdown wrapper to pass to template
        if wrap_markdown:
            message.text = wrap_llm_response(full_message)  # full_message)
        context = {"message": message, "swap_oob": True, "update_cost_bar": True}

        # Save sources and security label
        if source_nodes:
            await sync_to_async(save_sources_and_update_security_label)(
                source_nodes, message, chat
            )
            context["security_labels"] = await sync_to_async(
                SecurityLabel.objects.all
            )()

    except Exception as e:
        message = await sync_to_async(Message.objects.get)(id=message_id)
        full_message = _("An error occurred.")
        error_id = str(uuid.uuid4())[:7]
        full_message += f" _({_('Error ID:')} {error_id})_"
        logger.exception(
            "Error processing chat response",
            error_id=error_id,
            message_id=message.id,
            chat_id=chat.id,
        )
        message.text = full_message
        await sync_to_async(message.save)()
        message.text = wrap_llm_response(full_message)
        context = {"message": message, "swap_oob": True}

    # Render the message template, wrapped in SSE format
    context["message"].json = json.dumps(str(full_message))
    context["message"].mode = chat.options.mode
    context["mode"] = chat.options.mode
    context["show_steps"] = "true"

    yield sse_string(
        await sync_to_async(render_to_string)(
            "chat/components/chat_message.html", context
        ),
        wrap_markdown=False,
        remove_stop=True,
        cost_warning_buttons=cost_warning_buttons,
    )
