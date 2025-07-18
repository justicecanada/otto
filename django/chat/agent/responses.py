import os


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
    # Try to extract the most relevant fields
    lines = []

    if type(tool_call).__name__ == "ActionStep":
        # Show the step number if available
        step_number = getattr(tool_call, "step_number", None)
        # if step_number is not None:
        #     lines.append(f"### Step {step_number}\n")
        lines += ["<div class='agent-step'>\n"]
        llm_output = getattr(tool_call, "model_output_message", None)
        if llm_output and hasattr(llm_output, "content"):
            content = llm_output.content
            content = content.replace("<code>", "\n```python")
            content = content.replace("</code>", "```")
            content = content.replace("Thought: ", f"**Step {step_number}:** ")
            lines.append(content)
        lines.append("</div>")

    elif type(tool_call).__name__ == "FinalAnswerStep":
        # Show the final answer
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
        model_id="azure/gpt-4.1",
        api_base=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version=os.environ.get("AZURE_OPENAI_VERSION"),
    )

    # Create an agent with no tools
    agent = CodeAgent(tools=[WebSearchTool(), VisitWebpageTool()], model=model)

    # Run the agent with a task
    generator = agent.run(user_message.text, stream=True)
    for tool_call in generator:
        yield format_tool_call(tool_call)
