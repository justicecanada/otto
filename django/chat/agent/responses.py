import os
import re

from django.http import HttpResponse, StreamingHttpResponse
from django.utils.translation import gettext as _

# Regex to remove control characters (excluding line breaks) from strings
# Removes: 0x00-0x09, 0x0B-0x0C, 0x0E-0x1F, 0x7F (but keeps 0x0A and 0x0D: LF and CR)
_CONTROL_CHAR_REGEX = re.compile(r"[\x00-\x09\x0B-\x0C\x0E-\x1F\x7F]")


def sanitize(obj):
    """
    Recursively remove control characters (except line breaks) from strings in the given object.
    """
    if isinstance(obj, str):
        return _CONTROL_CHAR_REGEX.sub("", obj)
    if isinstance(obj, dict):
        return {key: sanitize(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [sanitize(item) for item in obj]
    return obj


from smolagents import CodeAgent, LiteLLMModel, ToolCallingAgent  # type: ignore
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from chat.utils import async_generator_from_sync, htmx_stream

from .tools.tool_registry import AVAILABLE_TOOLS

# from openinference.instrumentation.smolagents import SmolagentsInstrumentor
# from phoenix.otel import register

# if settings.DEBUG:
#     register()
#     SmolagentsInstrumentor().instrument()


def otto_agent(chat):

    model_id = "azure/" + chat.options.agent_model
    model = LiteLLMModel(
        model_id=model_id,
        api_base=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version=os.environ.get("AZURE_OPENAI_VERSION"),
    )

    enabled_tools = []
    if chat.options.agent_tools:
        for tool_key in chat.options.agent_tools:
            if tool_key in AVAILABLE_TOOLS:
                tool_info = AVAILABLE_TOOLS[tool_key]
                tool_class = tool_info["class"]
                init_params = tool_info["init_params"].copy()
                if "chat_id" in init_params:
                    init_params["chat_id"] = chat.id
                if "user_id" in init_params:
                    init_params["user_id"] = chat.user.id
                enabled_tools.append(tool_class(**init_params))

    agent = ToolCallingAgent(
        tools=enabled_tools,
        model=model,
        # planning_interval=1,
        instructions="IMPORTANT!!! Format the final answer in markdown.",
        stream_outputs=False,
    )

    return agent


def agent_response_generator(agent, user_message):
    generator = agent.run(user_message.content_string, stream=True)
    for smolagents_message in generator:
        yield smolagents_message


def agent_response(chat, response_message):
    bind_contextvars(feature="chat_agent", chat_id=chat.id)
    user_message = response_message.parent

    llm = OttoLLM()

    agent = otto_agent(chat)

    sync_gen = agent_response_generator(agent, user_message)
    async_gen = async_generator_from_sync(sync_gen)

    async def chat_agent_replacer(response_stream):
        # Collect tool call steps and extract final answer
        steps = []
        final_answer = ""
        async for smolagents_message in response_stream:
            msg = smolagents_message.__dict__
            name = msg.get("name")
            # Final answer should be displayed as response, not in steps
            if name == "final_answer":
                # Sanitize final answer to remove control chars
                raw_answer = msg.get("arguments", {}).get("answer", "")
                final_answer = sanitize(raw_answer)
                yield raw_answer, steps
                break
            elif name is not None:
                # Replace with translated, pretty name if possible
                tool_name = name
                if tool_name in AVAILABLE_TOOLS:
                    tool_name = str(AVAILABLE_TOOLS[tool_name]["name"])
                # Sanitize arguments
                raw_args = msg.get("arguments", {})
                args = sanitize(raw_args)
                steps.append(
                    {
                        "name": tool_name,
                        "arguments": args,
                    }
                )
            elif msg.get("output") or msg.get("observations"):
                # capture output or observation from the agent
                if not msg.get("is_final_answer"):
                    raw_output = msg.get("output") or msg.get("observations")
                    output = sanitize(raw_output)
                    steps.append(
                        {
                            "output": output,
                        }
                    )
            yield final_answer, steps

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_replacer=chat_agent_replacer(async_gen),
            dots=True,
        ),
        content_type="text/event-stream",
    )
