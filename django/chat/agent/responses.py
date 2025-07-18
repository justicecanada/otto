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

    # Show the step number if available
    step_number = getattr(tool_call, "step_number", None)
    if step_number is not None:
        lines.append(f"### Step {step_number}")

    # Show the LLM's output/thoughts if available
    # Try common attribute names
    llm_output = getattr(tool_call, "model_output_message", None)
    if llm_output and hasattr(llm_output, "content"):
        lines.append(f"**LLM output:**\n{llm_output.content.strip()}")
    else:
        # Fallback: try .output or .content
        output = getattr(tool_call, "output", None)
        if output:
            lines.append(f"**Output:**\n{output}")
        else:
            # Fallback: try .content
            content = getattr(tool_call, "content", None)
            if content:
                lines.append(f"**Content:**\n{content}")

    # # Show tool call details if available
    # tool_calls = getattr(tool_call, "tool_calls", None)
    # if tool_calls:
    #     lines.append("**Tool calls:**")
    #     for tc in tool_calls:
    #         name = getattr(tc, "name", None)
    #         arguments = getattr(tc, "arguments", None)
    #         lines.append(f"- `{name}` with arguments: `{arguments}`")
    # else:
    #     # Fallback: show name/arguments if present
    #     name = getattr(tool_call, "name", None)
    #     arguments = getattr(tool_call, "arguments", None)
    #     if name or arguments:
    #         lines.append(f"**Tool call:** `{name}` with arguments: `{arguments}`")

    # Show the final answer if available
    is_final = getattr(tool_call, "is_final_answer", False)
    if is_final:
        answer = getattr(tool_call, "output", None)
        if answer is not None:
            lines.append(f"**Final answer:** `{answer}`")

    # Add a separator
    lines.append("---\n")
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
