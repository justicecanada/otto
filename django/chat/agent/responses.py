import os

from django.utils.translation import gettext as _


def agent_response_string(user_message, chat):
    """
    Generate a response string for the agent based on the user message and chat context.
    """
    # Placeholder for actual agent response logic
    # return f"Agent response to: {user_message} in chat: {chat.id}"

    from smolagents import CodeAgent, LiteLLMModel, VisitWebpageTool, WebSearchTool

    model = LiteLLMModel(
        model_id="azure/gpt-4.1",
        api_base=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version=os.environ.get("AZURE_OPENAI_VERSION"),
    )

    # Create an agent with no tools
    agent = CodeAgent(tools=[WebSearchTool(), VisitWebpageTool()], model=model)

    # Run the agent with a task
    result = agent.run(user_message.text, stream=False)
    return str(result)


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


def agent_response_generator(user_message, chat):
    """
    Generate a response string for the agent based on the user message and chat context.
    """
    # Placeholder for actual agent response logic
    # return f"Agent response to: {user_message} in chat: {chat.id}"

    from smolagents import CodeAgent, LiteLLMModel, VisitWebpageTool, WebSearchTool

    model = LiteLLMModel(
        model_id="azure/gpt-4.1-mini",
        api_base=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version=os.environ.get("AZURE_OPENAI_VERSION"),
    )

    # Create an agent with no tools
    agent = CodeAgent(tools=[WebSearchTool(), VisitWebpageTool()], model=model)

    # Run the agent with a task
    generator = agent.run(user_message.text, stream=True)
    yield f"<div class='agent-steps'>\n<p><em>{_('Thinking...')}</em></p>\n\n"
    for tool_call in generator:
        if type(tool_call).__name__ == "FinalAnswerStep":
            yield "\n</div>\n"
        yield format_tool_call(tool_call)
