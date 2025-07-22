import os

from django.conf import settings
from django.utils.translation import gettext as _

from smolagents import (
    CodeAgent,
    LiteLLMModel,
    ToolCallingAgent,
    VisitWebpageTool,
    WebSearchTool,
)

from .tools.chat_history_retriever import ChatHistoryTool
from .tools.law_retriever import LawRetrieverTool
from .tools.tool_registry import AVAILABLE_TOOLS

# from openinference.instrumentation.smolagents import SmolagentsInstrumentor
# from phoenix.otel import register

# if settings.DEBUG:
#     register()
#     SmolagentsInstrumentor().instrument()


def format_tool_call(tool_call):
    """
    Format a ToolCall or ActionStep object for display.
    """
    # Prepare a nicely formatted step-by-step output for the user
    # Each step is a div.agent-step, with sub-divs for thought, tool call, and output
    # Try to handle both ActionStep and FinalAnswerStep, as well as generic dicts
    # 4. Final answer (if this is the last step)
    is_final = False
    if hasattr(tool_call, "is_final_answer") and getattr(
        tool_call, "is_final_answer", False
    ):
        is_final = True
    elif isinstance(tool_call, dict) and tool_call.get("is_final_answer"):
        is_final = True
    if getattr(tool_call, "name", None) == "final_answer":
        is_final = True
    if is_final:
        return ""

    step_html = ["<div class='agent-step'>"]
    # 1. Model thought/reasoning (if available)
    model_output = None
    if hasattr(tool_call, "model_output_message") and getattr(
        tool_call, "model_output_message", None
    ):
        llm_output = getattr(tool_call, "model_output_message")
        if hasattr(llm_output, "content") and llm_output.content:
            model_output = llm_output.content
    elif hasattr(tool_call, "model_output") and getattr(
        tool_call, "model_output", None
    ):
        model_output = getattr(tool_call, "model_output")
    elif isinstance(tool_call, dict) and tool_call.get("model_output"):
        model_output = tool_call["model_output"]
    if model_output:
        step_html.append(
            f"<div class='agent-thought'><b>Model thought:</b> {model_output}</div>"
        )

    # 2. Tool call (name + arguments)
    tool_name = None
    tool_args = None
    if hasattr(tool_call, "name") and hasattr(tool_call, "arguments"):
        tool_name = getattr(tool_call, "name")
        tool_args = getattr(tool_call, "arguments")
    elif (
        isinstance(tool_call, dict) and "name" in tool_call and "arguments" in tool_call
    ):
        tool_name = tool_call["name"]
        tool_args = tool_call["arguments"]
    if tool_name:
        step_html.append(
            f"<div class='agent-toolcall'><b>Tool:</b> {tool_name} <b>Args:</b> {tool_args}</div>"
        )

    # 3. Tool output/observation (if available)
    output_to_show = None
    # Try several possible fields for output/observation
    for key in ["action_output", "output", "observations", "observation"]:
        if hasattr(tool_call, key) and getattr(tool_call, key, None):
            output_to_show = getattr(tool_call, key)
            break
        elif isinstance(tool_call, dict) and tool_call.get(key):
            output_to_show = tool_call[key]
            break
    if output_to_show:
        step_html.append(f"<div class='agent-output'><pre>{output_to_show}</pre></div>")

    step_html.append("</div>")
    return "\n".join(step_html)


def otto_agent(chat):
    from chat.llm import chat_history_to_prompt
    from chat.utils import chat_to_history

    model_id = "azure/" + chat.options.agent_model
    model = LiteLLMModel(
        model_id=model_id,
        api_base=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version=os.environ.get("AZURE_OPENAI_VERSION"),
    )

    chat_history = chat_history_to_prompt(chat_to_history(chat))

    enabled_tools = []
    if chat.options.agent_tools:
        for tool_key in chat.options.agent_tools:
            if tool_key in AVAILABLE_TOOLS:
                tool_info = AVAILABLE_TOOLS[tool_key]
                tool_class = tool_info["class"]
                init_params = tool_info["init_params"].copy()
                if "chat_history" in init_params:
                    init_params["chat_history"] = chat_history
                enabled_tools.append(tool_class(**init_params))

    agent = ToolCallingAgent(
        tools=enabled_tools,
        model=model,
        # planning_interval=1,
        instructions="IMPORTANT!!! Format the final answer in markdown.",
    )

    return agent


def agent_response_generator(agent, user_message):
    # Run the agent with a task
    generator = agent.run(user_message.text, stream=True)

    yield f"<div class='agent-steps'>\n<p><em>{_('Thinking...')}</em></p>\n\n"
    for tool_call in generator:
        if getattr(tool_call, "name", None) == "final_answer":
            yield f"\n</div><div class='agent-final-answer'>\n\n{tool_call.arguments['answer']}\n</div>\n"
            break
        yield format_tool_call(tool_call)
    yield "\n</div>\n"
