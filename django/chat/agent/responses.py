import os

from django.conf import settings
from django.utils.translation import gettext as _

from smolagents import CodeAgent, LiteLLMModel, VisitWebpageTool, WebSearchTool

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
    lines = []

    if type(tool_call).__name__ == "ActionStep":
        step_number = getattr(tool_call, "step_number", None)
        lines += ["<div class='agent-step'>\n"]
        llm_output = getattr(tool_call, "model_output_message", None)
        if llm_output and hasattr(llm_output, "content"):
            content = llm_output.content
            content = content.replace("<code>", "\n```python")
            content = content.replace("</code>", "```")
            content = content.replace("Thought:", f"**Step {step_number}:**")
            lines.append(content)
        # Show LLM/code execution output in a dedicated div if present
        action_output = getattr(tool_call, "action_output", None)
        observations = getattr(tool_call, "observations", None)
        # Prefer 'action_output', but if not present, show 'observations' if available
        output_to_show = action_output or observations
        if output_to_show:
            lines.append(
                f"<div class='agent-output'>\n<pre>{output_to_show}</pre>\n</div>"
            )
        lines.append("</div>")

    elif type(tool_call).__name__ == "FinalAnswerStep":
        lines += ["<div class='agent-final-answer'>"]
        final_answer = getattr(tool_call, "output", None)
        if final_answer:
            content = (
                str(final_answer).replace("<code>", "```").replace("</code>", "```")
            )
            lines.append(content)
        lines += ["</div>"]

    return "\n".join(lines)


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

    agent = CodeAgent(
        tools=enabled_tools,
        model=model,
        stream_outputs=True,
        instructions="Format the final answer in markdown.",
    )

    return agent


def agent_response_generator(agent, user_message):
    # Run the agent with a task
    generator = agent.run(user_message.text, stream=True)

    yield f"<div class='agent-steps'>\n<p><em>{_('Thinking...')}</em></p>\n\n"
    for tool_call in generator:
        if type(tool_call).__name__ == "FinalAnswerStep":
            yield "\n</div>\n"
        yield format_tool_call(tool_call)
