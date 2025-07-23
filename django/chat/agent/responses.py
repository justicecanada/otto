import os

from django.http import HttpResponse, StreamingHttpResponse
from django.utils.translation import gettext as _

from smolagents import CodeAgent, LiteLLMModel, ToolCallingAgent
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
        response = ""
        async for smolagents_message in response_stream:
            response += str(smolagents_message.__dict__)
            yield response

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
