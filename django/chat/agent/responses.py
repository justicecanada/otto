import os
import re

from django.http import HttpResponse, StreamingHttpResponse
from django.utils.translation import gettext as _

from chat.htmx_stream import wrap_llm_response
from chat.utils import md

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


def otto_agent(chat, response_message):

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
                if "response_message_id" in init_params:
                    init_params["response_message_id"] = response_message.id
                enabled_tools.append(tool_class(**init_params))

    if chat.options.agent_type == "code_agent":
        agent = CodeAgent(
            tools=enabled_tools,
            model=model,
            # planning_interval=1,
            instructions="IMPORTANT!!! Format the final answer in markdown.",
            stream_outputs=False,
        )
    else:
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


async def tool_calling_agent_replacer(response_stream):
    # Collect tool call steps and extract final answer
    steps = []
    final_answer = ""
    async for smolagents_message in response_stream:
        msg = smolagents_message.__dict__
        name = msg.get("name")
        # Final answer should be displayed as response, not in steps
        if name == "final_answer":
            # Sanitize final answer to remove control chars
            final_answer = msg.get("arguments", {}).get("answer", "")
            yield final_answer, steps
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
                steps.append({"output": output})
        yield final_answer, steps


async def code_agent_replacer(response_stream):
    # Similar to the tool_calling_agent_replacer, but for CodeAgent
    # Collect tool call steps and extract final answer
    steps = []
    final_answer = ""
    async for smolagents_message in response_stream:
        msg = smolagents_message.__dict__
        name = msg.get("name")
        keys = msg.keys()
        llm_output = msg.get("model_output_message")
        if llm_output and hasattr(llm_output, "content"):
            content = llm_output.content
            content = content.replace("<code>", "\n```python")
            content = content.replace("</code>", "```")
            content = content.replace("Thought:", f"**Thought:**")
            steps.append(
                {"thought": wrap_llm_response(content, div_class="agent-thought")}
            )
        action_output = msg.get("action_output")
        observations = msg.get("observations")
        if action_output or observations:
            output = sanitize(action_output or observations)
            steps.append({"output": output})
        if name == "python_interpreter":
            steps.append({"code": msg.get("arguments")})
        elif msg.get("is_final_answer"):
            final_answer = msg.get("output")
            yield final_answer, steps
            break
        yield final_answer, steps


def agent_response(chat, response_message):
    bind_contextvars(feature="chat_agent", chat_id=chat.id)
    user_message = response_message.parent

    llm = OttoLLM()

    agent = otto_agent(chat, response_message)

    sync_gen = agent_response_generator(agent, user_message)
    async_gen = async_generator_from_sync(sync_gen)

    if chat.options.agent_type == "code_agent":
        generator = code_agent_replacer(async_gen)
    else:
        generator = tool_calling_agent_replacer(async_gen)

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_replacer=generator,
            dots=True,
        ),
        content_type="text/event-stream",
    )
