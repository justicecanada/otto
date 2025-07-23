import os

from django.conf import settings
from django.utils.translation import gettext as _

from smolagents import CodeAgent, LiteLLMModel, ToolCallingAgent

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
    generator = agent.run(user_message.text, stream=True)
    for chunk in generator:
        yield str(chunk.__dict__)
